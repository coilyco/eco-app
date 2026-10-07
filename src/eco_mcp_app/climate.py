"""Climate / atmosphere visibility — CO2 ppm, sea level, ground pollution.

Players who don't run polluting machines can't see the global atmospheric
state until it bites them (sea-level inundation, eco-collapse). This module
surfaces that data through MCP so any player can query the trend before
they're affected.

Three independent slices, each tolerant to absence — empty data is a valid
state, not an error:

1. **Time-series via ``/datasets/get``** — CO2 ppm, sea-level height, average
   ground pollution. Eco's stat catalog has shifted across versions and mods
   can register their own, so we try a list of candidate dataset names and
   use whichever returns data. Same fan-out + 200/500-tolerant pattern as
   ``server.fetch_economy``.

2. **Polluter attribution via ``/api/v1/exporter/actions``** — same stream-
   parse approach as ``crafting.py``. Tries several action-name candidates
   so the card stays useful regardless of which actions a given Eco version
   emits. Aggregates emitted-pollution count by citizen and station.

3. **Worldlayers ``Summary`` text** — for a headline ground-pollution
   percentage when no ``/datasets`` series exists. Same endpoint that
   ``ecoregion.py`` already hits.

Real-world Earth CO2 anchor: bundled NOAA Mauna Loa annual averages so the
card can say "412 ppm — Earth crossed that in 2019." Public-domain values
trimmed to ~10 inflection points so the bundle stays small.
"""

from __future__ import annotations

import asyncio
import csv
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
from cachetools import TTLCache

# Candidate dataset names. We try each in order and use the first that
# returns at least one point. An empty result simply means the server
# doesn't expose that name — not an error.
#
# Live catalog discovery on Eco 0.13.0.3 (cycle 13) confirmed the
# canonical names listed first; older Eco builds used different forms
# (`CO2PPM`, `AverageCO2PPM`) so both are kept for backward compat.
CO2_DATASET_CANDIDATES: tuple[str, ...] = (
    "TotalCO2",
    "LifetimeCO2FromPollution",
    "LifetimeCO2FromAnimals",
    "LifetimeCO2FromPlants",
    "CO2PPM",
    "AverageCO2PPM",
    "AtmosphereCO2",
    "CO2",
)
SEA_LEVEL_DATASET_CANDIDATES: tuple[str, ...] = (
    "SeaLevel",
    "SeaLevelHeight",
    "OceanLevel",
)
POLLUTION_DATASET_CANDIDATES: tuple[str, ...] = (
    "TotalGroundPollution",
    "GroundPollution",
    "AveragePollution",
    "Pollution",
)
# Average global temperature, in Celsius. Eco 0.13.0.3 exposes
# `AverageGlobalTemperature`; older / modded builds vary.
TEMPERATURE_DATASET_CANDIDATES: tuple[str, ...] = (
    "AverageGlobalTemperature",
    "GlobalTemperature",
    "AverageTemperature",
    "Temperature",
)
# CO2 source/sink attribution series. These are *cumulative lifetime* PPM
# totals — the same three lines the in-game pollution-machine status breaks
# out under "Global CO2 Sources and Sinks". Pollution and animals add CO2
# (positive); plants remove it (negative). The day-over-day delta of each
# series is that source's "ppm per day" contribution.
CO2_FROM_POLLUTION_CANDIDATES: tuple[str, ...] = ("LifetimeCO2FromPollution",)
CO2_FROM_ANIMALS_CANDIDATES: tuple[str, ...] = ("LifetimeCO2FromAnimals",)
CO2_FROM_PLANTS_CANDIDATES: tuple[str, ...] = ("LifetimeCO2FromPlants",)

# Columns the pollution rows might name their actor / source with. Named
# constants so the "we tried these and none matched" warning stays truthful
# without a second list to keep in sync (#226).
_POLLUTER_CITIZEN_KEYS: tuple[str, ...] = ("Citizen", "Player", "User", "Owner")
_POLLUTER_STATION_KEYS: tuple[str, ...] = (
    "WorldObjectItem",
    "Station",
    "Source",
    "Object",
    "ToolUsed",
)

# Action names for polluter attribution. `PolluteAir` is the canonical
# event-stat on Eco 0.13.0.3 ("Air pollution was released (triggered every
# 30 sec of operation)"). Older candidate names are kept for backward
# compatibility with modded / older servers.
POLLUTION_ACTION_TYPES: tuple[str, ...] = (
    "PolluteAir",
    "PollutionAction",
    "EmitPollutionAction",
    "EmitCO2Action",
    "BurnFuelAction",
)

# Real-world Mauna Loa annual mean CO2, ppm. NOAA, public domain, 2024 update:
# https://gml.noaa.gov/ccgg/trends/. Trimmed to inflection points so the
# anchor is meaningful without bloating the module.
EARTH_CO2_HISTORY: tuple[tuple[int, float], ...] = (
    (1750, 277.0),
    (1850, 285.0),
    (1900, 296.0),
    (1958, 315.0),
    (1980, 339.0),
    (1990, 354.0),
    (2000, 369.0),
    (2010, 389.0),
    (2015, 401.0),
    (2020, 414.0),
    (2024, 422.0),
)

# Pre-industrial baseline for the warming-status heuristic. Anything above
# 350 ppm is "warming"; >500 ppm tips into "critical" because Eco's default
# Sea Level Rise mod starts triggering above that threshold.
_PREINDUSTRIAL_PPM = 280.0
_WARMING_PPM = 350.0
_CRITICAL_PPM = 500.0

# Eco's *default* climate ruleset, from the game source
# (Server/Eco.Simulation/Settings/EcoDef.cs, ClimateSettings). These drive
# the in-game pollution-machine "CO2 Effects" lines:
#   "Temperature rises 1 degree for every {ppm_per_degree}ppm past {threshold}ppm"
#   "Sea level rises 1 meter every {ppm_per_meter}ppm past {threshold}ppm"
# Eco lets a server admin retune them. The telemetry mod now exposes the live
# values at /api/v1/climate-settings (eco-app#8), and ``_resolve_rules`` prefers
# those per-field — these constants are the fallback when that endpoint is
# absent (un-modded / older server) or omits a field. The env overrides remain
# as a second fallback so a differently-tuned un-modded deploy can still correct
# the explanation without a code change.
_RULE_MIN_CO2_PPM = float(os.environ.get("ECO_CLIMATE_MIN_CO2_PPM", "325"))
_RULE_TEMP_THRESHOLD_PPM = float(os.environ.get("ECO_CLIMATE_TEMP_THRESHOLD_PPM", "400"))
_RULE_PPM_PER_DEGREE = float(os.environ.get("ECO_CLIMATE_PPM_PER_DEGREE", "25"))
_RULE_SEA_THRESHOLD_PPM = float(os.environ.get("ECO_CLIMATE_SEA_THRESHOLD_PPM", "400"))
_RULE_PPM_PER_METER = float(os.environ.get("ECO_CLIMATE_PPM_PER_METER", "25"))

# Per-process cache. Climate moves slowly (one tick every game hour) so a 60s
# TTL is plenty and prevents the iframe-on-refresh case from hammering the
# admin endpoint.
_CACHE_TTL_S = float(os.environ.get("ECO_CLIMATE_CACHE_TTL", "60"))
_climate_cache: TTLCache[str, ClimateSnapshot] = TTLCache(maxsize=64, ttl=_CACHE_TTL_S)

# Action exporter row cap — same defensive bound as crafting.py uses.
_MAX_ROWS_PER_ACTION = int(os.environ.get("ECO_CLIMATE_MAX_ROWS", "200000"))

# Dataset ``Time`` values use the exporter/series clock: 86,400 source seconds
# per game day. This is deliberately distinct from /info's ``TimeSinceStart``
# clock. Source observations stay on their own plane and are compared with the
# semantic current game day, never relabeled with the backend fetch timestamp.
_SERIES_SECONDS_PER_GAME_DAY = 86400.0


