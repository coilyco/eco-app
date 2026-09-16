"""MCP server for public Eco game servers."""

from __future__ import annotations

import base64
import json
import os
import re
from datetime import UTC, datetime
from importlib.resources import files
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
from cachetools import TTLCache
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    Icon,
    TextContent,
    Tool,
)

from . import climate as climate_mod
from . import currency as currency_mod
from . import ecoregion as ecoregion_mod
from . import fair_price as fair_price_mod
from . import market as market_mod
from . import species as species_mod
from . import wave1_routes, wave2_routes, wave3_routes
from .civics import civics_markdown, fetch_civics
from .crafting import atlas_markdown, fetch_atlas
from .dual_routes import DualRouteRegistry
from .logistics import fetch_logistics, logistics_markdown
from .map import build_map_payload, fetch_map_bundle
from .progression import fetch_history, history_markdown
from .social import fetch_social, social_markdown
from .stores import directory_markdown, fetch_directory
from .telemetry import instrument_mcp_server
from .trades import fetch_ledger, ledger_markdown
from .watchers import (
    WatcherError,
    build_query,
    create_watcher,
    evaluate_all,
    evaluate_markdown,
    list_watchers,
    remove_watcher,
    watchers_list_markdown,
)
from .world import fetch_world, world_markdown

PUBLIC_SERVERS_OUTPUT_SCHEMA = wave1_routes.PUBLIC_SERVERS_OUTPUT_SCHEMA

DEFAULT_ECO_INFO_URL = os.environ.get("ECO_INFO_URL", "http://eco.coilysiren.me:3001/info")
DEFAULT_ECO_PORT = int(os.environ.get("ECO_INFO_PORT", "3001"))
# Base URL for non-/info endpoints on the same server. Derived from
# DEFAULT_ECO_INFO_URL at import time so overriding ECO_INFO_URL in tests or
# deploys redirects every endpoint consistently.
DEFAULT_ECO_BASE_URL = DEFAULT_ECO_INFO_URL.rsplit("/info", 1)[0]

# Economy dashboard: datasets pulled from the admin /datasets/get endpoint.
# Listed here so both tool wiring and tests share one source of truth; each
# string must appear in `/datasets/flatlist` on the live server.
#
# Flow datasets are per-event magnitudes: summing them over a window is
# meaningful, so these are what drive sparklines and the week-over-week
# activity delta.
ECONOMY_FLOW_DATASETS: tuple[str, ...] = (
    "OfferedLoanOrBond",
    "AcceptedLoanOrBond",
    "RepaidLoanOrBond",
    "DefaultedOnLoanOrBond",
    "PayWages",
    "PayRentOrMoveInFee",
    "PostedContract",
    "CompletedContract",
    "FailedContract",
    "PropertyTransfer",
    "ReputationTransfer",
    "TransferMoney",
    "PayTax",
    "ReceiveGovernmentFunds",
)

# Level datasets are balances sampled over time. The last point is the current
# value; summing them is meaningless, so they stay out of sparks and the WoW
# delta. `get_currency` reads the same government-holdings dataset, and the two
# tools contradicted each other while `get_economy` did not read it (#258).
ECONOMY_LEVEL_DATASETS: tuple[str, ...] = (currency_mod.GOVERNMENT_HOLDINGS_DATASET,)

ECONOMY_DATASETS: tuple[str, ...] = ECONOMY_FLOW_DATASETS + ECONOMY_LEVEL_DATASETS

# Admin endpoints (exporter/*) require an API key. We read it from the
# environment (populated by SSM at boot in the homelab deploy, or set by hand
# for local dev / tests). None → the tool will still run but get 401s, which
# surface as per-action warnings on the rendered card.
ADMIN_API_KEY_ENV = "ECO_ADMIN_API_KEY"

# Single source of truth for the public servers surfaced both as "try-others"
# pills on the rendered card and as the `list_public_servers` tool's
# response. Curated from eco-servers.org, chosen for variety in Eco markup
# patterns + ruleset (so the iframe gets exercised against diverse titles).
KNOWN_PUBLIC_SERVERS: list[dict[str, str]] = [
    {
        "label": "Eco via Sirens",
        "host": "eco.coilysiren.me:3001",
        "notes": "Kai's server (default for this MCP). Highly modded, collaborative.",
    },
    {
        "label": "AWLGaming",
        "host": "ecoserver.awlgaming.net:5679",
        "notes": "Hex + named color mix in the TMP title.",
    },
    {
        "label": "GreenLeaf Prime",
        "host": "eco.greenleafserver.com:3021",
        "notes": "<#RRGGBB> shorthand rainbow title.",
    },
    {
        "label": "GreenLeaf Vanilla",
        "host": "eco.greenleafserver.com:3031",
        "notes": "Same host as Prime, vanilla ruleset.",
    },
    {
        "label": "The Dao Kingdom",
        "host": "daokingdom.eu:3001",
        "notes": "Short-form hex + explicit </color> closes.",
    },
    {
        "label": "Peaceful Utopia",
        "host": "eco.bleedcraft.com:3001",
        "notes": "No markup in the title; meteor already passed.",
    },
]


def normalize_server_url(server: str | None) -> str:
    """Turn a user-supplied server string into a full /info URL.

    Accepts any of: a full URL (`http://host:3001/info`), host-only
    (`eco.example.com`, `192.168.1.5`), or host:port (`10.0.0.5:4001`).
    Most public Eco servers advertise as bare IPs, so we don't require a
    scheme — we assume http and the default Eco port when missing.
    """
    if not server:
        return DEFAULT_ECO_INFO_URL
    s = server.strip()
    if not s:
        return DEFAULT_ECO_INFO_URL
    if "://" not in s:
        s = f"http://{s}"
    parsed = urlparse(s)
    host = parsed.hostname or ""
    port = parsed.port or DEFAULT_ECO_PORT
    path = parsed.path if parsed.path and parsed.path != "/" else "/info"
    return urlunparse((parsed.scheme or "http", f"{host}:{port}", path, "", "", ""))


# In-memory cache for /info responses. The /preview route can get hammered by
# refreshes, and each cache miss fans out to a third-party Eco server — without
# this a single tab reloader can DoS a small community server. 30s matches
# Eco's own in-game stats update cadence closely enough that nothing visibly
# stale slips through. Cache key is the normalized URL so the same server
# expressed two ways (`host` vs `host:3001/info`) shares an entry.
_INFO_CACHE_TTL_S = float(os.environ.get("ECO_INFO_CACHE_TTL", "30"))
_info_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=128, ttl=_INFO_CACHE_TTL_S)


async def fetch_eco_info(server: str | None = None) -> dict[str, Any]:
    """Hit the Eco server /info endpoint. Raises on non-200. 30s memoized."""
    url = normalize_server_url(server)
    cached = _info_cache.get(url)
    if cached is not None:
        return dict(cached)
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(url)
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        data["_sourceUrl"] = url
        _info_cache[url] = dict(data)
        return data


# ---------------------------------------------------------------------------
# Government org-chart tool
# ---------------------------------------------------------------------------
#
# Eco law descriptions are authored in TextMeshPro rich-text markup, but the
# government panel doesn't try to color or style them — we just want a plain
# human-readable preview for the footer. This regex strips the tag families
# we see in practice on the live server (`<link=...>`, `<icon ...>`,
# `<color=...>`, `<style=...>`, plus bare `<i>`, `<u>`, `<linktext>`,
# `<foldout>`, `<title>`) and leaves surrounding text intact. See
# The post-name character class is `[\s=]` because Eco emits attribute forms
# like `<style="Header">` / `<color=#FFF>` with no whitespace before the `=`.
_LAW_MARKUP = re.compile(
    r"</?(?:link|icon|color|style|b|i|u|s|size|sprite|mark|lowercase|uppercase"
    r"|smallcaps|linktext|foldout|title)(?:[\s=][^>]*)?/?>",
    re.IGNORECASE,
)


def strip_law_markup(s: str | None) -> str:
    """Remove Eco rich-text tags from a law description."""
    if not s:
        return ""
    return _LAW_MARKUP.sub("", s).strip()


# Labels inside each title's Table rows that we care about. Keys are the
# normalized attribute name we expose; values are the exact `Property` labels
# the Eco API emits. These are verified live against
# `/api/v1/elections/titles` on Day 3 of Cycle 13; if upstream relabels them
# we'll start rendering "None" and that's fine — the layout still holds.
_TITLE_ROW_KEYS = {
    "election_process": "Election Process",
    "eligible_candidates": "Eligible Candidates",
    "successor": "Successor",
    "who_can_remove": "Who Can Remove From Office",
    "term_days": "Term Limit Days",
}


def _build_eco_url(base: str | None, path: str) -> str:
    """Compose an endpoint URL from a user-supplied server (or the default)."""
    if not base:
        return f"{DEFAULT_ECO_BASE_URL}{path}"
    normalized = normalize_server_url(base)
    # normalize_server_url always appends `/info` (or whatever path the user
    # supplied). Strip any path off — we want just scheme + host:port.
    parsed = urlparse(normalized)
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


async def _get_json(client: httpx.AsyncClient, url: str) -> Any:
    r = await client.get(url)
    r.raise_for_status()
    return r.json()


async def fetch_eco_government(server: str | None = None) -> dict[str, Any]:
    """Hit the three civic endpoints in parallel and return raw JSON.

    Returns a dict with keys `titles`, `elections`, `laws`, each the parsed
    JSON body. `elections` may be `[]` (verified empty on Day 3 of the
    current cycle) and callers must handle that. Raises on the first
    non-200 / connect error encountered.
    """
    titles_url = _build_eco_url(server, "/api/v1/elections/titles")
    elections_url = _build_eco_url(server, "/api/v1/elections")
    laws_url = _build_eco_url(server, "/api/v1/laws?byStates=Active")
    async with httpx.AsyncClient(timeout=8.0) as client:
        titles = await _get_json(client, titles_url)
        elections = await _get_json(client, elections_url)
        laws = await _get_json(client, laws_url)
    return {
        "titles": titles,
        "elections": elections,
        "laws": laws,
        "_sourceUrl": titles_url,
    }


def _row_value(table: list[list[str]], label: str) -> str | None:
    """Pull the value cell from a `[property, description, value]` row."""
    for row in table:
        if row and len(row) >= 3 and row[0] == label:
            return row[2]
    return None


def _extract_settlements(titles: list[dict[str, Any]]) -> list[str]:
    """List the distinct settlement/federation names the titles cover.

    Title names are shaped like `"<Scope> Mayor"` / `"<Scope> Governor"` /
    `"<Scope> Sheriff"`, so the scope is the name minus its trailing role
    word. Players name their own titles, so the last token is stripped
    unconditionally rather than matched against a role allowlist.

    Order follows first appearance in the payload, which keeps the caption
    stable across calls.
    """
    names: list[str] = []
    for title in titles:
        raw = title.get("Name", "") or ""
        parts = raw.rsplit(" ", 1)
        scope = parts[0] if len(parts) == 2 and parts[1] else raw
        if scope and scope not in names:
            names.append(scope)
    return names


def _government_scope(settlements: list[str]) -> str:
    """Name what the payload actually covers (#238).

    This used to read the first title's settlement, so a server-wide answer
    covering five settlements was captioned as one of them — a consumer would
    reasonably filter or headline the whole government as Costa Del Sol's.
    A multi-settlement payload is server-scoped; `settlements` carries the
    detail.
    """
    if not settlements:
        return "Unknown settlement"
    if len(settlements) == 1:
        return settlements[0]
    return "server"


def to_government_payload(
    data: dict[str, Any],
    *,
    fetched_at_iso: str | None = None,
) -> dict[str, Any]:
    """Shape the raw endpoint blob into the view dict the template consumes."""
    titles_raw: list[dict[str, Any]] = data.get("titles") or []
    elections_raw: list[dict[str, Any]] = data.get("elections") or []
    laws_raw: list[dict[str, Any]] = data.get("laws") or []

    titles: list[dict[str, Any]] = []
    for t in titles_raw:
        table = t.get("Table") or []
        titles.append(
            {
                "id": t.get("Id"),
                "name": t.get("Name") or "?",
                "state": t.get("State"),
                "occupants": list(t.get("OccupantNames") or []),
                "successor": _row_value(table, _TITLE_ROW_KEYS["successor"]),
                "who_can_remove": _row_value(table, _TITLE_ROW_KEYS["who_can_remove"]),
                "term_days": _row_value(table, _TITLE_ROW_KEYS["term_days"]),
                "eligible_candidates": _row_value(table, _TITLE_ROW_KEYS["eligible_candidates"]),
            }
        )

    # Elections — the endpoint accepts no arguments, so whatever it returns is
    # what is open; an empty list means the server reported no open elections,
    # not that the query was skipped. `EndTime` / `TimeLeft` field naming
    # drifts across Eco versions, so we check a few likely shapes.
    elections: list[dict[str, Any]] = []
    for e in elections_raw:
        ends_in_hours: float | None = None
        if isinstance(e.get("TimeLeft"), int | float):
            ends_in_hours = float(e["TimeLeft"]) / 3600.0
        elif isinstance(e.get("HoursLeft"), int | float):
            ends_in_hours = float(e["HoursLeft"])
        elections.append(
            {
                "id": e.get("Id"),
                "name": e.get("Name") or e.get("TitleName") or "Election",
                "ends_in_hours": ends_in_hours,
                "state": e.get("State"),
            }
        )

    # Client-side filter: the `byStates=Active` query param is advisory —
    # verified upstream returns mixed states anyway.
    active_laws = [law for law in laws_raw if (law.get("State") or "") == "Active"]
    active_laws_count = len(active_laws)

    cleaned_laws = [
        {
            "name": law.get("Name") or "?",
            "clean": strip_law_markup(law.get("Description") or ""),
        }
        for law in active_laws
    ]
    cleaned_laws = [law for law in cleaned_laws if law["clean"]]

    shortest_law: dict[str, Any] | None = None
    longest_law: dict[str, Any] | None = None
    if cleaned_laws:
        shortest = min(cleaned_laws, key=lambda law: len(law["clean"]))
        longest = max(cleaned_laws, key=lambda law: len(law["clean"]))
        shortest_preview = _law_preview(shortest["clean"])
        longest_preview = _law_preview(longest["clean"])
        shortest_law = {
            "name": shortest["name"],
            "preview": shortest_preview,
            "preview_lines": _law_preview_lines(shortest_preview),
        }
        longest_law = {
            "name": longest["name"],
            "preview": longest_preview,
            "preview_lines": _law_preview_lines(longest_preview),
        }

    settlements = _extract_settlements(titles_raw)
    return {
        "view": "eco_government",
        "fetchedAtISO": fetched_at_iso,
        "sourceUrl": data.get("_sourceUrl"),
        "scope": _government_scope(settlements),
        "settlements": settlements,
        "titles": titles,
        "elections": elections,
        "active_laws_count": active_laws_count,
        "shortest_law": shortest_law,
        "longest_law": longest_law,
    }


