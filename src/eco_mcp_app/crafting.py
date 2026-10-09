"""Crafting Activity Atlas — aggregate Eco action-exporter CSVs into a card.

The `/api/v1/exporter/actions?actionName=ItemCraftedAction` CSV grows to
multi-megabyte sizes late-cycle (295 KB on day 3 → tens of MB by meteor).
We stream-parse via `httpx.AsyncClient.stream(...)` + `csv.reader` on a line
iterator so we never materialize the whole body in memory.

Aggregation runs in a single pass across four action types that all describe
"production" in Eco:

    - ItemCraftedAction        (bench crafts)
    - HarvestOrHunt            (plant/animal harvest; damage=1 is a kill)
    - ChopTree                 (forestry — felled=true is a full chop)
    - DigOrMine                (excavation; output item is the block)

Everything is keyed on the Day 3 sparse-state — "no events yet" is a valid
response, not an error.

Cache: a tiny SQLite under `~/.cache/eco-mcp-app/crafting.sqlite` holds the
last successful aggregation + a fetched-at timestamp, TTL 5 min. Cache is
per (base_url, api_key_hash) so swapping servers doesn't cross-contaminate.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sqlite3
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

# Action types we aggregate. ItemCraftedAction is the canonical "crafted",
# others flesh out the full production picture for the sankey / leaderboard.
# If an admin disables one via a mod, the 401/404 on that endpoint is skipped
# rather than fatal — partial data is still useful.
CRAFT_ACTION_TYPES = (
    "ItemCraftedAction",
    "HarvestOrHunt",
    "ChopTree",
    "DigOrMine",
)

# ItemCraftedAction fires once per completed crafting *iteration* (work-order
# cycle), and its Count defaults to 1 per event. The server's StatsAggregator
# then collapses events older than its detail window into per-citizen HOURLY
# rollups: Count becomes the number of merged events, Time becomes the max,
# and the item/station/location columns keep the *first* merged event's values
# — i.e. on a Count>1 row those labels are an arbitrary representative, not an
# attribution ("Theo crafted 32 stump latrines" was 32 iterations of anything
# in one hour, 2 of them latrines). Citizen is the aggregation key, so citizen
# attribution stays valid on rollups. See eco-app#131.
#
# For the gather actions (HarvestOrHunt / ChopTree / DigOrMine) the Count column
# is a harvest *magnitude* — biomass / weight, hundreds-of-thousands per chop —
# not a unit count, so summing it across action types buries real crafting under
# plant biomass. We therefore keep two separate item boards: crafted counts
# iterations from per-event rows, gathered counts *events*. See eco-app#70.
CRAFTED_ACTION_TYPE = "ItemCraftedAction"
MINING_ACTION_TYPE = "DigOrMine"

# Rides the payload as a `*Note` key, so caveats_first fronts it ahead of the
# bulk arrays. The scientist's probe saw `byCitizenIterations` read as a mining
# count because nothing beside it said which actions it totals (COI-2049).
BY_MINER_NOTE = (
    "byMiner ranks players by DigOrMine exporter rows, one per dig or mine event "
    "(not blocks removed, not the Count magnitude), and is the board to read for "
    "the busiest miner. null means the DigOrMine exporter was not fetched, see "
    "warnings. byCitizen and byCitizenIterations total all four action types and "
    "are not mining counts."
)

DEFAULT_BASE_URL = os.environ.get("ECO_ADMIN_BASE_URL", "http://eco.coilysiren.me:3001")
DEFAULT_CACHE_TTL_S = float(os.environ.get("ECO_CRAFTING_CACHE_TTL", "300"))

# Max rows per action type before we bail out (defensive for pathological late-
# cycle CSVs). 500k is ~50 MB of CSV; well past the ~20 MB end-cycle estimate,
# and still sub-second to aggregate.
MAX_ROWS_PER_ACTION = int(os.environ.get("ECO_CRAFTING_MAX_ROWS", "500000"))

# Exporter rows sometimes carry an extra tool column the header doesn't
# declare (e.g. HarvestOrHunt rows gain "HandsItem" before Position), shifting
# every later field. `_corrected_index` detects and absorbs that column so
# header-keyed picks land on the right value; keys that still parse as a
# position triple / bare number are never legitimate items/stations, so they
# are dropped at fold time. See eco-app#5.
_NONSENSE_KEY_RE = re.compile(r"^-?\d+(?:\.\d+)?(?:,-?\d+(?:\.\d+)?)*$")

# Citizen is a numeric in-game user id in the exporter CSV; names come from a
# separate join against the jobs mod's /api/v1/citizens surface. When that
# surface is unreachable we still show the ids rather than dropping the
# dimension - this warning flags that the labels are ids, not names.
CITIZEN_NAMES_UNAVAILABLE_WARNING = (
    "citizen names unavailable (/api/v1/citizens) - showing numeric ids, see eco-app#5"
)

_POSITION_RE = re.compile(r"^-?\d+,-?\d+,-?\d+$")
_INT_RE = re.compile(r"^-?\d+$")


def _is_float(v: str) -> bool:
    try:
        float(v)
    except ValueError:
        return False
    return True


def _looks_like_name(v: str) -> bool:
    """A PascalCase item/species id - anything that isn't a number/position."""
    return not _NONSENSE_KEY_RE.match(v)


