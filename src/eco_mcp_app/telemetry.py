"""OpenTelemetry and Sentry setup for eco-app.

The deploy surface points private workloads at SigNoz's OTLP/HTTP NodePort.
ASGI instrumentation records uncaught exceptions as span events and marks the
request span as an error, which feeds SigNoz Exceptions.

Sentry also runs when SENTRY_DSN is set: uncaught exceptions as errors, every
non-health, non-static request as a trace, and ERROR-level log records as Sentry
Logs. Handled errors still do not become error events (COI-2905, parent COI-2901).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qsl, urlencode

import sentry_sdk
from mcp import types as mt
from mcp.server.lowlevel import Server
from opentelemetry import metrics, propagate, trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
from opentelemetry.metrics import Counter as ApiCounter
from opentelemetry.sdk.metrics import Counter, Histogram, MeterProvider
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Link, SpanKind, Status, StatusCode
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber

if TYPE_CHECKING:
    from sentry_sdk.types import Event, Log

_log = logging.getLogger(__name__)
_initialized = False
_enabled = False
_trace_provider: TracerProvider | None = None
_meter_provider: MeterProvider | None = None
_mcp_tool_calls: ApiCounter | None = None
_HEALTHCHECK_PATH = "/healthz"
_MCP_HANDLER_MARKER = "_eco_app_mcp_traced"
_ACTIVITY_METRIC_TEMPORALITY: dict[type, AggregationTemporality] = {
    Counter: AggregationTemporality.DELTA,
    Histogram: AggregationTemporality.DELTA,
}


class _HealthcheckAccessFilter(logging.Filter):
    """Drop only Uvicorn access records for the exact healthcheck path."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3:
            path = str(args[2]).partition("?")[0]
            return path != _HEALTHCHECK_PATH
        return True


def configure_healthcheck_logging() -> None:
    """Keep Kubernetes probes out of the web server's access log."""
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, _HealthcheckAccessFilter) for item in access_logger.filters):
        access_logger.addFilter(_HealthcheckAccessFilter())


_sentry_initialized = False
_sentry_active = False

# Team plan: 50k errors, 5M spans, 5GB logs a month. The pod's access log on
# 2026-10-25 held about 8 requests an hour outside health checks, which is roughly
# 6k transactions and 100k spans a month at 1.0, so sampling everything leaves
# about 50x headroom. Re-size this from Sentry's own span stats if traffic grows.
SENTRY_TRACES_SAMPLE_RATE = 1.0
# Health probes and static bytes carry no signal and would spend the span quota.
_SENTRY_UNTRACED_PREFIXES = (_HEALTHCHECK_PATH, "/assets/", "/og/")


def _sentry_traces_sampler(context: dict[str, Any]) -> float:
    path = str((context.get("asgi_scope") or {}).get("path", ""))
    if path.startswith(_SENTRY_UNTRACED_PREFIXES):
        return 0.0
    parent = context.get("parent_sampled")
    return float(parent) if parent is not None else SENTRY_TRACES_SAMPLE_RATE


# Frame locals and request bodies stay on, because they make a crash readable.
# These keys hold Discord member text, admin credentials, or raw bodies.
ECO_USER_DATA_KEYS = [
    "content",
    "message",
    "messages",
    "text",
    "author",
    "member",
    "username",
    "interaction",
    "query",
    "body",
    "payload",
    "admin_token",
    # FastAPI's raw request bytes, held in its own routing frame.
    "body_bytes",
]
_SCRUBBED = "[Filtered]"
_DENYLIST = {key.lower() for key in DEFAULT_DENYLIST + ECO_USER_DATA_KEYS}


def _sentry_scrub_query_string(event: Event, _hint: dict[str, Any]) -> Event:
    """Filter denylisted values out of the raw query string, which the event scrubber skips."""
    request = event.get("request")
    if isinstance(request, dict) and request.get("query_string"):
        pairs = parse_qsl(str(request["query_string"]), keep_blank_values=True)
        request["query_string"] = urlencode(
            [(k, _SCRUBBED if k.lower() in _DENYLIST else v) for k, v in pairs], safe="[]"
        )
    return event


