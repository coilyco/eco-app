"""ASGI wrapper that exposes the MCP server over Streamable-HTTP.

Used for the homelab deploy (eco-mcp.coilysiren.me). Claude Desktop still
talks to this process over stdio via `__main__.py`; HTTP is for hosts that
connect to an MCP server by URL.

Built on Starlette (not plain FastAPI) so `Mount("/mcp", ...)` cleanly
matches both `/mcp` and `/mcp/` without the trailing-slash redirect FastAPI
inserts by default. Middleware normalizes bare-path `/mcp` to the mount path
so both forms work.

Also exposes the `/preview*.json` data plane the React SPA consumes
(`/preview.json`, `/preview-map.json`, `/preview/<tool>.json`). Product UX
lives in the SPA (`frontend/`); MCP results are data-only.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import httpx
import mcp.types as mt
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import BaseRoute, Mount, Route
from starlette.staticfiles import StaticFiles

from . import page_auth, seo
from . import users as users_mod
from .admin import build_admin_server
from .cost import CostParams
from .dual_routes import DualRouteRegistry
from .food import fetch_food_report
from .items import fetch_item_index, fetch_item_pivot
from .livereload import DEBUG, livereload_route
from .map import build_map_payload, fetch_map_bundle
from .price_history import fetch_item_price_history
from .server import (
    ADMIN_API_KEY_ENV,
    DEFAULT_ECO_INFO_URL,
    _get_admin_token,
    build_server,
    fetch_eco_info,
)
from .telemetry import init_telemetry, instrument_asgi

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send


class FrameAncestorsCSP:
    """ASGI middleware — allow coilysiren.me to iframe-embed this site.

    Lived on the jobs tracker app when /jobs was its own Jinja surface (the
    eco-modding page on the personal site embeds it); now that every page is
    the SPA, the header applies site-wide. Keep X-Frame-Options unset
    everywhere (app + ingress).
    """

    CSP = "frame-ancestors 'self' https://www.coilysiren.me https://coilysiren.me"

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_csp(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = self.CSP
            await send(message)

        await self.app(scope, receive, send_with_csp)


class NormalizeMcpPath:
    """ASGI middleware — rewrites scope.path `/mcp` → `/mcp/` before routing.

    Starlette's `Mount("/mcp", ...)` matches `/mcp/` and `/mcp/anything` but
    not bare `/mcp`. Some MCP clients POST straight to `/mcp` and don't follow
    redirects. Normalizing here is less invasive than two overlapping routes.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") == "/mcp":
            scope = {**scope, "path": "/mcp/", "raw_path": b"/mcp/"}
        await self.app(scope, receive, send)


