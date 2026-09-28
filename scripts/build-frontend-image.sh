#!/usr/bin/env bash
# Builds the Dockerfile frontend stage from the repo-root context, without
# publishing. This is the PR gate that catches a Dockerfile context miss - a
# frontend `data/` import with no matching COPY - before it breaks main's
# build-image, which the pull_request event skips (eco-app#8316). The failure
# PR 347 shipped was TS2307 for data/server_brief.json, fixed by #348.
#
# The job runs under the `docker` runner in the agentic-os:release container,
# on the docker sidecar's bridge. That daemon has no direct egress, so pnpm
# inside the build reaches npm through FORGEJO_EGRESS_PROXY, exactly as
# scripts/publish-image.sh does for the full build. The base image is already
# in the daemon's image store (it is what the job container itself runs), so
# --pull=false keeps this step off the network for the base image.
set -euo pipefail

default_gateway() {
  local raw
  raw="$(awk '$2 == "00000000" && $8 == "00000000" { print $3; exit }' /proc/net/route 2>/dev/null)"
  [ -n "$raw" ] || return 1
  printf '%d.%d.%d.%d' \
    "0x${raw:6:2}" "0x${raw:4:2}" "0x${raw:2:2}" "0x${raw:0:2}"
}

resolve_docker_host() {
  local gateway
  if [ -n "${DOCKER_HOST:-}" ]; then
    printf '%s\n' "$DOCKER_HOST"
  fi
  if gateway="$(default_gateway)"; then
    printf 'tcp://%s:2375\n' "$gateway"
  fi
  printf 'tcp://172.17.0.1:2375\n'
  printf 'unix:///var/run/docker.sock\n'
}

host=""
while read -r candidate; do
  [ -z "$candidate" ] && continue
  if DOCKER_HOST="$candidate" timeout 20 docker version >/dev/null 2>&1; then
    host="$candidate"
    break
  fi
  echo "docker daemon not reachable at ${candidate}" >&2
done < <(resolve_docker_host)

if [ -z "$host" ]; then
  echo "no reachable docker daemon; cannot build the frontend stage here" >&2
  exit 1
fi

export DOCKER_HOST="$host"
echo "building frontend stage against ${host}"

docker build \
  --pull=false \
  --target frontend \
  --build-arg HTTP_PROXY="${FORGEJO_EGRESS_PROXY:-}" \
  --build-arg HTTPS_PROXY="${FORGEJO_EGRESS_PROXY:-}" \
  --build-arg NO_PROXY="forgejo.coilysiren.me,forgejo.forgejo.svc.cluster.local" \
  --build-arg VITE_SENTRY_DSN="" \
  -t eco-app:pr-frontend-check \
  .

echo "frontend stage build succeeded"