def _law_preview(text: str, max_chars: int = 600) -> str:
    """Trim a sanitized law body to a reasonable preview length."""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


def _law_preview_lines(text: str) -> list[str]:
    """Split a law preview into logical entries for bulleted rendering.

    Eco law descriptions emit one clause per newline, but the clause often
    wraps onto continuation lines that start with whitespace (e.g. the
    `then Prevent (...)` tail of an `On event ...` rule). We fold those
    continuations into the preceding entry so each returned string is a
    single reader-facing bullet.
    """
    entries: list[str] = []
    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        if raw_line[:1].isspace() and entries:
            entries[-1] = f"{entries[-1]} {raw_line.strip()}"
        else:
            entries.append(raw_line.strip())
    return entries


def _format_government_markdown(payload: dict[str, Any]) -> str:
    # Say what the payload covers. Captioning a five-settlement answer with one
    # settlement's name invites the reader to filter it as that settlement's
    # government (#238).
    settlements = payload.get("settlements") or []
    if len(settlements) > 1:
        header = f"**Server government** — {len(settlements)} settlements: {', '.join(settlements)}"
    else:
        header = f"**{payload['scope']} — Government**"
    lines = [header, ""]
    if payload["titles"]:
        for t in payload["titles"]:
            occs = ", ".join(t["occupants"]) if t["occupants"] else "_vacant_"
            lines.append(f"- **{t['name']}** — {occs}")
    else:
        lines.append("- No civic titles configured")
    lines.append("")
    if payload["elections"]:
        lines.append("**Active elections:**")
        for e in payload["elections"]:
            if e["ends_in_hours"] is not None:
                lines.append(f"- {e['name']} — ends in {round(e['ends_in_hours'])}h")
            else:
                lines.append(f"- {e['name']}")
    else:
        lines.append("_No active elections._")
    lines.append("")
    lines.append(f"Active laws: **{payload['active_laws_count']}**")
    if payload.get("shortest_law"):
        lines.append(f"- shortest: {payload['shortest_law']['name']}")
    if payload.get("longest_law"):
        lines.append(f"- longest: {payload['longest_law']['name']}")
    return "\n".join(lines)


def _opt_int(info: dict[str, Any], key: str) -> int | None:
    """Return ``info[key]`` as an int, or ``None`` when upstream did not send it.

    Absent and zero are different states (#214). Defaulting a missing field to
    ``0`` publishes a confident measurement for something the server never
    reported — `timeSinceStartS: 0` reads as "the server just restarted", which
    cost real triage time. Callers render ``None`` as unknown.
    """
    raw = info.get(key)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _opt_float(info: dict[str, Any], key: str) -> float | None:
    """Float counterpart to :func:`_opt_int`. See #214."""
    raw = info.get(key)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def to_payload(info: dict[str, Any]) -> dict[str, Any]:
    """Shape the public status payload from a bounded subset of ``/info``.

    Every numeric field is optional: `/info` varies by server version and by
    mod set, and a field it omits comes back as ``None`` rather than ``0``
    (#214).
    """
    per_day = info.get("ExhaustionHoursGainPerWeekday") or {}
    total_culture, culture_source = resolve_total_culture(info)
    # A countdown to a meteor that is not coming is not a measurement.
    # GreenLeaf Prime returns daysUntilMeteor: -17 with hasMeteor: false (#237).
    has_meteor = bool(info.get("HasMeteor"))
    days_until_meteor = _opt_int(info, "DaysUntilMeteor") if has_meteor else None
    animals = _opt_int(info, "Animals")
    return {
        "view": "eco_status",
        "fetchedAtISO": info.get("_fetchedAtISO"),
        "sourceUrl": info.get("_sourceUrl"),
        "server": {
            "description": info.get("Description", ""),
            "detailedDescription": info.get("DetailedDescription", ""),
            "category": info.get("Category"),
            "discord": info.get("DiscordAddress"),
            "version": info.get("Version"),
            "language": info.get("Language"),
            "paused": bool(info.get("IsPaused")),
            "hasPassword": bool(info.get("HasPassword")),
            "adminOnline": bool(info.get("AdminOnline")),
        },
        "players": {
            "online": _opt_int(info, "OnlinePlayers"),
            "onlineNames": [
                str(name) for name in (info.get("OnlinePlayersNames") or []) if str(name).strip()
            ],
            "total": _opt_int(info, "TotalPlayers"),
            "activeAndOnline": _opt_int(info, "ActiveAndOnlinePlayers"),
            "peakActive": _opt_int(info, "PeakActivePlayers"),
        },
        "world": {
            "size": info.get("WorldSize"),
            "plants": _opt_int(info, "Plants"),
            "animals": animals,
            # /info.Animals read 0 on every server tested while get_region
            # tracked live populations on the same fetch (Deer 248, Wolf 167,
            # Bison 114). A zero here is not evidence there are no animals, so
            # it does not get to pass as a count (#246).
            "animalsNote": (
                "Upstream /info reports 0 animals on every server observed, including ones "
                "with live fauna, so this field looks unpopulated rather than accurate. "
                "Use get_region for tracked animal populations."
                if animals == 0
                else None
            ),
            "laws": _opt_int(info, "Laws"),
            "totalCulture": total_culture,
            "totalCultureSource": culture_source,
        },
        "cycle": {
            "daysRunning": _opt_int(info, "DaysRunning"),
            "daysUntilMeteor": days_until_meteor,
            # Raw world clock in seconds since cycle start (1 in-game day = 3600s).
            # The SPA folds this into a day+hour caption via formatDayHour (eco-app#97).
            # Eco 0.13's /info does not send TimeSinceStart at all, so this is
            # routinely null — see #214.
            "timeSinceStartS": _opt_float(info, "TimeSinceStart"),
            "hasMeteor": has_meteor,
            "collaboration": info.get("CollaborationLevel"),
            "gameSpeed": info.get("GameSpeed"),
            "simulationLevel": info.get("SimulationLevel"),
        },
        "economy": {
            "description": info.get("EconomyDesc", ""),
        },
        "exhaustion": {
            "active": bool(info.get("ExhaustionActive")),
            "afterHours": _opt_float(info, "ExhaustionAfterHours"),
            "hoursPerWeekday": {str(k): _opt_float(per_day, str(k)) for k in per_day},
        },
        "playtimesPattern": info.get("Playtimes", ""),
        "achievements": [
            {"name": k, "text": v} for k, v in (info.get("ServerAchievementsDict") or {}).items()
        ],
    }


# Achievement markup strip — matches the Eco TMP-ish inline tags that show up
# only inside ServerAchievementsDict values. Narrower than _TMP_OTHER_TAG on
# purpose: the task spec calls for exactly these three tag families so the
# parser stays predictable even if Eco adds new tags elsewhere. The real
# payload ships `<style="Culture">` (attribute immediately after the tag
# name, no whitespace), so we broaden the spec's suggested regex to allow
# `=` or whitespace as the separator.
_ACHIEVEMENT_MARKUP = re.compile(r"</?(style|icon|color)(?:[\s=][^>]*)?>", re.IGNORECASE)
# Achievement sentences start with "Create 250 total culture..." — first int
# in the first line is the target.
_FIRST_INT = re.compile(r"\d+")
# Current progress is a decimal inside the <style="Culture"> block, e.g.
# "57.6 Culture". First decimal/integer in the post-strip string is the
# current value (the target is on line 1, the current on line 2).
_FIRST_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def parse_achievement(name: str, raw: str) -> dict[str, Any]:
    """Parse a single ServerAchievementsDict entry into a progress row.

    Returns a dict with `name`, `current`, `target`, `pct`, and `stripped`
    (the human-readable text with Eco's inline markup removed). Resilient to
    missing values — if either number is absent we return ``None`` for it and
    a ``pct`` of 0.0 so the caller can still render an empty-ish bar.
    """
    stripped = _ACHIEVEMENT_MARKUP.sub("", raw or "").strip()
    # The first line carries the target ("Create 250 total culture...").
    lines = stripped.splitlines()
    first_line = lines[0] if lines else stripped
    target_match = _FIRST_INT.search(first_line)
    target = int(target_match.group()) if target_match else None
    # The current value is the first number *after* the first line. Falling
    # back to the whole string means a single-line value still works.
    rest = "\n".join(lines[1:]) if len(lines) > 1 else ""
    current_match = _FIRST_NUMBER.search(rest) or (
        _FIRST_NUMBER.search(stripped[target_match.end() :]) if target_match else None
    )
    current: float | None
    if current_match:
        try:
            current = float(current_match.group())
        except ValueError:
            current = None
    else:
        current = None
    if target and current is not None:
        pct = max(0.0, min(100.0, current / target * 100.0))
    else:
        pct = 0.0
    return {
        "name": name.strip(),
        "current": current,
        "target": target,
        "pct": pct,
        "stripped": stripped,
    }


def _culture_floor_from_milestones(info: dict[str, Any]) -> float | None:
    """Largest culture figure visible in ``ServerAchievementsDict``, if any.

    Every milestone row is culture-denominated progress, so the largest
    ``current`` is a floor on the server's real total culture.
    """
    raw = info.get("ServerAchievementsDict") or {}
    values = [
        row["current"]
        for row in (parse_achievement(name, value) for name, value in raw.items())
        if row["current"] is not None and row["current"] > 0
    ]
    return max(values, default=None)


def resolve_total_culture(info: dict[str, Any]) -> tuple[float | None, str]:
    """Reconcile ``/info``'s TotalCulture against visible milestone progress.

    Sirens reports ``TotalCulture: 0`` while its own milestone list shows 910
    culture from 26 works by 18 artists; GreenLeaf Prime returns a real number
    through the same code path, so the field is unreliable per server rather
    than always broken (#237). Publishing the zero as an economic KPI told a
    reader the server had no cultural output at all, with no way to know the
    field was untrustworthy.

    Returns ``(value, source)`` where source is ``"info"`` or ``"milestones"``.
    """
    reported = _opt_float(info, "TotalCulture")
    if reported is not None and reported > 0:
        return reported, "info"
    floor = _culture_floor_from_milestones(info)
    if floor is not None:
        return floor, "milestones"
    return reported, "info"


# Shown wherever a milestone-derived culture figure is published, so a reader
# knows the number did not come from the server's own counter.
CULTURE_FROM_MILESTONES_NOTE = (
    "The server reported 0 total culture, which contradicts its own milestone "
    "progress. Showing the largest milestone figure as a floor instead."
)


def build_milestones_payload(info: dict[str, Any]) -> dict[str, Any]:
    """Shape the payload consumed by the milestone card template.

    Sorted by completion % descending (closest to target at top), matching the
    acceptance criterion in #13.
    """
    raw_dict = info.get("ServerAchievementsDict") or {}
    rows = [parse_achievement(name, value) for name, value in raw_dict.items()]
    rows.sort(key=lambda r: r["pct"], reverse=True)
    total_culture, culture_source = resolve_total_culture(info)
    return {
        "view": "eco_milestones",
        "fetchedAtISO": info.get("_fetchedAtISO"),
        "sourceUrl": info.get("_sourceUrl"),
        "totalCulture": total_culture,
        "totalCultureSource": culture_source,
        "totalCultureNote": (
            CULTURE_FROM_MILESTONES_NOTE if culture_source == "milestones" else None
        ),
        "milestones": rows,
    }


def _format_milestones_markdown(payload: dict[str, Any]) -> str:
    total = payload["totalCulture"]
    headline = _UNREPORTED if total is None else f"{total:.1f}"
    if payload.get("totalCultureSource") == "milestones":
        headline = f"{headline}+ (from milestones — the server reported 0)"
    lines = [
        f"**Eco milestones** — TotalCulture: **{headline}**",
        "",
    ]
    if not payload["milestones"]:
        lines.append("_No achievements recorded yet — it may be very early in the cycle._")
        return "\n".join(lines)
    for row in payload["milestones"]:
        current = "—" if row["current"] is None else f"{row['current']:g}"
        target = "?" if row["target"] is None else str(row["target"])
        lines.append(f"- **{row['name']}**: {current} / {target} Culture ({row['pct']:.0f}%)")
    return "\n".join(lines)