class NormalizeAdminPath:
    """ASGI middleware - rewrites bare `/admin` → `/admin/` before routing.

    Same trailing-slash fix as `NormalizeMcpPath`, for the feature-flagged
    privileged MCP mount. Only added to the stack when `ECO_ADMIN_ENABLED` is
    set, so the public app's routing is untouched when the flag is off.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") == "/admin":
            scope = {**scope, "path": "/admin/", "raw_path": b"/admin/"}
        await self.app(scope, receive, send)


def _env_flag(name: str) -> bool:
    """True when env var `name` is set to a truthy string (1/true/yes/on)."""
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_truthy(value: str | None) -> bool:
    """True when a query-param string reads truthy (1/true/yes/on)."""
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _cost_params(params: Any) -> CostParams:
    """Read `?caloriePrice=` / `?minutePrice=` into a `CostParams`.

    Non-numeric or absent values fall back to 0.0 (labor/time reported but not
    monetized), so a garbage query string never 500s the recipes page.
    """

    def _num(key: str) -> float:
        try:
            return float(params.get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    return CostParams(calorie_cost=_num("caloriePrice"), minute_cost=_num("minutePrice"))


# Feature flag: the privileged /admin MCP is off unless explicitly enabled, so
# the public eco-mcp.coilysiren.me deploy never exposes on-disk state tools.
ADMIN_ENABLED_ENV = "ECO_ADMIN_ENABLED"


async def _gather_user_sources(server_arg: str | None) -> dict[str, Any]:
    """Fan out concurrently to every per-user surface for one server.

    Returns a ``{source_key: payload_or_None}`` dict shaped for
    ``users.build_user_dossier``: each exporter's
    ``to_dict()`` payload, the jobs surface pre-shaped to
    ``{name, active, lastSeenISO, specialties}`` rows, and the currency
    snapshot's raw dict (which carries every currency's top-holders, unlike the
    currency *card* payload). A surface that raises degrades to ``None`` — the
    dossier renders panel-by-panel, so one dead exporter never sinks the page.
    Fetch functions are imported lazily so the stdio entrypoint stays lean, the
    same pattern the jobs/replay mounts use.
    """
    api_key = os.environ.get(ADMIN_API_KEY_ENV) or _get_admin_token()

    from eco_spec_tracker import mock_data, upstream

    from .civics import fetch_civics
    from .crafting import fetch_atlas
    from .currency import fetch_currency
    from .progression import fetch_history
    from .trades import fetch_ledger
    from .world import fetch_world

    async def _jobs() -> list[dict[str, Any]]:
        rows = await upstream.fetch_rows()
        out: list[dict[str, Any]] = []
        for player in mock_data.players(rows):
            seen = [s.last_seen for s in player.specialties if s.last_seen is not None]
            out.append(
                {
                    "name": player.name,
                    "active": player.active,
                    "lastSeenISO": max(seen).isoformat() if seen else None,
                    "specialties": [
                        {"specialty": s.specialty, "level": s.level, "active": s.active}
                        for s in player.specialties
                    ],
                }
            )
        return out

    async def _currency() -> dict[str, Any]:
        # Currency is the odd fetcher out: it needs /info (cycle day) and the
        # admin token, mirroring the get_currency tool dispatch in server.py.
        info = await fetch_eco_info(server_arg)
        days_elapsed = int(info.get("DaysRunning") or 0)
        if days_elapsed <= 0:
            tss: Any = info.get("TimeSinceStart")
            try:
                days_elapsed = max(1, int(float(tss) / 3600.0))
            except (TypeError, ValueError):
                days_elapsed = 1
        admin_token = os.environ.get("ECO_ADMIN_TOKEN") or _get_admin_token()
        default_admin_base = DEFAULT_ECO_INFO_URL.rsplit("/info", 1)[0]
        snapshot = await fetch_currency(
            server_arg,
            info=info,
            days_elapsed=days_elapsed,
            admin_token=admin_token,
            default_admin_base=default_admin_base,
        )
        return snapshot.to_dict()

    tasks: dict[str, Any] = {
        "jobs": _jobs(),
        "trades": fetch_ledger(base_url=server_arg, api_key=api_key),
        "crafting": fetch_atlas(base_url=server_arg, api_key=api_key),
        "civics": fetch_civics(base_url=server_arg, api_key=api_key),
        "progression": fetch_history(base_url=server_arg, api_key=api_key),
        "world": fetch_world(base_url=server_arg, api_key=api_key),
        "currency": _currency(),
    }
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    sources: dict[str, Any] = {}
    for key, result in zip(tasks.keys(), results, strict=True):
        if isinstance(result, BaseException):
            sources[key] = None
        elif key in ("jobs", "currency"):
            sources[key] = result  # already a plain list / dict
        else:
            sources[key] = result.to_dict()
    return sources


# Server-side script extensions. A request for one of these is never a client
# route on a Python service — it is a scanner looking for a PHP host.
# `.json` is here because every real JSON endpoint is a registered route above
# the catch-all, so anything reaching the fallback with that suffix is a probe
# — and a real file in dist is served before this check runs either way.
_NEVER_SPA_SUFFIXES: tuple[str, ...] = (
    ".php",
    ".asp",
    ".aspx",
    ".jsp",
    ".cgi",
    ".env",
    ".json",
)

# The one dotted prefix that is a legitimate web path (ACME challenges,
# security.txt). Everything else starting with a dot is a dotfile probe.
_ALLOWED_DOT_PREFIX = ".well-known"


def _is_never_a_spa_route(path: str) -> bool:
    """True for paths the SPA router could never own.

    The catch-all answered `/.env`, `/.git/config` and `/.aws/credentials`
    with 200 and the SPA shell. Nothing sensitive was served — it is
    index.html, not the file — but "200 for /.env" and "404 for /.env" are
    very different postures to anyone reading the response, and it is why
    obvious 404-bait showed `num4XX: 0` in the traces (eco-app#215).

    Only shapes that cannot be a client route are rejected, so a real SPA
    route is never caught by accident.
    """
    lowered = path.lower().strip("/")
    if not lowered:
        return False
    segments = lowered.split("/")
    if any(seg.startswith(".") for seg in segments) and not lowered.startswith(_ALLOWED_DOT_PREFIX):
        return True
    return lowered.endswith(_NEVER_SPA_SUFFIXES)


def _sanitize_nonfinite(value: Any) -> Any:
    """Recursively replace non-finite floats (``inf``/``-inf``/``nan``) with
    ``None`` so Starlette's ``JSONResponse`` (which renders with
    ``allow_nan=False``) can serialize the payload.

    An upstream exporter can emit a unit-price / rate like ``total/qty`` with
    ``qty == 0`` as ``inf``, which otherwise makes the whole endpoint 500
    (eco-app#83). We drop the offending leaf to ``null`` rather than fail the
    entire dossier.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _sanitize_nonfinite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_nonfinite(item) for item in value]
    return value


