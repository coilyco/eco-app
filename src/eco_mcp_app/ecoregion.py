"""Biodiversity drift + ecoregion-match tool implementation.

Pulls three slices of Eco server data and collapses them into a single card:

1. Biome composition from the public worldlayers endpoint — parsed out of the
   per-layer ``Summary`` strings, which look like ``"4%"``. Percentages do NOT
   sum to 100; large chunks of the world (shallow water, mountain, transitional
   terrain) are uncounted — see #19.
2. Nearest real-world ecoregion match via cosine similarity against a small,
   committed WWF-inspired fixture. The Eco biome vector is normalized to
   ``sum=1`` first so the classifier is comparing *shapes*, not absolute area.
3. Species drift from the admin exporter — per-species CSV where ``Time`` is
   seconds since cycle start, ``Value`` is population count. We bucket species
   into "boom" and "bust" lists by relative change from first to last sample.

Everything here is transport-agnostic so the same functions back both the MCP
call-tool path and the HTTP ``/preview`` route.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import math
import os
import re
import time
from dataclasses import dataclass, replace
from importlib.resources import files
from pathlib import Path
from typing import Any

import httpx

# Ordered so the donut chart / table always renders in the same order
# regardless of dict-iteration order in the response.
BIOME_LAYERS: tuple[str, ...] = (
    "TaigaBiome",
    "DesertBiome",
    "WetlandBiome",
    "ColdForestBiome",
    "ForestBiome",
    "WarmForestBiome",
    "TundraBiome",
    "DeepOceanBiome",
    "OceanBiome",
    "GrasslandBiome",
    "RainforestBiome",
    "IceBiome",
)

# Stable palette for the donut. Pulled from the CSS theme variables where they
# match intuitively (moss=forest, water=ocean, sun=desert) and filled in with
# neighbors otherwise. Not rigorous — just needs to be readable side-by-side.
BIOME_COLORS: dict[str, str] = {
    "TaigaBiome": "#3c5a3a",
    "ColdForestBiome": "#4a6b44",
    "ForestBiome": "#5a8a3a",
    "WarmForestBiome": "#7aa84a",
    "RainforestBiome": "#2e5e2a",
    "GrasslandBiome": "#a5d14a",
    "WetlandBiome": "#4a7a6a",
    "OceanBiome": "#4a9cb8",
    "DeepOceanBiome": "#2a5a78",
    "DesertBiome": "#e58f2c",
    "TundraBiome": "#c4b896",
    "IceBiome": "#e0eaf2",
}

# Water accounting — the missing 60% (eco-app#82). Eco's `Biome` category only
# tags Ocean + DeepOcean (deep, open water) as biomes, so the raw biome percents
# sum to ~39% and the old card showed ~61% as an undifferentiated grey
# "unclassified" slice. But the `World` layer reports `SaltWater` (total salt
# water, ~57%) and `Moisture` reports `FreshWater` (~5%): the bulk of that grey
# gap is simply *water* the biome layers don't name. We reclassify it into two
# synthetic slices so the donut reads honestly — coastal/shallow salt water
# (SaltWater minus what's already tagged Ocean/DeepOcean biome) and fresh water —
# leaving only genuine mountain/transitional terrain unclassified (~20%).
_SALTWATER_KEY = "CoastalWater"
_FRESHWATER_KEY = "FreshWater"
_SALTWATER_DISPLAY = "Coastal & shallow sea"
_FRESHWATER_DISPLAY = "Fresh water"
_SALTWATER_COLOR = "#5fb0cf"
_FRESHWATER_COLOR = "#7fc8bf"
# The two `Biome`-category layers that already count as (deep) salt water, so we
# don't double-count them when deriving the coastal remainder from World.SaltWater.
_OCEAN_BIOME_KEYS: tuple[str, ...] = ("OceanBiome", "DeepOceanBiome")

# Pretty labels for the card — these are what LayerDisplayName tends to be in
# live data, but we don't depend on the upstream name so the card renders even
# if the /worldlayers endpoint truncates one.
BIOME_DISPLAY: dict[str, str] = {
    "TaigaBiome": "Taiga",
    "DesertBiome": "Desert",
    "WetlandBiome": "Wetland",
    "ColdForestBiome": "Cold forest",
    "ForestBiome": "Forest",
    "WarmForestBiome": "Warm forest",
    "TundraBiome": "Tundra",
    "DeepOceanBiome": "Deep ocean",
    "OceanBiome": "Ocean",
    "GrasslandBiome": "Grassland",
    "RainforestBiome": "Rainforest",
    "IceBiome": "Ice",
}

# Drift needs >= 2 samples to compute a delta. With 600s (10-min) cadence
# that means the cycle has to have ticked at least twice — on Day 3 every
# species should meet this, but the guard is cheap.
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)")

_WORLDLAYERS_PATH = "/api/v1/worldlayers/layers"
_SPECIESLIST_PATH = "/api/v1/exporter/specieslist"
_SPECIES_PATH = "/api/v1/exporter/species"

# Live Eco data caches. `worldlayers` is essentially static over a 5-minute
# window (biome % only changes when terrain is physically edited at scale);
# species CSVs change every 600s so 60s is plenty. Keyed by `(base_url, path)`
# so multi-server use doesn't cross-pollinate.
_WORLDLAYERS_TTL_S = float(os.environ.get("ECO_WORLDLAYERS_CACHE_TTL", "300"))
_SPECIES_TTL_S = float(os.environ.get("ECO_SPECIES_CACHE_TTL", "60"))
_worldlayers_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_specieslist_cache: dict[str, tuple[float, list[str]]] = {}
_species_cache: dict[tuple[str, str], tuple[float, list[tuple[int, float]]]] = {}

# Biodiversity-risk evidence rules. These are deliberately relative to each
# species' own observed series. Eco does not expose a universal healthy
# population baseline, so eco-app never invents one.
_RISK_CURRENT_PEAK_RATIO = 0.25
_RISK_CYCLE_DECLINE = -0.30
_RISK_RECENT_DECLINE = -0.15
_RECOVERY_RECENT_GROWTH = 0.15
_SPARSE_OBSERVED_PEAK = 25.0
_MIN_RISK_SAMPLES = 4
_MIN_OBSERVATION_SECONDS = 1800
_STALE_LAG_SECONDS = 1800


# ---------- data loading ----------


def _load_ecoregions_bundled() -> list[dict[str, Any]]:
    """Load the committed WWF fixture.

    Installed wheels carry the file as package data under
    ``eco_mcp_app/data/ecoregions.json`` (see ``force-include`` in
    pyproject.toml). Editable/source checkouts keep it at repo-root
    ``data/ecoregions.json`` — the force-include isn't applied then, so we
    walk up from ``__file__`` to find it.
    """
    try:
        packaged = files("eco_mcp_app").joinpath("data", "ecoregions.json")
        if packaged.is_file():
            return list(json.loads(packaged.read_text()).get("regions") or [])
    except (FileNotFoundError, ModuleNotFoundError):
        pass
    here = Path(__file__).resolve().parent
    for parent in (here.parent.parent, here.parent, here):
        candidate = parent / "data" / "ecoregions.json"
        if candidate.exists():
            with candidate.open() as f:
                doc = json.load(f)
            return list(doc.get("regions") or [])
    return []


# ---------- HTTP fetchers ----------


def _base_url_from_info_url(info_url: str) -> str:
    """``http://host:3001/info`` → ``http://host:3001``. Admin routes share the host."""
    # Strip the trailing /info (or any path); keep scheme + netloc.
    from urllib.parse import urlparse, urlunparse

    p = urlparse(info_url)
    return urlunparse((p.scheme or "http", p.netloc, "", "", "", ""))