def _format_map_markdown(payload: dict[str, Any]) -> str:
    """Plain-text fallback for hosts without the iframe."""
    dim = payload.get("worldDim") or {}
    lines = [
        f"**Eco world map** — {dim.get('x', '?')} x {dim.get('z', '?')}",
        "",
        f"- Deeds: **{payload['deedCount']}** across "
        f"**{payload['ownerCount']}** owner{'s' if payload['ownerCount'] != 1 else ''}",
    ]
    owners = payload.get("owners") or []
    if owners:
        shown = ", ".join(owners[:10])
        more = f" (+{len(owners) - 10} more)" if len(owners) > 10 else ""
        lines.append(f"- Owners: {shown}{more}")
    # The largest deeds, with where they actually are. This is the part of the
    # map a text consumer can reason about; the SVG coordinates never were.
    deeds = payload.get("deeds") or []
    if deeds:
        lines.append("")
        lines.append("**Largest deeds:**")
        for deed in deeds[:10]:
            centroid = deed["centroid"]
            lines.append(
                f"- {deed['deed']} ({deed['owner']}) — "
                f"~{deed['areaBlocks']:,} blocks at ({centroid['x']:.0f}, {centroid['z']:.0f})"
            )
        if len(deeds) > 10:
            lines.append(f"- _+{len(deeds) - 10} more deeds_")
    if payload.get("sourceUrl"):
        lines.append(f"- Source: `{payload['sourceUrl']}`")
    return "\n".join(lines)


def _resolve_species_id(name: str) -> str:
    """Turn user input into a CamelCase species id.

    Accepts `WheatSpecies` (pass-through), `Wheat` (add suffix), or
    `Snapping Turtle` (CamelCase-join + suffix). The exporter endpoint only
    speaks the raw CamelCase form.
    """
    s = (name or "").strip()
    if not s:
        return ""
    if " " not in s and s.endswith("Species"):
        return s
    if " " not in s and s[:1].isupper() and not s.isupper():
        # Looks like `Wheat` / `Bison` — single-word common name.
        return f"{s}Species"
    # Spaces present or all-lowercase: split, title-case, join.
    parts = [p for p in re.split(r"\s+", s) if p]
    joined = "".join(p[:1].upper() + p[1:].lower() for p in parts)
    if not joined.endswith("Species"):
        joined += "Species"
    return joined


def _format_species_markdown(payload: dict[str, Any]) -> str:
    lines = [f"**{payload.get('name', 'Species')}** — `{payload.get('speciesId', '?')}`"]
    source = payload.get("source") or "none"
    if source == "inat":
        lines.append("- Source: iNaturalist")
    elif source == "wikipedia":
        lines.append("- Source: Wikipedia (no iNat match)")
    else:
        lines.append("- Source: none (modded or fictional species)")
    taxonomy = payload.get("taxonomy") or []
    if taxonomy:
        lines.append("- Taxonomy: " + " > ".join(t["name"] for t in taxonomy))
    if payload.get("conservationStatus"):
        lines.append(f"- Conservation: {payload['conservationStatus']}")
    if payload.get("wikiExtract"):
        lines.append("")
        lines.append(payload["wikiExtract"])
        lines.append("")
    population = payload.get("population") or []
    if population:
        first = payload.get("populationFirst")
        latest = payload.get("populationLatest")
        delta = payload.get("populationDelta")
        lines.append(
            f"- Population: {first} → {latest}"
            f" (Δ {'+' if (delta or 0) > 0 else ''}{delta})"
            f" across {len(population)} samples"
        )
    elif payload.get("error"):
        lines.append(f"- Population: _{payload['error']}_")
    else:
        lines.append("- Population: no samples yet")
    if payload.get("wikiUrl"):
        lines.append(f"- [Wikipedia]({payload['wikiUrl']})")
    return "\n".join(lines)


async def _in_game_reference_for(
    item: str | None, server_arg: str | None
) -> tuple[market_mod.InGameReference | None, str]:
    """Best-effort in-game price read for the fair-price cross-reference.

    Gated on an admin key (the trades exporter needs one) so a keyless host —
    and the FRED-only unit tests — never touch the exporter. Any failure
    (unreachable server, no matching in-game market) falls back to the pure
    FRED narrative.

    Returns the reference and a status string. Four silent nulls made a
    degraded answer indistinguishable from a complete one, so every failure
    path names itself (#234).
    """
    api_key = os.environ.get(ADMIN_API_KEY_ENV) or _get_admin_token()
    if not api_key:
        return None, "no_admin_key"
    eco_item = fair_price_mod.eco_item_for(item)
    if not eco_item:
        return None, "item_not_mapped_to_an_in_game_item"
    try:
        intel = await market_mod.fetch_market(base_url=server_arg, api_key=api_key)
    except Exception:  # the FRED path must survive any exporter fault
        return None, "exporter_unreachable"
    ref = market_mod.in_game_reference(intel, eco_item)
    if ref is None:
        return None, f"no_in_game_market_for_{eco_item}"
    return ref, "ok"


def _format_ecoregion_markdown(payload: dict[str, Any]) -> str:
    """Summarize an ecoregion payload for an MCP text result."""
    lines = ["**Biome composition**"]
    for b in payload.get("biomes") or []:
        pct = float(b.get("percent") or 0.0)
        if pct > 0:
            lines.append(f"- {b['display']}: {pct:.0f}%")
    unc = float(payload.get("unclassifiedPercent") or 0.0)
    if unc > 0:
        lines.append(f"- _Unclassified / mixed terrain: {unc:.0f}%_")
    lines += ["", "**Closest real-world ecoregions**"]
    for m in payload.get("ecoregionMatches") or []:
        lines.append(f"- {m['name']} (sim {m['similarity']:.2f}) — {m['description']}")
    drift = payload.get("drift") or {}
    lines += ["", "**Biodiversity drift**"]
    if not payload.get("adminAvailable"):
        lines.append("- Admin endpoints unavailable; configure the API key.")
    elif (drift.get("speciesWithDrift") or 0) == 0:
        lines.append(f"- Drift minimal so far across {drift.get('speciesSeen') or 0} species.")
    else:

        def _delta(d: dict[str, Any]) -> str:
            # A from-zero grower has deltaRel=None (see ecoregion._drift_entry).
            if d.get("fromZero") or d.get("deltaRel") is None:
                return "new"
            return f"{d['deltaRel'] * 100:+.0f}%"

        if drift.get("boom"):
            lines.append("- Boom: " + ", ".join(f"{d['name']} {_delta(d)}" for d in drift["boom"]))
        if drift.get("bust"):
            lines.append("- Bust: " + ", ".join(f"{d['name']} {_delta(d)}" for d in drift["bust"]))
    return "\n".join(lines)


# SSM fetch is lazy + best-effort. If boto3 isn't installed or the param is
# missing we just render without the drift section (public endpoints still
# work). Per CLAUDE.md the param lives in us-east-1 — AWS CLI default is
# us-west-2 so the region must be pinned explicitly.
_ECO_ADMIN_TOKEN_PARAM = "/eco-mcp-app/api-admin-token"
_ECO_ADMIN_TOKEN: str | None = None
_ECO_ADMIN_TOKEN_LOADED = False


def _get_admin_token() -> str | None:
    """Fetch + memoize the Eco admin API key.

    Order of precedence:
    1. ``ECO_ADMIN_TOKEN`` env var — wins for local dev + tests.
    2. SSM ``/eco-mcp-app/api-admin-token`` in us-east-1 (per CLAUDE.md).

    On any failure returns None and the drift strip renders its empty state.
    """
    global _ECO_ADMIN_TOKEN, _ECO_ADMIN_TOKEN_LOADED
    if _ECO_ADMIN_TOKEN_LOADED:
        return _ECO_ADMIN_TOKEN
    _ECO_ADMIN_TOKEN_LOADED = True
    env = os.environ.get("ECO_ADMIN_TOKEN")
    if env:
        _ECO_ADMIN_TOKEN = env
        return env
    try:
        import boto3  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        client = boto3.client("ssm", region_name="us-east-1")
        resp = client.get_parameter(Name=_ECO_ADMIN_TOKEN_PARAM, WithDecryption=True)
        _ECO_ADMIN_TOKEN = resp["Parameter"]["Value"]
    except Exception:
        _ECO_ADMIN_TOKEN = None
    return _ECO_ADMIN_TOKEN


def _fetch_failure(exc: Exception) -> str:
    """Describe a failed upstream fetch: what went wrong, and against what URL.

    httpx's connect-side errors routinely carry an empty ``str()``, so
    interpolating the exception alone produced "Could not reach Eco server: "
    with nothing after the colon (#228). That leaves an operator unable to
    tell a dead host from a wrong port, a block, or a timeout. The exception
    type is always present, so lead with it, add the detail when there is one,
    and name the URL httpx actually attempted.
    """
    kind = type(exc).__name__
    detail = str(exc).strip()
    cause = f"{kind}: {detail}" if detail else kind

    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is not None:
        cause = f"{cause} (HTTP {status})"

    # httpx.RequestError.request raises when the transport never set it.
    try:
        url = str(exc.request.url)  # type: ignore[attr-defined]
    except (AttributeError, RuntimeError):
        url = ""
    return f"{cause} while requesting {url}" if url else cause


# Where the same answer lives in full, per tool. An MCP response is a summary
# by necessity — the response cap is real (#240 family 3) — so every tool
# points at the page that carries the whole thing (#241). Tools with no page
# of their own are absent rather than pointed somewhere approximate.
PUBLIC_SITE_URL = os.environ.get("ECO_PUBLIC_SITE_URL", "https://eco-app.coilysiren.me").rstrip("/")

TOOL_SITE_PATHS: dict[str, str] = {
    "get_server_status": "/info",
    "get_milestones": "/info",
    "get_economy": "/trade",
    "get_currency": "/trade",
    "get_market": "/trade",
    "get_stores": "/trade",
    "get_trades": "/trade",
    "find_trade": "/uses/arbitrage",
    "trade_watchers": "/trade",
    "fair_price": "/uses/price",
    "get_crafting_atlas": "/crafting",
    "get_progression": "/jobs",
    "get_civics": "/civics",
    "get_government": "/civics",
    "get_world": "/map",
    "get_map": "/map",
    "get_region": "/map",
    "get_climate": "/map",
    "get_species": "/species",
    "explain_item": "/items",
    "get_social": "/civics",
    "list_public_servers": "/info",
}


def site_url_for(tool: str) -> str | None:
    """The live page carrying this tool's answer in full, if there is one."""
    path = TOOL_SITE_PATHS.get(tool)
    return f"{PUBLIC_SITE_URL}{path}" if path else None


def _append_site_link(tool: str, result: CallToolResult) -> CallToolResult:
    """Add a "see the full version here" line to a tool's markdown block.

    Only the human-readable block is touched. The JSON block is a typed
    contract — several tools validate it against a pydantic output model — so
    a link is not smuggled into it.
    """
    url = site_url_for(tool)
    if url is None or not result.content:
        return result
    first = result.content[0]
    if not isinstance(first, TextContent) or url in first.text:
        return result
    first.text = f"{first.text}\n\nFull detail: {url}"
    return result


def _unreachable_result(subject: str, exc: Exception) -> CallToolResult:
    """The one shape every "could not reach <subject>" tool error takes.

    Sixteen call sites hand-rolled this block, which is how the bare-exception
    interpolation in #228 spread across all of them. Routing them through
    :func:`_fetch_failure` keeps the cause and the attempted URL on every
    upstream failure.
    """
    failure = _fetch_failure(exc)
    payload = {"view": "error", "message": f"Could not reach {subject}: {failure}"}
    return CallToolResult(
        content=[
            TextContent(type="text", text=f"**{subject} unreachable:** {failure}"),
            TextContent(type="text", text=json.dumps(payload)),
        ],
        isError=True,
    )


def _is_truthy_arg(value: Any) -> bool:
    """Query-param truthiness, matching the SPA's `?cost=1` convention."""
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _recipe_warn(payload: dict[str, Any], message: str) -> None:
    """Append a warning to a recipe payload, creating the list if absent."""
    warnings: list[str] = list(payload.get("warnings") or [])
    warnings.append(message)
    payload["warnings"] = warnings


def _resolve_recipe_admin_key() -> str | None:
    """Admin key for the recipe cost engine's market read."""
    return os.environ.get(ADMIN_API_KEY_ENV) or _get_admin_token()


def _format_recipes_markdown(payload: dict[str, Any], tool: str) -> str:
    """Compact markdown for the recipe / cost tools."""
    recipes = payload.get("recipes") or []
    source = payload.get("source") or "unknown source"
    server_specific = payload.get("serverSpecific")
    provenance = "server-specific" if server_specific else "vanilla fallback"
    matched = payload.get("recipesMatched", len(recipes))
    lines = [
        f"**Eco recipes** — {len(recipes)} of {matched:,} shown ({provenance}: {source})",
        "",
    ]
    if not recipes:
        lines.append("_No recipe matched that filter._")
    for recipe in recipes[:10]:
        product = (recipe.get("product") or {}).get("displayName") or recipe.get("name", "?")
        skill = (recipe.get("skill") or {}).get("name") or "no skill"
        station = recipe.get("craftStation") or recipe.get("station") or "no station"
        line = f"- **{product}** — {skill} at {station}"
        cost = recipe.get("cost") or {}
        if tool == "price_recipe" and cost:
            per_unit = cost.get("perUnit")
            if per_unit is not None:
                line += f" · ~{per_unit:,.2f}/unit"
            margin = cost.get("marginPct")
            if margin is not None:
                line += f" · margin {margin:+.0f}%"
        lines.append(line)
    for warning in payload.get("warnings") or []:
        lines.append(f"- ⚠ {warning}")
    return "\n".join(lines)