# Value-shape validators for the columns we can recognize on sight. Only used
# to realign rows that carry an undeclared extra column: we score each
# candidate insertion point by how well the shifted columns match these shapes
# and keep the best fit. Both typed columns (numbers/positions) and name
# columns are validated so a shift that pushes a name into a numeric slot (or
# vice-versa) scores worse. Names span every action shape we aggregate plus the
# economy actions the same realign path will serve later.
_COLUMN_SHAPE: dict[str, Any] = {
    "Count": _is_float,
    "Time": lambda v: bool(_INT_RE.match(v)),
    "Position": lambda v: bool(_POSITION_RE.match(v)),
    "ActionLocation": lambda v: bool(_POSITION_RE.match(v)),
    "Citizen": lambda v: bool(_INT_RE.match(v)),
    "Buyer": lambda v: bool(_INT_RE.match(v)),
    "Seller": lambda v: bool(_INT_RE.match(v)),
    "ShopOwner": lambda v: bool(_INT_RE.match(v)),
    # Currency-trade numerics — let the trades ledger share this realign path.
    "CurrencyAmount": _is_float,
    "NumberOfItems": _is_float,
    "BoughtOrSold": lambda v: bool(_INT_RE.match(v)),
    # Social-action numerics — let the social surface share this realign path
    # (ReputationTransfer parties + amount; see social.py, eco-app#63).
    "Receiver": lambda v: bool(_INT_RE.match(v)),
    "ReceiverCitizen": lambda v: bool(_INT_RE.match(v)),
    "Giver": lambda v: bool(_INT_RE.match(v)),
    "Amount": _is_float,
    "Reputation": _is_float,
    "Species": _looks_like_name,
    "WorldObjectItem": _looks_like_name,
    "ItemUsed": _looks_like_name,
    "ToolUsed": _looks_like_name,
    "Currency": _looks_like_name,
    "BlockItemOnDestroy": _looks_like_name,
    "BlockDestroyed": _looks_like_name,
    # World/industry action name columns — let the world-mutation timeline share
    # this realign path (eco-app#62). Construction/road/object rows carry a
    # block or world-object id where these validate as names.
    "Block": _looks_like_name,
    "Item": _looks_like_name,
}


def _corrected_index(header: list[str], row: list[str]) -> list[int]:
    """Map each header column to its real index in `row`.

    The exporter occasionally inserts one or more undeclared columns (a tool
    item like "HandsItem") contiguously before some header position, shifting
    every later field right. We recover the shift by trying each candidate
    insertion point and scoring how well the recognizable columns
    (`_COLUMN_SHAPE`) validate under it, then return the index map for the best
    fit. Aligned rows (and the rare short row) get the identity map. See
    eco-app#5.
    """
    n = len(header)
    extra = len(row) - n
    if extra <= 0:
        return list(range(n))

    typed = [(i, name) for i, name in enumerate(header) if name in _COLUMN_SHAPE]
    best_p = 0
    best_score: int | None = None
    for p in range(n + 1):
        score = 0
        for i, name in typed:
            ri = i if i < p else i + extra
            if ri >= len(row):
                continue
            v = row[ri].strip()
            if not v:
                continue  # empty cells (missing tool, no item) are neutral
            score += 1 if _COLUMN_SHAPE[name](v) else -1
        # `>=` so that among equally-scoring insertion points we keep the
        # latest, which leaves the most leading columns at their header index -
        # the correct choice when the columns before the true insertion point
        # aren't typed enough to distinguish it.
        if best_score is None or score >= best_score:
            best_score, best_p = score, p
    return [i if i < best_p else i + extra for i in range(n)]


