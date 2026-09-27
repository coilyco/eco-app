# Per-repo task manifest. Run `just` (or `just --list`) to see every verb.
#
# Recipes take trailing arguments directly: `just <verb> a b`, where the
# retired form was `ward exec <verb> -- a b`.
#
# One line of comment per recipe on purpose: just reads only the LAST comment
# line above a recipe, so a wrapped description silently truncates to its tail.
#
# `ward exec` is retired, and so is the `.ward/ward.yaml` that outlived it.

set positional-arguments

# Default target: list every available recipe.
default:
    @just --list --unsorted

# uv lock + uv sync with dev deps.
sync *ARGS:
    @bash scripts/ward-command.sh sync "$@"

# Run the pytest suite (tests/mcp + tests/jobs + tests/replay).
test *ARGS:
    @bash scripts/ward-command.sh test "$@"

# ruff check + ruff format --check + mypy on src/ and tests/.
lint *ARGS:
    @bash scripts/ward-command.sh lint "$@"

# Apply ruff fixes and formatting in place.
fmt *ARGS:
    @bash scripts/ward-command.sh fmt "$@"

# Run all pre-commit hooks against every file.
precommit *ARGS:
    @bash scripts/ward-command.sh precommit "$@"

# End-to-end smoke test the MCP server via stdio.
smoke *ARGS:
    @bash scripts/ward-command.sh smoke "$@"

# Watch the resolved server's player list and report whether one join held. Native args - PLAYER [--server URL] [--timeout S] [--hold S] [--interval S] [--expect-version V] [--allow-already-online] [--json] [--quiet].
join-watch *ARGS:
    @bash scripts/ward-command.sh join-watch "$@"

# Regenerate data/eco_autogen_data.json from Eco's dedicated-server AutoGen C#. Downloads the server via anonymous steamcmd unless given a tree. Native args - [--root PATH] [--download-dir PATH] [--output PATH].
autogen-refresh *ARGS:
    @uv run python scripts/autogen_refresh.py "$@"

# Regenerate data/eco_trades_baseline.json.gz and eco_trades_norms.json.gz from #eco-trades. Native args - fetch RAW.jsonl (needs the tailnet discord MCP) | build RAW.jsonl | norms RAW.jsonl... --latest-cycle N.
trades-baseline *ARGS:
    @uv run python -m scripts.trades_baseline "$@"

# Install frontend deps into frontend/node_modules (pnpm).
frontend-install *ARGS:
    @pnpm --dir frontend install "$@"

# Vite dev server with HMR on :5173, proxying API routes to :4000.
frontend-dev *ARGS:
    @pnpm --dir frontend dev "$@"

# Typecheck + production-build the React SPA into frontend/dist.
frontend-build *ARGS:
    @pnpm --dir frontend build "$@"

# Run the frontend vitest suite.
frontend-test *ARGS:
    @pnpm --dir frontend test "$@"

# The coilyco kit's browser checks (axe, 16px floor, layering, overflow, weight) on every route. Native args - [--base URL] [--only /a,/b] [--json].
frontend-kit-check *ARGS:
    @pnpm --dir frontend kit-check "$@"

# ESLint over frontend/src.
frontend-lint *ARGS:
    @pnpm --dir frontend lint "$@"

# Run the fused server (MCP + /jobs) with autoreload, eco target auto-resolved. Native args - [--port PORT].
http *ARGS:
    @bash scripts/ward-command.sh http "$@"

# Run the fused server against the local snapshot fixture instead of a live eco server. Native args - [--port PORT] [--fixture-port PORT].
http-offline *ARGS:
    @bash scripts/ward-command.sh http-offline "$@"

# Capture every upstream eco-server dataset into a local snapshot dir. Native args - [--snapshot-dir PATH].
snapshot-capture *ARGS:
    @bash scripts/ward-command.sh snapshot-capture "$@"

# Tar the captured snapshot and push it to S3, timestamped plus latest. Native args - [--snapshot-dir PATH].
snapshot-push *ARGS:
    @bash scripts/ward-command.sh snapshot-push "$@"

# Pull a snapshot tarball from S3 into the local snapshot dir. Native args - [--snapshot STAMP] [--snapshot-dir PATH].
snapshot-pull *ARGS:
    @bash scripts/ward-command.sh snapshot-pull "$@"

# Replay the pulled snapshot as a fixture eco server on localhost. Native args - [--fixture-port PORT] [--snapshot-dir PATH].
snapshot-serve *ARGS:
    @bash scripts/ward-command.sh snapshot-serve "$@"

# Serve static/harness.html, the local Claude-Desktop-mimicking iframe host. Native args - [--port PORT].
harness *ARGS:
    @bash scripts/ward-command.sh harness "$@"

# Wire eco-mcp-app into Claude Desktop's claude_desktop_config.json.
install-desktop *ARGS:
    @python3 scripts/install_desktop_config.py "$@"

# Build the eco-app docker image locally. Native args - [--name NAME] [--revision REVISION].
build-docker *ARGS:
    @bash scripts/ward-command.sh build-docker "$@"

# Validate publisher syntax plus the trusted mod publisher's repository and credential handoff.
check-publish *ARGS:
    @bash scripts/publish-contract-test.sh "$@"

# Discover, restore, and build every real Eco mod project with per-project timings.
build-mods *ARGS:
    @sh scripts/mods-gate.sh build-mods "$@"

# Build deterministic, install-ready Eco mod ZIPs under .build/mod-packages. Native args - [--revision REVISION].
package-mods *ARGS:
    @bash scripts/ward-command.sh package-mods "$@"

# Publish .build/mod-packages to Forgejo's generic package registry. Requires FORGEJO_PACKAGE_URL, OWNER, USER, and TOKEN.
publish-mod-packages *ARGS:
    @python3 scripts/mod_packages.py publish "$@"

# Build the jobs-tracker Eco mod DLL (mods/jobs/src).
build-mod-jobs *ARGS:
    @dotnet build mods/jobs/src/EcoJobsTracker.csproj -c Release "$@"

# Build the replay Eco mod DLL (mods/replay/src).
build-mod-replay *ARGS:
    @dotnet build mods/replay/src/EcoReplay.csproj -c Release "$@"

# Restore and run the replay mod C# unit tests (mods/replay/tests) with per-phase timings.
test-mod-replay *ARGS:
    @sh scripts/mods-gate.sh test-mod-replay "$@"

# Build the telemetry Eco mod DLL (mods/telemetry).
build-mod-telemetry *ARGS:
    @dotnet build mods/telemetry/EcoTelemetry.csproj -c Release "$@"

# Build the store-exporter Eco mod DLL (mods/stores/src).
build-mod-stores *ARGS:
    @dotnet build mods/stores/src/EcoStoreExporter.csproj -c Release "$@"

# Run the jobs C# shell harness on :5100 (same API shape as the real Eco mod).
run-shell-jobs *ARGS:
    @dotnet run --project mods/jobs/shell/EcoJobsTracker.Shell.csproj "$@"

# Run the stores C# shell harness on :5101 (same API shape as the real Eco mod).
run-shell-stores *ARGS:
    @dotnet run --project mods/stores/shell/EcoStoreExporter.Shell.csproj "$@"