async def fetch_worldlayers(base_url: str) -> list[dict[str, Any]]:
    """GET /api/v1/worldlayers/layers — 7 categories, cached 5 min."""
    key = base_url
    now = time.monotonic()
    cached = _worldlayers_cache.get(key)
    if cached and (now - cached[0]) < _WORLDLAYERS_TTL_S:
        return list(cached[1])
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(base_url + _WORLDLAYERS_PATH)
        r.raise_for_status()
        data = r.json()
    cats = list(data) if isinstance(data, list) else []
    _worldlayers_cache[key] = (now, cats)
    return cats


async def fetch_specieslist(base_url: str, api_key: str) -> list[str]:
    """GET /api/v1/exporter/specieslist — newline-delimited plain text.

    Not JSON — the endpoint returns one species name per line. Lines are
    stripped and blank lines dropped so downstream code sees a clean list.
    """
    key = base_url
    now = time.monotonic()
    cached = _specieslist_cache.get(key)
    if cached and (now - cached[0]) < _SPECIES_TTL_S:
        return list(cached[1])
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(
            base_url + _SPECIESLIST_PATH,
            headers={"X-API-Key": api_key},
        )
        r.raise_for_status()
        names = [line.strip() for line in r.text.splitlines() if line.strip()]
    _specieslist_cache[key] = (now, names)
    return names