def _format_skills_markdown(payload: dict[str, Any]) -> str:
    skills = payload.get("skills") or []
    lines = [f"**Eco skills** — {len(skills)} skills gating recipes", ""]
    for skill in skills[:20]:
        lines.append(
            f"- **{skill.get('display') or skill.get('name')}** — "
            f"{skill.get('recipeCount', 0)} recipe(s)"
        )
    for warning in payload.get("warnings") or []:
        lines.append(f"- ⚠ {warning}")
    return "\n".join(lines)


_UNREPORTED = "not reported"


def _fmt_num(value: float | int | None, spec: str = ",") -> str:
    """Render an optional `/info` number, naming the absent case (#214).

    A missing upstream field reads as "not reported" rather than borrowing a
    zero that a reader would take for a measurement.
    """
    if value is None:
        return _UNREPORTED
    return format(value, spec)


def _format_markdown(payload: dict[str, Any]) -> str:
    p = payload["players"]
    w = payload["world"]
    c = payload["cycle"]
    s = payload["server"]
    title = s.get("description") or s.get("category") or "Eco server"
    laws = w["laws"]
    lines = [
        f"**{title}** — {s.get('category', 'Server')} · cycle day {_fmt_num(c['daysRunning'])}",
        "",
        f"- Online: **{_fmt_num(p['online'])} / {_fmt_num(p['total'])}** players"
        f" (peak {_fmt_num(p['peakActive'])}, active {_fmt_num(p['activeAndOnline'])})",
        f"- Days until meteor: **{_fmt_num(c['daysUntilMeteor'])}**"
        + (" ☄" if c["hasMeteor"] else ""),
        f"- World: {w['size']} · {_fmt_num(w['plants'])} plants"
        f" · {_fmt_num(w['animals'])} animals"
        f" · {_fmt_num(laws)} law{'' if laws == 1 else 's'}"
        f" · culture {_fmt_num(w['totalCulture'], '.1f')}",
        f"- Version: `{s.get('version', '?')}` · {c['collaboration']}"
        f" · game speed: {c['gameSpeed']}",
    ]
    if s.get("discord"):
        lines.append(f"- [Join Discord]({s['discord']})")
    if payload.get("sourceUrl"):
        lines.append(f"- Source: `{payload['sourceUrl']}`")
    return "\n".join(lines)


##
## Economy dashboard
##
## Separate code path from `/info`: hits the admin /datasets/get endpoint
## (requires X-API-Key header) and /info for cycle-day + EconomyDesc, computes
## KPIs on top, and renders a dedicated card partial with inline SVG sparklines.
##

# Base URL for admin endpoints. We derive the admin base from ECO_INFO_URL so a
# non-default server can be targeted by setting one env var.
_ADMIN_DEFAULT_BASE = os.environ.get(
    "ECO_ADMIN_BASE",
    DEFAULT_ECO_INFO_URL.rsplit("/info", 1)[0],
)

# SSM secret paths. Region is pinned us-east-1: the AWS CLI default is
# us-west-2 and would silently miss these params.
_SSM_REGION = os.environ.get("AWS_REGION", "us-east-1")
_ECO_ADMIN_SSM_PATH = os.environ.get("ECO_ADMIN_TOKEN_SSM", "/eco-mcp-app/api-admin-token")

# Admin token cache — loaded once per process at first-use, not per-request.
# An explicit env var ECO_ADMIN_TOKEN overrides SSM so tests and local dev
# don't need AWS credentials. TTL is effectively infinite (1 hour is plenty;
# the process restarts more often than the SSM value rotates).
_admin_token_cache: TTLCache[str, str | None] = TTLCache(maxsize=1, ttl=3600)


def _load_admin_token() -> str | None:
    """Return the Eco admin API token or None if unavailable.

    Order: `ECO_ADMIN_TOKEN` env var → SSM `/eco-mcp-app/api-admin-token` in
    us-east-1 → None (caller renders the empty-state card). Cached so we
    don't reach for boto3 on every call.
    """
    if "token" in _admin_token_cache:
        return _admin_token_cache["token"]
    token = os.environ.get("ECO_ADMIN_TOKEN")
    if not token:
        try:
            import boto3  # type: ignore[import-not-found]

            ssm = boto3.client("ssm", region_name=_SSM_REGION)
            resp = ssm.get_parameter(Name=_ECO_ADMIN_SSM_PATH, WithDecryption=True)
            token = resp["Parameter"]["Value"]
        except Exception:
            # boto3 missing, no creds, or param not found — all equivalent for
            # our purposes (we'll render the card with no series).
            token = None
    _admin_token_cache["token"] = token
    return token


# Per-process dataset cache. The dashboard is viewed in bursts (user alt-tabs
# between conversation + iframe), and each render fans out 14 admin requests —
# without this we'd hammer the Eco server's admin endpoint.
_ECONOMY_CACHE_TTL_S = float(os.environ.get("ECO_ECONOMY_CACHE_TTL", "45"))
_economy_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=64, ttl=_ECONOMY_CACHE_TTL_S)


async def _fetch_dataset(
    client: httpx.AsyncClient,
    base: str,
    name: str,
    day_end: int,
    headers: dict[str, str],
) -> list[tuple[float, float]] | None:
    """Fetch a single /datasets/get series.

    Returns ``None`` when the series could not be read at all (non-200,
    transport error, unparseable body) and a list — possibly empty — when the
    server answered. A single bad series must not blow up the whole card, but
    collapsing "could not measure" into "measured zero" made `get_economy`
    report confident zeros for datasets it never actually read (#261).
    """
    try:
        url = f"{base}/datasets/get"
        r = await client.get(
            url,
            params={"dataset": name, "dayStart": 0, "dayEnd": max(day_end, 1)},
            headers=headers,
        )
        if r.status_code != 200:
            return None
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return None
    # /datasets/get returns either a list of {Time, Value} dicts or a list of
    # two-item [time, value] pairs — tolerate both shapes defensively.
    out: list[tuple[float, float]] = []
    # Rows the body offered, against rows we understood. A 200 carrying a shape
    # this parser does not know yields nothing, and returning [] for that says
    # "measured zero" about a dataset never read. See #266.
    offered = 0
    if isinstance(data, list):
        offered = len(data)
        for pt in data:
            if isinstance(pt, dict):
                t = pt.get("Time", pt.get("time"))
                v = pt.get("Value", pt.get("value"))
            elif isinstance(pt, list | tuple) and len(pt) >= 2:
                t, v = pt[0], pt[1]
            else:
                continue
            try:
                out.append((float(t), float(v)))
            except (TypeError, ValueError):
                continue
    elif isinstance(data, dict):
        # Sometimes the endpoint wraps points under a "Values" / "Points" key.
        points = data.get("Values") or data.get("Points") or []
        # A non-empty envelope under neither key is a shape we cannot read.
        offered = len(points) if points else (1 if data else 0)
        for pt in points:
            try:
                out.append((float(pt["Time"]), float(pt["Value"])))
            except (KeyError, TypeError, ValueError):
                continue
    else:
        # Neither a list nor a dict: answered, and unreadable.
        offered = 1
    if offered and not out:
        return None
    return out