def _sentry_before_send_log(log: Log, _hint: dict[str, Any]) -> Log | None:
    """Apply the error-event key denylist to a log's attributes, as the event scrubber does."""
    attributes = log.get("attributes")
    if isinstance(attributes, dict):
        for key in attributes:
            if key.lower() in _DENYLIST:
                attributes[key] = _SCRUBBED
    return log


def init_error_tracking() -> bool:
    """Send unhandled exceptions, traces and ERROR logs to Sentry once, when SENTRY_DSN is set."""
    global _sentry_active, _sentry_initialized
    if _sentry_initialized:
        return _sentry_active
    _sentry_initialized = True
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        sentry_sdk.init(
            dsn=dsn,
            traces_sampler=_sentry_traces_sampler,
            enable_logs=True,
            before_send=_sentry_scrub_query_string,
            before_send_transaction=_sentry_scrub_query_string,
            before_send_log=_sentry_before_send_log,
            environment=os.getenv("OTEL_DEPLOYMENT_ENVIRONMENT", "homelab"),
            send_default_pii=False,
            event_scrubber=EventScrubber(
                denylist=DEFAULT_DENYLIST + ECO_USER_DATA_KEYS, recursive=True
            ),
            integrations=[
                # Breadcrumbs, plus ERROR records as Sentry Logs. An ERROR log is a handled
                # error, so it is never an error event and never spends the error quota.
                LoggingIntegration(event_level=None, sentry_logs_level=logging.ERROR),
                # Only uncaught exceptions, never an HTTPException the app raised on purpose.
                StarletteIntegration(failed_request_status_codes=set()),
                FastApiIntegration(failed_request_status_codes=set()),
            ],
        )
        sentry_sdk.set_tag(
            "service.name", os.getenv("OTEL_SERVICE_NAME", "eco-app").strip() or "eco-app"
        )
    except Exception as exc:
        # The class only: a BadDsn message can carry the DSN itself.
        _log.warning("Sentry initialization failed (%s); continuing", type(exc).__name__)
        return False
    _sentry_active = True
    return True


def init_telemetry() -> bool:
    """Configure OTLP tracing and metrics once and report whether either is enabled."""
    global _enabled, _initialized, _mcp_tool_calls, _meter_provider, _trace_provider
    init_error_tracking()
    if _initialized:
        return _enabled

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    service_name = os.getenv("OTEL_SERVICE_NAME", "eco-app").strip() or "eco-app"
    if not endpoint:
        _initialized = True
        return False

    try:
        resource = Resource.create(
            {
                "service.name": service_name,
                "deployment.environment": os.getenv("OTEL_DEPLOYMENT_ENVIRONMENT", "homelab"),
            }
        )
    except Exception:
        _log.warning("OpenTelemetry resource initialization failed; continuing", exc_info=True)
        _initialized = True
        return False

    try:
        trace_provider = TracerProvider(resource=resource)
        trace_exporter = OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces")
        trace_provider.add_span_processor(BatchSpanProcessor(trace_exporter))
        trace.set_tracer_provider(trace_provider)
    except Exception:
        _log.warning(
            "OpenTelemetry trace initialization failed; continuing without trace export",
            exc_info=True,
        )
    else:
        _trace_provider = trace_provider

    try:
        metric_exporter = OTLPMetricExporter(
            endpoint=f"{endpoint.rstrip('/')}/v1/metrics",
            preferred_temporality=_ACTIVITY_METRIC_TEMPORALITY,
        )
        metric_reader = PeriodicExportingMetricReader(metric_exporter)
        meter_provider = MeterProvider(metric_readers=[metric_reader], resource=resource)
        metrics.set_meter_provider(meter_provider)
    except Exception:
        _log.warning(
            "OpenTelemetry metric initialization failed; continuing without metric export",
            exc_info=True,
        )
    else:
        _meter_provider = meter_provider
        try:
            _mcp_tool_calls = meter_provider.get_meter("eco-app").create_counter(
                "eco_app.mcp.tool.calls",
                unit="{call}",
                description="Number of MCP tool calls handled by eco-app.",
            )
        except Exception:
            _log.warning(
                "OpenTelemetry MCP counter initialization failed; continuing with HTTP metrics",
                exc_info=True,
            )

    _enabled = _trace_provider is not None or _meter_provider is not None
    _initialized = True
    return _enabled


