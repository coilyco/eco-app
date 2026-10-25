"""Sentry receives crashes as errors, requests as traces, and ERROR records as logs (COI-2905)."""

from __future__ import annotations

import json
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
        self.transactions: list[dict] = []
        self.logs: list[dict] = []

    def capture_envelope(self, envelope) -> None:  # type: ignore[no-untyped-def]
        event = envelope.get_event()
        if event is not None:
            self.events.append(event)
        transaction = envelope.get_transaction_event()
        if transaction is not None:
            self.transactions.append(transaction)
        for item in envelope.items:
            if item.type == "log":
                self.logs.extend(item.payload.json["items"])


@pytest.fixture
def captured(monkeypatch):
    transport = _Capture()
    real_init = sentry_sdk.init
    monkeypatch.setattr(
        telemetry.sentry_sdk, "init", lambda **kwargs: real_init(transport=transport, **kwargs)
    )
    monkeypatch.setattr(telemetry, "_sentry_initialized", False)
    monkeypatch.setattr(telemetry, "_sentry_active", False)
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


def test_no_per_minute_cap_drops_a_crash(captured):
    telemetry.init_error_tracking()
    client = TestClient(_app(), raise_server_exceptions=False)
    crashes = 60
    for _ in range(crashes):
        assert client.get("/crash").status_code == 500
    sentry_sdk.flush()
    assert len(captured.events) == crashes


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


def _by_name(captured) -> dict[str, dict]:
    return {t["transaction"]: t for t in captured.transactions}


def test_a_request_is_a_trace_and_health_and_static_are_not(captured):
    async def ok(_request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/mcp", ok), Route("/healthz", ok), Route("/assets/{name}", ok)])
    telemetry.init_error_tracking()
    client = TestClient(app)
    for path in ("/mcp", "/healthz", "/assets/index.js"):
        assert client.get(path).status_code == 200
    sentry_sdk.flush()
    assert list(_by_name(captured)) == ["/mcp"]


def test_a_propagated_frontend_trace_is_continued(captured):
    async def ok(_request):
        return PlainTextResponse("ok")

    telemetry.init_error_tracking()
    client = TestClient(Starlette(routes=[Route("/jobs", ok)]))
    trace_id = "0123456789abcdef0123456789abcdef"
    client.get("/jobs", headers={"sentry-trace": f"{trace_id}-0123456789abcdef-1"})
    sentry_sdk.flush()
    (transaction,) = captured.transactions
    assert transaction["contexts"]["trace"]["trace_id"] == trace_id


def test_a_trace_scrubs_the_query_string_and_member_text(captured):
    async def ok(request):
        return PlainTextResponse("ok")

    telemetry.init_error_tracking()
    client = TestClient(Starlette(routes=[Route("/search", ok)]))
    secret = "-".join(["MEMBER", "SECRET"])
    client.get("/search", params={"query": secret}, headers={"authorization": secret})
    sentry_sdk.flush()
    assert secret not in json.dumps(captured.transactions)


def test_error_logs_reach_sentry_logs_and_not_the_error_quota(captured):
    telemetry.init_error_tracking()
    log = logging.getLogger("eco_mcp_app.test")
    log.error("upstream returned 502, serving cached")
    log.warning("slow upstream")
    sentry_sdk.flush()
    assert [entry["body"] for entry in captured.logs] == ["upstream returned 502, serving cached"]
    assert captured.events == []


def test_a_log_attribute_under_a_denylisted_key_is_filtered(captured):
    telemetry.init_error_tracking()
    secret = "-".join(["MEMBER", "SECRET"])
    logging.getLogger("eco_mcp_app.test").error("reply failed", extra={"content": secret})
    sentry_sdk.flush()
    assert secret not in json.dumps(captured.logs)
    assert captured.logs[0]["attributes"]["content"]["value"] == "[Filtered]"


def test_a_real_mcp_request_is_a_trace(captured):
    from fastapi.testclient import TestClient

    from eco_mcp_app.http_app import create_app

    with TestClient(create_app()) as client:
        response = client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert response.status_code == 200
    sentry_sdk.flush()
    assert any(t["transaction"].endswith("/mcp") for t in captured.transactions)
    assert captured.events == []


def test_a_crash_scrubs_the_query_string(captured):
    telemetry.init_error_tracking()
    secret = "-".join(["MEMBER", "SECRET"])
    client = TestClient(_app(), raise_server_exceptions=False)
    assert client.get("/crash", params={"query": secret, "page": "2"}).status_code == 500
    sentry_sdk.flush()
    (event,) = captured.events
    assert secret not in json.dumps(event)
    assert "page=2" in event["request"]["query_string"]