async def fetch_economy(server: str | None = None) -> dict[str, Any]:
    """Fetch /info + all ECONOMY_DATASETS series for the given Eco server.

    Shape: `{info, days_elapsed, series: {name: [(t,v), ...]}, admin_ok}`.
    Never raises for admin-token problems — we degrade to an empty series map
    and the card renders an "admin token missing" banner.
    """
    info_url = normalize_server_url(server)
    # Derive admin base from the /info URL so the same `server` arg routes both.
    parsed = urlparse(info_url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    cache_key = base
    cached = _economy_cache.get(cache_key)
    if cached is not None:
        return dict(cached)

    info = await fetch_eco_info(server)
    # TimeSinceStart is seconds since cycle start; some servers return a float.
    # One in-game "day" = 3600s by default, but the authoritative number is
    # `DaysRunning` on /info — match what the rest of the UI already shows.
    days_elapsed = int(info.get("DaysRunning") or 0)
    if days_elapsed <= 0:
        tss = info.get("TimeSinceStart")
        try:
            days_elapsed = max(1, int(float(tss) / 3600.0))
        except (TypeError, ValueError):
            days_elapsed = 1

    token = _load_admin_token()
    admin_ok = bool(token)
    series: dict[str, list[tuple[float, float]]] = {}
    unavailable: list[str] = []
    if token:
        headers = {"X-API-Key": token}
        async with httpx.AsyncClient(timeout=10.0) as client:
            import asyncio

            results = await asyncio.gather(
                *(
                    _fetch_dataset(client, base, name, days_elapsed, headers)
                    for name in ECONOMY_DATASETS
                ),
                return_exceptions=False,
            )
        for name, points in zip(ECONOMY_DATASETS, results, strict=True):
            if points is None:
                unavailable.append(name)
            else:
                series[name] = points
    else:
        # No token means nothing was measured, not that everything is zero.
        unavailable = list(ECONOMY_DATASETS)

    out: dict[str, Any] = {
        "info": info,
        "days_elapsed": days_elapsed,
        "series": series,
        "datasets_unavailable": unavailable,
        "admin_ok": admin_ok,
    }
    _economy_cache[cache_key] = dict(out)
    return out


def _series_total(points: list[tuple[float, float]]) -> float:
    """Sum of values (count-type stats are already cumulative/per-event)."""
    return float(sum(v for _, v in points))


def _series_last(points: list[tuple[float, float]]) -> float:
    return float(points[-1][1]) if points else 0.0


def _pct(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return round(100.0 * numerator / denominator, 1)


def _opt_total(series: dict[str, list[tuple[float, float]]], name: str) -> float | None:
    """Sum a flow dataset, or None when that dataset was never read.

    `series` only carries datasets the server actually answered for, so a
    missing key is "not collected" and an empty list is a measured zero.
    """
    points = series.get(name)
    return None if points is None else _series_total(points)


def _opt_last(series: dict[str, list[tuple[float, float]]], name: str) -> float | None:
    """Current value of a level dataset, or None when it was never read."""
    points = series.get(name)
    if points is None:
        return None
    return _series_last(points) if points else 0.0


def _opt_pct(numerator: float | None, denominator: float | None) -> float | None:
    """Percentage that stays None when either side is unmeasured.

    Also returns None for a zero denominator: "0% of nothing" is not a rate,
    and reporting it as 0.0 let a healthy-looking number stand in for an
    absence of activity (#261).
    """
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return round(100.0 * numerator / denominator, 1)


def _opt_sub(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def _opt_add(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left + right


def _opt_trunc(value: float | None) -> int | None:
    """Truncate an optional KPI to int, preserving the unmeasured case."""
    return None if value is None else int(value)


def _fmt_pct(value: float | None) -> str:
    """Render an optional percentage, naming the absent case."""
    return _UNREPORTED if value is None else f"{value}%"


# How many detail rows a tool ships before it needs asking. Sized so a
# no-argument call over MCP stays inside a client's response cap: six tools
# returned 60-220 KB of unbounded detail arrays and were truncated by the
# client with no parameter available to bound them (#256).
MCP_ROW_LIMIT = 50

# A population curve keeps more points than a detail list: 120 evenly-spaced
# samples still draw a readable shape and cost a few KB.
MCP_POPULATION_SAMPLES = 120


def _resolve_limit(args: dict[str, Any], default: int = MCP_ROW_LIMIT) -> int:
    """Read the caller's `limit`. 0 means no limit — the SPA uses that."""
    raw = args.get("limit")
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(value, 0)


def _bound_rows(payload: dict[str, Any], limit: int, *keys: str) -> None:
    """Truncate unbounded detail arrays, and say what was dropped.

    Follows the `get_progression` pattern the rest of the surface already
    uses: a rich summary always survives, the detail rows are bounded, and the
    truncation announces itself rather than looking like the whole population.
    Silent truncation reads as "covered everything" when it did not.
    """
    if limit <= 0:
        return
    for key in keys:
        rows = payload.get(key)
        if not isinstance(rows, list) or len(rows) <= limit:
            continue
        total = len(rows)
        payload[key] = rows[:limit]
        payload.setdefault("warnings", []).append(
            f"{key}: showing {limit:,} of {total:,} rows; pass limit=0 for all of them "
            "(the summary fields above already cover every row)"
        )


def _downsample(points: list[Any], limit: int) -> tuple[list[Any], bool]:
    """Thin a time series to at most `limit` evenly-spaced samples.

    A curve is better served by even spacing than by a head slice: taking the
    first N samples of a 2,000-point population series would report the shape
    of day one and call it the trend.
    """
    if limit <= 0 or len(points) <= limit:
        return points, False
    if limit == 1:
        return [points[-1]], True
    step = (len(points) - 1) / (limit - 1)
    thinned = [points[round(i * step)] for i in range(limit)]
    # Always keep the true endpoints so first/latest stay honest.
    thinned[0], thinned[-1] = points[0], points[-1]
    return thinned, True


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return var**0.5


def _wow_activity_delta(
    series: dict[str, list[tuple[float, float]]], days_elapsed: int
) -> float | None:
    """Week-over-week % change in economic activity: trailing 7 in-game days
    vs the prior 7, summing every dataset's per-day event values.

    Eco exposes a trade *count* only as a scalar in /info's EconomyDesc, never
    as a time-series, so a literal "trades/day WoW" is uncomputable. Total
    transactional activity across the datasets is the closest available
    velocity proxy, and it is what drives the `booming` classification.

    Returns None until there are two full weeks of runtime with activity in the
    prior window — WoW is undefined before then, and a young server should read
    `healthy`, never `booming`. (The previous implementation derived the
    trailing rate from the cumulative average, which is algebraically identical
    to that average, so the delta was always 0.0 and `booming` was unreachable.)
    """
    if days_elapsed < 14:
        return None
    per_day: dict[int, float] = {}
    for points in series.values():
        for t, v in points:
            day = int(t)
            per_day[day] = per_day.get(day, 0.0) + v
    if not per_day:
        return None
    last_day = max(days_elapsed, max(per_day))
    mid = last_day - 7
    prior_start = last_day - 14
    prior = sum(v for d, v in per_day.items() if prior_start < d <= mid)
    trailing = sum(v for d, v in per_day.items() if mid < d <= last_day)
    prior_rate = prior / 7.0
    if prior_rate <= 0:
        return None
    trailing_rate = trailing / 7.0
    return round((trailing_rate / prior_rate - 1.0) * 100.0, 1)


def compute_economy_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """Turn a fetch_economy() result into the dict consumed by the card template.

    Classification thresholds (per task spec):
      booming : default rate < 5% AND activity up ≥20% WoW (needs ≥14d runtime)
      stressed: default rate > 15% OR contract failure rate > 30%
      healthy : otherwise
    """
    info = raw.get("info") or {}
    series: dict[str, list[tuple[float, float]]] = raw.get("series") or {}
    days_elapsed = max(1, int(raw.get("days_elapsed") or 1))
    # Datasets the fetch could not read. Anything named here yields None KPIs
    # rather than zeros. Fall back to deriving it from `series` so a caller
    # holding an older raw dict still gets consistent output.
    unavailable: list[str] = list(
        raw.get("datasets_unavailable")
        if raw.get("datasets_unavailable") is not None
        else [name for name in ECONOMY_DATASETS if name not in series]
    )

    # KPI primitives. Every one of these is None when its dataset was not read,
    # so a caller can tell "no loans were taken out" from "we never looked".
    offered_loans = _opt_total(series, "OfferedLoanOrBond")
    accepted_loans = _opt_total(series, "AcceptedLoanOrBond")
    repaid_loans = _opt_total(series, "RepaidLoanOrBond")
    defaulted_loans = _opt_total(series, "DefaultedOnLoanOrBond")

    posted_contracts = _opt_total(series, "PostedContract")
    completed_contracts = _opt_total(series, "CompletedContract")
    failed_contracts = _opt_total(series, "FailedContract")

    wages = _opt_total(series, "PayWages")
    taxes_paid = _opt_total(series, "PayTax")
    # Flow out of the treasury over the cycle...
    govt_funds_received = _opt_total(series, "ReceiveGovernmentFunds")
    net_tax_flow = _opt_sub(taxes_paid, govt_funds_received)
    # ...versus the treasury's current balance, which is a level. get_currency
    # reports this same dataset as `money.governmentHoldings`; reading it here
    # is what makes the two tools agree (#258).
    govt_funds = _opt_last(series, currency_mod.GOVERNMENT_HOLDINGS_DATASET)

    # Trades/day: EconomyDesc on /info says "N trades, M contracts" authoritatively.
    # We parse it for the displayed number because /datasets doesn't have a
    # `Trade` series (TransferMoney is money transfers, not goods trades).
    econ_desc = str(info.get("EconomyDesc") or "")
    trades_total = 0
    m = re.search(r"(\d+)\s*trade", econ_desc)
    if m:
        trades_total = int(m.group(1))
    trades_per_day = round(trades_total / days_elapsed, 1) if days_elapsed else 0.0

    # Loan default rate — defaults vs (defaulted + repaid) gives the realized
    # rate; open loans (accepted-but-not-yet-repaid) aren't resolved yet. None
    # when no loan has resolved: there is no rate to report yet.
    resolved_loans = _opt_add(defaulted_loans, repaid_loans)
    default_rate = _opt_pct(defaulted_loans, resolved_loans)

    # Contract completion ratio — completed / (completed + failed). Posted-but-
    # open contracts haven't had a chance to fail yet, so excluding them avoids
    # a cold-start penalty that would wrongly trigger "stressed".
    settled_contracts = _opt_add(completed_contracts, failed_contracts)
    completion_ratio = _opt_pct(completed_contracts, settled_contracts)
    failure_rate = _opt_pct(failed_contracts, settled_contracts)

    # Week-over-week economic-activity delta (a real trailing-vs-prior window,
    # summed across the datasets' per-day events). None until two weeks of
    # runtime with prior-window activity. See _wow_activity_delta for why this
    # can't be a literal trades/day WoW.
    trades_wow_pct = _wow_activity_delta(
        {name: pts for name, pts in series.items() if name in ECONOMY_FLOW_DATASETS},
        days_elapsed,
    )

    # Classify. Only a measured rate can move the verdict. Note the three-way
    # distinction: a rate of None because no loan ever resolved is evidence of
    # a quiet credit market, while a rate of None because the dataset was never
    # read is evidence of nothing at all — and only the latter blocks `booming`.
    loans_measured = not {"DefaultedOnLoanOrBond", "RepaidLoanOrBond"} & set(unavailable)
    credit_ok = loans_measured and (default_rate is None or default_rate < 5.0)

    if (default_rate is not None and default_rate > 15.0) or (
        failure_rate is not None and failure_rate > 30.0
    ):
        health = "stressed"
    elif credit_ok and trades_wow_pct is not None and trades_wow_pct >= 20.0:
        health = "booming"
    else:
        health = "healthy"

    # Describe what was actually measured. The old narrative read a 0% default
    # rate off zero loans and a 0% completion ratio off zero contracts, then
    # presented both as evidence of health (#261).
    clauses: list[str] = []
    if default_rate is not None:
        clauses.append(f"{default_rate}% loan default rate")
    elif "DefaultedOnLoanOrBond" in unavailable or "RepaidLoanOrBond" in unavailable:
        clauses.append("loan data unavailable")
    else:
        clauses.append("no loans resolved")

    if completion_ratio is not None:
        clauses.append(f"{completion_ratio}% contracts completed")
    elif "CompletedContract" in unavailable or "FailedContract" in unavailable:
        clauses.append("contract data unavailable")
    else:
        clauses.append("no contract activity recorded")

    narrative = f"Economy is {health} — {', '.join(clauses)}"
    if unavailable:
        narrative += (
            f" (verdict is partial: {len(unavailable)} of {len(ECONOMY_DATASETS)} "
            "datasets could not be read)"
        )

    # Sparkline candidates: pick up to 4 series with the highest normalized
    # stddev (excluding series that have fewer than 2 points). Normalizing by
    # mean puts small-but-volatile series like DefaultedOnLoanOrBond on equal
    # footing with high-volume series like TransferMoney.
    candidates: list[tuple[str, float, list[tuple[float, float]]]] = []
    for name, pts in series.items():
        # Level datasets are balances, not activity — a treasury sparkline
        # ranked by normalized stddev would crowd out real economic volatility.
        if name in ECONOMY_LEVEL_DATASETS or len(pts) < 2:
            continue
        values = [v for _, v in pts]
        mean = sum(values) / len(values) if values else 0.0
        sd = _stddev(values)
        norm = sd / mean if mean > 0 else sd
        candidates.append((name, norm, pts))
    candidates.sort(key=lambda x: x[1], reverse=True)
    sparks = [
        {
            "name": name,
            "label": _HUMAN_STAT_LABELS.get(name, name),
            "last": _series_last(pts),
            "total": _series_total(pts),
        }
        for name, _sd, pts in candidates[:4]
    ]

    total_culture, culture_source = resolve_total_culture(info)

    return {
        "server": {
            "description": info.get("Description", ""),
            "category": info.get("Category"),
            "sourceUrl": info.get("_sourceUrl"),
        },
        "days_elapsed": days_elapsed,
        "admin_ok": bool(raw.get("admin_ok")),
        "kpis": {
            "trades_per_day": trades_per_day,
            "trades_total": trades_total,
            # Name the source. The ledger tools count a different population
            # and report a much larger number; a reader comparing the two
            # without this got contradictory economic conclusions (#221).
            "trades_total_source": "info.EconomyDesc",
            "trades_total_note": (
                "Eco's own trade counter, parsed from /info. The exporter ledger "
                "(get_trades, get_market, get_stores) counts trade events from the action "
                "log instead and reports a much larger total; see its `counts.note`. The two "
                "are not reconcilable from this tool's data."
            ),
            "contract_completion_ratio": completion_ratio,
            "contract_failure_rate": failure_rate,
            "contracts_posted": _opt_trunc(posted_contracts),
            "contracts_completed": _opt_trunc(completed_contracts),
            "contracts_failed": _opt_trunc(failed_contracts),
            "loan_default_rate": default_rate,
            "loans_offered": _opt_trunc(offered_loans),
            "loans_accepted": _opt_trunc(accepted_loans),
            "loans_repaid": _opt_trunc(repaid_loans),
            "loans_defaulted": _opt_trunc(defaulted_loans),
            "wages_total": wages,
            "taxes_paid": taxes_paid,
            "govt_funds": govt_funds,
            "govt_funds_source": currency_mod.GOVERNMENT_HOLDINGS_DATASET,
            "govt_funds_note": (
                "Current treasury balance, read from the same dataset "
                "get_currency reports as `money.governmentHoldings`. This is a "
                "balance, not a flow — `govt_funds_received` is the cycle-total "
                "paid out of the treasury."
            ),
            "govt_funds_received": govt_funds_received,
            "net_tax_flow": net_tax_flow,
            "total_culture": total_culture,
            "total_culture_source": culture_source,
            "trades_wow_pct": trades_wow_pct,
        },
        "sparks": sparks,
        "health": health,
        "narrative": narrative,
        # Name what could not be measured, so a null KPI above is explained
        # rather than merely absent (#261).
        "datasets_unavailable": unavailable,
        "datasets_note": (
            "A null KPI means its dataset was not read; a zero means the server "
            "reported no activity. Datasets listed in `datasets_unavailable` "
            "produced the nulls."
        )
        if unavailable
        else "",
        "economy_desc": econ_desc,
    }


# Human-readable labels for the datasets. Keys match ECONOMY_DATASETS.
_HUMAN_STAT_LABELS: dict[str, str] = {
    "OfferedLoanOrBond": "Loans offered",
    "AcceptedLoanOrBond": "Loans accepted",
    "RepaidLoanOrBond": "Loans repaid",
    "DefaultedOnLoanOrBond": "Loans defaulted",
    "PayWages": "Wages paid",
    "PayRentOrMoveInFee": "Rent & move-in",
    "PostedContract": "Contracts posted",
    "CompletedContract": "Contracts completed",
    "FailedContract": "Contracts failed",
    "PropertyTransfer": "Property transfers",
    "ReputationTransfer": "Reputation transfers",
    "TransferMoney": "Money transfers",
    "PayTax": "Taxes paid",
    "ReceiveGovernmentFunds": "Govt. funds paid out",
}


def _format_climate_markdown(payload: dict[str, Any]) -> str:
    """Summarize a climate payload for an MCP text result."""
    server = payload["server"].get("description") or payload["server"].get("category") or "Eco"
    co2 = payload["co2"]
    sea = payload["sea_level"]
    poll = payload["pollution"]
    lines = [f"**{server} — climate: {payload['status']}**", "", payload["narrative"], ""]
    if co2.get("current") is not None:
        delta = (
            f" ({co2['change_pct']:+.2f}% since cycle start)"
            if co2.get("change_pct") is not None
            else ""
        )
        lines.append(f"- CO2: **{co2['current']:.1f} ppm**{delta}")
    if sea.get("current") is not None:
        rate = sea.get("rate_per_day")
        rate_str = f", {rate:+.4f}/day" if rate else ""
        lines.append(f"- Sea level: **{sea['current']:.3f}**{rate_str}")
    if poll.get("current") is not None:
        unit = poll.get("unit") or ""
        unit_suffix = unit if unit == "%" else f" {unit}" if unit else ""
        lines.append(
            f"- Ground pollution: **{poll['current']:.1f}{unit_suffix}** ({poll['source']})"
        )
        observation = poll.get("observation") or {}
        freshness = observation.get("freshness_state")
        latest_day = observation.get("latest_game_day")
        current_day = observation.get("current_game_day")
        if freshness == "stale":
            lines.append(
                "- Pollution source: **stale**. "
                f"Latest sample game day {latest_day:g}, current game day {current_day}."
            )
        elif freshness == "current":
            lines.append(
                f"- Pollution source: current at game day {latest_day:g} "
                f"for current game day {current_day}."
            )
        elif latest_day is not None:
            lines.append(
                f"- Pollution source: sample game day {latest_day:g}, freshness unknown because "
                "the source cadence is unavailable."
            )
    temp = payload.get("temperature") or {}
    if temp.get("current") is not None:
        risen = temp.get("risen")
        risen_str = f" (+{risen:.2f} since cycle start)" if risen else ""
        lines.append(f"- Avg temperature: **{temp['current']:.2f} °C**{risen_str}")
    earth = payload.get("earth_match")
    if earth:
        lines.append(f"- Real-world anchor: {earth['note']} ({earth['ppm']:.0f} ppm).")

    breakdown = payload.get("breakdown") or {}
    if breakdown.get("has_data"):
        lines.append("")
        lines.append("**CO2 sources & sinks (lifetime / per day):**")
        for label, key in (
            ("From pollution", "pollution"),
            ("From animals", "animals"),
            ("From plants", "plants"),
        ):
            b = breakdown[key]
            lines.append(f"- {label}: {b['lifetime']:+,.0f} ppm ({b['per_day']:+,.2f}/day)")
        lines.append(f"- Net: {breakdown['net_per_day']:+,.2f} ppm/day")

    explainer = payload.get("explainer") or []
    if explainer:
        lines.append("")
        lines.append("**What this means:**")
        lines.extend(f"- {sentence}" for sentence in explainer)
    attrib = payload["attribution"]
    if attrib.get("has_data"):
        lines.append("")
        lines.append("**Top polluting citizens:**")
        for entry in attrib.get("top_citizens") or []:
            lines.append(f"- {entry['name']}: {entry['count']:.0f}")
        if attrib.get("top_stations"):
            lines.append("")
            lines.append("**Top polluting stations:**")
            for entry in attrib.get("top_stations") or []:
                lines.append(f"- {entry['name']}: {entry['count']:.0f}")
    if not payload["admin_ok"]:
        lines.extend(["", "_Admin token unavailable — series + attribution data are empty._"])
    return "\n".join(lines)


def _format_economy_markdown(payload: dict[str, Any]) -> str:
    k = payload["kpis"]
    server = payload["server"].get("description") or payload["server"].get("category") or "Eco"
    lines = [
        f"**{server} — economic health: {payload['health']}**",
        "",
        payload["narrative"],
        "",
        f"- Trades/day: **{k['trades_per_day']}** (total {k['trades_total']:,},"
        " per Eco's own counter — the exporter ledger counts differently)",
        f"- Contracts: {_fmt_num(k['contracts_completed'], ',.0f')}"
        f"/{_fmt_num(k['contracts_posted'], ',.0f')} completed"
        f" · {_fmt_pct(k['contract_failure_rate'])} failure rate",
        f"- Loans: {_fmt_num(k['loans_accepted'], ',.0f')} accepted /"
        f" {_fmt_num(k['loans_defaulted'], ',.0f')} defaulted"
        f" · {_fmt_pct(k['loan_default_rate'])} default rate",
        f"- Wages paid: **{_fmt_num(k['wages_total'], ',.0f')}**",
        f"- Net tax flow: **{_fmt_num(k['net_tax_flow'], '+,.0f')}**"
        f" (taxes in {_fmt_num(k['taxes_paid'], ',.0f')}"
        f" · govt paid out {_fmt_num(k['govt_funds_received'], ',.0f')})",
        f"- Government holdings: **{_fmt_num(k['govt_funds'], ',.0f')}**",
        f"- Total culture: {_fmt_num(k['total_culture'], '.1f')}"
        + ("+ (from milestones)" if k.get("total_culture_source") == "milestones" else ""),
    ]
    if not payload.get("admin_ok"):
        lines.extend(["", "_Admin token unavailable — series data is empty._"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Currency & money-supply card (meets DiscordLink Currency / Currencies)
# ---------------------------------------------------------------------------


def _format_currency_markdown(payload: dict[str, Any]) -> str:
    """Summarize a currency payload for an MCP text result."""
    server = payload["server"].get("description") or payload["server"].get("category") or "Eco"
    money = payload["money"]
    if payload["mode"] == "report":
        selected = payload.get("selected")
        if not selected:
            lines = [f"**{server} — currency `{payload.get('query')}`**", ""]
            lines.append("No currency by that name was found in the roster.")
            if payload["currencies"]:
                known = ", ".join(c["name"] for c in payload["currencies"][:12])
                lines.append(f"Known currencies: {known}.")
            return "\n".join(lines)
        kind = "minted / backed" if selected["isMinted"] else "personal / credit"
        lines = [
            f"**{selected['name']}** — {kind} currency ({server})",
            "",
            f"- Trades: **{selected['tradeCount']}**"
            + (f" · volume {selected['tradeVolume']:,.0f}" if selected["tradeVolume"] else ""),
        ]
        if selected["mintEvents"]:
            lines.append(
                f"- Minted issuance: **{selected['mintedAmount']:,.0f}**"
                f" across {selected['mintEvents']} mint event"
                f"{'' if selected['mintEvents'] == 1 else 's'}"
            )
        if selected.get("createdBy"):
            lines.append(f"- Created by: {selected['createdBy']}")
        holders = selected["holders"]
        if holders["reachable"] and holders["list"]:
            lines.append(
                f"- Top holders ({holders['accountsCounted']} accounts,"
                f" total {holders['totalHoldings']:,.0f}):"
            )
            for h in holders["list"]:
                who = f" ({h['holder']})" if h.get("holder") else ""
                lines.append(f"  - {h['account']}{who} — {h['balance']:,.0f}")
        elif holders["reachable"]:
            lines.append("- Top holders: _no accounts hold this currency yet_")
        else:
            lines.append(f"- Top holders: _{holders['note']}_")
        return "\n".join(lines)

    lines = [f"**{server} — currency market**", "", payload["narrative"], ""]
    # Name which measurement this is. The two counts disagreed by an order of
    # magnitude on the live server with nothing reconciling them (#257).
    if money.get("activeCurrenciesReported"):
        lines.append(
            f"- Active currencies: **{money['activeCurrenciesReported']}** "
            f"(server count) · {money['currencyIdsSeenInLedger']} seen in the trade ledger"
        )
    else:
        lines.append(
            f"- Currencies seen in the trade ledger: **{money['currencyIdsSeenInLedger']}**"
        )
    if money["hasSupplyData"]:
        lines.append(
            f"- Money supply: **{money['totalSupply']:,.0f}**"
            f" (players {money['personalWealth']:,.0f} · gov {money['governmentHoldings']:,.0f})"
        )
    if money["tradeValue7d"]:
        lines.append(f"- Trade value (7d): **{money['tradeValue7d']:,.0f}**")
    if payload["minted"]:
        lines.append("")
        lines.append("**Minted / backed:**")
        for c in payload["minted"][:10]:
            vol = f" · vol {c['tradeVolume']:,.0f}" if c["tradeVolume"] else ""
            mint = f"minted {c['mintedAmount']:,.0f} · " if c["mintedAmount"] else ""
            lines.append(f"- {c['name']} — {mint}{c['tradeCount']} trades{vol}")
    if payload["personal"]:
        lines.append("")
        lines.append("**Personal / credit:**")
        for c in payload["personal"][:10]:
            vol = f" · vol {c['tradeVolume']:,.0f}" if c["tradeVolume"] else ""
            lines.append(f"- {c['name']} — {c['tradeCount']} trades{vol}")
    if not payload["currencies"]:
        lines.append("_No currencies created or traded yet._")
    lines.append("")
    if payload.get("holders_reachable"):
        lines.append("_Open a currency's report for its live top-holder balances._")
    elif payload["currencies"]:
        lines.append(f"_Top holders: {payload['holders_unavailable_note']}_")
    if not payload["admin_ok"]:
        lines.append("")
        lines.append("_Admin token unavailable — roster + money-supply series are empty._")
    return "\n".join(lines)


def _eco_icon() -> Icon:
    """The Eco planet mark, embedded as a self-contained data-URI icon.

    Wired into the server's `initialize` response (`serverInfo.icons`) so clients
    that render server icons - the claude.ai / ChatGPT connector tile - show the
    Eco globe instead of a generic placeholder. Same shape as steam-ops'
    `_steam_icon`: the asset is committed at `assets/eco-icon.png` (the planet
    glyph from the official ECO wordmark, palette-compressed under 10KB for the
    ChatGPT icon cap) and read at import time, base64'd into a `data:` URI rather
    than served over HTTP so the icon rides inside the initialize payload itself.
    """
    png = files("eco_mcp_app.assets").joinpath("eco-icon.png").read_bytes()
    encoded = base64.b64encode(png).decode("ascii")
    return Icon.model_validate(
        {
            "src": f"data:image/png;base64,{encoded}",
            "mimeType": "image/png",
            "sizes": ["192x192"],
        }
    )


# What this server is for, sent on the MCP handshake so a client can tell which
# surface answers a question. Kept to what distinguishes this server from the
# others on a roster, since it is carried in the prompt on every turn.
SERVER_INSTRUCTIONS = (
    "Live and historical data for the Sirens Eco game server, and reference "
    "data for Eco itself. Reach for this to answer what is happening in the "
    "world right now or what something costs: players and activity, stores "
    "and market prices, trades, crafting recipes and their inputs, skills and "
    "specialties, laws and elections, climate and pollution, species and "
    "ecoregions. It reads the game; it never changes it. Prices and stock "
    "move, so prefer a fresh call over an earlier answer in the same "
    "conversation."
)


def build_server(route_registry: DualRouteRegistry | None = None) -> Server:
    """Construct the MCP Server with all handlers registered.

    Separated from `serve()` so it can be mounted in both the stdio transport
    (Claude Desktop) and the Streamable-HTTP transport (homelab FastAPI deploy).
    The icon + website ride on the Server object because the Streamable-HTTP
    path (StreamableHTTPSessionManager) derives its initialization options from
    the Server itself, not from build_initialization_options below.
    """
    dual_routes = route_registry if route_registry is not None else DualRouteRegistry()
    server: Server = Server(
        "eco-mcp-app",
        instructions=SERVER_INSTRUCTIONS,
        website_url="https://eco-app.coilysiren.me",
        icons=[_eco_icon()],
    )

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        tools = [
            Tool(
                name="get_social",
                title="Eco - community activity",
                description=(
                    "Reconstruct the community side of an Eco server from its "
                    "action-log exporter: play activity, new arrivals from "
                    "FirstLogin, and a reputation graph showing who reps whom. "
                    "ChatSent is deliberately not fetched. Player names are "
                    "hashed to stable handles by default. A names-in-the-clear "
                    "mode is operator-gated and needs "
                    "ECO_SOCIAL_ALLOW_NAMES set server-side plus reveal_names, "
                    "never public. Requires an admin API key configured "
                    "server-side (same exporter as get_trades). Returns a "
                    "markdown summary plus structured JSON data (no MCP-app "
                    "widget)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "server": {
                            "type": "string",
                            "description": (
                                "Eco admin base URL (`host`, `host:port`, or "
                                "full URL). Omit to use the configured "
                                "default (`eco.coilysiren.me:3001`)."
                            ),
                        },
                        "reveal_names": {
                            "type": "boolean",
                            "description": (
                                "Show player names instead of redacted handles. "
                                "Operator-gated: only takes "
                                "effect when the deploy sets ECO_SOCIAL_ALLOW_NAMES "
                                "(default-deny), so a public call is always "
                                "redacted regardless. Default false."
                            ),
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="trade_watchers",
                title="Eco — trade watchers",
                description=(
                    "Host-agnostic trade watchers — the website-and-MCP answer to "
                    "DiscordLink's WatchTradeFeed / WatchTradeDisplay / "
                    "UnwatchTradeFeed / ListTradeWatchers, with no Discord "
                    "dependency. Watch an item, a store, a trader, or a price "
                    "threshold (e.g. 'iron ingot under 2.5'), then evaluate against "
                    "the trades the server already exports. `evaluate` returns both "
                    "the feed (matching trades new since the watcher last checked) "
                    "and the display (the current matching state). Watchers persist "
                    "in SQLite and survive restarts; they also show on the SPA "
                    "`/trades` route. Use `action` to pick the verb:\n"
                    "- `create` — new watcher. `kind` + `value`; for `kind=price` "
                    "also `op` (under/over) + `threshold`.\n"
                    "- `list` — every stored watcher.\n"
                    "- `remove` — delete by `id`.\n"
                    "- `evaluate` — run all watchers against the live ledger."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["create", "list", "remove", "evaluate"],
                            "description": "Which watcher verb to run.",
                        },
                        "kind": {
                            "type": "string",
                            "enum": ["item", "store", "trader", "price"],
                            "description": (
                                "For `create`: what to watch. `item` / `store` / "
                                "`trader` are case-insensitive name matches; `price` "
                                "pairs an item with a threshold predicate."
                            ),
                        },
                        "value": {
                            "type": "string",
                            "description": (
                                "For `create`: the item / store / trader name to "
                                "match (the item name, for `kind=price`)."
                            ),
                        },
                        "op": {
                            "type": "string",
                            "enum": ["under", "over"],
                            "description": "For `kind=price`: threshold direction.",
                        },
                        "threshold": {
                            "type": "number",
                            "description": "For `kind=price`: the unit-price cutoff.",
                        },
                        "label": {
                            "type": "string",
                            "description": (
                                "Optional friendly label for the watcher; defaults "
                                "to a description of the query."
                            ),
                        },
                        "id": {
                            "type": "string",
                            "description": "For `remove`: the watcher id to delete.",
                        },
                        "server": {
                            "type": "string",
                            "description": (
                                "For `evaluate` (and stored on `create`): the Eco "
                                "admin base URL to pull the ledger from. Omit to use "
                                "the configured default."
                            ),
                        },
                        "advance": {
                            "type": "boolean",
                            "description": (
                                "For `evaluate`: whether to advance each watcher's "
                                "last-seen mark past its feed hits (default true — "
                                "the feed semantic). Pass false to peek without "
                                "consuming."
                            ),
                        },
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            ),
        ]
        registered_tools = dual_routes.mcp_tools()
        existing_names = {tool.name for tool in tools}
        duplicates = sorted(tool.name for tool in registered_tools if tool.name in existing_names)
        if duplicates:
            raise ValueError(f"dual routes duplicate existing MCP tools: {', '.join(duplicates)}")
        return [*tools, *registered_tools]

    async def _dispatch_call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
        if name == "explain_item":
            from .wikidata import build_ecopedia_card

            item_name = (arguments or {}).get("name", "").strip() if arguments else ""
            category = (arguments or {}).get("category") if arguments else None
            if not item_name:
                err = "`name` is required (e.g. 'Iron', 'Oak', 'Bison')."
                return CallToolResult(
                    content=[TextContent(type="text", text=err)],
                    isError=True,
                )
            # Default off for the same reason as get_species: the inlined image
            # dwarfs the text and blows the MCP response cap (#230).
            card = await build_ecopedia_card(
                item_name,
                category,
                include_image=bool((arguments or {}).get("include_image", False)),
            )
            card_dict = card.to_dict()
            md_lines = [f"**{card.title or card.name}**"]
            if card.category:
                md_lines[0] += f" — _{card.category}_"
            if card.description:
                md_lines.append("")
                md_lines.append(card.description)
            if card.facts:
                md_lines.append("")
                for label, value in card.facts:
                    md_lines.append(f"- **{label}**: {value}")
            if card.source_url:
                md_lines.append("")
                md_lines.append(f"Source: {card.source_url}")
            if card.not_found and not card.description:
                md_lines = [f"No Wikipedia / Wikidata entry found for '{card.name}'."]
            return CallToolResult(
                content=[
                    TextContent(type="text", text="\n".join(md_lines)),
                    TextContent(type="text", text=json.dumps(card_dict)),
                ],
            )

        if name == "get_crafting_atlas":
            server_arg = arguments.get("server") if arguments else None
            api_key = os.environ.get(ADMIN_API_KEY_ENV) or _get_admin_token()
            try:
                atlas = await fetch_atlas(base_url=server_arg, api_key=api_key)
            except httpx.HTTPError as e:
                return _unreachable_result("Eco exporter", e)
            atlas_payload = atlas.to_dict()
            # Every array here grows with world size, and bounding one of six
            # left ~45 KB at limit=1. The summaries above are computed from the
            # full population, so they still describe every row. See #267.
            _bound_rows(
                atlas_payload,
                _resolve_limit(arguments or {}),
                "byCrafted",
                "byGathered",
                "byStation",
                "byCitizen",
                "byCitizenIterations",
                "flows",
            )
            return CallToolResult(
                content=[
                    TextContent(type="text", text=atlas_markdown(atlas)),
                    TextContent(type="text", text=json.dumps(atlas_payload)),
                ],
            )

        if name == "get_world":
            server_arg = arguments.get("server") if arguments else None
            api_key = os.environ.get(ADMIN_API_KEY_ENV) or _get_admin_token()
            try:
                activity = await fetch_world(base_url=server_arg, api_key=api_key)
            except httpx.HTTPError as e:
                return _unreachable_result("Eco exporter", e)
            return CallToolResult(
                content=[
                    TextContent(type="text", text=world_markdown(activity)),
                    TextContent(type="text", text=json.dumps(activity.to_dict())),
                ],
            )

        if name == "get_trades":
            server_arg = arguments.get("server") if arguments else None
            api_key = os.environ.get(ADMIN_API_KEY_ENV) or _get_admin_token()
            try:
                ledger = await fetch_ledger(base_url=server_arg, api_key=api_key)
            except httpx.HTTPError as e:
                return _unreachable_result("Eco exporter", e)
            ledger_payload = ledger.to_dict()
            # Every one of these grows with the world: byItem with the item
            # catalogue, byCurrency with the currency roster, and topBuyers /
            # topSellers hold one row per trading citizen despite the name.
            # counts and totalCurrencyVolume stay whole. See #267.
            _bound_rows(
                ledger_payload,
                _resolve_limit(arguments or {}),
                "trades",
                "byItem",
                "byCurrency",
                "topBuyers",
                "topSellers",
            )
            return CallToolResult(
                content=[
                    TextContent(type="text", text=ledger_markdown(ledger)),
                    TextContent(type="text", text=json.dumps(ledger_payload)),
                ],
            )

        if name == "get_civics":
            server_arg = arguments.get("server") if arguments else None
            api_key = os.environ.get(ADMIN_API_KEY_ENV) or _get_admin_token()
            try:
                civics_report = await fetch_civics(base_url=server_arg, api_key=api_key)
            except httpx.HTTPError as e:
                return _unreachable_result("Eco exporter", e)
            civics_payload = civics_report.to_dict()
            _bound_rows(
                civics_payload,
                _resolve_limit(arguments or {}),
                "recentDemographics",
                "recentSettlements",
                "recentElections",
                "recentOutcomes",
                "topVoters",
            )
            return CallToolResult(
                content=[
                    TextContent(type="text", text=civics_markdown(civics_report)),
                    TextContent(type="text", text=json.dumps(civics_payload)),
                ],
            )

        if name == "get_progression":
            server_arg = arguments.get("server") if arguments else None
            api_key = os.environ.get(ADMIN_API_KEY_ENV) or _get_admin_token()
            try:
                history = await fetch_history(base_url=server_arg, api_key=api_key)
            except httpx.HTTPError as e:
                return _unreachable_result("Eco exporter", e)
            # Summary-first. The per-citizen timelines are 266 KB of a 275 KB
            # response and put the small, genuinely good aggregate layer behind
            # a payload no MCP client can accept (#232).
            progression_args = arguments or {}
            progression_payload = history.to_dict(
                include_citizens=bool(progression_args.get("include_timelines", False)),
                citizen=progression_args.get("citizen") or None,
            )
            return CallToolResult(
                content=[
                    TextContent(type="text", text=history_markdown(history)),
                    TextContent(type="text", text=json.dumps(progression_payload)),
                ],
            )

        if name == "trade_watchers":
            args = arguments or {}
            action = (args.get("action") or "").strip().lower()

            def _watchers_result(md: str, payload: dict[str, Any]) -> CallToolResult:
                return CallToolResult(
                    content=[
                        TextContent(type="text", text=md),
                        TextContent(type="text", text=json.dumps(payload)),
                    ],
                )

            if action == "create":
                try:
                    query = build_query(
                        kind=args.get("kind", ""),
                        value=args.get("value", ""),
                        op=args.get("op"),
                        threshold=args.get("threshold"),
                    )
                except WatcherError as e:
                    return CallToolResult(
                        content=[
                            TextContent(type="text", text=f"**Could not create watcher:** {e}"),
                            TextContent(type="text", text=json.dumps({"error": str(e)})),
                        ],
                        isError=True,
                    )
                watcher = create_watcher(query, label=args.get("label"), server=args.get("server"))
                # The stored label wins over the generated description (#239):
                # an explicit label is how the caller will refer to this
                # watcher, and `list` / `evaluate` already lead with it. Keep
                # the predicate alongside when the two differ so the summary
                # still says what is being watched.
                described = query.describe()
                subject = (
                    described if watcher.label == described else f"{watcher.label} ({described})"
                )
                md = f"**Watcher created** — `{watcher.id}` watching {subject}."
                return _watchers_result(
                    md, {"view": "watcher_created", "watcher": watcher.to_dict()}
                )

            if action == "list":
                watchers = list_watchers()
                return _watchers_result(
                    watchers_list_markdown(watchers),
                    {"view": "watchers", "watchers": [w.to_dict() for w in watchers]},
                )

            if action == "remove":
                watcher_id = (args.get("id") or "").strip()
                if not watcher_id:
                    return CallToolResult(
                        content=[
                            TextContent(
                                type="text", text="**`id` is required to remove a watcher.**"
                            ),
                            TextContent(type="text", text=json.dumps({"error": "missing id"})),
                        ],
                        isError=True,
                    )
                removed = remove_watcher(watcher_id)
                md = (
                    f"**Watcher `{watcher_id}` removed.**"
                    if removed
                    else f"**No watcher `{watcher_id}` found.**"
                )
                return _watchers_result(
                    md, {"view": "watcher_removed", "id": watcher_id, "removed": removed}
                )

            if action == "evaluate":
                server_arg = args.get("server")
                advance = bool(args.get("advance", True))
                api_key = os.environ.get(ADMIN_API_KEY_ENV) or _get_admin_token()
                try:
                    ledger = await fetch_ledger(base_url=server_arg, api_key=api_key)
                except httpx.HTTPError as e:
                    return _unreachable_result("Eco exporter", e)
                hits = evaluate_all(ledger.trades, advance=advance)
                payload = {
                    "view": "watcher_hits",
                    "fetchedAtISO": ledger.fetched_at_iso,
                    "sourceBaseUrl": ledger.source_base_url,
                    "advanced": advance,
                    "hits": [h.to_dict() for h in hits],
                }
                return _watchers_result(evaluate_markdown(hits), payload)

            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=(
                            f"**Unknown watcher action '{action}'** — "
                            "expected create / list / remove / evaluate."
                        ),
                    ),
                    TextContent(type="text", text=json.dumps({"error": "unknown action"})),
                ],
                isError=True,
            )

        if name == "get_stores":
            server_arg = arguments.get("server") if arguments else None
            api_key = os.environ.get(ADMIN_API_KEY_ENV) or _get_admin_token()
            try:
                directory = await fetch_directory(base_url=server_arg, api_key=api_key)
            except httpx.HTTPError as e:
                return _unreachable_result("Eco exporter", e)
            # `stores` (91 KB) + `traders` (79 KB) were 99% of the response.
            directory_payload = directory.to_dict()
            _bound_rows(directory_payload, _resolve_limit(arguments or {}), "stores", "traders")
            return CallToolResult(
                content=[
                    TextContent(type="text", text=directory_markdown(directory)),
                    TextContent(type="text", text=json.dumps(directory_payload)),
                ],
            )

        if name in ("get_recipes", "price_recipe", "get_skills"):
            from .cost import CostParams, annotate_payload
            from .recipes import filter_index, load_recipe_index, narrow_index_maps
            from .wave3_routes import skills_payload

            args = arguments or {}
            index = load_recipe_index()

            if name == "get_skills":
                recipe_payload: dict[str, Any] = skills_payload(index)
                # The recipe graph is bundled, so on a modded server it omits
                # the specialties players actually hold. Given a server, name
                # exactly which ones are missing instead of leaving the caller
                # to cross-reference get_progression (#263).
                if args.get("server"):
                    from .wave3_routes import annotate_skills_coverage

                    try:
                        history = await fetch_history(
                            base_url=args.get("server"), api_key=_get_admin_token()
                        )
                        # fetch_history records transport failures rather than
                        # raising, so an unreachable server arrives here as an
                        # empty history. per_action_counts is set only when an
                        # exporter answered, so an empty one means the server
                        # was never observed and no cross-check happened (#269).
                        if history.per_action_counts:
                            annotate_skills_coverage(
                                recipe_payload, [n for n, _ in history.by_specialty]
                            )
                        else:
                            recipe_payload["skillsCrossChecked"] = False
                            _recipe_warn(
                                recipe_payload,
                                "skills: no exporter on this server answered, so the "
                                "specialties in use were not cross-checked; the list "
                                "below is the bundled graph only",
                            )
                        # Surface what failed either way. A partially read server
                        # cross-checks against an incomplete specialty set, and a
                        # caller cannot see that from the boolean alone.
                        for detail in history.warnings:
                            _recipe_warn(recipe_payload, f"skills: {detail}")
                    except (httpx.HTTPError, OSError) as exc:
                        recipe_payload["skillsCrossChecked"] = False
                        _recipe_warn(
                            recipe_payload,
                            "skills: could not reach the server to cross-check which "
                            f"specialties are in use ({type(exc).__name__}); the list "
                            "below is the bundled graph only",
                        )
                return CallToolResult(
                    content=[
                        TextContent(type="text", text=_format_skills_markdown(recipe_payload)),
                        TextContent(type="text", text=json.dumps(recipe_payload)),
                    ],
                )

            product = args.get("product")
            recipe_payload = filter_index(
                index,
                product=product,
                skill=args.get("skill"),
                station=args.get("station"),
            )

            # `/preview/recipes.json?cost=1` is the SPA's established contract,
            # so get_recipes runs the same engine price_recipe does when asked.
            wants_cost = name == "price_recipe" or _is_truthy_arg(args.get("cost"))
            if wants_cost:
                try:
                    prices = await market_mod.fetch_price_map(
                        base_url=args.get("server"), api_key=_resolve_recipe_admin_key()
                    )
                except (httpx.HTTPError, OSError):
                    # Market unreachable: still ship the roll-up, every leaf
                    # just reads "unpriced".
                    prices = {}
                    _recipe_warn(
                        recipe_payload,
                        "cost: market unreachable, ingredient prices unavailable",
                    )
                annotate_payload(
                    recipe_payload,
                    index,
                    prices,
                    CostParams(
                        calorie_cost=float(
                            args.get("calorie_price") or args.get("caloriePrice") or 0.0
                        ),
                        minute_cost=float(
                            args.get("minute_price") or args.get("minutePrice") or 0.0
                        ),
                    ),
                )
            if name == "price_recipe":
                # price_recipe has exactly one job — cost one product — and the
                # recipe-graph schema around that answer was 99% of its
                # response (#254). Drop the index maps outright.
                for graph_key in ("byProduct", "bySkill", "byStation", "tags", "skills"):
                    recipe_payload.pop(graph_key, None)
                recipe_payload["indexScope"] = "omitted"
                recipe_payload["indexScopeNote"] = (
                    "price_recipe returns costed recipes only. Call get_recipes for "
                    "the byProduct / bySkill / byStation / tags graph index."
                )
            else:
                # Summary-first: the full graph is ~1,450 recipes and blows the
                # response cap, so bound it unless the caller opts out (#242,
                # and the #240 family-3 lesson).
                limit = int(args.get("limit", 25) or 0)
                matched: list[Any] = list(recipe_payload.get("recipes") or [])
                total = len(matched)
                if limit and total > limit:
                    recipe_payload["recipes"] = matched[:limit]
                    _recipe_warn(
                        recipe_payload,
                        f"showing {limit} of {total:,} matching recipes; filter by product, "
                        "skill or station, or raise `limit`",
                    )
                    # The maps have to follow the truncation, or a 25-recipe
                    # answer still carries the whole 1,450-recipe index.
                    narrow_index_maps(recipe_payload)
                recipe_payload["recipesMatched"] = total
                recipe_payload["recipesReturned"] = len(list(recipe_payload.get("recipes") or []))

            return CallToolResult(
                content=[
                    TextContent(type="text", text=_format_recipes_markdown(recipe_payload, name)),
                    TextContent(type="text", text=json.dumps(recipe_payload, default=str)),
                ],
            )

        if name == "get_social":
            server_arg = arguments.get("server") if arguments else None
            reveal_names = bool(arguments.get("reveal_names")) if arguments else False
            api_key = os.environ.get(ADMIN_API_KEY_ENV) or _get_admin_token()
            try:
                surface = await fetch_social(
                    base_url=server_arg, api_key=api_key, reveal_names=reveal_names
                )
            except httpx.HTTPError as e:
                return _unreachable_result("Eco exporter", e)
            return CallToolResult(
                content=[
                    TextContent(type="text", text=social_markdown(surface)),
                    TextContent(type="text", text=json.dumps(surface.to_dict())),
                ],
            )

        if name == "list_public_servers":
            lines = ["**Known public Eco servers:**", ""]
            for s in KNOWN_PUBLIC_SERVERS:
                lines.append(f"- **{s['label']}** — `{s['host']}` · {s['notes']}")
            return CallToolResult(
                content=[
                    TextContent(type="text", text="\n".join(lines)),
                    TextContent(
                        type="text",
                        text=json.dumps({"servers": KNOWN_PUBLIC_SERVERS}),
                    ),
                ],
                structuredContent={"servers": KNOWN_PUBLIC_SERVERS},
            )

        if name == "get_economy":
            server_arg = arguments.get("server") if arguments else None
            try:
                raw = await fetch_economy(server_arg)
            except httpx.HTTPError as e:
                return _unreachable_result("Eco server", e)
            payload = compute_economy_payload(raw)
            return CallToolResult(
                content=[
                    TextContent(type="text", text=_format_economy_markdown(payload)),
                    TextContent(type="text", text=json.dumps(payload, default=str)),
                ],
            )

        if name == "get_map":
            server_arg = arguments.get("server") if arguments else None
            try:
                bundle = await fetch_map_bundle(server_arg)
            except httpx.HTTPError as e:
                return _unreachable_result("Eco server", e)
            payload = build_map_payload(
                bundle,
                include_geometry=_is_truthy_arg((arguments or {}).get("include_geometry")),
            )
            json_payload = {
                k: v for k, v in payload.items() if k not in ("gifDataUri", "pollutionDataUri")
            }
            return CallToolResult(
                content=[
                    TextContent(type="text", text=_format_map_markdown(payload)),
                    TextContent(type="text", text=json.dumps(json_payload)),
                ],
            )

        if name == "get_region":
            server_arg = arguments.get("server") if arguments else None
            info_url = normalize_server_url(server_arg)
            try:
                payload = await ecoregion_mod.gather_ecoregion_payload(
                    info_url, api_key=_get_admin_token()
                )
            except httpx.HTTPError as e:
                return _unreachable_result("Eco worldlayers endpoint", e)
            return CallToolResult(
                content=[
                    TextContent(type="text", text=_format_ecoregion_markdown(payload)),
                    TextContent(type="text", text=json.dumps(payload)),
                ],
            )

        if name == "get_species":
            species_arg = (arguments or {}).get("name") or ""
            species_id = _resolve_species_id(species_arg)
            # Default off: the inlined photo is ~285 KB against a 150-character
            # extract and blows the MCP response cap on its own (#230).
            include_image = bool((arguments or {}).get("include_image", False))
            try:
                species_payload_obj = await species_mod.build_species_payload(
                    species_id, include_image=include_image
                )
            except httpx.HTTPError as e:
                failure = _fetch_failure(e)
                err_payload = {
                    "view": "error",
                    "message": f"Could not fetch species: {failure}",
                }
                return CallToolResult(
                    content=[
                        TextContent(type="text", text=f"**Species fetch failed:** {failure}"),
                        TextContent(type="text", text=json.dumps(err_payload)),
                    ],
                    isError=True,
                )
            species_payload = species_payload_obj.to_dict()
            # `include_image` was documented as the reason this tool is large,
            # but with it off the response was still 220 KB - `population`
            # alone is 219 KB of it, against 412 bytes for everything else. The
            # image was never the problem (#256). Thin the curve rather than
            # truncating it: a head slice would report day one and call it the
            # trend, and populationFirst / Latest / Delta already summarise.
            population_limit = _resolve_limit(arguments or {}, default=MCP_POPULATION_SAMPLES)
            samples = species_payload.get("population") or []
            thinned, was_thinned = _downsample(samples, population_limit)
            if was_thinned:
                species_payload["population"] = thinned
                species_payload["populationSampled"] = True
                species_payload["populationTotalSamples"] = len(samples)
                species_payload.setdefault("warnings", []).append(
                    f"population: thinned to {len(thinned):,} evenly-spaced samples of "
                    f"{len(samples):,} (endpoints preserved); pass limit=0 for every sample"
                )
            return CallToolResult(
                content=[
                    TextContent(type="text", text=_format_species_markdown(species_payload)),
                    TextContent(type="text", text=json.dumps(species_payload)),
                ],
            )

        if name == "get_government":
            server_arg = arguments.get("server") if arguments else None
            try:
                raw_gov = await fetch_eco_government(server_arg)
            except httpx.HTTPError as e:
                return _unreachable_result("Eco server", e)
            gov_payload = to_government_payload(
                raw_gov, fetched_at_iso=datetime.now(UTC).isoformat()
            )
            return CallToolResult(
                content=[
                    TextContent(type="text", text=_format_government_markdown(gov_payload)),
                    TextContent(type="text", text=json.dumps(gov_payload)),
                ],
            )

        if name == "get_climate":
            server_arg = arguments.get("server") if arguments else None
            try:
                info = await fetch_eco_info(server_arg)
            except httpx.HTTPError as e:
                return _unreachable_result("Eco server", e)
            # /info already gives DaysRunning; fall back to TimeSinceStart for
            # bootstrap servers that haven't ticked the daily counter yet.
            days_elapsed = int(info.get("DaysRunning") or 0)
            if days_elapsed <= 0:
                tss = info.get("TimeSinceStart")
                try:
                    days_elapsed = max(1, int(float(tss) / 3600.0))
                except (TypeError, ValueError):
                    days_elapsed = 1
            admin_token = os.environ.get("ECO_ADMIN_TOKEN") or _get_admin_token()
            default_admin_base = DEFAULT_ECO_INFO_URL.rsplit("/info", 1)[0]
            snapshot = await climate_mod.fetch_climate(
                server_arg,
                info=info,
                days_elapsed=days_elapsed,
                admin_token=admin_token,
                default_admin_base=default_admin_base,
            )
            payload = climate_mod.compute_climate_payload(snapshot)
            return CallToolResult(
                content=[
                    TextContent(type="text", text=_format_climate_markdown(payload)),
                    TextContent(type="text", text=json.dumps(payload, default=str)),
                ],
            )

        if name == "get_currency":
            server_arg = arguments.get("server") if arguments else None
            currency_arg = (arguments.get("currency") if arguments else None) or None
            try:
                info = await fetch_eco_info(server_arg)
            except httpx.HTTPError as e:
                return _unreachable_result("Eco server", e)
            days_elapsed = int(info.get("DaysRunning") or 0)
            if days_elapsed <= 0:
                tss = info.get("TimeSinceStart")
                try:
                    days_elapsed = max(1, int(float(tss) / 3600.0))
                except (TypeError, ValueError):
                    days_elapsed = 1
            admin_token = os.environ.get("ECO_ADMIN_TOKEN") or _get_admin_token()
            default_admin_base = DEFAULT_ECO_INFO_URL.rsplit("/info", 1)[0]
            currency_snapshot = await currency_mod.fetch_currency(
                server_arg,
                info=info,
                days_elapsed=days_elapsed,
                admin_token=admin_token,
                default_admin_base=default_admin_base,
            )
            payload = currency_mod.compute_currency_payload(
                currency_snapshot, currency=currency_arg
            )
            # `currencies` (71 KB) + `personal` (70 KB) were 97% of the
            # response, and the only filter path led to notFound on every
            # value because no currency on that server resolves to a name
            # (#256). A bounded roster is the workaround that actually works.
            #
            # Markdown first, off the full roster: rule 5 of eco-app#6076 keeps
            # summaries describing every row regardless of `limit`.
            markdown = _format_currency_markdown(payload)
            # `minted` and `personal` partition the same records `currencies`
            # already carries, so shipping them as views sent every record
            # twice. Names keep the partition and drop the copy (eco-app#6076).
            for key in ("minted", "personal"):
                rows = payload[key]
                if isinstance(rows, list):
                    payload[key] = [row["name"] for row in rows]
            _bound_rows(payload, _resolve_limit(arguments or {}), "currencies")
            return CallToolResult(
                content=[
                    TextContent(type="text", text=markdown),
                    TextContent(type="text", text=json.dumps(payload, default=str)),
                ],
            )

        if name == "fair_price":
            item = arguments.get("item") if arguments else None
            cycle_id = arguments.get("cycle_id") if arguments else None
            server_arg = arguments.get("server") if arguments else None
            ref, in_game_status = await _in_game_reference_for(item, server_arg)
            result = await fair_price_mod.fetch_fair_price(
                item,
                cycle_id=cycle_id,
                in_game_median=ref.median if ref else None,
                in_game_currency=ref.currency if ref else None,
                in_game_trend=ref.trend if ref else None,
                in_game_status=in_game_status,
            )
            payload = fair_price_mod.to_payload(result)
            return CallToolResult(
                content=[
                    TextContent(type="text", text=result.narrative),
                    TextContent(type="text", text=json.dumps(payload)),
                ],
                # Fair-price failures are handled empty states carried in the
                # typed payload. They must remain successful at the transport
                # layer so both REST pages and MCP clients can render them.
                isError=False,
            )

        if name == "get_market":
            server_arg = arguments.get("server") if arguments else None
            item_arg = (arguments.get("item") if arguments else None) or None
            currency_arg = (arguments.get("currency") if arguments else None) or None
            api_key = os.environ.get(ADMIN_API_KEY_ENV) or _get_admin_token()
            try:
                intel = await market_mod.fetch_market(
                    base_url=server_arg,
                    api_key=api_key,
                    item=item_arg,
                    currency=currency_arg,
                )
            except httpx.HTTPError as e:
                return _unreachable_result("Eco exporter", e)
            return CallToolResult(
                content=[
                    TextContent(type="text", text=market_mod.market_markdown(intel)),
                    TextContent(type="text", text=json.dumps(intel.to_dict(), default=str)),
                ],
            )

        if name == "find_trade":
            server_arg = arguments.get("server") if arguments else None
            item_arg = (arguments.get("item") if arguments else None) or None
            currency_arg = (arguments.get("currency") if arguments else None) or None
            api_key = os.environ.get(ADMIN_API_KEY_ENV) or _get_admin_token()
            try:
                report = await fetch_logistics(
                    base_url=server_arg,
                    api_key=api_key,
                    item=item_arg,
                    currency=currency_arg,
                )
            except httpx.HTTPError as e:
                return _unreachable_result("Eco exporter", e)
            return CallToolResult(
                content=[
                    TextContent(type="text", text=logistics_markdown(report)),
                    TextContent(type="text", text=json.dumps(report.to_dict(), default=str)),
                ],
            )

        if name not in ("get_server_status", "get_milestones"):
            raise ValueError(f"Unknown tool: {name}")

        server_arg = arguments.get("server") if arguments else None
        try:
            raw = await fetch_eco_info(server_arg)
        except httpx.HTTPError as e:
            return _unreachable_result("Eco server", e)

        raw["_fetchedAtISO"] = datetime.now(UTC).isoformat()

        if name == "get_milestones":
            milestones_payload = build_milestones_payload(raw)
            return CallToolResult(
                content=[
                    TextContent(type="text", text=_format_milestones_markdown(milestones_payload)),
                    TextContent(type="text", text=json.dumps(milestones_payload)),
                ],
            )

        payload = to_payload(raw)
        return CallToolResult(
            content=[
                TextContent(type="text", text=_format_markdown(payload)),
                TextContent(type="text", text=json.dumps(payload)),
            ],
        )

    wave1_routes.register_wave1_routes(dual_routes, _dispatch_call_tool)
    wave2_routes.register_wave2_routes(dual_routes, _dispatch_call_tool)
    wave3_routes.register_wave3_routes(dual_routes, _dispatch_call_tool)

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
        if dual_routes.has_tool(name):
            result = await dual_routes.call_mcp(name, arguments)
        else:
            result = await _dispatch_call_tool(name, arguments)
        # One place, every tool: point the caller at the page that shows the
        # same answer in full (#241).
        return _append_site_link(name, result)

    return instrument_mcp_server(server)


def build_initialization_options(server: Server) -> InitializationOptions:
    return InitializationOptions(
        server_name="eco-mcp-app",
        server_version="0.1.0",
        capabilities=server.get_capabilities(
            notification_options=NotificationOptions(),
            experimental_capabilities={},
        ),
        # Stdio parity with the Server-object values the HTTP transport reads.
        instructions=SERVER_INSTRUCTIONS,
        website_url="https://eco-app.coilysiren.me",
        icons=[_eco_icon()],
    )


async def serve() -> None:
    """Stdio transport — the Claude Desktop entry point used by __main__.main()."""
    server = build_server()
    options = build_initialization_options(server)
    async with stdio_server() as (read, write):
        await server.run(read, write, options)
