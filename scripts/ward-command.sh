#!/usr/bin/env bash
set -euo pipefail

# dev-base sets this to a root-owned location. eco-app targets Python 3.13, so
# uv needs a user-writable managed-Python directory in agent containers.
export UV_PYTHON_INSTALL_DIR="${HOME}/.cache/uv-python"

usage_error() {
  echo "$1" >&2
  exit 2
}

run_http() {
  local http_port="$1"
  local base
  local key

  base="$(scripts/resolve-eco-target.sh)"
  key="${UPSTREAM_API_KEY:-}"
  if [[ -z "$key" ]]; then
    key="$(
      ward-kdl ops aws ssm get-parameter \
        --name /eco-mcp-app/api-admin-token \
        --with-decryption \
        --query Parameter.Value \
        --output text 2>/dev/null || true
    )"
  fi

  DEBUG=1 \
    ECO_INFO_URL="${ECO_INFO_URL:-${base}/info}" \
    ECO_ADMIN_BASE_URL="${ECO_ADMIN_BASE_URL:-$base}" \
    ECO_MAP_BASE_URL="${ECO_MAP_BASE_URL:-$base}" \
    UPSTREAM_URL="${UPSTREAM_URL:-${base}/api/v1/skills}" \
    ECO_REPLAY_UPSTREAM_URL="${ECO_REPLAY_UPSTREAM_URL:-${base}/api/v1/events}" \
    UPSTREAM_API_KEY="$key" \
    ECO_ADMIN_TOKEN="${ECO_ADMIN_TOKEN:-$key}" \
    ECO_ADMIN_API_KEY="${ECO_ADMIN_API_KEY:-$key}" \
    exec uv run uvicorn eco_mcp_app.http_app:app \
      --reload \
      --reload-dir src \
      --host 0.0.0.0 \
      --port "$http_port"
}

snapshot_temp_dir=""
cleanup_snapshot_temp() {
  if [[ -n "$snapshot_temp_dir" ]]; then
    rm -rf -- "$snapshot_temp_dir"
  fi
}

action="${1:-}"
shift || true

