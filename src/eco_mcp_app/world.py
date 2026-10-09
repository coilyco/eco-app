"""World / Industry activity — the physical story of what players do to the world.

Net-new surface over the largest unconsumed slice of the pull-everything survey
(eco-app#7): construction, terraforming, roads, object movement, garbage, and
pollution events. Every input is a world/industry **action exporter** already
live on the server (`/api/v1/exporter/actions?actionName=…`), so there is **no
new C# mod and no game restart** — it reuses the crafting atlas's streamed-CSV
plumbing wholesale:

    - `crafting._stream_csv_rows`  — bounded line-by-line CSV streaming
    - `crafting._corrected_index`  — the eco-app#5 undeclared-extra-column fix
    - `crafting.fetch_citizen_name_map` — numeric-id → display-name join
    - `trades.SECONDS_PER_DAY`      — Time (seconds) → in-game day bucketing

Nine action types fold into seven world-mutation **categories**:

    ConstructOrDeconstruct → construction   PlaceOrPickUpObject → objects
    MoveWorldObject        → objects        TampRoad            → roads
    DropOrPickupGarbage    → garbage        ObjectExplosion     → explosions
    PolluteAir             → pollution      DigOrMine / ChopTree → extraction

The extraction pair (`DigOrMine`, `ChopTree`) is re-framed world-first here —
the crafting atlas reads them as *production* (what was made), this surface reads
them as *terraforming* (what was removed from the world). Same rows, two lenses.

Everything is keyed on the Day-3 sparse state: "no events yet" is a valid,
gracefully-degrading response, not an error. Cache mirrors the crafting atlas:
a tiny SQLite under `~/.cache/eco-mcp-app/world.sqlite`, per (base_url,
api_key_hash), TTL 5 min.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .crafting import (
    _INT_RE,
    _NONSENSE_KEY_RE,
    _POSITION_RE,
    _cache_dir,
    _corrected_index,
    _normalize_admin_base,
    _now_iso,
    _stream_csv_rows,
    fetch_citizen_name_map,
    prettify_eco_name,
    rank_citizen_counts,
)
from .trades import SECONDS_PER_DAY

# Action type → world-mutation category. Order sets the default series order in
# the timeline (construction first, extraction last) so the SPA's stacked chart
# is stable across servers. An action an admin disabled 401/404s and is skipped,
# never fatal — partial data is still the story (mirrors the crafting atlas).
WORLD_ACTIONS: tuple[tuple[str, str], ...] = (
    ("ConstructOrDeconstruct", "construction"),
    ("PlaceOrPickUpObject", "objects"),
    ("MoveWorldObject", "objects"),
    ("TampRoad", "roads"),
    ("DropOrPickupGarbage", "garbage"),
    ("ObjectExplosion", "explosions"),
    ("PolluteAir", "pollution"),
    ("DigOrMine", "extraction"),
    ("ChopTree", "extraction"),
)

# Human labels for the categories, in the order they should render.
CATEGORY_LABELS: dict[str, str] = {
    "construction": "Construction",
    "objects": "Objects moved",
    "roads": "Roads",
    "garbage": "Garbage",
    "explosions": "Explosions",
    "pollution": "Pollution",
    "extraction": "Extraction",
}
CATEGORY_ORDER: tuple[str, ...] = tuple(CATEGORY_LABELS.keys())

# Rides the payload as a `*Note` key, so caveats_first fronts it ahead of the
# bulk arrays. Names the unit because the unlabelled byCitizen total was the
# only per-player number get_world carried, and "who built the roads" had no
# honest answer from it (COI-2048).
BY_CITIZEN_BY_CATEGORY_NOTE = (
    "byCitizenByCategory splits the world-action exporter rows by player within "
    "each category key, the same keys as categories. Each players pair is "
    "[name, events]: one event per exporter row, the unit of categories[].events "
    "and byCitizen, not blocks placed and not volume. roads is TampRoad rows, so "
    "the first roads pair is the top road builder. Rows with no numeric Citizen "
    "are attributed to no one. players is null when every "
    "action behind the category failed to fetch (see warnings), and a category "
    "with a partly failed action lists only the players it could count."
)

DEFAULT_BASE_URL = os.environ.get("ECO_ADMIN_BASE_URL", "http://eco.coilysiren.me:3001")
DEFAULT_CACHE_TTL_S = float(os.environ.get("ECO_WORLD_CACHE_TTL", "300"))

# Per-action safety valve, matching the crafting atlas — 500k rows is ~50 MB of
# CSV, well past the late-cycle estimate and still sub-second to fold.
MAX_ROWS_PER_ACTION = int(os.environ.get("ECO_WORLD_MAX_ROWS", "500000"))

# Hotspot binning: world x/z are floored to this grid so nearby events cluster
# into one "where the bulldozers are" cell. 64 blocks ≈ a plaza-sized district.
HOTSPOT_BIN = int(os.environ.get("ECO_WORLD_HOTSPOT_BIN", "64"))

# Object/block id lives under a different column per action; try the broad set
# in priority order (a placed world object first, then the block/terrain id, then
# the chopped species). See the per-action shapes in the module docstring.
_OBJECT_CANDIDATES = (
    "WorldObjectItem",
    "Block",
    "BlockItemOnDestroy",
    "BlockDestroyed",
    "Species",
    "ItemUsed",
    "Item",
)


# ---------------------------------------------------------------------------
# Fold accumulator — mutable working state, unit-testable without HTTP.
# ---------------------------------------------------------------------------


@dataclass
class WorldAccumulator:
    """Running fold state across every action CSV. Not JSON — see WorldActivity."""

    total_events: int = 0
    per_action_counts: dict[str, int] = field(default_factory=dict)
    category_events: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    category_volume: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    # Citizen holds raw numeric ids during the fold; resolved to names once every
    # action has folded (eco-app#5). Value is the world-mutation event count.
    by_citizen: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    # Pollution-category events per citizen — the headline for the /climate
    # cross-link (who is filling the air), split out from the overall shaper board.
    by_polluter: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    # category → citizen → events: the same rows as by_citizen, split by category
    # so "top road builder" has an answer (COI-2048). Ids until names resolve.
    by_citizen_category: dict[str, dict[str, int]] = field(default_factory=dict)
    # Actions whose exporter fetch errored, so a category with no rows because
    # nothing was fetched reads as unavailable and not as nobody did it.
    failed_actions: set[str] = field(default_factory=set)
    # Pollution rows whose actor column was absent or non-numeric (#226).
    pollution_rows_without_actor: int = 0
    # "Most-touched objects" is a count of *events* touching each object — how
    # many times a block/object was placed, moved, dug, or chopped — NOT the
    # summed `Count` column. `Count` is per-event quantity (blocks in a stack,
    # units of ore, biomass of a felled tree), so summing it conflated units
    # with touches and produced absurd headline numbers like "Dirt Ramp
    # 19,516,641" (eco-app#82, the same harvest-biomass-vs-unit bug as #70).
    by_object: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    # day → category → event count, the raw material for the SPA's timeline.
    timeline: dict[int, dict[str, int]] = field(default_factory=dict)
    # binned (x, z) → event count, the "where" of world activity.
    hotspots: dict[tuple[int, int], int] = field(default_factory=lambda: defaultdict(int))
    warnings: list[str] = field(default_factory=list)


def _bin_position(pos: str) -> tuple[int, int] | None:
    """`"418,75,460"` → floored (x, z) grid cell, dropping the y (height) axis."""
    if not pos or not _POSITION_RE.match(pos):
        return None
    parts = pos.split(",")
    try:
        x = int(float(parts[0]))
        z = int(float(parts[2]))
    except (ValueError, IndexError):
        return None
    return ((x // HOTSPOT_BIN) * HOTSPOT_BIN, (z // HOTSPOT_BIN) * HOTSPOT_BIN)


def aggregate_world_rows(
    action_name: str,
    category: str,
    rows: Iterable[list[str]],
    acc: WorldAccumulator,
    max_rows: int = MAX_ROWS_PER_ACTION,
) -> int:
    """Fold one world-action CSV into the running accumulator.

    Returns the number of data rows consumed (excluding the header). Header-keyed
    picks (never fixed positions) plus the eco-app#5 column corrector keep the
    fold aligned even when the exporter inserts an undeclared tool column. Each
    row is one **event**; `Count` (blocks placed, garbage dropped, ppm emitted)
    accumulates as the category's **volume** and defaults to 1 when absent so a
    Count-less action still contributes to volume.
    """
    it = iter(rows)
    try:
        header = next(it)
    except StopIteration:
        return 0

    col = {name: i for i, name in enumerate(header)}

    def pick(row: list[str], idx: list[int], *candidates: str) -> str | None:
        for c in candidates:
            j = col.get(c)
            if j is not None and idx[j] < len(row):
                v = row[idx[j]].strip()
                if v:
                    return v
        return None

    consumed = 0
    for row in it:
        if not row:
            continue
        if consumed >= max_rows:
            acc.warnings.append(
                f"{action_name}: truncated at {max_rows} rows (late-cycle size cap)"
            )
            break
        idx = _corrected_index(header, row)

        try:
            count = float(pick(row, idx, "Count") or "0")
        except ValueError:
            count = 0.0
        volume = count if count > 0 else 1.0

        obj = pick(row, idx, *_OBJECT_CANDIDATES) or ""
        if obj and _NONSENSE_KEY_RE.match(obj):
            obj = ""  # a position/number leaked into the name slot — drop it

        citizen = pick(row, idx, "Citizen")

        time_raw = pick(row, idx, "Time")
        day: int | None = None
        if time_raw is not None:
            try:
                day = int(float(time_raw) // SECONDS_PER_DAY)
            except ValueError:
                day = None

        cell = _bin_position(pick(row, idx, "ActionLocation", "Position") or "")

        # --- fold ---
        acc.category_events[category] += 1
        acc.category_volume[category] += volume
        if obj:
            # One touch per event — see WorldAccumulator.by_object (eco-app#82).
            acc.by_object[obj] += 1
        if citizen and _INT_RE.match(citizen):
            acc.by_citizen[citizen] += 1
            per_category = acc.by_citizen_category.setdefault(category, {})
            per_category[citizen] = per_category.get(citizen, 0) + 1
            if category == "pollution":
                acc.by_polluter[citizen] += 1
        elif category == "pollution":
            # 838 PolluteAir events, none attributed: the rows carry no
            # recognised actor column. Count them so the emptiness explains
            # itself rather than reading as "nobody pollutes" (#226).
            acc.pollution_rows_without_actor += 1
        if day is not None:
            acc.timeline.setdefault(day, defaultdict(int))[category] += 1
        if cell is not None:
            acc.hotspots[cell] += 1
        consumed += 1

    acc.total_events += consumed
    acc.per_action_counts[action_name] = acc.per_action_counts.get(action_name, 0) + consumed
    return consumed


def _resolve_ids(counts: dict[str, int], name_map: dict[str, str]) -> dict[str, int]:
    """Rewrite numeric-id keys to display names, unmapped → ``Citizen #<id>``."""
    resolved: dict[str, int] = defaultdict(int)
    for cid, events in counts.items():
        resolved[name_map.get(cid) or f"Citizen #{cid}"] += events
    return resolved