async def preview_user_json(request: Request) -> JSONResponse:
    """`/preview/user.json?name=<username>` — the hidden `/users/<hex>` page's
    data plane (eco-app#80).

    The SPA base16-decodes the `/users/<hex>` path segment to the username and
    passes it here as `?name=`. Fans out to every per-user surface and pivots
    them into a single dossier — every field the exporters carry about that one
    player.
    """
    username = request.query_params.get("name")
    if not username:
        return JSONResponse(
            {"error": "missing ?name= (base16-decode the /users/<hex> segment first)"},
            status_code=400,
        )
    server_arg = request.query_params.get("server")
    sources = await _gather_user_sources(server_arg)
    dossier = users_mod.build_user_dossier(username, sources)
    dossier["fetchedAtISO"] = datetime.now(UTC).isoformat()
    return JSONResponse(_sanitize_nonfinite(dossier))


# The shell names the hashed bundle, so a stale shell after a deploy points at
# assets that no longer exist. It revalidates every load against its ETag, and
# the content-hashed assets never change. teable:coilyco/eco-app#8324.
SHELL_CACHE_CONTROL = "no-cache"
ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"


class HashedAssets(StaticFiles):
    """Vite's content-hashed `/assets`, cacheable forever."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = ASSET_CACHE_CONTROL
        return response


def create_app(route_registry: DualRouteRegistry | None = None) -> Starlette:
    init_telemetry()
    dual_routes = route_registry if route_registry is not None else DualRouteRegistry()
    mcp_server = build_server(dual_routes)
    # stateless=True: every request gets a fresh transport. Fits the tool shape
    # — each call is a one-shot /info fetch, no long-lived session state.
    session_manager = StreamableHTTPSessionManager(app=mcp_server, stateless=True)

    # The privileged /admin MCP (on-disk state tools) is built and mounted only
    # when ECO_ADMIN_ENABLED is set. Its session manager is entered in the same
    # lifespan so both transports share one process lifetime.
    admin_enabled = _env_flag(ADMIN_ENABLED_ENV)
    admin_session_manager = (
        StreamableHTTPSessionManager(app=build_admin_server(), stateless=True)
        if admin_enabled
        else None
    )

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(session_manager.run())
            if admin_session_manager is not None:
                await stack.enter_async_context(admin_session_manager.run())
            yield

    call_tool_handler = mcp_server.request_handlers[mt.CallToolRequest]
    list_tools_handler = mcp_server.request_handlers[mt.ListToolsRequest]

    async def _list_tools() -> mt.ListToolsResult:
        result = await list_tools_handler(mt.ListToolsRequest(method="tools/list"))
        return cast(mt.ListToolsResult, result.root)

    # The built React SPA (frontend/dist, baked into the Docker image) owns
    # the root and every client route. A checkout without a frontend build
    # has no HTML surface at all (the dev `/preview` cards were removed) — it
    # serves the JSON/MCP APIs only and points the visitor at the build verb.
    # Path is cwd-relative because the image WORKDIR and local `just http`
    # both run from the repo root; FRONTEND_DIST overrides for anything else.
    frontend_dist = Path(os.getenv("FRONTEND_DIST", "frontend/dist"))
    frontend_index = frontend_dist / "index.html"

    no_build_msg = (
        "Frontend not built. Run `just frontend-build` (or `just "
        "frontend-dev` for hot reload). JSON APIs are live at /preview.json, "
        "/preview/<tool>.json, /api/service, and /jobs/api/v1/*."
    )

    def _shell(path: str, query: str) -> Response:
        """The SPA shell, carrying this URL's crawl posture in its headers.

        The shell is byte-identical for every route — a crawler reading the
        body cannot tell `/trade` from `/item?name=Iron Ore`, which is exactly
        how one page became an unbounded set of duplicates. The headers are
        what distinguish them, and a header works where a `<meta>` tag would
        need the crawler to run the bundle first.
        """
        response = FileResponse(frontend_index)
        response.headers["Cache-Control"] = SHELL_CACHE_CONTROL
        verdict = seo.classify(path, query)
        if verdict.canonical:
            response.headers["Link"] = f'<{verdict.canonical}>; rel="canonical"'
        else:
            response.headers["X-Robots-Tag"] = seo.NOINDEX_HEADER
        return response

    async def root(request: Request) -> Response:
        if frontend_index.is_file():
            return _shell("/", request.url.query)
        return PlainTextResponse(no_build_msg, status_code=404)

    async def robots_txt(_: Request) -> Response:
        """`/robots.txt` — served by the app, not left to the catch-all.

        This path used to answer 200 with the SPA shell, so the crawler asked
        for rules and got HTML. No rules meant no limits.
        """
        return PlainTextResponse(seo.robots_txt(), media_type="text/plain")

    async def sitemap_xml(_: Request) -> Response:
        return Response(seo.sitemap_xml(), media_type="application/xml")

    async def spa_fallback(request: Request) -> Response:
        """Catch-all so the SPA's client-side routes survive a hard refresh.

        Registered last: every API route and mount above wins first. Real
        files in dist (favicon, the hashed assets) serve as themselves.
        Everything else is decided against `data/spa_routes.json`:

        * a retired path 301s to its replacement, so the crawler collapses the
          duplicate rather than seeing two live pages with one body
        * a path the router owns gets the shell, with its crawl posture
        * a path nothing owns gets a **404**

        That last one is the change that matters. The catch-all used to answer
        200 for every string anyone typed, which is a soft 404 to a crawler and
        an unbounded supply of them to an indexer.
        """
        path = request.path_params["path"]
        # A real file in dist always wins, so manifest.json and friends keep
        # serving regardless of the rules below.
        candidate = (frontend_dist / path).resolve()
        if candidate.is_file() and candidate.is_relative_to(frontend_dist.resolve()):
            served = FileResponse(candidate)
            if candidate.suffix == ".html":
                served.headers["Cache-Control"] = SHELL_CACHE_CONTROL
            return served
        if _is_never_a_spa_route(path):
            return PlainTextResponse("Not found", status_code=404)
        target = seo.redirect_target(path)
        if target is not None:
            query = request.url.query
            return RedirectResponse(f"{target}?{query}" if query else target, status_code=301)
        verdict = seo.classify(path, request.url.query)
        if not verdict.known:
            return PlainTextResponse("Not found", status_code=404)
        if not frontend_index.is_file():
            return PlainTextResponse(no_build_msg, status_code=404)
        return _shell(path, request.url.query)

    async def service_info(_: Request) -> JSONResponse:
        # Service-discovery blob. Debug/discovery, not a user surface, so it
        # lives under `/api/service` — every JSON endpoint under an `/api`-style
        # path (eco-app#96). `/info` is freed for the SPA's React Info page: it
        # falls through to the catch-all below and renders on a hard load, where
        # the old Starlette `/info` route would have returned this JSON instead.
        tools_result = await _list_tools()
        names = sorted(t.name for t in tools_result.tools)
        return JSONResponse(
            {
                "service": "eco-app",
                "self": "/api/service",
                "mcp": "/mcp/",
                "jobs": "/jobs",
                "jobsApi": "/jobs/api/v1",
                "replay": "/replay",
                "replayApi": "/replay/api/v1",
                "health": "/healthz",
                "previewJson": "/preview.json",
                "previewMapJson": "/preview-map.json",
                "previewToolsJson": [f"/preview/{name}.json" for name in names],
            }
        )

    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    async def page_auth_status(_: Request) -> JSONResponse:
        """`GET /page-auth` — does the SPA need to prompt for a password?

        The SPA gates /replay and /social on this: `required=false` (nothing
        configured, e.g. a bare local checkout) means render straight through.
        """
        return JSONResponse({"required": page_auth.password_required()})

    async def page_auth_verify(request: Request) -> JSONResponse:
        """`POST {"password": ...}` — verify the page password (eco-app#73).

        Timing-safe compare against the configured password; the password
        itself never leaves the service. `ok=false` on mismatch or when no
        password is set. A soft gate, not a security boundary.
        """
        try:
            body = await request.json()
        except (ValueError, TypeError):
            body = {}
        candidate = body.get("password", "") if isinstance(body, dict) else ""
        return JSONResponse({"ok": page_auth.verify_password(str(candidate))})

    # The `/preview*.json` family is the SPA's data plane: the React pages
    # (/info, /trade, /crafting, /jobs, the World map) fetch these. The HTML
    # `/preview` cards that used to share these handlers were removed — product
    # UX lives in the SPA, the Jinja cards live only on MCP `_meta.ui` for in-chat
    # hosts.
    async def preview_map_json(request: Request) -> JSONResponse:
        server_arg = request.query_params.get("server")
        try:
            # include_biomes: the SPA /map page overlays per-biome rasters for
            # its hover-highlight (eco-app#82); the compact MCP card doesn't.
            bundle = await fetch_map_bundle(server_arg, include_biomes=True)
        except httpx.HTTPError as e:
            return JSONResponse({"error": str(e)}, status_code=502)
        return JSONResponse(build_map_payload(bundle))

    async def preview_social_json(request: Request) -> JSONResponse:
        """`/preview/social.json` — the SPA's `/social` route data plane.

        Dispatches `get_social` and returns its JSON block. This is a
        **public** path, so it never forwards `reveal_names`: the surface is
        always redacted to stable handles, regardless of any server-side names
        gate. A dedicated route (not the generic
        `/preview/<tool>.json`) so `?server=` passes straight through and the
        redaction posture is pinned here rather than left to a query param
        (eco-app#63).
        """
        args = {k: v for k, v in request.query_params.items() if k in ("server",)}
        req = mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name="get_social", arguments=args),
        )
        try:
            result = await call_tool_handler(req)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        payload = _extract_json_block(cast(mt.CallToolResult, result.root))
        if payload is None:
            return JSONResponse({"error": "no JSON block from get_social"}, status_code=502)
        return JSONResponse(payload)

    async def preview_watchers_json(request: Request) -> JSONResponse:
        """`/preview/watchers.json` — the SPA's trade-watcher data plane.

        Dispatches `trade_watchers` with `action=evaluate` in *peek* mode
        (`advance=false`), so loading the `/trades` page shows each watcher's
        current matching state without consuming its feed mark — only the MCP
        `evaluate` verb (advance defaulting true) advances the last-seen marks.
        A dedicated route (not the generic `/preview/<tool>.json`) so the bool
        arg is a real boolean, not the truthy string a query param would be.
        """
        args: dict[str, Any] = {"action": "evaluate", "advance": False}
        if "server" in request.query_params:
            args["server"] = request.query_params["server"]
        req = mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name="trade_watchers", arguments=args),
        )
        try:
            result = await call_tool_handler(req)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        payload = _extract_json_block(cast(mt.CallToolResult, result.root))
        if payload is None:
            return JSONResponse({"error": "no JSON block from trade_watchers"}, status_code=502)
        return JSONResponse(payload)

    def _resolve_admin_key() -> str | None:
        # Same precedence the MCP tool handlers use (env override, then SSM).
        return os.environ.get(ADMIN_API_KEY_ENV) or _get_admin_token()

    async def preview_items_json(request: Request) -> JSONResponse:
        """`/preview/items.json` — the SPA's `/items` list-page data plane.

        The union of every item ever bought / sold / crafted, built over the
        trades ledger + crafting atlas. A dedicated route (not the generic
        `/preview/<tool>.json`) so `?server=` passes straight through and the
        admin key is resolved server-side (eco-app#81).
        """
        server_arg = request.query_params.get("server")
        try:
            index = await fetch_item_index(base_url=server_arg, api_key=_resolve_admin_key())
        except httpx.HTTPError as e:
            return JSONResponse({"error": str(e)}, status_code=502)
        return JSONResponse(index.to_dict())

    async def preview_food_json(request: Request) -> JSONResponse:
        """`/preview/food.json` - confirmed-food restock and overstock signals."""
        server_arg = request.query_params.get("server")
        try:
            report = await fetch_food_report(base_url=server_arg, api_key=_resolve_admin_key())
        except httpx.HTTPError as e:
            return JSONResponse({"error": str(e)}, status_code=502)
        return JSONResponse(report.to_dict())

    async def preview_item_json(request: Request) -> JSONResponse:
        """`/preview/item.json?item=<id>` — the SPA's per-item pivot data plane.

        Every trade + crafting event that pivots on one item id, newest first
        (eco-app#81). `?server=` passes through; the admin key is resolved
        server-side.
        """
        item = request.query_params.get("item", "").strip()
        if not item:
            return JSONResponse({"error": "item query param is required"}, status_code=400)
        server_arg = request.query_params.get("server")
        try:
            pivot = await fetch_item_pivot(item, base_url=server_arg, api_key=_resolve_admin_key())
        except httpx.HTTPError as e:
            return JSONResponse({"error": str(e)}, status_code=502)
        return JSONResponse(pivot.to_dict())

    async def preview_price_history_json(request: Request) -> JSONResponse:
        """Current-cycle unit-price distribution and specialty unlock markers.

        `item` and `currency` are both required so two currency markets are
        never blended. The source fetches degrade inside the contract rather
        than turning missing progression or recipes into a misleading 500.
        """
        item = request.query_params.get("item", "").strip()
        currency = request.query_params.get("currency", "").strip()
        if not item or not currency:
            return JSONResponse(
                {"error": "item and currency query params are required"}, status_code=400
            )
        payload = await fetch_item_price_history(
            item,
            currency,
            base_url=request.query_params.get("server"),
            api_key=_resolve_admin_key(),
        )
        return JSONResponse(payload)

    def _extract_json_block(call_result: mt.CallToolResult) -> Any:
        # Each tool emits markdown + JSON TextContent blocks. Find the JSON
        # block by trying to parse each text block; first that parses wins.
        for block in call_result.content:
            text = getattr(block, "text", "") or ""
            try:
                return json.loads(text)
            except (ValueError, TypeError):
                continue
        return None

    async def preview_tool(request: Request) -> JSONResponse:
        """Dispatch any MCP tool by name and return its JSON content block.

        The SPA consumes `/preview/<tool>.json?<args>` — query-string args pass
        straight through as the tool's `arguments`, so
        `/preview/get_species.json?name=Bison` works. The `.json` suffix is
        required; the product UI is the SPA.
        """
        raw_name = request.path_params["tool"]
        if not raw_name.endswith(".json"):
            return JSONResponse(
                {"error": "only the .json data endpoint is served (e.g. /preview/<tool>.json)"},
                status_code=404,
            )
        tool_name = raw_name[: -len(".json")]
        args = dict(request.query_params)
        req = mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name=tool_name, arguments=args),
        )
        try:
            result = await call_tool_handler(req)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        call_result = cast(mt.CallToolResult, result.root)
        payload = _extract_json_block(call_result)
        if payload is None:
            return JSONResponse(
                {"error": f"tool '{tool_name}' did not return a JSON content block"},
                status_code=404,
            )
        return JSONResponse(payload)

    async def handle_mcp(scope: Scope, receive: Receive, send: Send) -> None:
        await session_manager.handle_request(scope, receive, send)

    async def handle_admin_mcp(scope: Scope, receive: Receive, send: Send) -> None:
        assert admin_session_manager is not None  # only mounted when enabled
        await admin_session_manager.handle_request(scope, receive, send)

    # The jobs JSON API (eco_spec_tracker) and the replay JSON API (eco_replay,
    # the Chronicler mirror) are self-contained FastAPI apps mounted under
    # /jobs/api and /replay/api. Their browser UIs are the SPA's /jobs and
    # /replay routes, served by the catch-all below. Imported here (not module
    # top) so the mounts only exist on the ASGI path, keeping stdio lean.
    from eco_replay.main import app as replay_app
    from eco_spec_tracker.main import app as jobs_app

    routes: list[BaseRoute] = [
        Route("/", root, methods=["GET"]),
        # Service-discovery JSON moved off `/info` → `/api/service` so `/info`
        # falls through to the SPA (eco-app#96). `/healthz` and `/page-auth`
        # stay on their bare paths: both are probes with external contracts
        # (the k8s liveness/readiness check hits `/healthz`; the SPA's page-auth
        # gate hardcodes `/page-auth`), not new user-facing surfaces, so moving
        # them would break those consumers for no SPA-collision gain.
        Route("/api/service", service_info, methods=["GET"]),
        # Crawl surface. Explicit routes so neither can fall through to the
        # catch-all and answer as the SPA shell (eco-app: search-console noise).
        Route("/robots.txt", robots_txt, methods=["GET"]),
        Route("/sitemap.xml", sitemap_xml, methods=["GET"]),
        Route("/healthz", healthz, methods=["GET"]),
        Route("/page-auth", page_auth_status, methods=["GET"]),
        Route("/page-auth", page_auth_verify, methods=["POST"]),
        *dual_routes.starlette_routes(),
        Route("/preview-map.json", preview_map_json, methods=["GET"]),
        Route("/preview/social.json", preview_social_json, methods=["GET"]),
        Route("/preview/watchers.json", preview_watchers_json, methods=["GET"]),
        Route("/preview/user.json", preview_user_json, methods=["GET"]),
        Route("/preview/items.json", preview_items_json, methods=["GET"]),
        Route("/preview/food.json", preview_food_json, methods=["GET"]),
        Route("/preview/item.json", preview_item_json, methods=["GET"]),
        Route("/preview/price-history.json", preview_price_history_json, methods=["GET"]),
        Route("/preview/{tool}", preview_tool, methods=["GET"]),
        Mount("/mcp", app=handle_mcp),
        Mount("/jobs/api", app=jobs_app),
        Mount("/replay/api", app=replay_app),
    ]
    if admin_enabled:
        routes.append(Mount("/admin", app=handle_admin_mcp))
    if (frontend_dist / "assets").is_dir():
        routes.append(Mount("/assets", app=HashedAssets(directory=frontend_dist / "assets")))
    if DEBUG:
        routes.append(livereload_route)
    routes.append(Route("/{path:path}", spa_fallback, methods=["GET"]))
    inner = Starlette(lifespan=lifespan, routes=routes)
    inner.add_middleware(NormalizeMcpPath)
    if admin_enabled:
        inner.add_middleware(NormalizeAdminPath)
    inner.add_middleware(FrameAncestorsCSP)
    return instrument_asgi(inner)


app = create_app()