case "$action" in
  sync)
    uv lock
    uv sync --group dev
    ;;
  test)
    exec uv run pytest "$@"
    ;;
  lint)
    uv run ruff check src tests scripts
    uv run ruff format --check src tests scripts
    uv run mypy src tests scripts
    ;;
  fmt)
    uv run ruff check --fix src tests scripts
    uv run ruff format src tests scripts
    ;;
  precommit)
    exec uv run pre-commit run --all-files "$@"
    ;;
  join-watch)
    # Resolved the same way as run_http so a watch and a dev server never end up
    # pointed at different servers. An explicit ECO_INFO_URL still wins.
    base="$(scripts/resolve-eco-target.sh)"
    ECO_INFO_URL="${ECO_INFO_URL:-${base}/info}" \
      exec uv run python scripts/join_watch.py "$@"
    ;;
  smoke)
    (
      printf '%s\n' \
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"claude-ai","version":"0.1.0"}}}' \
        '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
        '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
        '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_server_status","arguments":{}}}'
      sleep 8
    ) | uv run python -m eco_mcp_app
    ;;
  http)
    http_port=4000
    while [[ "$#" -gt 0 ]]; do
      case "$1" in
        --port)
          [[ "$#" -ge 2 ]] || usage_error "--port requires a value"
          http_port="$2"
          shift 2
          ;;
        *)
          usage_error "usage: $0 http [--port PORT]"
          ;;
      esac
    done
    run_http "$http_port"
    ;;
  http-offline)
    http_port=4000
    fixture_port=3101
    while [[ "$#" -gt 0 ]]; do
      case "$1" in
        --port)
          [[ "$#" -ge 2 ]] || usage_error "--port requires a value"
          http_port="$2"
          shift 2
          ;;
        --fixture-port)
          [[ "$#" -ge 2 ]] || usage_error "--fixture-port requires a value"
          fixture_port="$2"
          shift 2
          ;;
        *)
          usage_error "usage: $0 http-offline [--port PORT] [--fixture-port PORT]"
          ;;
      esac
    done
    export ECO_INFO_URL="http://localhost:${fixture_port}/info"
    export UPSTREAM_API_KEY="${UPSTREAM_API_KEY:-offline-fixture}"
    run_http "$http_port"
    ;;
  snapshot-capture)
    snapshot_dir=".snapshots/current"
    while [[ "$#" -gt 0 ]]; do
      case "$1" in
        --snapshot-dir)
          [[ "$#" -ge 2 ]] || usage_error "--snapshot-dir requires a value"
          snapshot_dir="$2"
          shift 2
          ;;
        *)
          usage_error "usage: $0 snapshot-capture [--snapshot-dir PATH]"
          ;;
      esac
    done
    base="$(scripts/resolve-eco-target.sh)"
    key="${UPSTREAM_API_KEY:-}"
    if [[ -z "$key" ]]; then
      key="$(
        ward-kdl ops aws ssm get-parameter \
          --name /eco-mcp-app/api-admin-token \
          --with-decryption \
          --query Parameter.Value \
          --output text 2>/dev/null || true
      )"
    fi
    UPSTREAM_API_KEY="$key" \
      exec uv run python -m eco_snapshot capture \
        --base-url "$base" \
        --out "$snapshot_dir"
    ;;
  snapshot-push)
    snapshot_dir=".snapshots/current"
    while [[ "$#" -gt 0 ]]; do
      case "$1" in
        --snapshot-dir)
          [[ "$#" -ge 2 ]] || usage_error "--snapshot-dir requires a value"
          snapshot_dir="$2"
          shift 2
          ;;
        *)
          usage_error "usage: $0 snapshot-push [--snapshot-dir PATH]"
          ;;
      esac
    done
    [[ -f "${snapshot_dir}/manifest.json" ]] ||
      usage_error "no snapshot at ${snapshot_dir}. Run snapshot-capture first."
    stamp="$(date -u +%Y-%m-%dT%H%M%SZ)"
    snapshot_temp_dir="$(mktemp -d)"
    trap cleanup_snapshot_temp EXIT
    archive="${snapshot_temp_dir}/snapshot.tar.gz"
    bucket="s3://kai-game-backups/eco-app/snapshots"
    tar -czf "$archive" -C "$snapshot_dir" .
    ward-kdl ops aws s3 cp "$archive" "${bucket}/${stamp}.tar.gz"
    ward-kdl ops aws s3 cp "${bucket}/${stamp}.tar.gz" "${bucket}/latest.tar.gz"
    echo "pushed ${bucket}/${stamp}.tar.gz (+ latest.tar.gz)"
    ;;
  snapshot-pull)
    snapshot="latest"
    snapshot_dir=".snapshots/current"
    while [[ "$#" -gt 0 ]]; do
      case "$1" in
        --snapshot)
          [[ "$#" -ge 2 ]] || usage_error "--snapshot requires a value"
          snapshot="$2"
          shift 2
          ;;
        --snapshot-dir)
          [[ "$#" -ge 2 ]] || usage_error "--snapshot-dir requires a value"
          snapshot_dir="$2"
          shift 2
          ;;
        *)
          usage_error "usage: $0 snapshot-pull [--snapshot STAMP] [--snapshot-dir PATH]"
          ;;
      esac
    done
    snapshot_path="$(realpath -m -- "$snapshot_dir")"
    repo_path="$(pwd -P)"
    case "$snapshot_path" in
      / | "$HOME" | "$repo_path")
        usage_error "refusing unsafe snapshot directory: ${snapshot_dir}"
        ;;
    esac
    snapshot_temp_dir="$(mktemp -d)"
    trap cleanup_snapshot_temp EXIT
    archive="${snapshot_temp_dir}/snapshot.tar.gz"
    bucket="s3://kai-game-backups/eco-app/snapshots"
    ward-kdl ops aws s3 cp "${bucket}/${snapshot}.tar.gz" "$archive"
    rm -rf -- "$snapshot_path"
    mkdir -p -- "$snapshot_path"
    tar -xzf "$archive" -C "$snapshot_path"
    echo "pulled ${bucket}/${snapshot}.tar.gz into ${snapshot_dir}"
    ;;
  snapshot-serve)
    fixture_port=3101
    snapshot_dir=".snapshots/current"
    while [[ "$#" -gt 0 ]]; do
      case "$1" in
        --fixture-port)
          [[ "$#" -ge 2 ]] || usage_error "--fixture-port requires a value"
          fixture_port="$2"
          shift 2
          ;;
        --snapshot-dir)
          [[ "$#" -ge 2 ]] || usage_error "--snapshot-dir requires a value"
          snapshot_dir="$2"
          shift 2
          ;;
        *)
          usage_error "usage: $0 snapshot-serve [--fixture-port PORT] [--snapshot-dir PATH]"
          ;;
      esac
    done
    exec uv run python -m eco_snapshot serve \
      --dir "$snapshot_dir" \
      --port "$fixture_port"
    ;;
  harness)
    harness_port=8765
    while [[ "$#" -gt 0 ]]; do
      case "$1" in
        --port)
          [[ "$#" -ge 2 ]] || usage_error "--port requires a value"
          harness_port="$2"
          shift 2
          ;;
        *)
          usage_error "usage: $0 harness [--port PORT]"
          ;;
      esac
    done
    echo "Harness: http://localhost:${harness_port}/static/harness.html"
    exec python3 -m http.server "$harness_port"
    ;;
  build-docker)
    image_name="eco-app"
    revision="$(git rev-parse HEAD 2>/dev/null || echo dev)"
    while [[ "$#" -gt 0 ]]; do
      case "$1" in
        --name)
          [[ "$#" -ge 2 ]] || usage_error "--name requires a value"
          image_name="$2"
          shift 2
          ;;
        --revision)
          [[ "$#" -ge 2 ]] || usage_error "--revision requires a value"
          revision="$2"
          shift 2
          ;;
        *)
          usage_error "usage: $0 build-docker [--name NAME] [--revision REVISION]"
          ;;
      esac
    done
    exec docker build \
      --progress plain \
      --build-arg "MOD_SOURCE_REVISION=${revision}" \
      -t "${image_name}:${revision}" \
      -t "${image_name}:latest" \
      .
    ;;
  package-mods)
    revision="$(git rev-parse HEAD 2>/dev/null || echo dev)"
    while [[ "$#" -gt 0 ]]; do
      case "$1" in
        --revision)
          [[ "$#" -ge 2 ]] || usage_error "--revision requires a value"
          revision="$2"
          shift 2
          ;;
        *)
          usage_error "usage: $0 package-mods [--revision REVISION]"
          ;;
      esac
    done
    sh scripts/mods-gate.sh build-mods
    exec python3 scripts/mod_packages.py package \
      --output .build/mod-packages \
      --revision "$revision"
    ;;
  *)
    usage_error "unknown Ward action: ${action}"
    ;;
esac