def apply_citizen_names(acc: WorldAccumulator, name_map: dict[str, str]) -> None:
    """Rewrite the by_citizen / by_polluter id keys to display names, in place.

    Unmapped ids render as ``Citizen #<id>`` so a player the join misses still
    ranks rather than vanishing (mirrors the crafting atlas, eco-app#5).
    """
    if acc.by_citizen:
        acc.by_citizen = _resolve_ids(acc.by_citizen, name_map)
    if acc.by_polluter:
        acc.by_polluter = _resolve_ids(acc.by_polluter, name_map)
    acc.by_citizen_category = {
        category: dict(rank_citizen_counts(counts, name_map))
        for category, counts in acc.by_citizen_category.items()
    }


# ---------------------------------------------------------------------------
# Finalized report — ranked + JSON-serializable for the SPA and the MCP card.
# ---------------------------------------------------------------------------


@dataclass
class WorldActivity:
    """Ranked, serializable world-mutation report. Built from a WorldAccumulator."""

    fetched_at_iso: str
    source_base_url: str
    total_events: int = 0
    per_action_counts: dict[str, int] = field(default_factory=dict)
    # (category_key, events, volume), ordered by CATEGORY_ORDER for stable series.
    categories: list[tuple[str, int, float]] = field(default_factory=list)
    # (day, {category: events}) sorted by day ascending — the mutation timeline.
    timeline: list[tuple[int, dict[str, int]]] = field(default_factory=list)
    by_citizen: list[tuple[str, int]] = field(default_factory=list)
    by_polluter: list[tuple[str, int]] = field(default_factory=list)
    # (category_key, events or None, ranked (player, events) or None), in
    # CATEGORY_ORDER. None, never an empty list, when every action behind the
    # category failed to fetch.
    by_citizen_category: list[tuple[str, int | None, list[tuple[str, int]] | None]] = field(
        default_factory=list
    )
    # (object id, touch-event count) — see WorldAccumulator.by_object.
    by_object: list[tuple[str, int]] = field(default_factory=list)
    # (x, z, events) coarse-binned, ranked by event count.
    hotspots: list[tuple[int, int, int]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def category_keys(self) -> list[str]:
        return [k for k, _, _ in self.categories]

    def to_dict(self) -> dict[str, Any]:
        return {
            "view": "world",
            "fetchedAtISO": self.fetched_at_iso,
            "sourceBaseUrl": self.source_base_url,
            "totalEvents": self.total_events,
            "perActionCounts": dict(self.per_action_counts),
            "categories": [
                {"key": k, "label": CATEGORY_LABELS.get(k, k), "events": e, "volume": round(v, 2)}
                for k, e, v in self.categories
            ],
            "categoryKeys": self.category_keys,
            "timeline": [{"day": d, "counts": dict(c)} for d, c in self.timeline],
            "byCitizen": [[n, e] for n, e in self.by_citizen],
            "byPolluter": [[n, e] for n, e in self.by_polluter],
            "byCitizenByCategory": [
                {
                    "key": k,
                    "label": CATEGORY_LABELS.get(k, k),
                    "events": e,
                    "players": None if players is None else [[n, c] for n, c in players],
                }
                for k, e, players in self.by_citizen_category
            ],
            "byCitizenByCategoryNote": BY_CITIZEN_BY_CATEGORY_NOTE,
            "byObject": [[n, e] for n, e in self.by_object],
            "hotspots": [{"x": x, "z": z, "events": e} for x, z, e in self.hotspots],
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorldActivity:
        return cls(
            fetched_at_iso=data["fetchedAtISO"],
            source_base_url=data["sourceBaseUrl"],
            total_events=int(data.get("totalEvents", 0)),
            per_action_counts=dict(data.get("perActionCounts", {})),
            categories=[
                (c["key"], int(c["events"]), float(c["volume"])) for c in data["categories"]
            ],
            timeline=[
                (int(t["day"]), {k: int(v) for k, v in t["counts"].items()})
                for t in data.get("timeline", [])
            ],
            by_citizen=[(n, int(e)) for n, e in data.get("byCitizen", [])],
            by_polluter=[(n, int(e)) for n, e in data.get("byPolluter", [])],
            by_citizen_category=[
                (
                    str(c["key"]),
                    None if c.get("events") is None else int(c["events"]),
                    None if c.get("players") is None else [(n, int(e)) for n, e in c["players"]],
                )
                for c in data.get("byCitizenByCategory", [])
            ],
            by_object=[(n, int(e)) for n, e in data.get("byObject", [])],
            hotspots=[
                (int(h["x"]), int(h["z"]), int(h["events"])) for h in data.get("hotspots", [])
            ],
            warnings=list(data.get("warnings", [])),
        )


def finalize(
    acc: WorldAccumulator,
    fetched_at_iso: str,
    source_base_url: str,
    *,
    top_citizens: int | None = None,
    top_objects: int = 25,
    top_hotspots: int = 12,
) -> WorldActivity:
    """Rank the accumulator's dicts into a serializable WorldActivity.

    ``by_citizen`` / ``by_polluter`` list **every** shaper / polluter, not a
    truncated top-N: these are user-pivoted lists and eco-app#80 requires them
    complete (the per-user dossier joins against them, and the /world board is
    already rank-ordered). ``top_citizens=None`` means no cap — ``[:None]``
    keeps the whole ranked list; pass an int only to bound it (tests, cards).
    """
    categories = [
        (k, acc.category_events.get(k, 0), acc.category_volume.get(k, 0.0))
        for k in CATEGORY_ORDER
        if acc.category_events.get(k)
    ]
    timeline = sorted(
        ((day, dict(counts)) for day, counts in acc.timeline.items()),
        key=lambda kv: kv[0],
    )
    by_citizen = sorted(acc.by_citizen.items(), key=lambda kv: kv[1], reverse=True)[:top_citizens]
    by_polluter = sorted(acc.by_polluter.items(), key=lambda kv: kv[1], reverse=True)[:top_citizens]
    by_object = sorted(acc.by_object.items(), key=lambda kv: kv[1], reverse=True)[:top_objects]
    hotspots = sorted(
        ((x, z, n) for (x, z), n in acc.hotspots.items()),
        key=lambda t: t[2],
        reverse=True,
    )[:top_hotspots]
    by_citizen_category: list[tuple[str, int | None, list[tuple[str, int]] | None]] = []
    for key in CATEGORY_ORDER:
        events = acc.category_events.get(key, 0)
        actions = {a for a, c in WORLD_ACTIONS if c == key}
        if not events and actions <= acc.failed_actions:
            by_citizen_category.append((key, None, None))
        elif events:
            ranked = sorted(
                acc.by_citizen_category.get(key, {}).items(), key=lambda kv: (-kv[1], kv[0])
            )
            by_citizen_category.append((key, events, ranked[:top_citizens]))
    warnings = list(acc.warnings)
    if acc.pollution_rows_without_actor and not by_polluter:
        warnings.append(
            f"{acc.pollution_rows_without_actor} pollution event(s) carry no recognised actor "
            "column, so byPolluter is empty rather than incomplete (eco-app#226)."
        )
    return WorldActivity(
        fetched_at_iso=fetched_at_iso,
        source_base_url=source_base_url,
        total_events=acc.total_events,
        per_action_counts=dict(acc.per_action_counts),
        categories=categories,
        timeline=timeline,
        by_citizen=list(by_citizen),
        by_polluter=list(by_polluter),
        by_citizen_category=by_citizen_category,
        by_object=list(by_object),
        hotspots=hotspots,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# SQLite cache — per (base_url, api_key_hash), mirrors the crafting atlas.
# ---------------------------------------------------------------------------


def _cache_path() -> Path:
    return _cache_dir() / "world.sqlite"


def _cache_key(base_url: str, api_key: str | None) -> str:
    token = hashlib.sha256((api_key or "").encode()).hexdigest()[:12]
    return f"{base_url}|{token}"


def _cache_get(base_url: str, api_key: str | None, ttl_s: float) -> WorldActivity | None:
    try:
        conn = sqlite3.connect(_cache_path())
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS activity "
                "(key TEXT PRIMARY KEY, stored_at REAL, payload TEXT)"
            )
            row = conn.execute(
                "SELECT stored_at, payload FROM activity WHERE key = ?",
                (_cache_key(base_url, api_key),),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return None
    if not row:
        return None
    stored_at, payload = row
    if (time.time() - float(stored_at)) > ttl_s:
        return None
    return WorldActivity.from_dict(json.loads(payload))


def _cache_put(base_url: str, api_key: str | None, activity: WorldActivity) -> None:
    try:
        conn = sqlite3.connect(_cache_path())
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS activity "
                "(key TEXT PRIMARY KEY, stored_at REAL, payload TEXT)"
            )
            conn.execute(
                "INSERT OR REPLACE INTO activity (key, stored_at, payload) VALUES (?, ?, ?)",
                (_cache_key(base_url, api_key), time.time(), json.dumps(activity.to_dict())),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        # A disk-full / permission error on the cache must not fail the tool.
        return


# ---------------------------------------------------------------------------
# Fetch — stream every world-action CSV, fold, resolve names, finalize.
# ---------------------------------------------------------------------------


async def _stream_action_into(
    http: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    action_name: str,
    category: str,
    acc: WorldAccumulator,
) -> None:
    """Stream one action CSV in bounded batches, folding each into `acc`.

    Batches every ~1k rows so we never hold more than that in Python-land at
    once; the per-action row budget shrinks across batches (mirrors fetch_atlas).
    """
    remaining = MAX_ROWS_PER_ACTION
    header: list[str] | None = None
    batch: list[list[str]] = []
    async for row in _stream_csv_rows(http, url, headers):
        if header is None:
            header = row
            batch = [row]
            continue
        batch.append(row)
        if len(batch) >= 1024:
            consumed = aggregate_world_rows(action_name, category, batch, acc, max_rows=remaining)
            remaining -= consumed
            if remaining <= 0:
                break
            batch = [header]
    if header is not None and len(batch) > 1 and remaining > 0:
        aggregate_world_rows(action_name, category, batch, acc, max_rows=remaining)
    acc.per_action_counts.setdefault(action_name, 0)


async def fetch_world(
    base_url: str | None = None,
    api_key: str | None = None,
    cache_ttl_s: float = DEFAULT_CACHE_TTL_S,
    client: httpx.AsyncClient | None = None,
) -> WorldActivity:
    """Stream every world-action CSV and fold them into one activity report.

    `client` is injectable so tests hand in a respx-stubbed httpx client. A
    per-action fetch failure (disabled exporter, auth) becomes a non-fatal
    warning rather than sinking the whole report — partial data still tells the
    world-mutation story.
    """
    normalized = _normalize_admin_base(base_url)
    cached = _cache_get(normalized, api_key, cache_ttl_s)
    if cached is not None:
        return cached

    acc = WorldAccumulator()
    headers = {"X-API-Key": api_key} if api_key else {}

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0))
    try:
        for action, category in WORLD_ACTIONS:
            url = f"{normalized}/api/v1/exporter/actions?actionName={action}"
            try:
                await _stream_action_into(http, url, headers, action, category, acc)
            except httpx.HTTPStatusError as e:
                acc.failed_actions.add(action)
                acc.warnings.append(f"{action}: HTTP {e.response.status_code}")
            except httpx.HTTPError as e:
                acc.failed_actions.add(action)
                acc.warnings.append(f"{action}: {type(e).__name__}: {e}")

        if acc.by_citizen:
            name_map = await fetch_citizen_name_map(http, normalized, headers, acc.warnings)
            apply_citizen_names(acc, name_map)
    finally:
        if owns_client:
            await http.aclose()

    activity = finalize(acc, _now_iso(), normalized)
    _cache_put(normalized, api_key, activity)
    return activity


# ---------------------------------------------------------------------------
# Card / text shaping — the SPA owns product UX; these stay compact summaries.
# ---------------------------------------------------------------------------


def world_template_context(activity: WorldActivity, *, top: int = 10) -> dict[str, Any]:
    """Shape for the MCP `_meta.ui` Jinja card (compact in-chat summary)."""
    cat_events = {k: e for k, e, _ in activity.categories}
    max_cat = max((e for e in cat_events.values()), default=0) or 1
    max_builder = max((e for _, e in activity.by_citizen), default=0) or 1
    max_object = max((e for _, e in activity.by_object), default=0) or 1
    max_hot = max((e for _, _, e in activity.hotspots), default=0) or 1
    return {
        "empty": activity.total_events == 0,
        "fetched_at_iso": activity.fetched_at_iso,
        "source_base_url": activity.source_base_url,
        "total_events": activity.total_events,
        "categories": [
            {
                "key": k,
                "label": CATEGORY_LABELS.get(k, k),
                "events": e,
                "volume": v,
                "pct": (e / max_cat) * 100.0,
            }
            for k, e, v in activity.categories
        ],
        "top_builders": [
            {"name": n, "events": e, "pct": (e / max_builder) * 100.0}
            for n, e in activity.by_citizen[:top]
        ],
        "top_objects": [
            {
                "name": n,
                "pretty": prettify_eco_name(n),
                "events": e,
                "pct": (e / max_object) * 100.0,
            }
            for n, e in activity.by_object[:top]
        ],
        "hotspots": [
            {"x": x, "z": z, "events": e, "pct": (e / max_hot) * 100.0}
            for x, z, e in activity.hotspots[:top]
        ],
        "warnings": list(activity.warnings),
    }


def world_markdown(activity: WorldActivity) -> str:
    """Summarize world activity for an MCP text result."""
    if activity.total_events == 0:
        return (
            f"**World activity** — no world-mutation events recorded yet "
            f"({activity.source_base_url})."
        )
    lines = [
        f"**World activity** — {activity.total_events:,} world-mutation events "
        f"(`{activity.source_base_url}`)",
        "",
        "**By category:**",
    ]
    for k, e, v in activity.categories:
        lines.append(f"- {CATEGORY_LABELS.get(k, k)}: {e:,} events ({v:,.0f} volume)")
    if activity.by_citizen:
        lines.append("")
        lines.append("**Top world-shapers:**")
        for i, (name, events) in enumerate(activity.by_citizen[:10], 1):
            lines.append(f"{i}. {name} — {events:,} events")
    leaders = [(k, players[0]) for k, _, players in activity.by_citizen_category if players]
    if leaders:
        lines.append("")
        lines.append("**Top player per category** (events):")
        for k, (name, events) in leaders:
            lines.append(f"- {CATEGORY_LABELS.get(k, k)}: {name} — {events:,} events")
    if activity.by_object:
        lines.append("")
        lines.append("**Most-touched objects:**")
        for i, (name, events) in enumerate(activity.by_object[:10], 1):
            lines.append(f"{i}. {prettify_eco_name(name)} — {events:,} touches")
    if activity.hotspots:
        lines.append("")
        lines.append("**Activity hotspots (x, z):**")
        for x, z, events in activity.hotspots[:5]:
            lines.append(f"- ({x}, {z}) — {events:,} events")
    if activity.warnings:
        lines.append("")
        for w in activity.warnings:
            lines.append(f"- ⚠ {w}")
    return "\n".join(lines).rstrip()


__all__ = [
    "CATEGORY_LABELS",
    "CATEGORY_ORDER",
    "WORLD_ACTIONS",
    "WorldAccumulator",
    "WorldActivity",
    "aggregate_world_rows",
    "apply_citizen_names",
    "fetch_world",
    "finalize",
    "world_markdown",
    "world_template_context",
]