def instrument_asgi(app: Any) -> Any:
    """Trace every non-health ASGI request and suppress health access logs."""
    configure_healthcheck_logging()
    if init_telemetry():
        app.add_middleware(
            OpenTelemetryMiddleware,
            excluded_urls=rf"{_HEALTHCHECK_PATH}$",
            exclude_spans=["receive", "send"],
            tracer_provider=_trace_provider,
            meter_provider=_meter_provider,
        )
    return app


def _record_mcp_tool_call(tool_name: str, outcome: str) -> None:
    """Record one bounded MCP call without letting telemetry break the tool."""
    if _mcp_tool_calls is None:
        return
    try:
        _mcp_tool_calls.add(
            1,
            {
                "gen_ai.tool.name": tool_name,
                "mcp.tool.outcome": outcome,
            },
        )
    except Exception:
        _log.warning("OpenTelemetry MCP call metric failed", exc_info=True)


def instrument_mcp_server(server: Server) -> Server:
    """Give every MCP tool call one semantic server span across all transports."""
    handler = server.request_handlers.get(mt.CallToolRequest)
    if handler is None:
        raise RuntimeError("MCP tool instrumentation requires a registered call-tool handler")
    if getattr(handler, _MCP_HANDLER_MARKER, False):
        return server

    original = cast(
        Callable[[mt.CallToolRequest], Awaitable[mt.ServerResult]],
        handler,
    )

    async def traced_call_tool(request: mt.CallToolRequest) -> mt.ServerResult:
        if not init_telemetry():
            return await original(request)

        tool_name = request.params.name
        carrier: dict[str, str] = {}
        if request.params.meta is not None:
            metadata = request.params.meta.model_dump(exclude_none=True)
            for key in ("traceparent", "tracestate", "baggage"):
                value = metadata.get(key)
                if isinstance(value, str):
                    carrier[key] = value
        # MCP context travels in params._meta independently from its transport.
        # Start at an empty root and link any ambient HTTP span instead of
        # making the logical tool operation an ordinary child of that request.
        parent_context = propagate.extract(carrier, context=Context())
        ambient_context = trace.get_current_span().get_span_context()
        links = [Link(ambient_context)] if ambient_context.is_valid else None
        tracer = trace.get_tracer("eco-app")
        with tracer.start_as_current_span(
            f"tools/call {tool_name}",
            context=parent_context,
            kind=SpanKind.SERVER,
            attributes={
                "mcp.method.name": "tools/call",
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": tool_name,
            },
            links=links,
        ) as span:
            try:
                result = await original(request)
            except Exception as exc:
                error_type = f"{type(exc).__module__}.{type(exc).__qualname__}"
                span.set_attribute("error.type", error_type)
                span.set_status(Status(StatusCode.ERROR))
                _record_mcp_tool_call(tool_name, "exception")
                raise
            if isinstance(result.root, mt.CallToolResult) and result.root.isError:
                span.set_attribute("error.type", "tool_error")
                span.set_status(Status(StatusCode.ERROR))
                _record_mcp_tool_call(tool_name, "tool_error")
            else:
                _record_mcp_tool_call(tool_name, "success")
            return result

    setattr(traced_call_tool, _MCP_HANDLER_MARKER, True)
    server.request_handlers[mt.CallToolRequest] = traced_call_tool
    return server


def record_exception(exc: Exception, operation: str) -> None:
    """Export a fatal non-ASGI exception as a short error span and a Sentry event."""
    if init_error_tracking():
        sentry_sdk.capture_exception(exc)
        # The process is about to exit, so do not leave the event in the queue.
        sentry_sdk.flush(timeout=2.0)
    if not init_telemetry():
        return
    tracer = trace.get_tracer("eco-app")
    with tracer.start_as_current_span(operation) as span:
        span.record_exception(exc)
        span.set_status(Status(StatusCode.ERROR))
