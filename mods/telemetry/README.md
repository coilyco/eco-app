# EcoTelemetry

OpenTelemetry observability for [Eco](https://play.eco) game servers. Exports exceptions,
runtime metrics, simulation perf and traces over OTLP to SigNoz, Grafana, Datadog, Honeycomb
or any OTLP backend. Eco's built-in stats cover economy and ecology, not SRE signals such as
process health, exception rates, tick latency or web-request latency. This fills that gap.

## Status
Early. v1 ships logs (exception capture via `AppDomain.UnhandledException`, optional
`FirstChanceException`, and an Eco `ILogWriter` decorator) and metrics (runtime GC,
threadpool, allocations, plus `eco.players.online`). Each signal can route to its own endpoint.
Traces are stubbed.

## Install
1. Download `EcoTelemetry-<version>.zip` from the
   [latest release](https://github.com/coilyco-flight-deck/eco-telemetry/releases) and extract
   it into the server's `Mods/` so you get `Mods/EcoTelemetry/EcoTelemetry.dll`.
2. Copy `Configs/EcoTelemetry.example.jsonc` to `Configs/EcoTelemetry.json` and set
   `OtlpEndpoint`, `OtlpHeaders` (backend auth), `ServiceName`, `ResourceAttributes`, and the
   toggles `EnableLogs`, `EnableMetrics`, `EnableTraces`.
3. Restart the server. An empty `OtlpEndpoint` falls back to the console exporter, to check
   the pipeline before pointing at a real backend.
## Build
`dotnet build EcoTelemetry.csproj -c Release` (or `just build-mod-telemetry`). It pulls
`Eco.ReferenceAssemblies` from NuGet for type-checking, and the DLL ships with its
OpenTelemetry dependencies (see `.github/workflows/release.yml`).
## Routing signals
`OtlpLogsEndpoint` and `OtlpMetricsEndpoint` override per signal, and a blank one falls back
to `OtlpEndpoint`. For **SigNoz**, point at the collector, which turns exception span events
into entries in the Exceptions pane. For **VictoriaMetrics**, vmsingle takes native OTLP at
`http://<vmsingle-host>:8428/opentelemetry/api/v1/push` with `OtlpMetricsProtocol`
`HttpProtobuf` and no auth header. Any OTLP backend works the same, with no backend-specific path.
## Sources, license, commands
Built against [Eco.ReferenceAssemblies](https://www.nuget.org/packages/Eco.ReferenceAssemblies),
<https://wiki.play.eco/en/Modding>, <https://docs.play.eco/> and
<https://github.com/StrangeLoopGames/EcoModKit>. MIT, see [LICENSE](LICENSE). Dev verbs are in
the repo-root [`justfile`](../../justfile). See also [AGENTS.md](../../AGENTS.md) and
[docs/FEATURES.md](docs/FEATURES.md).