@dataclass
class CraftingAtlas:
    """Crafting activity summary. JSON-serializable."""

    fetched_at_iso: str
    source_base_url: str
    total_events: int = 0
    # Crafted items: confirmed crafting iterations per item. A per-event row
    # (Count == 1) is one iteration; an hourly-rollup row (Count > 1) counts
    # exactly 1 - its labels come from the first merged event, so one iteration
    # of the label item is proven and the rest are unattributable (eco-app#131).
    # These are floors, not totals, once history rolls up.
    by_crafted: list[tuple[str, float]] = field(default_factory=list)
    # Gathered resources: gather *events* per species/block from HarvestOrHunt /
    # ChopTree / DigOrMine. Their Count is a biomass magnitude, not a unit count,
    # so we count events (comparable across resources) rather than sum it.
    # See eco-app#70.
    by_gathered: list[tuple[str, int]] = field(default_factory=list)
    by_station: list[tuple[str, int]] = field(default_factory=list)
    # Production *events* per citizen — one per exporter row, the same unit
    # `get_world.byCitizen` uses. The two tools share the field name, so they
    # have to share the unit: this used to carry craft iterations, which made
    # one citizen's value 15x the server's whole `totalEvents` under an
    # event-shaped name (eco-app#222).
    by_citizen: list[tuple[str, int]] = field(default_factory=list)
    # Craft iterations per citizen — the Count-weighted board. On a rollup row
    # Count is the merged event total keyed by that citizen, so unlike the item
    # boards this one stays correct across all history (eco-app#131). Gathers
    # stay event-weighted here too, so a plant harvester's biomass cannot
    # dominate the ranking (eco-app#70).
    by_citizen_iterations: list[tuple[str, int]] = field(default_factory=list)
    # DigOrMine exporter rows per citizen: one per dig or mine event, never the
    # Count magnitude. The only board that answers "busiest miner" - the two
    # boards above total every action type (COI-2049).
    by_miner: list[tuple[str, int]] = field(default_factory=list)
    # Sankey edges: (source_station, target_item, event_count). Event-weighted
    # for the same reason — a single 200k-biomass chop would otherwise swamp the
    # diagram. See eco-app#70.
    flows: list[tuple[str, str, float]] = field(default_factory=list)
    # Per-action-type row count, so the UI can say "4 types fed the atlas".
    per_action_counts: dict[str, int] = field(default_factory=dict)
    # Hourly-rollup craft rows seen, and the iterations on them beyond the one
    # attributable representative each — surfaced so the UI can say how much
    # history has no per-item detail (eco-app#131).
    rollup_events: int = 0
    rollup_iterations: float = 0.0
    # Non-fatal fetch problems — shown as a hint under the card.
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fetchedAtISO": self.fetched_at_iso,
            "sourceBaseUrl": self.source_base_url,
            "totalEvents": self.total_events,
            "byCrafted": [[n, c] for n, c in self.by_crafted],
            "byGathered": [[n, c] for n, c in self.by_gathered],
            "byStation": [[n, c] for n, c in self.by_station],
            "byCitizen": [[n, c] for n, c in self.by_citizen],
            "byCitizenIterations": [[n, c] for n, c in self.by_citizen_iterations],
            # null, never [], when the DigOrMine exporter was not fetched, so a
            # failed fetch cannot read as "nobody mines" (COI-2049).
            "byMiner": (
                [[n, c] for n, c in self.by_miner]
                if MINING_ACTION_TYPE in self.per_action_counts
                else None
            ),
            "byMinerNote": BY_MINER_NOTE,
            "flows": [[s, t, c] for s, t, c in self.flows],
            "perActionCounts": dict(self.per_action_counts),
            "rollupEvents": self.rollup_events,
            "rollupIterations": self.rollup_iterations,
            "warnings": list(self.warnings),
        }