@dataclass(frozen=True)
class DatasetSeries:
    """One normalized Eco dataset response plus its source metadata."""

    points: list[tuple[float, float]] = field(default_factory=list)
    unit: str | None = None
    interval_seconds: float | None = None


@dataclass
class ClimateSnapshot:
    """Raw fetch result. JSON-serializable via :meth:`to_dict`."""

    fetched_at_iso: str
    source_base_url: str
    info: dict[str, Any]
    days_elapsed: int
    admin_ok: bool
    co2_series: list[tuple[float, float]] = field(default_factory=list)
    sea_level_series: list[tuple[float, float]] = field(default_factory=list)
    pollution_series: list[tuple[float, float]] = field(default_factory=list)
    temperature_series: list[tuple[float, float]] = field(default_factory=list)
    co2_dataset_name: str | None = None
    sea_level_dataset_name: str | None = None
    pollution_dataset_name: str | None = None
    temperature_dataset_name: str | None = None
    co2_unit: str | None = None
    sea_level_unit: str | None = None
    pollution_unit: str | None = None
    temperature_unit: str | None = None
    co2_interval_seconds: float | None = None
    sea_level_interval_seconds: float | None = None
    pollution_interval_seconds: float | None = None
    temperature_interval_seconds: float | None = None
    dataset_metadata: dict[str, dict[str, str | float | None]] = field(default_factory=dict)
    # CO2 source/sink breakdown — cumulative lifetime PPM per origin. Mirrors
    # the in-game "Global CO2 Sources and Sinks" status block.
    co2_pollution_series: list[tuple[float, float]] = field(default_factory=list)
    co2_animals_series: list[tuple[float, float]] = field(default_factory=list)
    co2_plants_series: list[tuple[float, float]] = field(default_factory=list)
    # Worldlayers fallback for ground-pollution headline (e.g. "4%").
    pollution_layer_summary: str | None = None
    # Polluter attribution. Values are summed *emissions*, not event counts —
    # a fractional value under a `count` key read as a broken counter (#226).
    top_polluter_citizens: list[tuple[str, float]] = field(default_factory=list)
    top_polluter_stations: list[tuple[str, float]] = field(default_factory=list)
    # Rows whose actor / station column was absent, so nothing could be
    # attributed. Reported rather than bucketed under a fake "(unknown)" name.
    unattributed_polluter_rows: int = 0
    unattributed_station_rows: int = 0
    pollution_actions_total: int = 0
    pollution_action_types_seen: list[str] = field(default_factory=list)
    # Climate-related dataset names exposed by /datasets/flatlist on this
    # server. Surfaced on the card so an empty-data path is debuggable
    # without the user having to grep server logs.
    available_climate_datasets: list[str] = field(default_factory=list)
    # Live per-server climate ruleset from the telemetry mod's
    # /api/v1/climate-settings endpoint (EcoDef.Obj.ClimateSettings). ``None``
    # when the endpoint is absent (un-modded / older server), in which case the
    # effects block falls back to the documented Eco defaults (``_RULE_*``).
    climate_settings: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fetchedAtISO": self.fetched_at_iso,
            "sourceBaseUrl": self.source_base_url,
            "daysElapsed": self.days_elapsed,
            "adminOk": self.admin_ok,
            "co2Series": [[t, v] for t, v in self.co2_series],
            "seaLevelSeries": [[t, v] for t, v in self.sea_level_series],
            "pollutionSeries": [[t, v] for t, v in self.pollution_series],
            "temperatureSeries": [[t, v] for t, v in self.temperature_series],
            "co2DatasetName": self.co2_dataset_name,
            "seaLevelDatasetName": self.sea_level_dataset_name,
            "pollutionDatasetName": self.pollution_dataset_name,
            "temperatureDatasetName": self.temperature_dataset_name,
            "co2Unit": self.co2_unit,
            "seaLevelUnit": self.sea_level_unit,
            "pollutionUnit": self.pollution_unit,
            "temperatureUnit": self.temperature_unit,
            "co2IntervalSeconds": self.co2_interval_seconds,
            "seaLevelIntervalSeconds": self.sea_level_interval_seconds,
            "pollutionIntervalSeconds": self.pollution_interval_seconds,
            "temperatureIntervalSeconds": self.temperature_interval_seconds,
            "datasetMetadata": {name: dict(meta) for name, meta in self.dataset_metadata.items()},
            "co2PollutionSeries": [[t, v] for t, v in self.co2_pollution_series],
            "co2AnimalsSeries": [[t, v] for t, v in self.co2_animals_series],
            "co2PlantsSeries": [[t, v] for t, v in self.co2_plants_series],
            "pollutionLayerSummary": self.pollution_layer_summary,
            "topPolluterCitizens": [[n, c] for n, c in self.top_polluter_citizens],
            "topPolluterStations": [[n, c] for n, c in self.top_polluter_stations],
            "pollutionActionsTotal": self.pollution_actions_total,
            "pollutionActionTypesSeen": list(self.pollution_action_types_seen),
            "availableClimateDatasets": list(self.available_climate_datasets),
            "climateSettings": self.climate_settings,
            "warnings": list(self.warnings),
        }


def _admin_base(server: str | None, default_base: str) -> str:
    """Resolve a user-supplied server hint to an admin base URL.

    Same accepted shapes as the rest of the toolset: bare host, host:port,
    full URL, or ``None`` to use the configured default.
    """
    if not server:
        return default_base.rstrip("/")
    s = server.strip()
    if not s:
        return default_base.rstrip("/")
    if "://" not in s:
        s = f"http://{s}"
    parsed = urlparse(s)
    host = parsed.hostname or ""
    port = parsed.port or 3001
    return f"{parsed.scheme or 'http'}://{host}:{port}".rstrip("/")


async def _fetch_dataset(
    client: httpx.AsyncClient,
    base: str,
    name: str,
    day_end: int,
    headers: dict[str, str],
) -> DatasetSeries:
    """GET ``/datasets/get`` for one series and retain its source metadata.

    Mirrors ``server._fetch_dataset`` — duplicated rather than imported to
    keep this module free of a server.py dependency (server.py imports here
    via the call_tool path).
    """
    try:
        r = await client.get(
            f"{base}/datasets/get",
            params={"dataset": name, "dayStart": 0, "dayEnd": max(day_end, 1)},
            headers=headers,
        )
        if r.status_code != 200:
            return DatasetSeries()
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return DatasetSeries()
    out: list[tuple[float, float]] = []
    unit: str | None = None
    interval_seconds: float | None = None
    if isinstance(data, list):
        for pt in data:
            parsed = _parse_dataset_point(pt)
            if parsed is not None:
                out.append(parsed)
    elif isinstance(data, dict):
        out.extend(_parse_parallel_arrays(data))
        raw_unit = data.get("Unit", data.get("unit"))
        if raw_unit is not None and str(raw_unit).strip():
            unit = str(raw_unit).strip()
        raw_interval = data.get("Interval", data.get("interval"))
        if isinstance(raw_interval, int | float) and not isinstance(raw_interval, bool):
            parsed_interval = float(raw_interval)
            if parsed_interval > 0:
                interval_seconds = parsed_interval
    return DatasetSeries(points=out, unit=unit, interval_seconds=interval_seconds)


def _parse_parallel_arrays(data: dict[str, Any]) -> list[tuple[float, float]]:
    """Zip an Eco 0.13 parallel-array dataset payload to ``[(t, v), ...]``.

    Live ContinuousValue stats on Eco 0.13.0.3 return:
        {"Times": [...], "Values": [...], "Interval": N, "Unit": "PPM"}
    instead of a list of point dicts. Both keys are case-tolerant.
    """
    times = data.get("Times") or data.get("times")
    values = data.get("Values") or data.get("values")
    if not isinstance(times, list) or not isinstance(values, list):
        return []
    pairs: list[tuple[float, float]] = []
    for t_raw, v_raw in zip(times, values, strict=False):
        try:
            pairs.append((float(t_raw), float(v_raw)))
        except (TypeError, ValueError):
            continue
    return pairs


