"""MCP server for public Eco game servers."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from datetime import UTC, datetime
from importlib.resources import files
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

import httpx
from cachetools import TTLCache
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    Icon,
    Resource,
    TextContent,
    Tool,
)
from pydantic import AnyUrl

from . import climate as climate_mod
from . import commodity_benchmark as benchmark_mod
from . import currency as currency_mod
from . import ecoregion as ecoregion_mod
from . import market as market_mod
from . import norms as norms_mod
from . import species as species_mod
from . import vocab as vocab_mod
from . import wave1_routes, wave2_routes, wave3_routes, wave4_routes
from .caveats import reorder_result
from .civics import civics_markdown, fetch_civics
from .crafting import CraftingAtlas, atlas_markdown, fetch_atlas
from .dual_routes import DualRouteRegistry
from .logistics import fetch_logistics, logistics_markdown, resolved_item_id
from .mods import read_mods
from .progression import fetch_history, history_markdown
from .public_routes import STORES_MAX_JSON_BYTES, STORES_NESTED_LIMIT, STORES_ROW_LIMIT
from .reply_templates import with_reply_templates
from .social import fetch_social, social_markdown
from .stores import directory_markdown, fetch_directory
from .tech_progression import build_tech_progression, tech_progression_markdown
from .telemetry import instrument_mcp_server
from .trades import fetch_ledger, ledger_markdown
from .world import fetch_world, world_markdown
from .worldgen import read_world_generator

DEFAULT_ECO_INFO_URL = os.environ.get("ECO_INFO_URL", "http://eco.coilysiren.me:3001/info")
DEFAULT_ECO_PORT = int(os.environ.get("ECO_INFO_PORT", "3001"))
# Base URL for non-/info endpoints on the same server. Derived from
# DEFAULT_ECO_INFO_URL at import time so overriding ECO_INFO_URL in tests or
# deploys redirects every endpoint consistently.
DEFAULT_ECO_BASE_URL = DEFAULT_ECO_INFO_URL.rsplit("/info", 1)[0]

# Admin endpoints (exporter/*) require an API key. We read it from the
# environment (populated by SSM at boot in the homelab deploy, or set by hand
# for local dev / tests). None → the tool will still run but get 401s, which
# surface as per-action warnings on the rendered card.
ADMIN_API_KEY_ENV = "ECO_ADMIN_API_KEY"


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


# Once the meteor is destroyed /info keeps HasMeteor true but stops sending a
# countdown, so the only record of it is the world achievement line
# "Destroyed the meteor on Day 57, 23:13" (COI-2044). The SPA parses the same
# text in frontend/src/lib/serverBrief.ts, keep the two patterns in step.
_METEOR_DESTROYED = re.compile(
    r"destroyed the meteor on day\s+(\d+)(?:,\s*(\d{1,2}:\d{2}))?", re.IGNORECASE
)


def _meteor_state(
    info: dict[str, Any], has_meteor: bool, days_until_meteor: int | None
) -> dict[str, Any]:
    """Name the meteor's state in words, so a null countdown never has to be read.

    ``state`` is ``destroyed`` (achievement text wins over any countdown),
    ``pending`` (a countdown is reported), ``none`` (the world has no meteor), or
    ``unreported`` (a meteor is flagged but upstream sent neither a countdown nor
    a destruction record).
    """
    for text in (info.get("ServerAchievementsDict") or {}).values():
        match = _METEOR_DESTROYED.search(_ACHIEVEMENT_MARKUP.sub("", str(text)))
        if match:
            day, time = int(match.group(1)), match.group(2)
            when = f"day {day}" + (f" at {time}" if time else "")
            return {
                "state": "destroyed",
                "destroyedOnDay": day,
                "destroyedAtTime": time,
                "summary": f"The meteor was destroyed on {when}. There is no countdown.",
            }
    if days_until_meteor is not None:
        unit = "day" if days_until_meteor == 1 else "days"
        return {
            "state": "pending",
            "destroyedOnDay": None,
            "destroyedAtTime": None,
            "summary": f"The meteor is still coming, {days_until_meteor} {unit} away.",
        }
    if not has_meteor:
        return {
            "state": "none",
            "destroyedOnDay": None,
            "destroyedAtTime": None,
            "summary": "This world has no meteor.",
        }
    return {
        "state": "unreported",
        "destroyedOnDay": None,
        "destroyedAtTime": None,
        "summary": "A meteor is flagged but the server did not report a countdown.",
    }


def to_payload(info: dict[str, Any]) -> dict[str, Any]:
    """Shape the public status payload from a bounded subset of ``/info``.

    Every numeric field is optional: `/info` varies by server version and by
    mod set, and a field it omits comes back as ``None`` rather than ``0``
    (#214).
    """
    per_day = info.get("ExhaustionHoursGainPerWeekday") or {}
    total_culture, culture_source = resolve_total_culture(info)
    # A countdown to a meteor that is not coming, or has already passed, is not a
    # measurement: GreenLeaf reports -17 with no meteor (#237), Sirens -22 with one.
    has_meteor = bool(info.get("HasMeteor"))
    days_until_meteor = _opt_int(info, "DaysUntilMeteor") if has_meteor else None
    if days_until_meteor is not None and days_until_meteor < 0:
        days_until_meteor = None
    meteor = _meteor_state(info, has_meteor, days_until_meteor)
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
            "meteor": meteor,
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
    "get_currency": "/trade",
    "get_market": "/trade",
    "get_stores": "/trade",
    "get_trades": "/trade",
    "find_trade": "/uses/arbitrage",
    "get_crafting_atlas": "/crafting",
    "get_progression": "/jobs",
    "get_civics": "/civics",
    "get_government": "/civics",
    "get_world": "/map",
    "get_region": "/map",
    "get_climate": "/map",
    "get_species": "/species",
    "explain_item": "/items",
    "get_social": "/civics",
}


def site_url_for(tool: str) -> str | None:
    """The live page carrying this tool's answer in full, if there is one."""
    path = TOOL_SITE_PATHS.get(tool)
    return f"{PUBLIC_SITE_URL}{path}" if path else None


def _item_site_url(
    tool: str, arguments: dict[str, Any] | None, result: CallToolResult
) -> str | None:
    """The item's own page when `find_trade` was asked about one item (COI-759).

    The id comes from the offers the call matched, not from the raw user string
    (`charred sausage` is not an id). None sends the caller to the tool's
    general page: no item asked, no match, or a filter spanning several items.
    """
    query = (arguments or {}).get("item")
    if tool != "find_trade" or not isinstance(query, str) or not query.strip():
        return None
    payload = result.structuredContent
    if not isinstance(payload, dict):
        for block in result.content[1:]:
            try:
                parsed = json.loads(block.text) if isinstance(block, TextContent) else None
            except ValueError:
                continue
            if isinstance(parsed, dict):
                payload = parsed
                break
    if not isinstance(payload, dict):
        return None
    item_id = resolved_item_id(payload, query)
    return f"{PUBLIC_SITE_URL}/item?item={quote(item_id, safe='')}" if item_id else None


def _append_site_link(
    tool: str, result: CallToolResult, arguments: dict[str, Any] | None = None
) -> CallToolResult:
    """Add a "see the full version here" line to a tool's markdown block.

    Only the human-readable block is touched. The JSON block is a typed
    contract — several tools validate it against a pydantic output model — so
    a link is not smuggled into it.
    """
    url = _item_site_url(tool, arguments, result) or site_url_for(tool)
    if url is None or not result.content:
        return result
    first = result.content[0]
    if not isinstance(first, TextContent) or url in first.text:
        return result
    first.text = f"{first.text}\n\nFull detail: {url}"
    return result


async def _currency_vocabulary_entries() -> list[dict[str, Any]]:
    """The default server's named currencies, empty rather than failing when the
    server or its admin data is out of reach."""
    try:
        info = await fetch_eco_info(None)
        snapshot = await currency_mod.fetch_currency(
            None,
            info=info,
            days_elapsed=int(info.get("DaysRunning") or 1),
            admin_token=os.environ.get("ECO_ADMIN_TOKEN") or _get_admin_token(),
            default_admin_base=DEFAULT_ECO_INFO_URL.rsplit("/info", 1)[0],
        )
    except Exception:  # a vocabulary degrades to empty, never an error
        return []
    return vocab_mod.currency_vocabulary(snapshot.currencies.values())


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


_UNREPORTED = "not reported"


def _fmt_num(value: float | int | None, spec: str = ",") -> str:
    """Render an optional `/info` number, naming the absent case (#214).

    A missing upstream field reads as "not reported" rather than borrowing a
    zero that a reader would take for a measurement.
    """
    if value is None:
        return _UNREPORTED
    return format(value, spec)


def _meteor_line(cycle: dict[str, Any]) -> str:
    """One markdown phrase for the meteor, naming a destroyed or absent one."""
    meteor = cycle["meteor"]
    if meteor["state"] == "pending":
        return f"{_fmt_num(cycle['daysUntilMeteor'])} days away"
    if meteor["state"] == "destroyed":
        at = f" at {meteor['destroyedAtTime']}" if meteor["destroyedAtTime"] else ""
        return f"destroyed on day {meteor['destroyedOnDay']}{at}, no countdown"
    if meteor["state"] == "none":
        return "none in this world"
    return _UNREPORTED


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
        f"- Meteor: **{_meteor_line(c)}**" + (" ☄" if c["hasMeteor"] else ""),
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


# The nested lists that made a default get_stores 135 KB (eco-app#8354).
_DIRECTORY_NESTED = {
    "stores": ("topItems", "topCounterparties"),
    "traders": ("topSells", "topBuys"),
}


def _shorten_directory_lists(payload: dict[str, Any], keep: int) -> None:
    """Cut each row's nested lists to `keep` entries, and say so."""
    shortened = False
    for key, fields in _DIRECTORY_NESTED.items():
        for row in payload.get(key) or []:
            for name in fields:
                entries = row.get(name)
                if isinstance(entries, list) and len(entries) > keep:
                    row[name] = entries[:keep]
                    shortened = True
    if shortened:
        payload.setdefault("warnings", []).append(
            f"each row's {', '.join(f for fs in _DIRECTORY_NESTED.values() for f in fs)} "
            f"is cut to its top {keep}; pass store or item for whole matching rows, or "
            "limit=0 for everything"
        )


def _filter_directory(payload: dict[str, Any], store: str | None, item: str | None) -> None:
    """Keep only rows matching the store/trader name and the traded item, whole."""
    store_q = (store or "").strip().lower()
    item_q = (item or "").strip().lower()

    def names(row: dict[str, Any], *keys: str) -> str:
        return " ".join(str(row.get(k) or "") for k in keys).lower()

    def trades(row: dict[str, Any], key: str) -> bool:
        return any(
            item_q in f"{e.get('item') or ''} {e.get('pretty') or ''}".lower()
            for e in row.get(key) or []
            if isinstance(e, dict)
        )

    stores = [
        row
        for row in payload.get("stores") or []
        if store_q in names(row, "label", "owner", "storeKey")
        and (not item_q or trades(row, "topItems"))
    ]
    traders = [
        row
        for row in payload.get("traders") or []
        if store_q in names(row, "name")
        and (not item_q or trades(row, "topSells") or trades(row, "topBuys"))
    ]
    payload["stores"], payload["traders"] = stores, traders
    payload.setdefault("warnings", []).append(
        f"filtered to {len(stores)} store(s) and {len(traders)} trader(s) matching "
        f"store={store!r} item={item!r}; an item match reads each row's top items only"
    )


def _fit_directory(payload: dict[str, Any], max_bytes: int) -> None:
    """Drop whole rows from the end of the longer list until the JSON fits."""
    dropped = 0
    while len(json.dumps(payload)) > max_bytes:
        stores, traders = payload.get("stores") or [], payload.get("traders") or []
        longer = "stores" if len(stores) >= len(traders) else "traders"
        if not payload.get(longer):
            break
        payload[longer] = payload[longer][:-1]
        dropped += 1
    if dropped:
        payload.setdefault("warnings", []).append(
            f"dropped {dropped} more row(s) to fit the response under {max_bytes:,} bytes; "
            "narrow store or item, or pass limit=0 for everything"
        )


def _filter_trades_by_item(
    payload: dict[str, Any], trade_norms: norms_mod.Norms, word: str
) -> dict[str, Any]:
    """Keep only the ledger rows for the item `word` names, resolved as `price_by_stage` does.

    A stage qualifier ("iron at au3") is read and dropped, since a trade row carries no
    upgrade stage. An unresolved word empties `trades` and says why, never dumps the
    ledger. The summary arrays still describe every item, and the warning says so."""
    name, miss, qualifier = trade_norms.find_qualified(word, _recipe_items())
    rows = payload.get("trades") or []
    info: dict[str, Any] = {"query": word, "resolved": name, "of": len(rows)}
    if qualifier is not None:
        info["stageIgnored"] = qualifier
    if name is None:
        info["candidates"] = miss.get("candidates") or trade_norms.candidates(word, _recipe_items())
        if "note" in miss:
            info["note"] = miss["note"]
        payload["trades"] = []
        info["matched"] = 0
    else:
        payload["trades"] = [r for r in rows if trade_norms.is_item(name, r.get("item") or "")]
        info["matched"] = len(payload["trades"])
    payload["itemFilter"] = info
    payload.setdefault("warnings", []).append(
        "item filter: trades holds the matching rows only. byItem, byCurrency, topBuyers, "
        "topSellers and the totals still describe every item"
    )
    return info


def _item_filter_line(info: dict[str, Any]) -> str:
    if info["resolved"] is None:
        return f"- Item filter: `{info['query']}` could not be matched to one Eco item."
    return f"- Item filter: {info['resolved']}, {info['matched']:,} of {info['of']:,} ledger rows."


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


def _bound_nested_rows(payload: dict[str, Any], limit: int, key: str, inner: str) -> None:
    """Bound the ``inner`` list of every entry in ``payload[key]``, and say so.

    `_bound_rows` only sees top-level lists, so a list of groups each carrying
    its own growing list would slip past it. Each inner list gets the same
    `limit` and its own warning, so no group's truncation is silent (COI-2048).
    """
    if limit <= 0:
        return
    for group in payload.get(key) or []:
        rows = group.get(inner) if isinstance(group, dict) else None
        if not isinstance(rows, list) or len(rows) <= limit:
            continue
        total = len(rows)
        group[inner] = rows[:limit]
        payload.setdefault("warnings", []).append(
            f"{key}.{group.get('key')}.{inner}: showing {limit:,} of {total:,} rows; "
            "pass limit=0 for all of them"
        )


def _thin_series(payload: dict[str, Any], limit: int, *keys: str) -> None:
    """Thin time series in place, and say what was thinned.

    The curve-shaped sibling of `_bound_rows`. A head slice of a series reports
    the shape of day one and calls it the trend, so these get even spacing with
    the endpoints preserved. See eco-app#6076.
    """
    if limit <= 0:
        return
    for key in keys:
        points = payload.get(key)
        if not isinstance(points, list):
            continue
        thinned, was_thinned = _downsample(points, limit)
        if not was_thinned:
            continue
        payload[key] = thinned
        payload.setdefault("warnings", []).append(
            f"{key}: thinned to {len(thinned):,} evenly-spaced samples of "
            f"{len(points):,} (endpoints preserved); pass limit=0 for every sample"
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
def _recipe_items() -> list[dict[str, Any]]:
    """The recipe graph's items, so price_by_stage knows an item that never traded."""
    from .recipes import load_recipe_index

    return vocab_mod.item_vocabulary(load_recipe_index())


SERVER_INSTRUCTIONS = (
    "Live and historical data for the Sirens Eco game server, and reference "
    "data for Eco itself. Reach for this to answer what is happening in the "
    "world right now or what something costs: players and activity, stores "
    "and market prices, trades, crafting recipes and their inputs, skills and "
    "specialties, laws and elections, climate and pollution, species and "
    "ecoregions. It reads the game; it never changes it. Prices and stock "
    "move, so prefer a fresh call over an earlier answer in the same "
    "conversation. For what to sell or buy an item for, answer from "
    "price_by_stage alone. When it resolves no item, say the word could not be "
    "matched to one Eco item and stop, with no guess, other game, or other price."
)


# Tools that never served their purpose, hidden from tools/list and refused by
# name until fixed. Delete a name to re-enable it. Empty since COI-2087 and
# COI-2088 deleted the last two.
DISABLED_TOOLS: frozenset[str] = frozenset()


def build_server(
    route_registry: DualRouteRegistry | None = None,
    disabled_tools: frozenset[str] = DISABLED_TOOLS,
) -> Server:
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
                    "Show how active the community has been over recent days: "
                    "play activity, new players arriving, and a reputation graph "
                    "of who gives reputation to whom. Answers 'are people still "
                    "playing' and 'who is most respected'. For who is online right "
                    "now use get_server_status. Built from the action-log "
                    "exporter's Play, FirstLogin, and ReputationTransfer rows. "
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
                        "limit": {
                            "type": "integer",
                            "minimum": 0,
                            "description": (
                                "Maximum detail rows to return per unbounded "
                                "list (newArrivals, reputationEdges). Defaults "
                                "to a slice that keeps a no-argument call "
                                "inside an MCP client's response cap; the "
                                "summary and aggregate fields always cover "
                                "every row regardless. 0 means no limit."
                            ),
                        },
                    },
                    "additionalProperties": False,
                },
            ),
        ]
        registered_tools = dual_routes.mcp_tools()
        existing_names = {tool.name for tool in tools}
        duplicates = sorted(tool.name for tool in registered_tools if tool.name in existing_names)
        if duplicates:
            raise ValueError(f"dual routes duplicate existing MCP tools: {', '.join(duplicates)}")
        listed = [tool for tool in [*tools, *registered_tools] if tool.name not in disabled_tools]
        return with_reply_templates(listed)

    # Argument vocabularies for model-free callers (sirens-echo#8249). No
    # annotations, so no client pulls them into a prompt as grounding.
    @server.list_resources()
    async def list_resources() -> list[Resource]:
        return [
            Resource(
                uri=AnyUrl(vocab_mod.ITEMS_URI),
                name="eco-item-vocabulary",
                description="Every Eco item name, for matching a player's words to an item.",
                mimeType="application/json",
            ),
            Resource(
                uri=AnyUrl(vocab_mod.CURRENCIES_URI),
                name="eco-currency-vocabulary",
                description="Every named currency on the server, for a currency argument.",
                mimeType="application/json",
            ),
            Resource(
                uri=AnyUrl(vocab_mod.PRICED_ITEMS_URI),
                name="eco-priced-item-vocabulary",
                description="Every item price_by_stage resolves, shorthand included.",
                mimeType="application/json",
            ),
            Resource(
                uri=AnyUrl(vocab_mod.STAGES_URI),
                name="eco-stage-vocabulary",
                description="Every upgrade stage with its shorthand, for price_by_stage's stage.",
                mimeType="application/json",
            ),
        ]

    @server.read_resource()
    async def read_resource(uri: AnyUrl) -> list[ReadResourceContents]:
        key = str(uri)
        if key == vocab_mod.ITEMS_URI:
            from .recipes import load_recipe_index

            entries = vocab_mod.item_vocabulary(load_recipe_index())
        elif key == vocab_mod.CURRENCIES_URI:
            entries = await _currency_vocabulary_entries()
        elif key == vocab_mod.PRICED_ITEMS_URI:
            price_norms = norms_mod.load()
            entries = price_norms.vocabulary(_recipe_items()) if price_norms else []
        elif key == vocab_mod.STAGES_URI:
            from .upgrade_words import stage_vocabulary

            entries = stage_vocabulary()
        else:
            raise ValueError(f"unknown resource {key}")
        body = json.dumps({"entries": entries})
        return [ReadResourceContents(content=body, mime_type="application/json")]

    async def _dispatch_call_tool_raw(name: str, arguments: dict[str, Any]) -> CallToolResult:
        if name == "price_by_stage":
            args = arguments or {}
            price_norms = norms_mod.load()
            if price_norms is None:
                return CallToolResult(
                    content=[TextContent(type="text", text="the trade norms file is not bundled")],
                    isError=True,
                )
            ctx = await norms_mod.live_context(args.get("server"))
            stage_payload = price_norms.price_by_stage(
                args.get("item"), ctx, stage=args.get("stage"), catalog=_recipe_items()
            )
            return CallToolResult(
                content=[
                    TextContent(type="text", text=norms_mod.price_by_stage_markdown(stage_payload)),
                    TextContent(type="text", text=json.dumps(stage_payload)),
                ],
            )
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
                "byMiner",
                "flows",
            )
            return CallToolResult(
                content=[
                    TextContent(type="text", text=atlas_markdown(atlas)),
                    TextContent(type="text", text=json.dumps(atlas_payload)),
                ],
            )

        if name == "get_mods":
            mods_payload = await asyncio.to_thread(read_mods)
            _bound_rows(mods_payload, _resolve_limit(arguments or {}), "mods")
            return CallToolResult(
                content=[
                    TextContent(type="text", text=wave4_routes.mods_markdown(mods_payload)),
                    TextContent(type="text", text=json.dumps(mods_payload)),
                ],
            )

        if name == "get_world":
            server_arg = arguments.get("server") if arguments else None
            api_key = os.environ.get(ADMIN_API_KEY_ENV) or _get_admin_token()
            try:
                activity = await fetch_world(base_url=server_arg, api_key=api_key)
            except httpx.HTTPError as e:
                return _unreachable_result("Eco exporter", e)
            # Markdown off the full activity; totalEvents and perActionCounts
            # keep describing every event either way (eco-app#6076).
            world_text = world_markdown(activity)
            world_payload = activity.to_dict()
            # Seed, size and cluster centers live in one host file, not behind
            # any HTTP route. Null plus a warning when it is not mounted (COI-763).
            generator, generator_warning = await asyncio.to_thread(read_world_generator)
            world_payload["worldGenerator"] = generator
            if generator_warning:
                world_payload.setdefault("warnings", []).append(generator_warning)
            world_limit = _resolve_limit(arguments or {})
            # timeline is a day series, so it thins rather than truncating: a
            # head slice would report the first days and call it the history.
            _thin_series(world_payload, world_limit, "timeline")
            _bound_rows(
                world_payload,
                world_limit,
                "byCitizen",
                "byPolluter",
                "byObject",
                "hotspots",
            )
            _bound_nested_rows(world_payload, world_limit, "byCitizenByCategory", "players")
            return CallToolResult(
                content=[
                    TextContent(type="text", text=world_text),
                    TextContent(type="text", text=json.dumps(world_payload)),
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
            item_arg = ((arguments or {}).get("item") or "").strip()
            item_filter: dict[str, Any] | None = None
            if item_arg:
                trade_norms = norms_mod.load()
                if trade_norms is None:
                    return CallToolResult(
                        content=[
                            TextContent(type="text", text="the trade norms file is not bundled")
                        ],
                        isError=True,
                    )
                # Whole ledger first, so `limit` bounds the matches and not the newest rows.
                item_filter = _filter_trades_by_item(ledger_payload, trade_norms, item_arg)
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
            ledger_text = ledger_markdown(ledger)
            if item_filter is not None:
                ledger_text += "\n" + _item_filter_line(item_filter)
            return CallToolResult(
                content=[
                    TextContent(type="text", text=ledger_text),
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
            # Tech progression (COI-2090): the crafted-upgrade half comes from the
            # crafting atlas, so an unreachable exporter leaves that half null with
            # a warning and never fails the history.
            craft_atlas: CraftingAtlas | None = None
            atlas_error: str | None = None
            try:
                craft_atlas = await fetch_atlas(base_url=server_arg, api_key=api_key)
            except httpx.HTTPError as e:
                atlas_error = f"{type(e).__name__}: {e}"
            tech, tech_warnings = build_tech_progression(history, craft_atlas, atlas_error)
            progression_payload["techProgression"] = tech
            progression_payload["warnings"].extend(tech_warnings)
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=history_markdown(history) + "\n" + tech_progression_markdown(tech),
                    ),
                    TextContent(type="text", text=json.dumps(progression_payload)),
                ],
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
            args = arguments or {}
            limit = _resolve_limit(args, default=STORES_ROW_LIMIT)
            if args.get("store") or args.get("item"):
                _filter_directory(directory_payload, args.get("store"), args.get("item"))
            elif limit > 0:
                _shorten_directory_lists(directory_payload, STORES_NESTED_LIMIT)
            _bound_rows(directory_payload, limit, "stores", "traders")
            if limit > 0:
                _fit_directory(directory_payload, STORES_MAX_JSON_BYTES)
            return CallToolResult(
                content=[
                    TextContent(type="text", text=directory_markdown(directory)),
                    TextContent(type="text", text=json.dumps(directory_payload)),
                ],
            )

        if name in ("get_recipes", "price_recipe"):
            from .cost import CostParams, annotate_payload
            from .recipes import filter_index, load_recipe_index, narrow_index_maps

            args = arguments or {}
            index = load_recipe_index()

            product = args.get("product")
            recipe_payload: dict[str, Any] = filter_index(
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
            try:
                surface.apply_world_clock(await fetch_eco_info(server_arg))
            except Exception:  # the clock only labels the day series, it must not break the tool
                surface.warnings.append(
                    "world clock: /info unreachable, so `today` and the cycle numbers are omitted"
                )
            # Markdown first, off the full surface: rule 5 of eco-app#6076 keeps
            # summaries describing every row regardless of `limit`.
            markdown = social_markdown(surface)
            social_payload = surface.to_dict()
            _bound_rows(
                social_payload,
                _resolve_limit(arguments or {}),
                "newArrivals",
                "reputationEdges",
            )
            return CallToolResult(
                content=[
                    TextContent(type="text", text=markdown),
                    TextContent(type="text", text=json.dumps(social_payload)),
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
            # Markdown off the full payload; the curves thin after it, so the
            # narrative and the status still read every point (eco-app#6076).
            climate_markdown = _format_climate_markdown(payload)
            climate_limit = _resolve_limit(arguments or {})
            _thin_series(
                payload,
                climate_limit,
                "co2Series",
                "seaLevelSeries",
                "pollutionSeries",
                "temperatureSeries",
                "co2PollutionSeries",
                "co2AnimalsSeries",
                "co2PlantsSeries",
            )
            _bound_rows(payload, climate_limit, "topPolluterCitizens")
            return CallToolResult(
                content=[
                    TextContent(type="text", text=climate_markdown),
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
            # Markdown first: its totals describe every market, and the JSON
            # rows are what a client truncates blind.
            market_payload = intel.to_dict()
            market_markdown = market_mod.market_markdown(intel)
            # One optional field, present only when an item filter was passed. Null
            # with no warning when the item has no FRED mapping (the common case),
            # null with one when the lookup itself failed.
            if item_arg:
                benchmark, benchmark_warning = await benchmark_mod.fetch_benchmark(item_arg)
                market_payload["commodityBenchmark"] = benchmark
                if benchmark:
                    market_markdown += "\n" + benchmark_mod.benchmark_markdown(benchmark)
                if benchmark_warning:
                    market_payload["warnings"].append(benchmark_warning)
                    market_markdown += f"\n- ⚠ {benchmark_warning}"
            _bound_rows(market_payload, _resolve_limit(arguments or {}), "markets")
            return CallToolResult(
                content=[
                    TextContent(type="text", text=market_markdown),
                    TextContent(type="text", text=json.dumps(market_payload, default=str)),
                ],
            )

        if name == "find_trade":
            server_arg = arguments.get("server") if arguments else None
            item_arg = (arguments.get("item") if arguments else None) or None
            currency_arg = (arguments.get("currency") if arguments else None) or None
            store_arg = (arguments.get("store") if arguments else None) or None
            api_key = os.environ.get(ADMIN_API_KEY_ENV) or _get_admin_token()
            try:
                report = await fetch_logistics(
                    base_url=server_arg,
                    api_key=api_key,
                    item=item_arg,
                    currency=currency_arg,
                    store=store_arg,
                )
            except httpx.HTTPError as e:
                return _unreachable_result("Eco exporter", e)
            logistics_payload = report.to_dict()
            logistics_md = logistics_markdown(report)
            _bound_rows(
                logistics_payload,
                _resolve_limit(arguments or {}),
                "cheapest",
                "resale",
                "arbitrage",
                "supplyGaps",
                "marketSummaries",
                "stores",
            )
            return CallToolResult(
                content=[
                    TextContent(type="text", text=logistics_md),
                    TextContent(type="text", text=json.dumps(logistics_payload, default=str)),
                ],
            )

        if name != "get_server_status":
            raise ValueError(f"Unknown tool: {name}")

        server_arg = arguments.get("server") if arguments else None
        try:
            raw = await fetch_eco_info(server_arg)
        except httpx.HTTPError as e:
            return _unreachable_result("Eco server", e)

        raw["_fetchedAtISO"] = datetime.now(UTC).isoformat()

        payload = to_payload(raw)
        return CallToolResult(
            content=[
                TextContent(type="text", text=_format_markdown(payload)),
                TextContent(type="text", text=json.dumps(payload)),
            ],
        )

    async def _dispatch_call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
        # One place, every tool: a listed item price carries its historical norm
        # (teable:coilyco/eco-app#8368). See norms.py.
        result = await _dispatch_call_tool_raw(name, arguments)
        if name not in norms_mod.PRICE_FIELDS or result.isError:
            return reorder_result(result)
        ctx = await norms_mod.live_context((arguments or {}).get("server"))
        for index, block in enumerate(result.content):
            if not isinstance(block, TextContent):
                continue
            try:
                payload = json.loads(block.text)
            except (TypeError, ValueError):
                continue
            if not norms_mod.annotate(name, payload, ctx):
                break
            if name == "get_stores" and _resolve_limit(arguments or {}, STORES_ROW_LIMIT) > 0:
                _fit_directory(payload, STORES_MAX_JSON_BYTES)
            result.content[index] = TextContent(type="text", text=json.dumps(payload, default=str))
            if isinstance(result.structuredContent, dict):
                result.structuredContent = payload
            break
        return reorder_result(result)

    wave1_routes.register_wave1_routes(dual_routes, _dispatch_call_tool)
    wave2_routes.register_wave2_routes(dual_routes, _dispatch_call_tool)
    wave3_routes.register_wave3_routes(dual_routes, _dispatch_call_tool)
    wave4_routes.register_wave4_routes(dual_routes, _dispatch_call_tool)

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
        if name in disabled_tools:
            return CallToolResult(
                content=[TextContent(type="text", text=f"{name} is disabled until it is fixed.")],
                isError=True,
            )
        if dual_routes.has_tool(name):
            result = await dual_routes.call_mcp(name, arguments)
        else:
            result = await _dispatch_call_tool(name, arguments)
        # One place, every tool: point the caller at the page that shows the
        # same answer in full (#241).
        return _append_site_link(name, result, arguments)

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
