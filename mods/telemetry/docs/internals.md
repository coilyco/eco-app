# EcoTelemetry internals

Design notes behind the one-line pointers in `src/`. Full: `git show b012073:mods/telemetry/docs/internals.md`.
## Config (EcoTelemetryConfig)
Loaded from `Configs/EcoTelemetry.json` at plugin init, comments and trailing commas tolerated.
`OtlpEndpoint` is the fallback for any signal (empty means console exporter). `OtlpProtocol` is
`Grpc` or `HttpProtobuf` (default). `OtlpHeaders` is `key1=val1,key2=val2` for auth.
`OtlpLogsEndpoint`, `OtlpMetricsEndpoint`, `OtlpTracesEndpoint` override per signal, e.g. SigNoz
for logs and traces and VictoriaMetrics for metrics. `EmitConsoleAlongsideOtlp` adds a console
exporter for diagnosis. `SlowHandlerThresholdMs` (100) gates handler spans.
`FirstChanceExceptionsEnabled` catches every throw, high-volume, off by default.
`InterceptLogWriter` wraps Eco's `ILogWriter`.
## Plugin, exceptions, logs
Eco discovers the plugin via `IInitializablePlugin` and calls `Initialize` once. The
`AppDomain.UnhandledException` handler is always on. The first-chance handler must swallow its
own errors, since a logging failure inside it can re-enter and recurse. `Log.Writer` is set-once,
so `LogWriterInterceptor` replaces it by reflecting on the static backing field, and skips
silently if the field shape changes.
## Metrics worker (MetricsWorker)
Registers pull-based observable instruments on the `Meter`, so the OTel reader polls on its export interval. Instruments: `eco.players.online`,
`eco.world_objects.count` (tagged `eco.world_object.type`, via the static
`WorldObjectManager.ForEach`), `eco.sim.world_time_seconds`, a deliberately narrow `eco.stats.*`
slice of `GlobalStats`, and runtime counters. `UserManager`, `WorldObjectManager`, `WorldTime`
and `GlobalStats` can be unready early or mutate mid-iteration, so each callback runs in a `try`
and reports 0 or what it gathered rather than throwing into the reader.
## Pipeline (TelemetryPipeline)
Owns the LoggerFactory, MeterProvider and TracerProvider for the plugin lifetime. The metrics
path carries issue #5 diagnostics, to trim once proven: `Console.Error` prints show the config
branch, both `AddOtlpExporter` overloads silently added no reader so the exporter and reader are
built by hand for `builder.AddReader`, and a startup probe POSTs to the metrics endpoint and
writes `Logs/EcoTelemetry/smoke-probe.txt`.
## Trace surface (TraceSurface)
Owns the one `EcoTelemetry` `ActivitySource`. With traces off every helper returns `null`.
`Start(name)` wraps the plugin's own `Initialize`. `TrackHandler(name, work, tags...)` times
`work` and, only past `SlowHandlerThresholdMs`, emits a span backdated to the real start
(`StartActivity(..., startTime)` plus `SetEndTime`), so fast handlers stay allocation-free.
Exceptions mark the span `Error` and propagate. Not wired, pending live-server hook points:
`PluginManager` init wrapping and a Kestrel request hook.

See also [FEATURES.md](FEATURES.md) and [README.md](../README.md).