def _cache_dir() -> Path:
    root = Path(os.environ.get("ECO_CACHE_DIR") or (Path.home() / ".cache" / "eco-mcp-app"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cache_path() -> Path:
    return _cache_dir() / "crafting.sqlite"


def _cache_key(base_url: str, api_key: str | None) -> str:
    token = hashlib.sha256((api_key or "").encode()).hexdigest()[:12]
    return f"{base_url}|{token}"


def _cache_get(base_url: str, api_key: str | None, ttl_s: float) -> CraftingAtlas | None:
    try:
        conn = sqlite3.connect(_cache_path())
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS atlas "
                "(key TEXT PRIMARY KEY, stored_at REAL, payload TEXT)"
            )
            row = conn.execute(
                "SELECT stored_at, payload FROM atlas WHERE key = ?",
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
    data = json.loads(payload)
    return CraftingAtlas(
        fetched_at_iso=data["fetchedAtISO"],
        source_base_url=data["sourceBaseUrl"],
        total_events=int(data["totalEvents"]),
        by_crafted=[(n, float(c)) for n, c in data.get("byCrafted", [])],
        by_gathered=[(n, int(c)) for n, c in data.get("byGathered", [])],
        by_station=[(n, int(c)) for n, c in data.get("byStation", [])],
        by_citizen=[(n, int(c)) for n, c in data.get("byCitizen", [])],
        by_citizen_iterations=[(n, int(c)) for n, c in data.get("byCitizenIterations", [])],
        by_miner=[(n, int(c)) for n, c in data.get("byMiner") or []],
        flows=[(s, t, float(c)) for s, t, c in data.get("flows", [])],
        per_action_counts=dict(data.get("perActionCounts", {})),
        rollup_events=int(data.get("rollupEvents", 0)),
        rollup_iterations=float(data.get("rollupIterations", 0.0)),
        warnings=list(data.get("warnings", [])),
    )


def _cache_put(base_url: str, api_key: str | None, atlas: CraftingAtlas) -> None:
    try:
        conn = sqlite3.connect(_cache_path())
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS atlas "
                "(key TEXT PRIMARY KEY, stored_at REAL, payload TEXT)"
            )
            conn.execute(
                "INSERT OR REPLACE INTO atlas (key, stored_at, payload) VALUES (?, ?, ?)",
                (
                    _cache_key(base_url, api_key),
                    time.time(),
                    json.dumps(atlas.to_dict()),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        # A disk-full or permission error on the cache shouldn't fail the tool.
        return


def _normalize_admin_base(base: str | None) -> str:
    """`host`, `host:port`, `http://host`, full URL → `scheme://host:port`."""
    s = (base or DEFAULT_BASE_URL).strip()
    if "://" not in s:
        s = f"http://{s}"
    parsed = urlparse(s)
    host = parsed.hostname or ""
    port = parsed.port or 3001
    return f"{parsed.scheme or 'http'}://{host}:{port}"


async def _stream_csv_rows(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
) -> AsyncIterator[list[str]]:
    """Stream a CSV endpoint line-by-line without buffering the whole body.

    Yields row lists (including the header). Raises httpx.HTTPStatusError on
    non-2xx.
    """
    async with client.stream("GET", url, headers=headers) as resp:
        resp.raise_for_status()

        async def _line_iter() -> AsyncIterator[str]:
            async for line in resp.aiter_lines():
                yield line

        # csv.reader wants a sync iterable; pull lines into a bounded buffer
        # one at a time. This stays streaming — we only hold `MAX_ROWS` cap.
        pending: list[str] = []
        async for line in _line_iter():
            pending.append(line)
            # Flush every 4 KB worth of rows to the csv parser so we don't
            # build up more than a few-hundred-line buffer.
            if len(pending) >= 256:
                for row in csv.reader(pending):
                    yield row
                pending.clear()
        if pending:
            for row in csv.reader(pending):
                yield row


def aggregate_rows(
    action_name: str,
    rows: Iterable[list[str]],
    atlas: CraftingAtlas,
    max_rows: int = MAX_ROWS_PER_ACTION,
) -> int:
    """Fold one action's CSV rows into the running atlas.

    Returns the number of data rows consumed (excluding header).

    Action shapes (columns we care about, see cycle-13 live capture):
      ItemCraftedAction: WorldObjectItem, Citizen, ItemUsed, Count
      HarvestOrHunt:     Species, Citizen, Count   (Species is the item)
      ChopTree:          Species, Citizen, Count   (Species is the tree)
      DigOrMine:         BlockItemOnDestroy, Citizen, Count (or ItemUsed)

    The exporter occasionally gives us slightly different column orders per
    action type, so we key off the header row instead of fixed positions.

    Count semantics differ by action class, so we fold them differently
    (eco-app#70): a gather's Count is a biomass magnitude, so it never gets
    summed — each gather row contributes one *event* to `by_gathered`. A
    craft's Count is 1 on a per-event row and the merged-event total on an
    hourly rollup row (eco-app#131). A rollup's item/station labels come from
    the FIRST merged event — one genuine iteration of that item — so every
    craft row contributes exactly 1 to the item-attributed boards
    (`by_crafted`, `by_station`, `flows`), making them confirmed floors, and
    the remaining count-1 unattributable iterations accumulate into
    `rollup_events` / `rollup_iterations`. The citizen leaderboard weighs by
    the full Count: Citizen is the server aggregator's grouping key, so a
    rollup's Count is that citizen's true iteration total for the hour.
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

    # Accumulators live on the atlas's per-pass dicts; we store them on the
    # call-side via closure-like bags so each call can fold into shared totals.
    by_crafted: dict[str, float] = dict(atlas.by_crafted)
    by_gathered: dict[str, int] = dict(atlas.by_gathered)
    by_station: dict[str, int] = dict(atlas.by_station)
    # Both citizen boards hold raw numeric ids here; fetch_atlas resolves them
    # to names once, after every action has folded. See eco-app#5.
    by_citizen: dict[str, int] = dict(atlas.by_citizen)
    by_citizen_iterations: dict[str, int] = dict(atlas.by_citizen_iterations)
    by_miner: dict[str, int] = dict(atlas.by_miner)
    flows: dict[tuple[str, str], float] = {(s, t): c for s, t, c in atlas.flows}

    # Only crafts contribute a meaningful unit Count; everything else is
    # event-counted so biomass magnitude can't swamp the boards (eco-app#70).
    is_crafted = action_name == CRAFTED_ACTION_TYPE

    consumed = 0
    for row in it:
        if not row:
            continue
        if consumed >= max_rows:
            atlas.warnings.append(
                f"{action_name}: truncated at {max_rows} rows (late-cycle size cap)"
            )
            break
        # Absorb any undeclared extra column so header-keyed picks stay aligned.
        idx = _corrected_index(header, row)
        try:
            count = float(pick(row, idx, "Count") or "0")
        except ValueError:
            count = 0.0
        # A craft row with Count > 1 is a server-side hourly rollup: its item /
        # station / location are one merged event's values, not an attribution.
        # Only its citizen + Count survive as signal. See eco-app#131.
        is_rollup = is_crafted and count > 1.0
        # Item: for crafts the output is ItemUsed; for harvests/chops the
        # Species IS the produced stack; for mining the block destroyed.
        item = (
            pick(
                row,
                idx,
                "ItemUsed",
                "Species",
                "BlockItemOnDestroy",
                "BlockDestroyed",
            )
            or ""
        )
        # Station: only crafts have a distinct WorldObjectItem; harvests are
        # hand/tool-driven, record the tool when we have it.
        station = pick(row, idx, "WorldObjectItem", "ToolUsed") or "(hand)"
        # Citizen: a numeric user id (resolved to a name later). Anything not a
        # bare integer is a residual misalignment artifact - skip it.
        citizen = pick(row, idx, "Citizen")

        # Rows that still put positions/numbers where names belong - drop those
        # keys rather than render them. See eco-app#5.
        if item and _NONSENSE_KEY_RE.match(item):
            item = ""
        if station and _NONSENSE_KEY_RE.match(station):
            station = ""

        if is_rollup:
            # The aggregator keeps the FIRST merged event as the row's labels,
            # so exactly one iteration of the label item/station is proven; the
            # remaining count-1 iterations have no item detail (eco-app#131).
            atlas.rollup_events += 1
            atlas.rollup_iterations += count - 1.0
        if item:
            if is_crafted:
                by_crafted[item] = by_crafted.get(item, 0.0) + 1.0
            else:
                by_gathered[item] = by_gathered.get(item, 0) + 1
        if station:
            by_station[station] = by_station.get(station, 0) + 1
        if station and item:
            flows[(station, item)] = flows.get((station, item), 0.0) + 1.0
        if citizen and _INT_RE.match(citizen):
            # One row, one event — the unit `get_world.byCitizen` reports.
            by_citizen[citizen] = by_citizen.get(citizen, 0) + 1
            # The iteration board weighs crafts by Count (valid on rollups
            # too); gathers stay event-weighted so biomass can't dominate
            # (eco-app#70).
            weight = int(max(count, 1.0)) if is_crafted else 1
            by_citizen_iterations[citizen] = by_citizen_iterations.get(citizen, 0) + weight
            if action_name == MINING_ACTION_TYPE:
                by_miner[citizen] = by_miner.get(citizen, 0) + 1
        consumed += 1

    atlas.total_events += consumed
    atlas.per_action_counts[action_name] = atlas.per_action_counts.get(action_name, 0) + consumed
    atlas.by_crafted = sorted(by_crafted.items(), key=lambda kv: kv[1], reverse=True)
    atlas.by_gathered = sorted(by_gathered.items(), key=lambda kv: kv[1], reverse=True)
    atlas.by_station = sorted(by_station.items(), key=lambda kv: kv[1], reverse=True)
    atlas.by_citizen = sorted(by_citizen.items(), key=lambda kv: kv[1], reverse=True)
    atlas.by_citizen_iterations = sorted(
        by_citizen_iterations.items(), key=lambda kv: kv[1], reverse=True
    )
    atlas.by_miner = sorted(by_miner.items(), key=lambda kv: kv[1], reverse=True)
    atlas.flows = sorted(
        ((s, t, c) for (s, t), c in flows.items()),
        key=lambda edge: edge[2],
        reverse=True,
    )
    return consumed


async def _fetch_citizen_names(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    atlas: CraftingAtlas,
) -> dict[str, str]:
    """Fetch the id -> display-name map from the jobs mod's citizens surface.

    The exporter keys Citizen by a numeric in-game user id; only the jobs mod
    (running inside the Eco process, with UserManager access) can join that id
    to a name. Best-effort: a missing / erroring endpoint is a non-fatal
    warning, and callers fall back to showing the bare ids. See eco-app#5.
    """
    return await fetch_citizen_name_map(client, base_url, headers, atlas.warnings)


async def fetch_citizen_name_map(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    warnings: list[str],
) -> dict[str, str]:
    """Fetch the numeric-id -> display-name map from the jobs mod's citizens surface.

    Shared by the crafting atlas and the trades ledger (both key exporter rows
    by numeric in-game ids). Best-effort: a missing / erroring endpoint appends
    a non-fatal warning and returns an empty map so callers fall back to bare
    ids. See eco-app#5.
    """
    url = f"{base_url}/api/v1/citizens"
    try:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        warnings.append(f"citizens: {type(e).__name__}: {e}")
        return {}
    mapping: dict[str, str] = {}
    for entry in payload or []:
        cid = entry.get("id")
        name = entry.get("name")
        if cid is not None and name:
            mapping[str(cid)] = str(name)
    return mapping


def rank_citizen_counts(
    counts: Mapping[str, int], name_map: Mapping[str, str]
) -> list[tuple[str, int]]:
    """Per-citizen-id counts as ranked ``(display name, count)`` pairs.

    Shared by the atlas's miner board and get_world's per-category boards, which
    fold the same exporter rows keyed by numeric Citizen id. An id the join
    misses renders as ``Citizen #<id>`` so the player still ranks, and ids that
    resolve to one name add together. Ties break by name so the order is stable.
    """
    resolved: dict[str, int] = {}
    for cid, count in counts.items():
        label = name_map.get(cid) or f"Citizen #{cid}"
        resolved[label] = resolved.get(label, 0) + count
    return sorted(resolved.items(), key=lambda kv: (-kv[1], kv[0]))


def _apply_citizen_names(atlas: CraftingAtlas, name_map: dict[str, str]) -> None:
    """Rewrite both citizen boards' id keys to display names, in place.

    Unmapped ids render as ``Citizen #<id>`` so a player the join misses still
    shows up ranked rather than vanishing. When nothing resolved we flag it so
    the card reads as "ids, not names" instead of silently wrong.
    """
    if not atlas.by_citizen and not atlas.by_citizen_iterations:
        return

    def relabel(board: list[tuple[str, int]]) -> tuple[list[tuple[str, int]], int]:
        resolved: dict[str, int] = {}
        matched = 0
        for cid, count in board:
            name = name_map.get(cid)
            if name is None:
                label = f"Citizen #{cid}"
            else:
                label = name
                matched += 1
            resolved[label] = resolved.get(label, 0) + count
        return sorted(resolved.items(), key=lambda kv: kv[1], reverse=True), matched

    atlas.by_citizen, matched = relabel(atlas.by_citizen)
    atlas.by_citizen_iterations, _ = relabel(atlas.by_citizen_iterations)
    atlas.by_miner = rank_citizen_counts(dict(atlas.by_miner), name_map)
    if matched == 0 and CITIZEN_NAMES_UNAVAILABLE_WARNING not in atlas.warnings:
        atlas.warnings.append(CITIZEN_NAMES_UNAVAILABLE_WARNING)


async def fetch_atlas(
    base_url: str | None = None,
    api_key: str | None = None,
    cache_ttl_s: float = DEFAULT_CACHE_TTL_S,
    client: httpx.AsyncClient | None = None,
) -> CraftingAtlas:
    """Stream all configured action CSVs and fold them into a single atlas.

    `client` is injectable so tests can hand in a pre-stubbed httpx client
    (respx plays nice with `httpx.AsyncClient()`). When omitted we construct
    one with a 30 s timeout — individual CSV fetches can legitimately take
    a few seconds late-cycle.
    """
    normalized = _normalize_admin_base(base_url)
    cached = _cache_get(normalized, api_key, cache_ttl_s)
    if cached is not None:
        return cached

    atlas = CraftingAtlas(
        fetched_at_iso=_now_iso(),
        source_base_url=normalized,
    )
    headers = {"X-API-Key": api_key} if api_key else {}

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0))
    try:
        for action in CRAFT_ACTION_TYPES:
            url = f"{normalized}/api/v1/exporter/actions?actionName={action}"
            try:
                # Batch-fold every N rows so we never hold more than that many
                # in Python-land at once; pass a shrinking `remaining` budget
                # so the per-action cap is respected across batches.
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
                        consumed = aggregate_rows(action, batch, atlas, max_rows=remaining)
                        remaining -= consumed
                        if remaining <= 0:
                            break
                        batch = [header]
                if header is not None and len(batch) > 1 and remaining > 0:
                    aggregate_rows(action, batch, atlas, max_rows=remaining)
                # Record a zero-count entry on success-but-empty so callers
                # can tell "we fetched it and it was empty" apart from "never
                # fetched / errored" (the latter shows up in `warnings`).
                atlas.per_action_counts.setdefault(action, 0)
            except httpx.HTTPStatusError as e:
                atlas.warnings.append(f"{action}: HTTP {e.response.status_code}")
            except httpx.HTTPError as e:
                atlas.warnings.append(f"{action}: {type(e).__name__}: {e}")

        if atlas.rollup_events:
            atlas.warnings.append(
                f"{int(atlas.rollup_iterations)} older craft iterations have no item "
                f"detail (hourly per-citizen rollups, {atlas.rollup_events} rows) - "
                "item/station numbers are confirmed floors, not totals (eco-app#131)"
            )

        # Join the accumulated numeric Citizen ids to display names once every
        # action has folded. Skipped when no citizen rows survived. See eco-app#5.
        if atlas.by_citizen or atlas.by_citizen_iterations:
            name_map = await _fetch_citizen_names(http, normalized, headers, atlas)
            _apply_citizen_names(atlas, name_map)
    finally:
        if owns_client:
            await http.aclose()

    _cache_put(normalized, api_key, atlas)
    return atlas


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def prettify_eco_name(name: str) -> str:
    """`CampfireItem` → `Campfire`; `BunWulfRawMeatItem` → `Bun Wulf Raw Meat`.

    Heuristic only — Eco item IDs are PascalCase `FooItem` by convention.
    Vanilla + mod items both follow it; we do not rely on a lookup table so
    this survives unknown mods.
    """
    if not name:
        return ""
    stem = name
    for suffix in ("Item", "Species", "Block"):
        if stem.endswith(suffix) and len(stem) > len(suffix):
            stem = stem[: -len(suffix)]
            break
    # Split camel case — insert a space between a lowercase letter or digit
    # and an uppercase letter.
    out = []
    for i, ch in enumerate(stem):
        if i > 0 and ch.isupper() and (stem[i - 1].islower() or stem[i - 1].isdigit()):
            out.append(" ")
        out.append(ch)
    return "".join(out)


def atlas_markdown(atlas: CraftingAtlas) -> str:
    """Summarize a crafting atlas for an MCP text result."""
    if atlas.total_events == 0:
        return f"**Crafting atlas** — no production events recorded yet ({atlas.source_base_url})."
    lines = [
        f"**Crafting atlas** — {atlas.total_events:,} events (`{atlas.source_base_url}`)",
        "",
        "**Top items crafted (units):**",
    ]
    for index, (name, count) in enumerate(atlas.by_crafted[:10], 1):
        lines.append(f"{index}. {prettify_eco_name(name)} — {count:,.0f}")
    if atlas.by_gathered:
        lines.extend(["", "**Top resources gathered (events):**"])
        for index, (name, count) in enumerate(atlas.by_gathered[:10], 1):
            lines.append(f"{index}. {prettify_eco_name(name)} — {count:,} events")
    if atlas.by_station:
        lines.extend(["", "**Station utilization:**"])
        for index, (name, count) in enumerate(atlas.by_station[:10], 1):
            lines.append(f"{index}. {prettify_eco_name(name)} — {count:,} events")
    if atlas.by_miner:
        lines.extend(["", "**Top miners** (dig and mine events):"])
        for index, (name, count) in enumerate(atlas.by_miner[:10], 1):
            lines.append(f"{index}. {name} — {count:,} events")
    if atlas.by_citizen_iterations:
        # Name the unit. This board weighs crafts by iteration count, so its
        # values legitimately exceed totalEvents — unlabelled, that read as a
        # broken event count (eco-app#222).
        lines.extend(["", "**Top crafters** (craft iterations + gather events):"])
        for index, (name, count) in enumerate(atlas.by_citizen_iterations[:10], 1):
            lines.append(f"{index}. {name} — {count:,.0f}")
    lines.extend(f"- ⚠ {warning}" for warning in atlas.warnings)
    return "\n".join(lines)


def atlas_template_context(
    atlas: CraftingAtlas,
    top_items: int = 20,
    top_stations: int = 15,
    top_citizens: int = 10,
    top_flows: int = 30,
) -> dict[str, Any]:
    """Shape for the Jinja2 partial. All SVG is rendered server-side."""
    crafted = atlas.by_crafted[:top_items]
    gathered = atlas.by_gathered[:top_items]
    stations = atlas.by_station[:top_stations]
    # The card's crafter board is the iteration-weighted one; see eco-app#222.
    citizens = atlas.by_citizen_iterations[:top_citizens]
    flows = atlas.flows[:top_flows]

    max_crafted = max((c for _, c in crafted), default=0.0) or 1.0
    max_gathered = max((c for _, c in gathered), default=0) or 1
    max_station = max((c for _, c in stations), default=0) or 1
    max_citizen = max((c for _, c in citizens), default=0) or 1

    sankey = _build_sankey_layout(flows, width=720, height=420) if flows else None

    return {
        "empty": atlas.total_events == 0,
        "fetched_at_iso": atlas.fetched_at_iso,
        "source_base_url": atlas.source_base_url,
        "total_events": atlas.total_events,
        "per_action_counts": [
            (name, count) for name, count in atlas.per_action_counts.items() if count
        ],
        "top_crafted": [
            {
                "name": name,
                "pretty": prettify_eco_name(name),
                "count": count,
                "pct": (count / max_crafted) * 100.0,
            }
            for name, count in crafted
        ],
        "top_gathered": [
            {
                "name": name,
                "pretty": prettify_eco_name(name),
                "count": count,
                "pct": (count / max_gathered) * 100.0,
            }
            for name, count in gathered
        ],
        "top_stations": [
            {
                "name": name,
                "pretty": prettify_eco_name(name),
                "count": count,
                "pct": (count / max_station) * 100.0,
            }
            for name, count in stations
        ],
        "top_citizens": [
            {
                "name": name,
                "count": count,
                "pct": (count / max_citizen) * 100.0,
            }
            for name, count in citizens
        ],
        "sankey": sankey,
        "warnings": list(atlas.warnings),
    }


def _build_sankey_layout(
    flows: list[tuple[str, str, float]],
    width: int,
    height: int,
) -> dict[str, Any]:
    """Minimal two-column sankey layout, rendered inline as SVG.

    d3-sankey would be ~20 KB of JS for a visualization we compute once and
    never re-layout. A static SVG satisfies CSP trivially and is easier to
    visually inspect in tests. Edges are drawn in decreasing thickness so the
    "top 5 crossings" acceptance check is easy to satisfy — the layout sorts
    both axes by total flow, which minimizes crossings for this shape.
    """
    if not flows:
        return {}

    # Sum per-source and per-target weights; rank from heaviest down so the
    # thickest edges sit at the top of each column.
    src_weight: dict[str, float] = defaultdict(float)
    tgt_weight: dict[str, float] = defaultdict(float)
    for s, t, c in flows:
        src_weight[s] += c
        tgt_weight[t] += c

    sources = sorted(src_weight.items(), key=lambda kv: kv[1], reverse=True)
    targets = sorted(tgt_weight.items(), key=lambda kv: kv[1], reverse=True)
    total = sum(src_weight.values()) or 1.0

    pad = 6
    node_w = 10
    left_x = 0
    right_x = width - node_w

    # Vertical layout: pad-separated bars whose height is proportional to weight.
    def _lay(col: list[tuple[str, float]]) -> dict[str, tuple[float, float]]:
        avail = height - pad * (len(col) + 1)
        pos: dict[str, tuple[float, float]] = {}
        y: float = float(pad)
        for name, w in col:
            h = max(6.0, (w / total) * avail * (len(col)))
            pos[name] = (y, h)
            y += h + pad
        return pos

    left = _lay(sources)
    right = _lay(targets)

    # Edge paths — cubic bezier from right-edge of source bar to left-edge of
    # target bar. Stroke width proportional to edge count.
    max_edge = max((c for _, _, c in flows), default=1.0) or 1.0
    edges = []
    # Running consumption of each node's bar so parallel edges stack.
    left_off: dict[str, float] = defaultdict(float)
    right_off: dict[str, float] = defaultdict(float)
    for s, t, c in flows:
        if s not in left or t not in right:
            continue
        ly, lh = left[s]
        ry, rh = right[t]
        share = c / (src_weight[s] or 1.0)
        sh = lh * share
        share_r = c / (tgt_weight[t] or 1.0)
        rh2 = rh * share_r
        y1 = ly + left_off[s] + sh / 2
        y2 = ry + right_off[t] + rh2 / 2
        left_off[s] += sh
        right_off[t] += rh2
        x1 = left_x + node_w
        x2 = right_x
        cx = (x1 + x2) / 2
        path = f"M{x1:.1f},{y1:.1f} C{cx:.1f},{y1:.1f} {cx:.1f},{y2:.1f} {x2:.1f},{y2:.1f}"
        edges.append(
            {
                "path": path,
                "width": max(1.0, (c / max_edge) * 14.0),
                "count": c,
                "source": s,
                "target": t,
            }
        )

    return {
        "width": width,
        "height": height,
        "node_w": node_w,
        "left_x": left_x,
        "right_x": right_x,
        "left_nodes": [
            {
                "name": name,
                "pretty": prettify_eco_name(name),
                "y": left[name][0],
                "h": left[name][1],
                "count": src_weight[name],
            }
            for name, _ in sources
        ],
        "right_nodes": [
            {
                "name": name,
                "pretty": prettify_eco_name(name),
                "y": right[name][0],
                "h": right[name][1],
                "count": tgt_weight[name],
            }
            for name, _ in targets
        ],
        "edges": edges,
    }