# Time-key candidates ordered by how common each form is in the wild:
# - ``Time`` is the event-stat / older economy-stat convention.
# - ``_id`` is the ContinuousValue convention on Eco 0.13.0.3 (per the
#   stat catalog's ``TimeKey`` metadata).
_DATASET_TIME_KEYS: tuple[str, ...] = ("Time", "time", "_id", "Id", "T")


def _parse_dataset_point(pt: Any) -> tuple[float, float] | None:
    """Coerce one ``/datasets/get`` point to ``(time, value)``.

    Tolerates several shapes seen across Eco versions:

    - ``{"Time": t, "Value": v}`` — legacy economy stats, event stats.
    - ``{"_id": t, "Value": v}`` — ContinuousValue stats on 0.13.0.3.
    - ``{"_id": t, "<ShortName>": v}`` — same, but the value lives under
      the stat's short-name key (e.g. ``"tl"`` for ``TotalCO2``). We
      identify it as "the only numeric field that isn't the time key".
    - ``[t, v]`` — flat tuple pair, occasionally returned by mod stats.

    Returns ``None`` if neither time nor value can be recovered — the
    caller drops the point.
    """
    if isinstance(pt, list | tuple) and len(pt) >= 2:
        try:
            return float(pt[0]), float(pt[1])
        except (TypeError, ValueError):
            return None
    if not isinstance(pt, dict):
        return None
    time_key: str | None = None
    t_raw: Any = None
    for k in _DATASET_TIME_KEYS:
        if k in pt:
            time_key = k
            t_raw = pt[k]
            break
    if time_key is None:
        return None
    v_raw: Any = pt.get("Value", pt.get("value"))
    if v_raw is None:
        # Fall back to "the lone numeric field that isn't the time key" —
        # handles ContinuousValue points where the value lives under the
        # stat's ShortName (e.g. {"_id": 123, "tl": 414.2}).
        for k, val in pt.items():
            if k == time_key:
                continue
            if isinstance(val, int | float) and not isinstance(val, bool):
                v_raw = val
                break
    if v_raw is None:
        return None
    try:
        return float(t_raw), float(v_raw)
    except (TypeError, ValueError):
        return None


async def _fetch_first_nonempty(
    client: httpx.AsyncClient,
    base: str,
    candidates: tuple[str, ...],
    day_end: int,
    headers: dict[str, str],
    *,
    flatlist: list[str] | None = None,
    discovery_keywords: tuple[str, ...] = (),
) -> tuple[str | None, DatasetSeries]:
    """Try each candidate dataset name; return the first that has data.

    Probes are issued sequentially because we want to *stop* on the first hit
    rather than fan out. The catalog is small (3-4 names) and most servers
    will hit on the first probe, so the sequential cost is minimal.

    Fallback: if no explicit candidate matches and ``flatlist`` is given,
    scan it for any name containing one of the ``discovery_keywords`` and
    try that. Stat names drift across Eco versions (and mods can register
    their own), so the catalog-driven fallback is what keeps the card
    populated when our hard-coded candidates miss.
    """
    for name in candidates:
        series = await _fetch_dataset(client, base, name, day_end, headers)
        if series.points:
            return name, series
    if flatlist and discovery_keywords:
        seen = {c.lower() for c in candidates}
        for name in flatlist:
            lower = str(name).lower()
            if lower in seen:
                continue
            if any(kw in lower for kw in discovery_keywords):
                series = await _fetch_dataset(client, base, name, day_end, headers)
                if series.points:
                    return name, series
    return None, DatasetSeries()


async def _fetch_dataset_flatlist(
    client: httpx.AsyncClient, base: str, headers: dict[str, str]
) -> list[str]:
    """GET ``/datasets/flatlist`` — the catalog of all dataset stat names.

    Eco's stat catalog can shift across versions (and mods register their
    own names), so the explicit candidate lists in this module can miss.
    The flatlist is the source of truth — we use it to discover climate
    datasets when our hard-coded candidates don't match.

    Response shape varies by Eco version:
    - 0.13.0.3 returns ``list[dict]`` where each dict has rich metadata
      (``Name``, ``DisplayName``, ``Unit``, ``Tags``, ``StatType``, ...).
      We extract ``Name`` — that's what ``/datasets/get?dataset=...`` uses.
    - Older / modded builds may return ``list[str]`` directly.
    Both shapes are normalized to a plain list of canonical names.

    Returns ``[]`` on any non-200, missing endpoint, or unexpected shape.
    """
    try:
        r = await client.get(f"{base}/datasets/flatlist", headers=headers)
        if r.status_code != 200:
            return []
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return []
    # Some Eco builds wrap the list under a key.
    if isinstance(data, dict):
        for key in ("datasets", "Datasets", "values", "Values"):
            v = data.get(key)
            if isinstance(v, list):
                data = v
                break
        else:
            return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for entry in data:
        if isinstance(entry, str):
            if entry:
                out.append(entry)
        elif isinstance(entry, dict):
            name = entry.get("Name") or entry.get("name")
            if name:
                out.append(str(name))
    return out


# Substring keywords for the flatlist-based fallback. Lowercase, matched
# against the lowercased dataset name. Kept narrow so we don't accidentally
# pick up unrelated stats (e.g. "co2" alone instead of "carbon" because
# "carbonate" would also match the latter).
CO2_DISCOVERY_KEYWORDS: tuple[str, ...] = ("co2", "ppm", "carbondioxide")
SEA_LEVEL_DISCOVERY_KEYWORDS: tuple[str, ...] = ("sealevel", "sea level", "ocean level")
POLLUTION_DISCOVERY_KEYWORDS: tuple[str, ...] = (
    "groundpollution",
    "airpollution",
    "pollution",
)


async def _fetch_climate_settings(
    client: httpx.AsyncClient, base: str, headers: dict[str, str]
) -> dict[str, Any] | None:
    """GET the telemetry mod's ``/api/v1/climate-settings`` endpoint.

    Returns the live per-server climate ruleset (``EcoDef.Obj.ClimateSettings``
    serialized by ``mods/telemetry``) as a dict of camelCase keys, or ``None``
    when the endpoint is absent (un-modded / older server) or returns anything
    unexpected. Absence is a valid state — the effects block falls back to the
    documented Eco defaults for whatever the server doesn't emit.

    Admin-gated like the rest of the ``/api/v1`` surface, so it rides the same
    ``X-API-Key`` header the dataset probes use.
    """
    try:
        r = await client.get(f"{base}/api/v1/climate-settings", headers=headers)
        if r.status_code != 200:
            return None
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


async def _fetch_pollution_layer_summary(client: httpx.AsyncClient, base: str) -> str | None:
    """Pull the ``Pollution`` layer's ``Summary`` (e.g. ``"4%"``) from worldlayers.

    The worldlayers endpoint is public — no admin token needed — which is
    why we use it as the fallback when the admin /datasets path is locked
    down. Returns ``None`` on any structural surprise.
    """
    try:
        r = await client.get(f"{base}/api/v1/worldlayers/layers")
        if r.status_code != 200:
            return None
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    for cat in data:
        if not isinstance(cat, dict):
            continue
        # Layer name varies: "Pollution", "GroundPollution", or a category
        # whose `List` contains a layer with one of those names.
        if cat.get("Category") in ("Pollution", "Atmosphere"):
            for entry in cat.get("List") or []:
                summary = entry.get("Summary")
                if summary:
                    return str(summary)
        for entry in cat.get("List") or []:
            name = (entry.get("LayerName") or "").lower()
            if "pollut" in name or "co2" in name:
                summary = entry.get("Summary")
                if summary:
                    return str(summary)
    return None


