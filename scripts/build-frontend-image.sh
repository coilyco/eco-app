#!/usr/bin/env bash
# Builds the Dockerfile frontend stage from the repo-root context, without publishing.
# This is the PR gate that catches a Dockerfile context miss - a frontend `data/` import
# with no matching COPY - before it breaks main's build-image, which the pull_request
# event skips (eco-app#8316). The failure PR 347 shipped was TS2307 for
# data/server_brief.json, fixed by #348.
set -euo pipefail

cd "$(dirname "$0")/.."

docker build \
  --target frontend \
  --build-arg VITE_SENTRY_DSN="" \
  -t eco-app:pr-frontend-check \
  .

echo "frontend stage build succeeded"
