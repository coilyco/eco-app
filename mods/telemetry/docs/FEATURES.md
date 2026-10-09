# EcoTelemetry features

Baseline snapshot of what this repo does. Compare against this to detect scope drift over time.

Last refreshed: 2026-07-02, against v0.1.0.

## What this repo is

EcoTelemetry is an OpenTelemetry-backed observability mod for Eco game servers. It bridges Eco's built-in economic and ecological stats with SRE-shaped operational signals (logs, metrics, runtime health, exceptions) and exports them over OTLP to any compatible backend (SigNoz, Grafana, VictoriaMetrics, Honeycomb, Datadog, others). Ships as a precompiled DLL that drops into the server's `Mods/` directory and reads JSON config from `Configs/`.

## Headline features

### Telemetry signals

- **Exception capture** - Subscribes to `AppDomain.UnhandledException` (always on) plus an optional `FirstChanceException` hook for high-volume exception tracing.
- **Log interception** - Mirrors Eco's built-in `ILogWriter` through the OTel logs pipeline via a reflection-based decorator. Warnings and errors flow to the backend.
- **Runtime metrics** - Auto-instruments .NET runtime via `OpenTelemetry.Instrumentation.Runtime` (GC, threadpool, memory allocation counters).
- **Eco game metrics** - Observable instruments pulled from live server state: `eco.players.online` (gauge), `eco.world_objects.count` (gauge, tagged by object type), `eco.sim.world_time_seconds` (counter), and a curated `eco.stats.*` gauge set (population + economy) from `GlobalStats`. All callbacks are init-safe and never throw into the OTel reader.
- **Traces** - A `TracerProvider` exports the `EcoTelemetry` `ActivitySource` over OTLP (or console). Ships plugin-init spans plus a config-driven slow-handler detector (`TraceSurface.TrackHandler`, `SlowHandlerThresholdMs`) that emits a span only when a timed handler exceeds the threshold.

### HTTP surfaces

- **Climate-settings endpoint** - A read-only `GET /api/v1/climate-settings` `[ApiController]` serializes the live per-server climate ruleset (`EcoDef.Obj.ClimateSettings`: `MinCO2ppm`, the temperature/sea-level thresholds and their ppm-per-degree / ppm-per-meter rates, `PollutionMultiplier`, and the animal/plant CO2 caps) as camelCase JSON. Eco exposes no HTTP surface for these values, so the eco-app `/climate` card otherwise ships hardcoded Eco defaults that silently disagree with a retuned server ([eco-app#8](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/8)); this mirrors the real thresholds. Reflection-based and best-effort (same defensive pattern as `mods/stores`): unreadable fields serialize as `null` and the consumer falls back to the documented Eco default per field; a `404` when the simulation isn't up yet is a valid answer. Rides Eco's existing `/api/v1/*` admin-token (`X-API-Key`) middleware - the mod adds no auth of its own.

### Configuration and routing

- **Per-signal endpoint overrides** - Logs can route to SigNoz, metrics to VictoriaMetrics, and so on. Each signal independently configurable with its own OTLP endpoint, protocol (gRPC or HttpProtobuf), and auth headers.
- **Fallback endpoint logic** - Per-signal endpoint, protocol, and headers fall back to top-level defaults. Empty everywhere triggers a console-only exporter for local validation.
- **JSON config file** - `Configs/EcoTelemetry.json`, loaded at plugin init. Comments and trailing commas supported. Sensible defaults plus optional resource attributes for service metadata.
- **Toggleable signals** - Feature flags `EnableLogs`, `EnableMetrics`, `EnableTraces`. Metrics export interval configurable (default 15s); slow-handler span threshold configurable via `SlowHandlerThresholdMs` (default 100ms). Traces route through the same per-signal endpoint/protocol/header override scheme as logs and metrics.

### Operations and build

Operational tooling, resilience, and packaging live in [operations.md](operations.md).

## Scope and boundaries

- **Version** - v0.1.0 (early). Targets `net10.0` to match current Eco `EcoServerTargetFramework`. Pinned to OpenTelemetry SDK 1.12.0.
- **Out of scope (today)** - `PluginManager`-wide init spans and a Kestrel request-pipeline hook (both need live-server integration points, see internals.md). No `IConfigurablePlugin` web UI integration. No runtime modification of game-simulation logic. Publishing to mod.io.
- **Eco version coupling** - Depends on the `Eco.ReferenceAssemblies` NuGet package (currently 0.14.2-beta-release-1099, matching the cycle 15 server on 14.2.0). No forward or backward compatibility guarantees.
- **Public repo discipline** - All references anchor to public wikis (`wiki.play.eco/en/Modding`, `docs.play.eco/`) and the official ModKit on GitHub. No internal Eco source leaks.

## See also

- [README.md](../README.md) - human-facing intro.
- [AGENTS.md](../../../AGENTS.md) - agent-facing operating rules.
- [justfile](../../../justfile) - dev verbs.

Cross-reference convention from [coilysiren/agentic-os#59](https://github.com/coilyco-flight-deck/agentic-os/issues/59).