async def _stream_csv_rows(
    client: httpx.AsyncClient, url: str, headers: dict[str, str]
) -> AsyncIterator[list[str]]:
    """Yield CSV rows from a streaming response. Header row is yielded too."""
    async with client.stream("GET", url, headers=headers) as resp:
        resp.raise_for_status()
        pending: list[str] = []
        async for line in resp.aiter_lines():
            pending.append(line)
            if len(pending) >= 256:
                for row in csv.reader(pending):
                    yield row
                pending.clear()
        if pending:
            for row in csv.reader(pending):
                yield row


async def _aggregate_pollution_action(
    client: httpx.AsyncClient,
    base: str,
    action_name: str,
    headers: dict[str, str],
    by_citizen: dict[str, float],
    by_station: dict[str, float],
    unattributed: dict[str, int],
) -> tuple[int, str | None]:
    """Stream one action CSV and fold pollution emissions into the totals.

    Returns ``(rows_consumed, warning_or_none)``. The warning string is set
    on a non-2xx so the caller can surface it on the rendered card without
    treating it as fatal.
    """
    url = f"{base}/api/v1/exporter/actions?actionName={action_name}"
    rows_consumed = 0
    try:
        header: list[str] | None = None
        col: dict[str, int] = {}
        async for row in _stream_csv_rows(client, url, headers):
            if header is None:
                header = row
                col = {name: i for i, name in enumerate(header)}
                continue
            if rows_consumed >= _MAX_ROWS_PER_ACTION:
                break
            count_s = _pick(row, col, "Count", "Amount", "Value")
            try:
                count = float(count_s) if count_s is not None else 1.0
            except ValueError:
                count = 1.0
            citizen = _pick(row, col, *_POLLUTER_CITIZEN_KEYS) or ""
            station = _pick(row, col, *_POLLUTER_STATION_KEYS) or ""
            if citizen:
                by_citizen[citizen] = by_citizen.get(citizen, 0.0) + count
            else:
                unattributed["citizen"] += 1
            if station:
                by_station[station] = by_station.get(station, 0.0) + count
            else:
                # Bucketing these under "(unknown)" produced a single row that
                # looked like a station and carried the whole server's
                # emissions (#226). Count them; do not name them.
                unattributed["station"] += 1
            rows_consumed += 1
        return rows_consumed, None
    except httpx.HTTPStatusError as e:
        return 0, f"{action_name}: HTTP {e.response.status_code}"
    except httpx.HTTPError as e:
        return 0, f"{action_name}: {type(e).__name__}"


def _pick(row: list[str], col: dict[str, int], *keys: str) -> str | None:
    """Read the first non-empty value among ``keys`` from a CSV row.

    The exporter's column order shifts between Eco versions, so we key off
    the header instead of fixed positions — same approach as ``crafting``.
    """
    for k in keys:
        if k in col and col[k] < len(row):
            v = row[col[k]].strip()
            if v:
                return v
    return None


async def fetch_climate(
    server: str | None,
    *,
    info: dict[str, Any],
    days_elapsed: int,
    admin_token: str | None,
    default_admin_base: str,
) -> ClimateSnapshot:
    """Fetch all climate slices in parallel where possible.

    The caller owns ``info`` (already retrieved via ``fetch_eco_info``) — we
    don't fetch /info ourselves because most call sites already have it. This
    keeps the per-call request count down and matches how
    ``ecoregion.gather_ecoregion_payload`` is shaped.
    """
    base = _admin_base(server, default_admin_base)

    cache_key = base
    cached = _climate_cache.get(cache_key)
    if cached is not None:
        return cached

    snapshot = ClimateSnapshot(
        fetched_at_iso=_now_iso(),
        source_base_url=base,
        info=dict(info or {}),
        days_elapsed=max(1, int(days_elapsed or 1)),
        admin_ok=bool(admin_token),
    )

    headers = {"X-API-Key": admin_token} if admin_token else {}
    async with httpx.AsyncClient(timeout=10.0) as client:
        # Public worldlayers fetch happens regardless of the admin token —
        # this is the fallback that gives non-admins *something* to look at.
        layer_task = asyncio.create_task(_fetch_pollution_layer_summary(client, base))

        if admin_token:
            # Pull the dataset catalog up front so the per-series probes can
            # fall back to keyword discovery when our hard-coded candidates
            # miss (Eco renames stats across versions; mods register their
            # own).
            flatlist = await _fetch_dataset_flatlist(client, base, headers)

            co2_task = asyncio.create_task(
                _fetch_first_nonempty(
                    client,
                    base,
                    CO2_DATASET_CANDIDATES,
                    snapshot.days_elapsed,
                    headers,
                    flatlist=flatlist,
                    discovery_keywords=CO2_DISCOVERY_KEYWORDS,
                )
            )
            sea_task = asyncio.create_task(
                _fetch_first_nonempty(
                    client,
                    base,
                    SEA_LEVEL_DATASET_CANDIDATES,
                    snapshot.days_elapsed,
                    headers,
                    flatlist=flatlist,
                    discovery_keywords=SEA_LEVEL_DISCOVERY_KEYWORDS,
                )
            )
            poll_task = asyncio.create_task(
                _fetch_first_nonempty(
                    client,
                    base,
                    POLLUTION_DATASET_CANDIDATES,
                    snapshot.days_elapsed,
                    headers,
                    flatlist=flatlist,
                    discovery_keywords=POLLUTION_DISCOVERY_KEYWORDS,
                )
            )

            # Temperature (Celsius) and the three CO2 source/sink series.
            # These power the verbose "sources & sinks" + "effects" sections
            # that mirror the in-game pollution-machine status. Each is
            # absence-tolerant: a server without the stat just leaves the
            # section out.
            temp_task = asyncio.create_task(
                _fetch_first_nonempty(
                    client,
                    base,
                    TEMPERATURE_DATASET_CANDIDATES,
                    snapshot.days_elapsed,
                    headers,
                    flatlist=flatlist,
                    discovery_keywords=("temperature",),
                )
            )
            # Live per-server climate ruleset (thresholds/rates). Absent on
            # un-modded servers — the effects block falls back to Eco defaults.
            settings_task = asyncio.create_task(_fetch_climate_settings(client, base, headers))
            poll_src_task = asyncio.create_task(
                _fetch_first_nonempty(
                    client, base, CO2_FROM_POLLUTION_CANDIDATES, snapshot.days_elapsed, headers
                )
            )
            animal_src_task = asyncio.create_task(
                _fetch_first_nonempty(
                    client, base, CO2_FROM_ANIMALS_CANDIDATES, snapshot.days_elapsed, headers
                )
            )
            plant_src_task = asyncio.create_task(
                _fetch_first_nonempty(
                    client, base, CO2_FROM_PLANTS_CANDIDATES, snapshot.days_elapsed, headers
                )
            )

            (snapshot.co2_dataset_name, co2_result) = await co2_task
            snapshot.co2_series = co2_result.points
            snapshot.co2_unit = co2_result.unit
            snapshot.co2_interval_seconds = co2_result.interval_seconds

            (snapshot.sea_level_dataset_name, sea_result) = await sea_task
            snapshot.sea_level_series = sea_result.points
            snapshot.sea_level_unit = sea_result.unit
            snapshot.sea_level_interval_seconds = sea_result.interval_seconds

            (snapshot.pollution_dataset_name, pollution_result) = await poll_task
            snapshot.pollution_series = pollution_result.points
            snapshot.pollution_unit = pollution_result.unit
            snapshot.pollution_interval_seconds = pollution_result.interval_seconds

            (snapshot.temperature_dataset_name, temperature_result) = await temp_task
            snapshot.temperature_series = temperature_result.points
            snapshot.temperature_unit = temperature_result.unit
            snapshot.temperature_interval_seconds = temperature_result.interval_seconds
            snapshot.climate_settings = await settings_task
            (pollution_source_name, pollution_source_result) = await poll_src_task
            snapshot.co2_pollution_series = pollution_source_result.points
            (animal_source_name, animal_source_result) = await animal_src_task
            snapshot.co2_animals_series = animal_source_result.points
            (plant_source_name, plant_source_result) = await plant_src_task
            snapshot.co2_plants_series = plant_source_result.points

            for dataset_name, result in (
                (snapshot.co2_dataset_name, co2_result),
                (snapshot.sea_level_dataset_name, sea_result),
                (snapshot.pollution_dataset_name, pollution_result),
                (snapshot.temperature_dataset_name, temperature_result),
                (pollution_source_name, pollution_source_result),
                (animal_source_name, animal_source_result),
                (plant_source_name, plant_source_result),
            ):
                if dataset_name is not None:
                    snapshot.dataset_metadata[dataset_name] = {
                        "unit": result.unit,
                        "interval_seconds": result.interval_seconds,
                    }

            # Surface every climate-related dataset name the catalog
            # exposes so the empty-state card can hint at why we found
            # nothing, and so future debugging doesn't need server logs.
            climate_kws = (
                CO2_DISCOVERY_KEYWORDS + SEA_LEVEL_DISCOVERY_KEYWORDS + POLLUTION_DISCOVERY_KEYWORDS
            )
            snapshot.available_climate_datasets = sorted(
                {name for name in flatlist if any(kw in name.lower() for kw in climate_kws)}
            )

            # Polluter attribution. Sequential per-action because the exporter
            # CSVs can be many MB late-cycle; we stream-parse each one in turn
            # to avoid spiking memory by N parallel streams.
            by_citizen: dict[str, float] = {}
            by_station: dict[str, float] = {}
            unattributed: dict[str, int] = {"citizen": 0, "station": 0}
            actions_seen: list[str] = []
            for action in POLLUTION_ACTION_TYPES:
                rows, warn = await _aggregate_pollution_action(
                    client, base, action, headers, by_citizen, by_station, unattributed
                )
                if rows:
                    actions_seen.append(action)
                    snapshot.pollution_actions_total += rows
                if warn and "404" not in warn and "405" not in warn:
                    # 404/405 just means "this server doesn't emit that action
                    # type" — silent. Other failures are worth surfacing.
                    snapshot.warnings.append(warn)
            snapshot.pollution_action_types_seen = actions_seen
            snapshot.top_polluter_citizens = sorted(
                by_citizen.items(), key=lambda kv: kv[1], reverse=True
            )[:10]
            snapshot.top_polluter_stations = sorted(
                by_station.items(), key=lambda kv: kv[1], reverse=True
            )[:10]
            snapshot.unattributed_polluter_rows = unattributed["citizen"]
            snapshot.unattributed_station_rows = unattributed["station"]
            # Say what was tried and why it gave up, the way get_social does
            # for its unrecognised giver column (#226).
            if unattributed["citizen"] and not by_citizen:
                snapshot.warnings.append(
                    f"{unattributed['citizen']} pollution row(s) carry no recognised actor "
                    f"column (tried {', '.join(_POLLUTER_CITIZEN_KEYS)}), so no polluter can "
                    "be attributed."
                )
            if unattributed["station"] and not by_station:
                snapshot.warnings.append(
                    f"{unattributed['station']} pollution row(s) carry no recognised station "
                    f"column (tried {', '.join(_POLLUTER_STATION_KEYS)}), so no source can be "
                    "attributed."
                )

        snapshot.pollution_layer_summary = await layer_task

    _climate_cache[cache_key] = snapshot
    return snapshot


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Payload computation — server.py renders the card from this shape.
# ---------------------------------------------------------------------------


