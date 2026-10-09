#!/usr/bin/env bash
# The kit's browser checks against the built SPA, as CI runs them (COI-2057).
# Walkthrough: docs/frontend/kit-checks.md.
#
# The fused service serves frontend/dist with every upstream pointed at a port
# nothing listens on, so each page draws its outage state, the one a11y.test.tsx
# already checks. The real service is needed because /info is a server route and
# vite's dev proxy does not stand in for it.
#
# Browser: CHROME_PATH wins. Otherwise on Linux this installs Playwright's
# Chromium into the job itself (apt for the system libraries when root), so the
# shared runner image gains nothing. On a Mac kit-check uses the installed Chrome.
#
# Needs installed frontend deps and a built frontend/dist (just frontend-build).
# pnpm runs from frontend/ because corepack picks the pinned version from the
# package.json in the working directory, and from the repo root it picks latest.
set -euo pipefail

cd "$(dirname "$0")/.."

port="${KIT_CHECK_PORT:-4010}"
base="http://127.0.0.1:${port}"
dead="http://127.0.0.1:9"

if [ ! -f frontend/dist/index.html ]; then
  echo "frontend/dist is missing, run just frontend-build first" >&2
  exit 1
fi

if [ -z "${CHROME_PATH:-}" ] && [ "$(uname -s)" = "Linux" ]; then
  deps=()
  [ "$(id -u)" -eq 0 ] && deps=(--with-deps)
  (cd frontend && pnpm exec playwright-core install "${deps[@]}" chromium)
  CHROME_PATH="$(cd frontend && node --input-type=module -e \
    "import { chromium } from 'playwright-core'; console.log(chromium.executablePath())")"
  export CHROME_PATH
  echo "using ${CHROME_PATH}"
fi

log="$(mktemp)"
FRONTEND_DIST="${PWD}/frontend/dist" \
  ECO_INFO_URL="${dead}/info" \
  ECO_ADMIN_BASE_URL="${dead}" \
  ECO_MAP_BASE_URL="${dead}" \
  UPSTREAM_URL="${dead}/api/v1/skills" \
  ECO_REPLAY_UPSTREAM_URL="${dead}/api/v1/events" \
  UPSTREAM_API_KEY="ci-offline" \
  uv run --frozen --no-dev uvicorn eco_mcp_app.http_app:app --host 127.0.0.1 --port "${port}" \
  >"${log}" 2>&1 &
server=$!
trap 'kill "${server}" 2>/dev/null || true; rm -f "${log}"' EXIT

ready=0
for _ in $(seq 1 90); do
  if curl -fsS -o /dev/null "${base}/"; then
    ready=1
    break
  fi
  kill -0 "${server}" 2>/dev/null || break
  sleep 1
done
if [ "${ready}" -ne 1 ]; then
  echo "the fused service did not come up on ${base}" >&2
  cat "${log}" >&2
  exit 1
fi

status=0
(cd frontend && pnpm kit-check --base "${base}") || status=$?
if [ "${status}" -ne 0 ]; then
  echo "--- fused service log (last 40 lines)" >&2
  tail -n 40 "${log}" >&2
fi
exit "${status}"
