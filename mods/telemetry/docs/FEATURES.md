# EcoTelemetry features

Baseline to detect scope drift, refreshed 2026-07-02 at v0.1.0. Full: `git show b012073:mods/telemetry/docs/FEATURES.md`.
## What this is
An OpenTelemetry observability mod for Eco servers. It bridges Eco's economic and ecological
stats with SRE-shaped signals (logs, metrics, runtime health, exceptions) over OTLP to any
backend (SigNoz, Grafana, VictoriaMetrics, Honeycomb, Datadog). A precompiled DLL in `Mods/`
reads JSON config from `Configs/`.
## Signals
- **Exceptions:** `AppDomain.UnhandledException` always, plus an optional `FirstChanceException` hook.
- **Logs:** a reflection-based `ILogWriter` decorator sends warnings and errors through OTel.
- **Runtime metrics:** `OpenTelemetry.Instrumentation.Runtime` (GC, threadpool, allocations).
- **Eco metrics:** `eco.players.online`, `eco.world_objects.count` (tagged by object type),
  `eco.sim.world_time_seconds`, and a curated `eco.stats.*` set (population, economy). Callbacks
  are init-safe and never throw into the OTel reader.
- **Traces:** the `EcoTelemetry` `ActivitySource` over OTLP or console, with plugin-init spans
  and a slow-handler detector (`TraceSurface.TrackHandler`, `SlowHandlerThresholdMs`).
## HTTP surface
`GET /api/v1/climate-settings` is a read-only `[ApiController]` serving the live climate ruleset
(`EcoDef.Obj.ClimateSettings`: CO2 and temperature thresholds and rates, `PollutionMultiplier`,
animal and plant caps) as camelCase JSON, because Eco has no HTTP surface for them and the
eco-app `/climate` card would otherwise show Eco defaults on a retuned server
([eco-app#8](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/8)). Reflection-based and
best-effort like `mods/stores`: unreadable fields are `null`, and `404` before the simulation is
up is valid. It rides the `/api/v1/*` admin-token (`X-API-Key`) middleware.
## Configuration
Per-signal endpoint, protocol (gRPC or HttpProtobuf) and headers fall back to the top-level
defaults, and empty everywhere means console-only. `Configs/EcoTelemetry.json` loads at init
with comments and trailing commas allowed. Flags: `EnableLogs`, `EnableMetrics`,
`EnableTraces`. Export interval defaults to 15s, `SlowHandlerThresholdMs` to 100ms.
Operations, resilience and packaging: [operations.md](operations.md).
## Scope
- **Version:** v0.1.0, `net10.0` to match `EcoServerTargetFramework`, OpenTelemetry SDK 1.12.0.
- **Out of scope:** `PluginManager`-wide init spans and a Kestrel request hook (both need live
  server integration points, see internals.md), `IConfigurablePlugin` web UI, runtime changes to
  simulation logic, mod.io publishing.
- **Eco coupling:** `Eco.ReferenceAssemblies` 0.14.2-beta-release-1099 (cycle 15, 14.2.0), no
  compatibility guarantees. **Public discipline:** references anchor to public wikis and ModKit.

See also [README.md](../README.md) and [AGENTS.md](../../../AGENTS.md).