def _series_last(pts: list[tuple[float, float]]) -> float:
    return float(pts[-1][1]) if pts else 0.0


def _series_first(pts: list[tuple[float, float]]) -> float:
    return float(pts[0][1]) if pts else 0.0


def _source_observation(
    pts: list[tuple[float, float]],
    *,
    interval_seconds: float | None,
    current_game_day: int,
) -> dict[str, Any]:
    """Describe source freshness without borrowing a backend timestamp.

    Dataset timestamps use the series/exporter clock. ``current_game_day`` is
    the shared semantic reference from /info. An interval is required before
    the backend can make a freshness claim. Legacy list-shaped datasets retain
    their latest sample but remain explicitly unknown-cadence.
    """
    latest_time = float(pts[-1][0]) if pts else None
    latest_game_day = (
        round(latest_time / _SERIES_SECONDS_PER_GAME_DAY, 6) if latest_time is not None else None
    )
    observation: dict[str, Any] = {
        "latest_game_time_seconds": latest_time,
        "latest_game_day": latest_game_day,
        "current_game_day": current_game_day,
        "interval_seconds": interval_seconds,
        "lag_intervals": None,
        "freshness_state": "unknown",
        "freshness_reason": "no_sample" if latest_time is None else "unknown_cadence",
    }
    if latest_time is None or interval_seconds is None or interval_seconds <= 0:
        return observation

    current_source_time = current_game_day * _SERIES_SECONDS_PER_GAME_DAY
    lag_seconds = max(0.0, current_source_time - latest_time)
    lag_intervals = int(lag_seconds // interval_seconds)
    observation["lag_intervals"] = lag_intervals
    if lag_intervals >= 1:
        observation["freshness_state"] = "stale"
        observation["freshness_reason"] = "behind_current_game_day"
    else:
        observation["freshness_state"] = "current"
        observation["freshness_reason"] = "within_source_cadence"
    return observation


def _series_peak(pts: list[tuple[float, float]]) -> float:
    return max((float(v) for _, v in pts), default=0.0)


def _series_baseline(pts: list[tuple[float, float]]) -> float:
    """First *meaningful* value, skipping leading zeros.

    Some series (e.g. ``AverageGlobalTemperature``) record a 0 on the day the
    stat is first allocated, before the world reports a real reading. Using
    that 0 as the baseline would invent a bogus "rose 15 degrees" delta, so we
    take the first non-zero sample instead and fall back to the raw first.
    """
    for _, v in pts:
        if v != 0.0:
            return float(v)
    return _series_first(pts)


def _daily_rate(pts: list[tuple[float, float]], days_elapsed: int) -> float:
    """Most-recent day-over-day change of a daily-sampled series.

    For the cumulative lifetime CO2 series this is "ppm this source added
    today"; the consecutive-sample diff is already a per-day figure. Falls
    back to the whole-series average when there's only sparse data.
    """
    if len(pts) >= 2:
        return float(pts[-1][1]) - float(pts[-2][1])
    if pts:
        return float(pts[-1][1]) / max(days_elapsed, 1)
    return 0.0


def _percent_change(first: float, last: float) -> float | None:
    """Percent change from first to last. ``None`` if there's no baseline."""
    if first == 0:
        return None
    return round(((last - first) / first) * 100.0, 2)


def _earth_year_for_ppm(ppm: float) -> dict[str, Any] | None:
    """Find the closest historical Earth year for a given ppm.

    Returns ``{"year": int, "ppm": float, "note": str}`` or ``None`` if the
    in-game value is below the pre-industrial baseline (rare, but possible
    on heavily-modded peaceful servers).
    """
    if ppm < EARTH_CO2_HISTORY[0][1] - 5.0:
        return None
    # Above the most-recent recorded year — flag as "beyond present-day Earth".
    latest_year, latest_ppm = EARTH_CO2_HISTORY[-1]
    if ppm >= latest_ppm:
        return {
            "year": latest_year,
            "ppm": latest_ppm,
            "note": f"beyond present-day Earth ({latest_year}: {latest_ppm:.0f} ppm)",
        }
    best_year, best_ppm = EARTH_CO2_HISTORY[0]
    best_diff = abs(ppm - best_ppm)
    for year, hist_ppm in EARTH_CO2_HISTORY:
        diff = abs(ppm - hist_ppm)
        if diff < best_diff:
            best_year, best_ppm, best_diff = year, hist_ppm, diff
    return {
        "year": best_year,
        "ppm": best_ppm,
        "note": f"Earth crossed this in ~{best_year}",
    }


def _classify_status(ppm: float, sea_change_pct: float | None) -> str:
    """Three-tier health classification matching the economy card's vocabulary.

    Picked to align with Eco's default Sea Level Rise mod thresholds: 350 ppm
    is the "noticeable warming" line, 500 ppm is where the mod typically
    starts inundating coastal property.
    """
    if ppm >= _CRITICAL_PPM or (sea_change_pct is not None and sea_change_pct >= 5.0):
        return "critical"
    if ppm >= _WARMING_PPM or (sea_change_pct is not None and sea_change_pct >= 1.0):
        return "warming"
    return "stable"


def _parse_layer_pct(summary: str | None) -> float | None:
    """``"4%"`` → ``4.0``. Tolerates whitespace + missing percent sign."""
    if not summary:
        return None
    s = str(summary).strip().rstrip("%").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _compute_breakdown(snapshot: ClimateSnapshot, days_elapsed: int) -> dict[str, Any]:
    """CO2 source/sink attribution — the in-game "Sources and Sinks" block.

    Pollution and animals *add* CO2; plants *remove* it. We report each
    source's cumulative lifetime total and its most-recent per-day rate, plus
    the net daily change, so a player can see at a glance whether the world is
    accumulating or shedding CO2 and which lever dominates.

    ``net_per_day`` is a **flux** — how much the sources and sinks moved — not
    the rate of change of the observed CO2 level. The two diverge sharply once
    CO2 is pinned at the simulation floor, where enormous sink capacity coexists
    with a level that is not moving at all. ``observed_per_day`` is the actual
    day-over-day change in the level, so a caller can tell them apart (#235).
    """
    poll = snapshot.co2_pollution_series
    animals = snapshot.co2_animals_series
    plants = snapshot.co2_plants_series
    if not (poll or animals or plants):
        return {"has_data": False}

    poll_day = _daily_rate(poll, days_elapsed)
    animals_day = _daily_rate(animals, days_elapsed)
    plants_day = _daily_rate(plants, days_elapsed)
    net_day = poll_day + animals_day + plants_day
    # The level's own movement. Only meaningful with two samples to difference:
    # _daily_rate's single-sample fallback divides a lifetime total by the day
    # count, which is right for a cumulative source series and nonsense for a
    # level.
    observed_day = (
        _daily_rate(snapshot.co2_series, days_elapsed) if len(snapshot.co2_series) >= 2 else None
    )
    return {
        "has_data": True,
        "pollution": {"lifetime": round(_series_last(poll), 1), "per_day": round(poll_day, 2)},
        "animals": {"lifetime": round(_series_last(animals), 1), "per_day": round(animals_day, 2)},
        "plants": {"lifetime": round(_series_last(plants), 1), "per_day": round(plants_day, 2)},
        "net_per_day": round(net_day, 2),
        "observed_per_day": round(observed_day, 2) if observed_day is not None else None,
    }


# Live climate-settings JSON keys (camelCase, as emitted by mods/telemetry)
# paired with the module-level default used when the endpoint is absent or
# omits that field. The five core thresholds/rates are all the CO2-effects card
# needs; the trailing three are extras Eco also exposes, surfaced for parity
# with the in-game status even though the card doesn't chart them yet.
def _resolve_rules(settings: dict[str, Any] | None) -> dict[str, Any]:
    """Resolve the CO2-effects ruleset, preferring live per-server values.

    Each field falls back independently to its documented Eco default, so a
    server that emits only some of the block still corrects the rest. ``source``
    is ``"live"`` when the endpoint supplied at least one of the five core
    thresholds/rates, else ``"default"`` — the card keys its "these are the real
    values" vs "these are Eco defaults" disclaimer off that flag.
    """
    live_any = False

    def pick(key: str, default: float) -> float:
        nonlocal live_any
        if settings:
            v = settings.get(key)
            if isinstance(v, int | float) and not isinstance(v, bool):
                live_any = True
                return float(v)
        return default

    def pick_optional(key: str) -> float | None:
        if settings:
            v = settings.get(key)
            if isinstance(v, int | float) and not isinstance(v, bool):
                return float(v)
        return None

    rules: dict[str, Any] = {
        "min_co2_ppm": pick("minCo2Ppm", _RULE_MIN_CO2_PPM),
        "temp_threshold_ppm": pick("temperatureThresholdPpm", _RULE_TEMP_THRESHOLD_PPM),
        "ppm_per_degree": pick("co2PpmPerDegree", _RULE_PPM_PER_DEGREE),
        "sea_threshold_ppm": pick("seaLevelThresholdPpm", _RULE_SEA_THRESHOLD_PPM),
        "ppm_per_meter": pick("co2PpmPerMeter", _RULE_PPM_PER_METER),
        "pollution_multiplier": pick_optional("pollutionMultiplier"),
        "max_co2_per_day_from_animals": pick_optional("maxCo2PerDayFromAnimals"),
        "min_co2_per_day_from_plants": pick_optional("minCo2PerDayFromPlants"),
    }
    rules["source"] = "live" if live_any else "default"
    return rules


def _compute_effects(
    *,
    co2_series: list[tuple[float, float]],
    sea_series: list[tuple[float, float]],
    temp_series: list[tuple[float, float]],
    rules: dict[str, Any],
) -> dict[str, Any]:
    """Translate the live state into the in-game "CO2 Effects" mechanics.

    Returns the ruleset constants (so the card can state the exact formula),
    the current CO2 standing relative to each threshold, and the observed
    real-world deltas (degrees warmed, meters of sea-level rise) computed
    straight from the series. ``rules`` carries the resolved per-server
    thresholds/rates (live where the server emits them, Eco defaults otherwise)
    plus a ``source`` flag the card uses to drop the "these are defaults"
    disclaimer once the values are live.
    """
    min_co2 = rules["min_co2_ppm"]
    temp_threshold = rules["temp_threshold_ppm"]
    ppm_per_degree = rules["ppm_per_degree"]
    sea_threshold = rules["sea_threshold_ppm"]
    ppm_per_meter = rules["ppm_per_meter"]

    co2_now = _series_last(co2_series)
    co2_peak = _series_peak(co2_series)

    temp_now = _series_last(temp_series)
    temp_base = _series_baseline(temp_series)
    sea_now = _series_last(sea_series)
    sea_base = _series_baseline(sea_series)

    temp_headroom = temp_threshold - co2_now
    sea_headroom = sea_threshold - co2_now
    effects: dict[str, Any] = {
        "co2_now": round(co2_now, 1) if co2_series else None,
        "co2_peak": round(co2_peak, 1) if co2_series else None,
        "min_floor_ppm": min_co2,
        "at_floor": bool(co2_series) and co2_now <= min_co2 + 0.5,
        "source": rules["source"],
        "temperature": {
            "threshold_ppm": temp_threshold,
            "ppm_per_degree": ppm_per_degree,
            "headroom_ppm": round(temp_headroom, 1) if co2_series else None,
            "current_c": round(temp_now, 2) if temp_series else None,
            "risen_c": round(temp_now - temp_base, 2) if temp_series else None,
            "peak_drives_c": (
                round((co2_peak - temp_threshold) / ppm_per_degree, 2)
                if co2_series and co2_peak > temp_threshold and ppm_per_degree
                else 0.0
            ),
        },
        "sea_level": {
            "threshold_ppm": sea_threshold,
            "ppm_per_meter": ppm_per_meter,
            "headroom_ppm": round(sea_headroom, 1) if co2_series else None,
            "current_m": round(sea_now, 2) if sea_series else None,
            "risen_m": round(sea_now - sea_base, 2) if sea_series else None,
            "peak_drives_m": (
                round((co2_peak - sea_threshold) / ppm_per_meter, 2)
                if co2_series and co2_peak > sea_threshold and ppm_per_meter
                else 0.0
            ),
        },
    }
    # Extra Eco climate knobs, surfaced only when the server actually emits
    # them (no documented default to fall back to, unlike the five core rules).
    for key in (
        "pollution_multiplier",
        "max_co2_per_day_from_animals",
        "min_co2_per_day_from_plants",
    ):
        if rules.get(key) is not None:
            effects[key] = rules[key]
    return effects


def _build_explainer(breakdown: dict[str, Any], effects: dict[str, Any]) -> list[str]:
    """Plain-language "what's happening and what to expect" sentences.

    This is the verbose explanation the request asked for — the same story the
    in-game pollution machine tells, but narrated rather than tabulated. Each
    sentence is self-contained so the card can render them as a bullet list.
    """
    out: list[str] = []
    co2_now = effects.get("co2_now")
    if co2_now is None:
        return out

    temp = effects["temperature"]
    sea = effects["sea_level"]

    # 1. Where CO2 stands right now.
    if effects["at_floor"]:
        out.append(
            f"CO2 sits at {co2_now:.0f} ppm — pinned to the simulation floor "
            f"({effects['min_floor_ppm']:.0f} ppm), the cleanest the atmosphere can get."
        )
    else:
        out.append(f"CO2 currently stands at {co2_now:.0f} ppm.")

    # 2. Who's pushing it which way.
    #
    # net_per_day is a flux, not the rate of change of the level. Narrating it
    # as the latter claimed "CO2 is falling about 361.4 ppm per day" about a
    # value of 325 ppm that was pinned flat at its 325 ppm floor (#235). Lead
    # with what the level is doing; describe the flux as capacity.
    if breakdown.get("has_data"):
        net = breakdown["net_per_day"]
        observed = breakdown.get("observed_per_day")
        absorbed_day = abs(breakdown["plants"]["per_day"])
        emitted_day = breakdown["pollution"]["per_day"] + breakdown["animals"]["per_day"]
        plants_life = breakdown["plants"]["lifetime"]
        poll_life = breakdown["pollution"]["lifetime"]
        if effects["at_floor"]:
            out.append(
                f"Sinks have capacity to spare — plants absorb about {absorbed_day:,.0f} ppm/day "
                f"against roughly {emitted_day:,.0f} ppm/day emitted — but the level itself is "
                "not moving, because it is already at the floor."
            )
        elif observed is not None and abs(observed) >= 0.05:
            direction = "falling" if observed < 0 else "climbing"
            out.append(
                f"CO2 is {direction} about {abs(observed):,.1f} ppm per day. Over the cycle "
                f"plants have pulled {abs(plants_life):,.0f} ppm out of the air against "
                f"{poll_life:,.0f} ppm added by machines."
            )
        elif net < 0:
            out.append(
                f"Plants absorb about {absorbed_day:,.0f} ppm/day against roughly "
                f"{emitted_day:,.0f} ppm/day emitted, but the level is holding steady."
            )
        elif net > 0:
            out.append(
                f"Machines are outpacing the plants — about {emitted_day:,.0f} ppm/day emitted "
                f"against {absorbed_day:,.0f} ppm/day absorbed ({poll_life:,.0f} ppm added by "
                "pollution so far) — though the level is not moving yet."
            )
        else:
            out.append("Sources and sinks are roughly balanced — CO2 is holding steady.")

    # 3. The warming / sea-level mechanic and how much margin remains.
    out.append(
        f"Past {temp['threshold_ppm']:.0f} ppm, every {temp['ppm_per_degree']:.0f} ppm of CO2 "
        f"adds 1 °C of warming, and every {sea['ppm_per_meter']:.0f} ppm above "
        f"{sea['threshold_ppm']:.0f} ppm raises the sea 1 m."
    )
    headroom = temp.get("headroom_ppm")
    if headroom is not None and headroom > 0:
        out.append(
            f"That leaves {headroom:.0f} ppm of headroom before new warming kicks in — the "
            "current trajectory is safe."
        )
    elif headroom is not None and headroom <= 0:
        out.append(
            f"CO2 is already {abs(headroom):.0f} ppm over the warming line — expect temperature "
            "and sea level to keep climbing until emissions drop."
        )

    # 4. What's already baked in from the cycle's peak.
    co2_peak = effects.get("co2_peak")
    risen_m = sea.get("risen_m") or 0.0
    if co2_peak and co2_peak > sea["threshold_ppm"] and risen_m > 0.05:
        out.append(
            f"Earlier in the cycle CO2 peaked at {co2_peak:.0f} ppm, which is what raised the "
            f"sea {risen_m:.2f} m so far — that rise holds at its high-water mark even as CO2 "
            "falls."
        )
    elif risen_m > 0.05:
        out.append(f"Sea level has risen {risen_m:.2f} m since the cycle began.")

    return out


def compute_climate_payload(snapshot: ClimateSnapshot) -> dict[str, Any]:
    """Shape the snapshot for the Jinja card. Pure — no IO, no SVG.

    Sparkline SVGs are attached by the server.py render path so we don't pull
    the SVG renderer into this module (and circular-import on it).
    """
    info = snapshot.info or {}
    days_elapsed = snapshot.days_elapsed

    co2_last = _series_last(snapshot.co2_series)
    co2_first = _series_first(snapshot.co2_series)
    co2_change_pct = _percent_change(co2_first, co2_last) if snapshot.co2_series else None

    sea_last = _series_last(snapshot.sea_level_series)
    sea_first = _series_first(snapshot.sea_level_series)
    sea_change_pct = _percent_change(sea_first, sea_last) if snapshot.sea_level_series else None
    # Linear extrapolation: at the current rate, when does sea level cross +1
    # unit? The mod reads sea level in arbitrary units, so we project the
    # number of cycle-days, not meters — interpretation is the player's job.
    sea_rate_per_day = (
        (sea_last - sea_first) / max(days_elapsed, 1) if snapshot.sea_level_series else 0.0
    )

    pollution_last = _series_last(snapshot.pollution_series)
    pollution_layer_pct = _parse_layer_pct(snapshot.pollution_layer_summary)
    # Prefer the live series; fall back to the worldlayers summary number.
    pollution_value: float | None
    pollution_source: str
    pollution_unit: str | None
    if snapshot.pollution_series:
        pollution_value = pollution_last
        pollution_source = snapshot.pollution_dataset_name or "series"
        # Every dataset-backed ground-pollution candidate is a concentration
        # series. Eco 0.13 supplies Unit=PPM. Older list payloads omit Unit, so
        # retain the documented PPM meaning rather than fabricating percent.
        pollution_unit = snapshot.pollution_unit or "PPM"
    elif pollution_layer_pct is not None:
        pollution_value = pollution_layer_pct
        pollution_source = "worldlayers"
        pollution_unit = "%"
    else:
        pollution_value = None
        pollution_source = "none"
        pollution_unit = None

    temp_last = _series_last(snapshot.temperature_series)
    temp_base = _series_baseline(snapshot.temperature_series)
    temp_rate_per_day = _daily_rate(snapshot.temperature_series, days_elapsed)

    earth_match = _earth_year_for_ppm(co2_last) if snapshot.co2_series else None
    status = _classify_status(co2_last, sea_change_pct) if snapshot.co2_series else "unknown"

    breakdown = _compute_breakdown(snapshot, days_elapsed)
    rules = _resolve_rules(snapshot.climate_settings)
    effects = _compute_effects(
        co2_series=snapshot.co2_series,
        sea_series=snapshot.sea_level_series,
        temp_series=snapshot.temperature_series,
        rules=rules,
    )
    explainer = _build_explainer(breakdown, effects)

    has_attribution = bool(snapshot.top_polluter_citizens or snapshot.top_polluter_stations)

    narrative = _narrative(
        status=status,
        co2_ppm=co2_last,
        co2_change_pct=co2_change_pct,
        sea_change_pct=sea_change_pct,
        admin_ok=snapshot.admin_ok,
        any_series=bool(snapshot.co2_series or snapshot.sea_level_series),
        available_climate_datasets=snapshot.available_climate_datasets,
    )

    return {
        "view": "eco_climate",
        "server": {
            "description": info.get("Description", ""),
            "category": info.get("Category"),
            "sourceUrl": info.get("_sourceUrl"),
        },
        "days_elapsed": days_elapsed,
        "admin_ok": snapshot.admin_ok,
        "status": status,
        "narrative": narrative,
        "co2": {
            "current": round(co2_last, 1) if snapshot.co2_series else None,
            "change_pct": co2_change_pct,
            "dataset_name": snapshot.co2_dataset_name,
            "unit": snapshot.co2_unit or ("PPM" if snapshot.co2_series else None),
            "observation": _source_observation(
                snapshot.co2_series,
                interval_seconds=snapshot.co2_interval_seconds,
                current_game_day=days_elapsed,
            ),
            "series": [list(p) for p in snapshot.co2_series],
        },
        "sea_level": {
            "current": round(sea_last, 3) if snapshot.sea_level_series else None,
            "change_pct": sea_change_pct,
            "rate_per_day": round(sea_rate_per_day, 4),
            "dataset_name": snapshot.sea_level_dataset_name,
            "unit": snapshot.sea_level_unit or ("Meters" if snapshot.sea_level_series else None),
            "observation": _source_observation(
                snapshot.sea_level_series,
                interval_seconds=snapshot.sea_level_interval_seconds,
                current_game_day=days_elapsed,
            ),
            "series": [list(p) for p in snapshot.sea_level_series],
        },
        "pollution": {
            "current": (round(pollution_value, 2) if pollution_value is not None else None),
            "source": pollution_source,
            "dataset_name": snapshot.pollution_dataset_name,
            "unit": pollution_unit,
            "observation": _source_observation(
                snapshot.pollution_series,
                interval_seconds=snapshot.pollution_interval_seconds,
                current_game_day=days_elapsed,
            ),
            "layer_summary": snapshot.pollution_layer_summary,
            "series": [list(p) for p in snapshot.pollution_series],
        },
        "temperature": {
            "current": round(temp_last, 2) if snapshot.temperature_series else None,
            "risen": (round(temp_last - temp_base, 2) if snapshot.temperature_series else None),
            "rate_per_day": round(temp_rate_per_day, 3),
            "dataset_name": snapshot.temperature_dataset_name,
            "unit": snapshot.temperature_unit
            or ("Celsius" if snapshot.temperature_series else None),
            "observation": _source_observation(
                snapshot.temperature_series,
                interval_seconds=snapshot.temperature_interval_seconds,
                current_game_day=days_elapsed,
            ),
            "series": [list(p) for p in snapshot.temperature_series],
        },
        "breakdown": breakdown,
        "effects": effects,
        "explainer": explainer,
        "earth_match": earth_match,
        "attribution": {
            "has_data": has_attribution,
            "actions_total": snapshot.pollution_actions_total,
            "action_types_seen": list(snapshot.pollution_action_types_seen),
            # `emissions`, not `count`: these are summed pollution amounts, so
            # a fractional value is expected. Under a `count` key the single
            # "(unknown)" station reading 6858.75 looked like a broken counter
            # rather than a whole server's emissions in one unnamed bucket
            # (#226).
            "top_citizens": [
                {"name": n, "emissions": c} for n, c in snapshot.top_polluter_citizens[:5]
            ],
            "top_stations": [
                {"name": n, "emissions": c} for n, c in snapshot.top_polluter_stations[:5]
            ],
            "unattributed_rows": {
                "citizen": snapshot.unattributed_polluter_rows,
                "station": snapshot.unattributed_station_rows,
            },
        },
        "warnings": list(snapshot.warnings),
        "available_climate_datasets": list(snapshot.available_climate_datasets),
        "fetched_at_iso": snapshot.fetched_at_iso,
        "source_base_url": snapshot.source_base_url,
    }


def _narrative(
    *,
    status: str,
    co2_ppm: float,
    co2_change_pct: float | None,
    sea_change_pct: float | None,
    admin_ok: bool,
    any_series: bool,
    available_climate_datasets: list[str] | None = None,
) -> str:
    """One-sentence summary for the card header."""
    if not admin_ok and not any_series:
        return (
            "Admin token unavailable — showing public worldlayers data only. "
            "CO2 and sea-level series require ECO_ADMIN_TOKEN."
        )
    if not any_series:
        # When the catalog *did* expose climate datasets but none had values,
        # call that out specifically — it's a different failure mode from
        # "this server's stat catalog has no climate stats at all" and the
        # latter usually means a mod is missing.
        if available_climate_datasets:
            sample = ", ".join(f"`{n}`" for n in available_climate_datasets[:5])
            return (
                f"Server exposes climate datasets ({sample}) but none have "
                "recorded values yet — early in the cycle, or the polluting "
                "machinery hasn't run."
            )
        return (
            "No atmospheric series exposed by this server's stat catalog. "
            "Likely a missing climate / pollution mod, or a vanilla ruleset."
        )

    bits: list[str] = []
    if co2_ppm:
        bits.append(f"CO2 at {co2_ppm:.0f} ppm")
    if co2_change_pct is not None and abs(co2_change_pct) >= 0.1:
        sign = "+" if co2_change_pct > 0 else ""
        bits.append(f"{sign}{co2_change_pct:.1f}% since cycle start")
    if sea_change_pct is not None and abs(sea_change_pct) >= 0.05:
        sign = "+" if sea_change_pct > 0 else ""
        bits.append(f"sea level {sign}{sea_change_pct:.2f}%")

    head = f"Climate is {status}"
    if not bits:
        return f"{head} — no measurable change yet."
    return f"{head} — {', '.join(bits)}."


# ---------------------------------------------------------------------------
# Pollution heatmap GIF — used by the SPA /map overlay.
# ---------------------------------------------------------------------------


async def fetch_pollution_overlay_gif(
    base_url: str, *, client: httpx.AsyncClient | None = None
) -> bytes | None:
    """GET ``/Layers/Pollution.gif``. Returns ``None`` on any non-200.

    Eco serves world-layer rasters under ``/Layers/<Name>.gif`` alongside the
    main ``WorldPreview.gif``. The pollution layer isn't always exposed (the
    server config can disable individual rasters), so a 404 is normal — the
    map card just renders without the overlay.
    """
    base = base_url.rstrip("/")
    url = f"{base}/Layers/Pollution.gif"
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=5.0)
    try:
        try:
            r = await http.get(url)
        except httpx.HTTPError:
            return None
        if r.status_code != 200:
            return None
        return r.content
    finally:
        if owns_client:
            await http.aclose()


def _clear_cache() -> None:
    """Test hook — drop the per-process climate cache."""
    _climate_cache.clear()


__all__ = [
    "CO2_DATASET_CANDIDATES",
    "CO2_DISCOVERY_KEYWORDS",
    "EARTH_CO2_HISTORY",
    "POLLUTION_ACTION_TYPES",
    "POLLUTION_DATASET_CANDIDATES",
    "POLLUTION_DISCOVERY_KEYWORDS",
    "SEA_LEVEL_DATASET_CANDIDATES",
    "SEA_LEVEL_DISCOVERY_KEYWORDS",
    "ClimateSnapshot",
    "compute_climate_payload",
    "fetch_climate",
    "fetch_pollution_overlay_gif",
]