async def fetch_species_samples(
    base_url: str, species_name: str, api_key: str
) -> list[tuple[int, float]]:
    """GET /api/v1/exporter/species?speciesName=X — CSV ``"Time","Value"``.

    Time is seconds since cycle start at 600s cadence; Value is population.
    Returns an empty list on parse failure so an individual corrupt series
    doesn't kill the whole drift column.
    """
    key = (base_url, species_name)
    now = time.monotonic()
    cached = _species_cache.get(key)
    if cached and (now - cached[0]) < _SPECIES_TTL_S:
        return list(cached[1])
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(
            base_url + _SPECIES_PATH,
            params={"speciesName": species_name},
            headers={"X-API-Key": api_key},
        )
        r.raise_for_status()
        text = r.text
    samples: list[tuple[int, float]] = []
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if not row:
            continue
        # Skip header row and any other non-numeric pair.
        try:
            t = int(float(row[0].strip().strip('"')))
            v = float(row[1].strip().strip('"'))
        except (ValueError, IndexError):
            continue
        samples.append((t, v))
    _species_cache[key] = (now, samples)
    return samples


# ---------- biome extraction + normalization ----------


def extract_biome_percents(categories: list[dict[str, Any]]) -> dict[str, float]:
    """Pull out the 12 biome layers from the 7-category worldlayers response.

    Returns a dict keyed by LayerName (e.g. ``TaigaBiome``) with the % of
    world area as a float 0..100. Missing layers get 0.0 so the chart always
    has a full row of keys even on a sparse / custom-modded world.
    """
    out = dict.fromkeys(BIOME_LAYERS, 0.0)
    for cat in categories:
        if not isinstance(cat, dict):
            continue
        if cat.get("Category") != "Biome":
            continue
        for entry in cat.get("List") or []:
            name = entry.get("LayerName")
            if name in out:
                summary = entry.get("Summary") or ""
                m = _PERCENT_RE.match(str(summary).strip())
                if m:
                    try:
                        out[name] = float(m.group(1))
                    except ValueError:
                        pass
    return out


def _layer_percent(categories: list[dict[str, Any]], category: str, layer: str) -> float:
    """Pull one layer's ``Summary`` percent from a named worldlayers category.

    Returns 0.0 when the category or layer is absent, or the summary isn't a
    percentage (some ``World`` layers report bare floats or "N meters"). Only
    the leading number of a ``"57%"``-style summary is used.
    """
    for cat in categories:
        if not isinstance(cat, dict) or cat.get("Category") != category:
            continue
        for entry in cat.get("List") or []:
            if entry.get("LayerName") != layer:
                continue
            summary = str(entry.get("Summary") or "").strip()
            if not summary.endswith("%"):
                return 0.0
            m = _PERCENT_RE.match(summary)
            if m:
                try:
                    return float(m.group(1))
                except ValueError:
                    return 0.0
    return 0.0


