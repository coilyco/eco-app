"""`/preview*` JSON responses gzip when the client accepts it (eco-app#8390).

`recipes.json` ships the whole recipe graph (~1.2 MB) and gzips to ~125 KB.
Gzip is scoped to the `/preview*` data plane so the `/mcp/` Streamable-HTTP
transport — which answers with SSE — still streams its events incrementally
instead of sitting behind a compression decision.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from eco_mcp_app.http_app import GZIP_MIN_SIZE, PreviewGzip, create_app

RECIPES = "/preview/recipes.json?limit=0"


@pytest.fixture
def client() -> Iterator[TestClient]:
    # Context manager engages the lifespan that starts the MCP session manager,
    # which the /mcp streaming test needs.
    with TestClient(create_app()) as c:
        yield c


def test_preview_recipes_gzips_when_accept_encoding_gzip(client: TestClient) -> None:
    r = client.get(RECIPES, headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers["content-encoding"] == "gzip"
    # The compressed wire size (httpx keeps the raw Content-Length header) is
    # well under the 200 KB acceptance floor.
    assert int(r.headers["content-length"]) < 200_000
    # httpx decodes `.content`, so this is the full uncompressed graph.
    assert len(r.content) > 1_000_000


def test_preview_recipes_body_unchanged_without_accept_encoding(client: TestClient) -> None:
    r = client.get(RECIPES, headers={"Accept-Encoding": "identity"})
    assert r.status_code == 200
    assert "content-encoding" not in r.headers
    assert len(r.content) > 1_000_000


def test_gzipped_body_decodes_to_the_identity_body(client: TestClient) -> None:
    gzipped = client.get(RECIPES, headers={"Accept-Encoding": "gzip"})
    identity = client.get(RECIPES, headers={"Accept-Encoding": "identity"})
    assert gzipped.content == identity.content


def test_mcp_sse_stream_still_streams_and_is_not_gzipped(client: TestClient) -> None:
    r = client.post(
        "/mcp",
        headers={"Accept": "application/json, text/event-stream", "Accept-Encoding": "gzip"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "gzip-test", "version": "0"},
            },
        },
    )
    assert r.status_code == 200
    # The Streamable-HTTP transport answers with SSE, never gzipped, so its
    # events reach the client incrementally.
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "content-encoding" not in r.headers
    assert "serverInfo" in r.text


def test_small_preview_response_skips_gzip(client: TestClient) -> None:
    # A tiny error body sits under GZIP_MIN_SIZE, so it ships uncompressed even
    # when the client asks for gzip — the minimum-size guard, not a failure.
    r = client.get("/preview/price_by_stage.json", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 422
    assert "content-encoding" not in r.headers
    assert len(r.content) < GZIP_MIN_SIZE


async def test_chunked_non_preview_response_streams_through() -> None:
    """A chunked response outside the /preview scope is forwarded verbatim.

    PreviewGzip only wraps the /preview* JSON routes, so a streaming
    (more_body=True) response on another path must pass through chunk-by-chunk
    — never buffered into one gzip blob and never tagged Content-Encoding.
    """
    chunks = [b"part-1-", b"part-2-", b"part-3"]

    async def raw_app(scope: Scope, receive: Receive, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/octet-stream")],
            }
        )
        for i, chunk in enumerate(chunks):
            await send(
                {
                    "type": "http.response.body",
                    "body": chunk,
                    "more_body": i < len(chunks) - 1,
                }
            )

    middleware = PreviewGzip(raw_app)
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/mcp/",
        "headers": [(b"accept-encoding", b"gzip")],
        "query_string": b"",
    }

    await middleware(scope, receive, send)

    bodies = [m for m in sent if m["type"] == "http.response.body"]
    assert [m["body"] for m in bodies] == chunks
    assert [m["more_body"] for m in bodies] == [True, True, False]
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert b"content-encoding" not in dict(start["headers"])
