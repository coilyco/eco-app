"""Sentry receives crashes only (teable:coilyco/deploy#8347)."""

from __future__ import annotations

import logging

import pytest
import sentry_sdk
from sentry_sdk.transport import Transport
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import eco_mcp_app.telemetry as telemetry

DSN = "https://public@example.invalid/1"


class _Capture(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict] = []

    def capture_envelope(self, envelope) -> None:  # type: ignore[no-untyped-def]
        event = envelope.get_event()
        if event is not None:
            self.events.append(event)


@pytest.fixture
def captured(monkeypatch):
    transport = _Capture()
    real_init = sentry_sdk.init
    monkeypatch.setattr(
        telemetry.sentry_sdk, "init", lambda **kwargs: real_init(transport=transport, **kwargs)
    )
    monkeypatch.setattr(telemetry, "_sentry_initialized", False)
    monkeypatch.setattr(telemetry, "_sentry_active", False)
    monkeypatch.setattr(telemetry, "_sentry_window", [])
    monkeypatch.setenv("SENTRY_DSN", DSN)
    yield transport
    real_init()


def _app() -> Starlette:
    async def crash(_request):
        raise RuntimeError("route crashed")

    async def handled(_request):
        logging.getLogger("eco_mcp_app.test").error("upstream returned 502, serving cached")
        return PlainTextResponse("ok")

    async def refused(_request):
        raise HTTPException(status_code=503, detail="deliberate")

    return Starlette(
        routes=[Route("/crash", crash), Route("/handled", handled), Route("/refused", refused)]
    )


def test_no_dsn_leaves_sentry_off(monkeypatch):
    monkeypatch.setattr(telemetry, "_sentry_initialized", False)
    monkeypatch.setattr(telemetry, "_sentry_active", False)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert telemetry.init_error_tracking() is False


def test_an_uncaught_route_exception_reaches_sentry(captured):
    assert telemetry.init_error_tracking() is True
    client = TestClient(_app(), raise_server_exceptions=False)
    assert client.get("/crash").status_code == 500
    sentry_sdk.flush()
    assert [e["exception"]["values"][-1]["value"] for e in captured.events] == ["route crashed"]


def test_handled_errors_stay_out_of_sentry(captured):
    telemetry.init_error_tracking()
    client = TestClient(_app(), raise_server_exceptions=False)
    assert client.get("/handled").status_code == 200
    assert client.get("/refused").status_code == 503
    sentry_sdk.flush()
    assert captured.events == []


def test_record_exception_sends_the_fatal_crash(captured, monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    telemetry.record_exception(RuntimeError("worker died"), "eco.discord.worker")
    assert [e["exception"]["values"][-1]["value"] for e in captured.events] == ["worker died"]


def test_budget_caps_events_per_process_minute(monkeypatch):
    monkeypatch.setattr(telemetry, "_sentry_window", [])
    allowed = [
        telemetry._sentry_within_budget(100.0)
        for _ in range(telemetry.SENTRY_EVENTS_PER_MINUTE + 1)
    ]
    assert allowed.count(True) == telemetry.SENTRY_EVENTS_PER_MINUTE
    assert allowed[-1] is False
    assert telemetry._sentry_within_budget(161.0) is True


def test_init_failure_logs_the_class_and_never_the_dsn(monkeypatch, caplog):
    def refuse(**_kwargs):
        raise ValueError("https://secret-key@o0.ingest.example/1")

    monkeypatch.setattr(telemetry, "_sentry_initialized", False)
    monkeypatch.setattr(telemetry, "_sentry_active", False)
    monkeypatch.setattr(telemetry.sentry_sdk, "init", refuse)
    monkeypatch.setenv("SENTRY_DSN", "https://secret-key@o0.ingest.example/1")
    with caplog.at_level(logging.WARNING, logger="eco_mcp_app.telemetry"):
        assert telemetry.init_error_tracking() is False
    assert "ValueError" in caplog.text
    assert "secret-key" not in caplog.text


def _annotated_crash_app() -> Starlette:
    from starlette.requests import Request

    async def crash(request: Request):
        route_name = "economy"  # noqa: F841
        content = (await request.json())["content"]  # noqa: F841
        logging.getLogger("eco_mcp_app.test").warning("resolving interaction")
        raise RuntimeError("route crashed")

    return Starlette(routes=[Route("/crash", crash, methods=["POST"])])


def _crash_event(captured) -> tuple[dict, str]:
    telemetry.init_error_tracking()
    secret = "-".join(["MEMBER", "SECRET"])
    client = TestClient(_annotated_crash_app(), raise_server_exceptions=False)
    assert client.post("/crash", json={"content": secret}).status_code == 500
    sentry_sdk.flush()
    (event,) = captured.events
    return event, secret


def test_a_crash_keeps_its_locals_and_scrubs_member_text(captured):
    import json

    event, secret = _crash_event(captured)
    frame_vars = event["exception"]["values"][-1]["stacktrace"]["frames"][-1]["vars"]
    # Locals are what make a crash readable, so a harmless one survives.
    assert "economy" in frame_vars["route_name"]
    assert secret not in json.dumps(captured.events)


def test_a_crash_carries_its_breadcrumbs_and_request_context(captured):
    event, _secret = _crash_event(captured)
    crumbs = [crumb.get("message") for crumb in event["breadcrumbs"]["values"]]
    assert "resolving interaction" in crumbs
    assert event["request"]["method"] == "POST"
    assert event["request"]["url"].endswith("/crash")


def test_mcp_integration_is_on_without_tool_payloads(captured):
    telemetry.init_error_tracking()
    client = sentry_sdk.get_client()
    assert client.get_integration("mcp") is not None
    # The MCP integration records tool arguments and results only with PII on.
    assert client.options["send_default_pii"] is False