def extract_water_percents(categories: list[dict[str, Any]]) -> dict[str, float]:
    """Pull the world's salt- and fresh-water coverage from the non-biome layers.

    ``World.SaltWater`` is the total salt-water fraction of the world (deep
    ocean + coastline + shallow sea); ``Moisture.FreshWater`` is lakes/rivers.
    Both are ``"NN%"`` summaries. Used to reclassify the biome-layer gap
    (eco-app#82) — see ``_SALTWATER_KEY`` for the full rationale.
    """
    return {
        "saltwater": _layer_percent(categories, "World", "SaltWater"),
        "freshwater": _layer_percent(categories, "Moisture", "FreshWater"),
    }


def normalize_vector(raw: dict[str, float]) -> dict[str, float]:
    """Scale so the values sum to 1.0. All-zero input maps to all-zero output."""
    total = sum(raw.values())
    if total <= 0:
        return dict.fromkeys(raw, 0.0)
    return {k: v / total for k, v in raw.items()}


def cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity over the shared key set. 0..1 for non-negative inputs."""
    keys = set(a) | set(b)
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in keys)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


@dataclass
class EcoregionMatch:
    name: str
    description: str
    similarity: float


def top_ecoregions(
    normalized_biomes: dict[str, float],
    regions: list[dict[str, Any]],
    n: int = 3,
) -> list[EcoregionMatch]:
    """Rank the committed regions by cosine similarity to the world vector.

    Ties are broken alphabetically by name so the ordering is deterministic
    across consecutive calls (acceptance criterion in the spec).
    """
    scored: list[EcoregionMatch] = []
    for r in regions:
        vec = r.get("biome_vector") or {}
        sim = cosine_similarity(normalized_biomes, vec)
        scored.append(
            EcoregionMatch(
                name=r.get("name") or "Unnamed",
                description=r.get("description") or "",
                similarity=sim,
            )
        )
    scored.sort(key=lambda m: (-m.similarity, m.name))
    return scored[:n]


# ---------- drift ----------


@dataclass
class SpeciesDrift:
    name: str
    first: float
    latest: float
    delta_rel: float  # (latest - first) / first; 0 if first == 0


def compute_drift(samples: list[tuple[int, float]]) -> SpeciesDrift | None:
    """Reduce a CSV series to a single first→latest relative change.

    Returns None when fewer than two samples are available (e.g. the cycle
    just ticked over and only one datapoint exists yet). Samples are sorted
    on ``Time`` before reduction so a non-monotonic CSV doesn't blow it up.
    """
    if len(samples) < 2:
        return None
    ordered = sorted(samples, key=lambda s: s[0])
    first_v = ordered[0][1]
    last_v = ordered[-1][1]
    if first_v == 0.0:
        delta_rel = 0.0 if last_v == 0.0 else float("inf")
    else:
        delta_rel = (last_v - first_v) / first_v
    return SpeciesDrift(name="", first=first_v, latest=last_v, delta_rel=delta_rel)


def rank_drift(
    series: dict[str, list[tuple[int, float]]],
    n: int = 5,
) -> tuple[list[SpeciesDrift], list[SpeciesDrift]]:
    """Return (boom, bust) lists of top-n relative movers.

    ``boom`` and ``bust`` are mutually exclusive — a species with ``delta_rel
    == 0`` appears in neither list. On Day 3 many species will have 0 delta;
    the card handles the empty case by rendering a placeholder.
    """
    drifts: list[SpeciesDrift] = []
    for name, samples in series.items():
        d = compute_drift(samples)
        if d is None:
            continue
        d.name = name
        drifts.append(d)
    boom = sorted(
        (d for d in drifts if d.delta_rel > 0),
        key=lambda d: -d.delta_rel,
    )[:n]
    bust = sorted(
        (d for d in drifts if d.delta_rel < 0),
        key=lambda d: d.delta_rel,
    )[:n]
    return boom, bust


@dataclass
class SpeciesRisk:
    name: str
    state: str
    warning: bool
    reason: str
    current: float | None
    change_abs: float | None
    change_pct: float | None
    recent_change_pct: float | None
    observed_peak: float | None
    first_time: int | None
    latest_time: int | None
    recent_from_time: int | None
    sample_count: int
    freshness: str


def _relative_change(first: float, latest: float) -> float | None:
    if first == 0:
        return None
    return (latest - first) / first


def classify_species_risk(
    series: dict[str, list[tuple[int, float]]],
    *,
    expected_species: list[str] | None = None,
) -> list[SpeciesRisk]:
    """Classify species using only their own observed population evidence.

    A warning requires either a collapse below 25% of that species' observed
    peak, or a >=30% cycle decline that is still falling >=15% in the recent
    window. Thin, missing, and stale series never produce a healthy or at-risk
    claim. Naturally sparse means the observed peak stayed <=25 without a
    relative collapse, not that 25 is a universal healthy target.
    """
    names = sorted(set(expected_species or []) | set(series))
    latest_global = max((t for samples in series.values() for t, _ in samples), default=None)
    out: list[SpeciesRisk] = []

    for name in names:
        ordered = sorted(series.get(name, []), key=lambda sample: sample[0])
        if not ordered:
            out.append(
                SpeciesRisk(
                    name=name,
                    state="missing",
                    warning=False,
                    reason="No population samples were exported.",
                    current=None,
                    change_abs=None,
                    change_pct=None,
                    recent_change_pct=None,
                    observed_peak=None,
                    first_time=None,
                    latest_time=None,
                    recent_from_time=None,
                    sample_count=0,
                    freshness="missing",
                )
            )
            continue

        first_time, first = ordered[0]
        latest_time, current = ordered[-1]
        observed_peak = max(value for _, value in ordered)
        change_abs = current - first
        change_pct = _relative_change(first, current)
        span = latest_time - first_time
        stale = latest_global is not None and latest_global - latest_time > _STALE_LAG_SECONDS

        recent_target = latest_time - max(_MIN_OBSERVATION_SECONDS, int(span * 0.25))
        recent_time, recent_value = min(ordered, key=lambda sample: abs(sample[0] - recent_target))
        recent_change_pct = _relative_change(recent_value, current)

        evidence = SpeciesRisk(
            name=name,
            state="",
            warning=False,
            reason="",
            current=current,
            change_abs=change_abs,
            change_pct=change_pct,
            recent_change_pct=recent_change_pct,
            observed_peak=observed_peak,
            first_time=first_time,
            latest_time=latest_time,
            recent_from_time=recent_time,
            sample_count=len(ordered),
            freshness="current",
        )

        if stale:
            out.append(
                replace(
                    evidence,
                    state="stale",
                    reason=(
                        "Latest sample trails the newest exporter data by more than "
                        f"{_STALE_LAG_SECONDS // 60} minutes."
                    ),
                    freshness="stale",
                )
            )
            continue
        if len(ordered) < _MIN_RISK_SAMPLES or span < _MIN_OBSERVATION_SECONDS:
            out.append(
                replace(
                    evidence,
                    state="insufficient",
                    reason=(
                        f"Need {_MIN_RISK_SAMPLES} samples across "
                        f"{_MIN_OBSERVATION_SECONDS // 60} minutes before classifying risk."
                    ),
                )
            )
            continue

        evidence_floor = max(3.0, observed_peak * 0.10)
        collapse = (
            observed_peak > 0
            and current <= observed_peak * _RISK_CURRENT_PEAK_RATIO
            and observed_peak - current >= evidence_floor
        )
        sustained_decline = (
            change_pct is not None
            and recent_change_pct is not None
            and change_pct <= _RISK_CYCLE_DECLINE
            and recent_change_pct <= _RISK_RECENT_DECLINE
            and first - current >= evidence_floor
        )
        if collapse or sustained_decline:
            reasons: list[str] = []
            if collapse:
                reasons.append("current population is at or below 25% of its own observed peak")
            if sustained_decline:
                reasons.append(
                    "cycle decline is at least 30% and the recent window is still down at least 15%"
                )
            out.append(
                replace(
                    evidence,
                    state="at_risk",
                    warning=True,
                    reason="; ".join(reasons).capitalize() + ".",
                )
            )
        elif (
            change_pct is not None
            and change_pct < 0
            and recent_change_pct is not None
            and recent_change_pct >= _RECOVERY_RECENT_GROWTH
        ):
            out.append(
                replace(
                    evidence,
                    state="recovering",
                    reason=(
                        "Cycle population is down, but the recent window has recovered "
                        "at least 15%."
                    ),
                )
            )
        elif observed_peak <= _SPARSE_OBSERVED_PEAK and (
            change_pct is None or abs(change_pct) < abs(_RISK_CYCLE_DECLINE)
        ):
            out.append(
                replace(
                    evidence,
                    state="naturally_sparse",
                    reason=(
                        "Observed counts stayed sparse without crossing the relative "
                        "decline threshold."
                    ),
                )
            )
        elif recent_change_pct is not None and recent_change_pct <= _RISK_RECENT_DECLINE:
            out.append(
                replace(
                    evidence,
                    state="declining",
                    reason=(
                        "Population is declining, but the combined at-risk threshold "
                        "was not crossed."
                    ),
                )
            )
        elif recent_change_pct is not None and recent_change_pct >= _RECOVERY_RECENT_GROWTH:
            # Growth past the same band. This branch used to be absent, so the
            # `declining` else-arm swallowed it: the test was on
            # `abs(recent_change_pct)`, which reported a species up 74% in the
            # recent window as declining (#220). This is the field a reader
            # uses to decide what to protect, so it was naming the winners.
            out.append(
                replace(
                    evidence,
                    state="growing",
                    reason="Recent population is up at least 15%.",
                )
            )
        else:
            out.append(
                replace(
                    evidence,
                    state="stable",
                    reason=(
                        "Recent movement stayed inside the 15% warning band, but the "
                        "population is still well down across the cycle."
                        if change_pct is not None and change_pct <= _RISK_CYCLE_DECLINE
                        else "Recent movement stayed inside the 15% warning band."
                    ),
                )
            )

    state_order = {
        "at_risk": 0,
        "declining": 1,
        "recovering": 2,
        "growing": 3,
        "stable": 4,
        "naturally_sparse": 5,
        "insufficient": 6,
        "stale": 7,
        "missing": 8,
    }
    out.sort(key=lambda row: (state_order[row.state], row.name))
    return out


# ---------- payload + cache invalidation for tests ----------


def _drift_entry(d: SpeciesDrift) -> dict[str, Any]:
    """Serialize one drift row with a JSON-safe ``deltaRel``.

    A species that grew from a zero baseline has an infinite relative delta —
    correct for ranking (it tops the boom list) but not JSON-serializable, and
    Starlette's ``JSONResponse`` rejects ``inf`` outright. We emit ``deltaRel:
    null`` with ``fromZero: true`` in that case so every consumer (the SPA, the
    Jinja card, the markdown fallback) can render a "new" badge instead of a
    bogus ``inf%``.
    """
    finite = math.isfinite(d.delta_rel)
    return {
        "name": d.name,
        "first": d.first,
        "latest": d.latest,
        "deltaRel": d.delta_rel if finite else None,
        "fromZero": not finite,
    }


def _risk_entry(row: SpeciesRisk) -> dict[str, Any]:
    return {
        "name": row.name,
        "state": row.state,
        "warning": row.warning,
        "reason": row.reason,
        "current": row.current,
        "changeAbs": row.change_abs,
        "changePct": row.change_pct,
        "recentChangePct": row.recent_change_pct,
        "observedPeak": row.observed_peak,
        "firstTime": row.first_time,
        "latestTime": row.latest_time,
        "recentFromTime": row.recent_from_time,
        "observationSeconds": (
            row.latest_time - row.first_time
            if row.latest_time is not None and row.first_time is not None
            else None
        ),
        "sampleCount": row.sample_count,
        "freshness": row.freshness,
    }


def _clear_caches() -> None:
    """Wipe in-process caches — used by the test suite."""
    _worldlayers_cache.clear()
    _specieslist_cache.clear()
    _species_cache.clear()


def build_payload(
    biome_percents: dict[str, float],
    matches: list[EcoregionMatch],
    boom: list[SpeciesDrift],
    bust: list[SpeciesDrift],
    *,
    species_seen: int,
    species_with_drift: int,
    admin_available: bool,
    source_url: str,
    saltwater_percent: float = 0.0,
    freshwater_percent: float = 0.0,
    species_risk: list[SpeciesRisk] | None = None,
    species_unfetched: int = 0,
) -> dict[str, Any]:
    """Assemble the serializable payload used by both the Jinja card and JSON content.

    ``saltwater_percent`` / ``freshwater_percent`` come from the non-biome
    ``World`` / ``Moisture`` layers (see ``extract_water_percents``). They
    reclassify most of the old grey "unclassified" gap into named water slices
    (eco-app#82). Both default to 0.0, so a caller that passes only biome
    percents keeps the pre-#82 behaviour (raw biome sum, everything else grey).
    """
    raw_sum = sum(biome_percents.values())
    normalized = normalize_vector(biome_percents)

    # Derive the coastal/shallow-water remainder: the salt water the biome
    # layers *didn't* already tag as (deep) ocean. Clamp at zero so a server
    # whose Ocean biomes exceed reported SaltWater never emits a negative slice.
    ocean_biome = sum(biome_percents.get(k, 0.0) for k in _OCEAN_BIOME_KEYS)
    coastal_water = max(0.0, saltwater_percent - ocean_biome)
    fresh_water = max(0.0, freshwater_percent)
    water_classified = coastal_water + fresh_water

    # `rawSumPercent` stays the pure biome sum (the "% that is a named biome");
    # `classifiedPercent` additionally credits the water slices, so the grey
    # remainder shrinks to genuine mountain/transitional terrain.
    classified = min(100.0, raw_sum + water_classified)
    unclassified = max(0.0, 100.0 - classified)

    biomes: list[dict[str, Any]] = [
        {
            "name": key,
            "display": BIOME_DISPLAY.get(key, key),
            "percent": biome_percents.get(key, 0.0),
            "sharePercent": normalized.get(key, 0.0) * 100.0,
            "color": BIOME_COLORS.get(key, "#888888"),
        }
        for key in BIOME_LAYERS
    ]
    # Append the derived water slices so the donut + legend render them as
    # first-class classified area. They carry sharePercent 0 because they're
    # not part of the WWF biome-shape vector the ecoregion matcher compares.
    for key, display, color, pct in (
        (_SALTWATER_KEY, _SALTWATER_DISPLAY, _SALTWATER_COLOR, coastal_water),
        (_FRESHWATER_KEY, _FRESHWATER_DISPLAY, _FRESHWATER_COLOR, fresh_water),
    ):
        biomes.append(
            {
                "name": key,
                "display": display,
                "percent": pct,
                "sharePercent": 0.0,
                "color": color,
                "isWater": True,
            }
        )

    risk_rows = species_risk or []
    risk_counts: dict[str, int] = {}
    for row in risk_rows:
        risk_counts[row.state] = risk_counts.get(row.state, 0) + 1

    return {
        "view": "eco_ecoregion",
        "sourceUrl": source_url,
        "biomes": biomes,
        "unclassifiedPercent": unclassified,
        "rawSumPercent": raw_sum,
        "classifiedPercent": classified,
        "ecoregionMatches": [
            {
                "name": m.name,
                "description": m.description,
                "similarity": m.similarity,
            }
            for m in matches
        ],
        "drift": {
            "boom": [_drift_entry(d) for d in boom],
            "bust": [_drift_entry(d) for d in bust],
            "speciesSeen": species_seen,
            "speciesWithDrift": species_with_drift,
            # Species the fetch budget ran out on, so a partial drift is never silent.
            "speciesUnfetched": species_unfetched,
        },
        "speciesRisk": {
            "sourceState": (
                "unavailable"
                if not admin_available
                else "insufficient"
                if not any(
                    row.state not in {"missing", "stale", "insufficient"} for row in risk_rows
                )
                else "available"
            ),
            "threshold": {
                "currentPeakRatio": _RISK_CURRENT_PEAK_RATIO,
                "cycleDeclinePct": _RISK_CYCLE_DECLINE,
                "recentDeclinePct": _RISK_RECENT_DECLINE,
                "minSamples": _MIN_RISK_SAMPLES,
                "minObservationSeconds": _MIN_OBSERVATION_SECONDS,
                "staleLagSeconds": _STALE_LAG_SECONDS,
                "recentGrowthPct": _RECOVERY_RECENT_GROWTH,
                "description": (
                    "Warn when current population falls to 25% of its own observed peak, "
                    "or when a 30% cycle decline is still down 15% in the recent window. "
                    "Movement past the same 15% band the other way is `growing`, not "
                    "`declining` — the classifier tests the signed change. "
                    "No universal healthy population baseline is assumed."
                ),
            },
            "counts": risk_counts,
            "atRiskCount": sum(1 for row in risk_rows if row.warning),
            "species": [_risk_entry(row) for row in risk_rows],
        },
        "adminAvailable": admin_available,
    }


# Sequential per-species calls on a cold cache outran the 120s MCP transport.
# Bounded fan-out under one budget. teable:coilyco/eco-app#8321.
_SPECIES_CONCURRENCY = 8
_SPECIES_BUDGET_S = 30.0


async def _fetch_species_series(
    base_url: str, names: list[str], api_key: str
) -> tuple[dict[str, list[tuple[int, float]]], int]:
    """Fetch every species' samples within the budget. Returns the series that
    arrived and how many species the budget ran out on."""
    gate = asyncio.Semaphore(_SPECIES_CONCURRENCY)

    async def one(name: str) -> tuple[str, list[tuple[int, float]] | None]:
        async with gate:
            try:
                return name, await fetch_species_samples(base_url, name, api_key)
            except httpx.HTTPError:
                return name, None

    tasks = [asyncio.create_task(one(name)) for name in names]
    if not tasks:
        return {}, 0
    done, pending = await asyncio.wait(tasks, timeout=_SPECIES_BUDGET_S)
    for task in pending:
        task.cancel()
    series: dict[str, list[tuple[int, float]]] = {}
    for task in tasks:
        if task in done:
            name, samples = task.result()
            if samples:
                series[name] = samples
    return series, len(pending)


async def gather_ecoregion_payload(
    info_url: str,
    *,
    api_key: str | None,
    regions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Orchestrator: fetch everything, tolerate admin-endpoint failure.

    The public worldlayers endpoint is required. Admin endpoints
    (specieslist + species) are best-effort — if no API key is configured or
    the server returns a 4xx/5xx, the drift strip renders its empty state
    instead of the whole tool failing.
    """
    base_url = _base_url_from_info_url(info_url)
    if regions is None:
        regions = _load_ecoregions_bundled()

    categories = await fetch_worldlayers(base_url)
    biomes_raw = extract_biome_percents(categories)
    water = extract_water_percents(categories)
    normalized = normalize_vector(biomes_raw)
    matches = top_ecoregions(normalized, regions)

    boom: list[SpeciesDrift] = []
    bust: list[SpeciesDrift] = []
    species_seen = 0
    species_with_drift = 0
    admin_available = False
    species_risk: list[SpeciesRisk] = []
    species_unfetched = 0

    if api_key:
        try:
            names = await fetch_specieslist(base_url, api_key)
            admin_available = True
            series, species_unfetched = await _fetch_species_series(base_url, names, api_key)
            species_seen = len(series)
            boom, bust = rank_drift(series)
            species_with_drift = len(boom) + len(bust)
            species_risk = classify_species_risk(series, expected_species=names)
        except httpx.HTTPError:
            admin_available = False

    return build_payload(
        biomes_raw,
        matches,
        boom,
        bust,
        species_seen=species_seen,
        species_with_drift=species_with_drift,
        admin_available=admin_available,
        species_unfetched=species_unfetched,
        source_url=info_url,
        saltwater_percent=water["saltwater"],
        freshwater_percent=water["freshwater"],
        species_risk=species_risk,
    )
