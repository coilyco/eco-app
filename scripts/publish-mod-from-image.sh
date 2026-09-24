#!/usr/bin/env bash
set -euo pipefail

registry="forgejo.coilysiren.me"
image_name="coilyco/eco-app"
dev_image="${registry}/coilyco-flight-deck/agentic-os:release"

for required in REGISTRY_TOKEN MOD_PACKAGE_NAME; do
  if [ -z "${!required:-}" ]; then
    echo "${required} is required for the trusted mod-package lane." >&2
    exit 1
  fi
done

sha="${GITHUB_SHA:-$(git rev-parse HEAD)}"
case "${sha}" in
  *[!0-9a-f]*|"")
    echo "eco-app source sha is not a lowercase hexadecimal commit id." >&2
    exit 1
    ;;
esac
if [ "${#sha}" -ne 40 ]; then
  echo "eco-app source sha must be a full 40-character commit id." >&2
  exit 1
fi

image="${registry}/${image_name}:${sha}"
docker_config="$(mktemp -d)"
package_dir="$(mktemp -d)"
image_container_id=""
publisher_container_id=""
cleanup() {
  if [ -n "${image_container_id}" ]; then
    docker rm -f "${image_container_id}" >/dev/null 2>&1 || true
  fi
  if [ -n "${publisher_container_id}" ]; then
    docker rm -f "${publisher_container_id}" >/dev/null 2>&1 || true
  fi
  rm -rf "${docker_config}" "${package_dir}"
}
trap cleanup EXIT
chmod 700 "${docker_config}" "${package_dir}"
export DOCKER_CONFIG="${docker_config}"

printf '%s' "${REGISTRY_TOKEN}" \
  | docker login "${registry}" --username coilyco-ops --password-stdin
docker pull "${image}"

image_container_id="$(docker create "${image}")"
docker cp "${image_container_id}:/mod-packages/." "${package_dir}/"
docker rm -f "${image_container_id}" >/dev/null
image_container_id=""

export FORGEJO_PACKAGE_URL="${GITHUB_SERVER_URL:-https://forgejo.coilysiren.me}"
export FORGEJO_PACKAGE_OWNER="coilyco"
export FORGEJO_PACKAGE_USER="coilyco-ops"
# The trusted publisher runner injects one current write:package credential.
# Reuse it for both OCI and generic packages so a stale repository Actions
# secret cannot diverge from the credential already proven by docker login.
export FORGEJO_PACKAGE_TOKEN="${REGISTRY_TOKEN}"
export MOD_PACKAGE_DIR="/packages"

repo_root="$(pwd -P)"
publisher_container_id="$(docker create \
  --workdir /workspace \
  --env FORGEJO_PACKAGE_URL \
  --env FORGEJO_PACKAGE_OWNER \
  --env FORGEJO_PACKAGE_USER \
  --env FORGEJO_PACKAGE_TOKEN \
  --env MOD_PACKAGE_DIR \
  --env MOD_PACKAGE_NAME \
  "${dev_image}" \
  just publish-mod-packages)"

# docker cp crosses the runner-to-daemon boundary through the Docker API.
# Bind mounts cannot use the checkout and package paths from the Actions job
# container because those paths do not exist in the publisher daemon.
docker cp "${repo_root}/." "${publisher_container_id}:/workspace/"
docker cp "${package_dir}" "${publisher_container_id}:/packages"
docker start --attach "${publisher_container_id}"
publisher_status="$(docker wait "${publisher_container_id}")"
if [ "${publisher_status}" -ne 0 ]; then
  echo "mod package publisher exited with status ${publisher_status}." >&2
  exit "${publisher_status}"
fi
docker rm -f "${publisher_container_id}" >/dev/null
publisher_container_id=""
