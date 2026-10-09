"""Progression / skills history — reconstruct skill trajectories from the
Eco progression action exporters (eco-app#64).

The jobs mod's ``/api/v1/skills`` surface (consumed by ``eco_spec_tracker``,
the ``/jobs`` page) shows *current* skills: who holds which specialty at what
level right now. It says nothing about **how** citizens got there. The
progression action exporters already live on the server carry that history —
every profession gained, specialty gained / lost, level-up, class completed,
and enrollment, each stamped with a citizen id and an in-game time. This module
folds them into a history surface: per-citizen trajectories, server-wide
per-day trends, and the leaderboards that fall out of them.

No new C# mod, no game restart — everything is already exported. We reuse the
same streamed-CSV plumbing the crafting atlas (eco-app#5) and trades ledger
(eco-app#6) built:

* **Streaming.** Progression logs grow all cycle, so we stream-parse via
  ``crafting._stream_csv_rows`` + a batched fold rather than buffering the body.
* **Defensive parsing.** Exporter rows occasionally carry an undeclared extra
  column that shifts every later field; ``crafting._corrected_index`` absorbs
  it. We key off the header, never fixed positions.
* **Numeric ids.** ``Citizen`` is a numeric in-game id; we join it to a name via
  the jobs mod's ``/api/v1/citizens`` surface
  (``crafting.fetch_citizen_name_map``), falling back to ``Citizen #<id>``.
* **Time.** Integer seconds since cycle start (the species-CSV convention,
  ``species.py``) — in-game day = seconds / 86400.

**Column-shape caveat.** The progression exporters were not reachable from the
build container to capture a live header (public server was down, no admin key),
so the skill / profession / level column *names* are best-effort candidates
drawn from Eco's ``GameActions`` field conventions. We pick the first matching
candidate per semantic field and validate its shape, so a differently-named
column degrades to "unknown skill" rather than corrupting the row. The citizen
id, time, and per-action counts are anchored on universal columns and stay
correct regardless. See eco-app#64 — a live capture should confirm the names.

Cache: an in-process ``TTLCache`` keyed per (base_url, api_key_hash), mirroring
``trades._trades_cache``. History is viewed in bursts; a short TTL keeps us off
the admin endpoint without going stale.
"""

from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import httpx
from cachetools import TTLCache

from .crafting import (
    _INT_RE,
    _NONSENSE_KEY_RE,
    _corrected_index,
    _normalize_admin_base,
    _now_iso,
    _stream_csv_rows,
    fetch_citizen_name_map,
    prettify_eco_name,
)

# The seven progression action exporters the survey (#7) flags as overlapping
# the jobs view. Each is fetched from `/api/v1/exporter/actions?actionName=<n>`.
PROGRESSION_ACTION_TYPES = (
    "GainProfession",
    "GainSpecialty",
    "LoseSpecialty",
    "SpecialtyLevelUp",
    "CharacterLevelUp",
    "CompleteClass",
    "EnrollAction",
)

# Normalized event kind per exporter — the SPA / card group and color by this,
# not the raw action name, so a rename upstream only touches this map.
ACTION_KIND: dict[str, str] = {
    "GainProfession": "profession",
    "GainSpecialty": "specialty",
    "LoseSpecialty": "specialty_loss",
    "SpecialtyLevelUp": "specialty_levelup",
    "CharacterLevelUp": "character_levelup",
    "CompleteClass": "class",
    "EnrollAction": "enroll",
}

# Candidate column names for the "which skill/profession/class" field, tried in
# order. Eco's GameActions name these fields after the concept, and the exporter
# serializes the field name as the CSV header. We accept several spellings so a
# version / mod drift degrades to a blank skill rather than a wrong row. See the
# column-shape caveat in the module docstring.
SKILL_COLUMNS = (
    "Specialty",
    "SpecialtyType",
    "Skill",
    "SkillType",
    "SkillName",
    "Profession",
    "ProfessionType",
    "Class",
    "ClassName",
    "Curriculum",
    "Item",
)

# Candidate columns for a numeric level (specialty / character level-ups).
LEVEL_COLUMNS = ("Level", "SkillLevel", "NewLevel", "CharacterLevel", "Star")

DEFAULT_CACHE_TTL_S = float(os.environ.get("ECO_PROGRESSION_CACHE_TTL", "60"))

# Per-action safety valve, same rationale as crafting / trades: 500k rows is
# ~50 MB of CSV, well past the late-cycle estimate and still sub-second to fold.
MAX_ROWS_PER_ACTION = int(os.environ.get("ECO_PROGRESSION_MAX_ROWS", "500000"))

# How many per-citizen trajectory cards ship to the client (busiest first).
MAX_CITIZENS = int(os.environ.get("ECO_PROGRESSION_MAX_CITIZENS", "80"))

# Per-citizen timeline cap — the newest N events per citizen. The derived
# summaries (professions, specialties, level count) see every event regardless.
MAX_TIMELINE_PER_CITIZEN = int(os.environ.get("ECO_PROGRESSION_TIMELINE_ROWS", "60"))

# In-game day length in real seconds, matching the species population CSV's
# `seconds / 86400` convention (species.py, trades.py).
SECONDS_PER_DAY = 86400.0

# Best-effort discovery keywords for the three progression *daily series* the
# survey lists (#7). We scan `/datasets/flatlist` for names containing one of
# these and fold whatever `/datasets/get` returns. Kept progression-specific to
# avoid false positives (e.g. "level" would catch the SeaLevel climate series),
# so an unreachable / mismatched catalog simply yields no series rather than
# garbage — the action-derived trends carry the surface regardless. See eco-app#64.
SERIES_DISCOVERY_KEYWORDS = (
    "specialt",
    "profession",
    "skillrate",
    "skillgain",
    "skillpoint",
    "curriculum",
    "graduat",
    "enroll",
)
MAX_DISCOVERED_SERIES = int(os.environ.get("ECO_PROGRESSION_SERIES", "3"))

_progression_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=64, ttl=DEFAULT_CACHE_TTL_S)


def _cache_key(base_url: str, api_key: str | None) -> str:
    token = hashlib.sha256((api_key or "").encode()).hexdigest()[:12]
    return f"{base_url}|{token}"


@dataclass
class _ParsedEvent:
    """One progression event before id->name resolution. Ids stay numeric."""

    action: str
    kind: str
    time_s: float
    day: float
    citizen_id: str
    skill: str
    level: float | None


@dataclass
class ProgressionHistory:
    """Skill-history surface + derived aggregates. JSON-serializable."""

    fetched_at_iso: str
    source_base_url: str
    total_events: int = 0
    # exporter name -> rows folded (0 = fetched-but-empty).
    per_action_counts: dict[str, int] = field(default_factory=dict)
    # Per-citizen trajectory dicts, busiest first, capped at MAX_CITIZENS. Each
    # is already camelCase for the SPA (see `_citizen_dict`).
    citizens: list[dict[str, Any]] = field(default_factory=list)
    # Server-wide per-day trends: kind -> [(day, count)] sorted by day.
    trends: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    # Leaderboards: (name, count), heaviest first.
    by_specialty: list[tuple[str, int]] = field(default_factory=list)
    by_profession: list[tuple[str, int]] = field(default_factory=list)
    class_completions: list[tuple[str, int]] = field(default_factory=list)
    # (citizen_name, level_up_count), heaviest first.
    top_levelers: list[tuple[str, int]] = field(default_factory=list)
    # First server-wide observation for every gained specialty. This is folded
    # from the uncapped event set before citizen timelines are bounded, so item
    # price-history markers never disappear behind the presentation cap.
    first_specialty_gains: list[dict[str, Any]] = field(default_factory=list)
    # Every specialty anyone has taken, from the uncapped event set (the citizen
    # cards are capped at MAX_CITIZENS, so they cannot answer "was it ever taken").
    # Feeds the tech-progression view, see tech_progression.py (COI-2090).
    specialties_taken: list[dict[str, Any]] = field(default_factory=list)
    # GainSpecialty rows whose skill column was blank or unrecognised, so the
    # count above cannot name them (the column-shape caveat in the module docstring).
    unnamed_specialty_gains: int = 0
    # Best-effort discovered progression daily series: name -> [(day, value)].
    daily_series: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def cache_dict(self) -> dict[str, Any]:
        """``to_dict`` plus the fields the tech-progression view reads. The response
        keeps them out of the top level, techProgression is their one home."""
        return {
            **self.to_dict(),
            "specialtiesTaken": list(self.specialties_taken),
            "unnamedSpecialtyGains": self.unnamed_specialty_gains,
        }

    def select_citizens(self, citizen: str | None) -> list[dict[str, Any]]:
        """The per-citizen cards matching ``citizen``, exact match preferred."""
        wanted = (citizen or "").strip().lower()
        if not wanted:
            return []
        exact = [c for c in self.citizens if str(c.get("name", "")).lower() == wanted]
        if exact:
            return exact
        return [c for c in self.citizens if wanted in str(c.get("name", "")).lower()]

    def to_dict(
        self, *, include_citizens: bool = True, citizen: str | None = None
    ) -> dict[str, Any]:
        """Serialize the history.

        The per-citizen timelines are 266 KB of a 275 KB response on a
        80-citizen server, which put the excellent ~8 KB summary layer behind a
        payload no MCP client can accept (#232). ``include_citizens=False``
        drops them; ``citizen`` keeps just one person's.
        """
        if citizen:
            citizens = self.select_citizens(citizen)
        elif include_citizens:
            citizens = list(self.citizens)
        else:
            citizens = []
        return {
            "fetchedAtISO": self.fetched_at_iso,
            "sourceBaseUrl": self.source_base_url,
            "totalEvents": self.total_events,
            "perActionCounts": dict(self.per_action_counts),
            "citizens": citizens,
            # How many are on the server versus how many this response carries,
            # so a summary answer never reads as "only one citizen levelled".
            "citizensAvailable": len(self.citizens),
            "citizensReturned": len(citizens),
            "trends": {kind: [[d, c] for d, c in points] for kind, points in self.trends.items()},
            "bySpecialty": [[n, c] for n, c in self.by_specialty],
            "byProfession": [[n, c] for n, c in self.by_profession],
            "classCompletions": [[n, c] for n, c in self.class_completions],
            "topLevelers": [[n, c] for n, c in self.top_levelers],
            "firstSpecialtyGains": list(self.first_specialty_gains),
            "dailySeries": {
                name: [[d, v] for d, v in points] for name, points in self.daily_series.items()
            },
            "warnings": list(self.warnings),
        }


def _clean_name(value: str | None) -> str:
    """Blank out values that are really positions / bare numbers where a name
    belongs (a misaligned exporter row artifact — mirrors trades._clean_name)."""
    v = (value or "").strip()
    if not v or _NONSENSE_KEY_RE.match(v):
        return ""
    return v


def parse_progression_rows(
    action_name: str,
    rows: Iterable[list[str]],
    history: ProgressionHistory,
    parsed: list[_ParsedEvent],
    max_rows: int = MAX_ROWS_PER_ACTION,
) -> int:
    """Fold one action's CSV rows into `parsed` (raw ids) and bump per-action count.

    Returns the number of data rows consumed (excluding the header). Aggregates
    needing id->name resolution are computed later, once every action folds.
    """
    it = iter(rows)
    try:
        header = next(it)
    except StopIteration:
        return 0

    col = {name: i for i, name in enumerate(header)}
    kind = ACTION_KIND.get(action_name, action_name)

    def pick(row: list[str], idx: list[int], *candidates: str) -> str:
        for c in candidates:
            j = col.get(c)
            if j is not None and idx[j] < len(row):
                v = row[idx[j]].strip()
                if v:
                    return v
        return ""

    def pick_float(row: list[str], idx: list[int], *candidates: str) -> float | None:
        raw = pick(row, idx, *candidates)
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    consumed = 0
    for row in it:
        if not row:
            continue
        if consumed >= max_rows:
            history.warnings.append(
                f"{action_name}: truncated at {max_rows} rows (late-cycle size cap)"
            )
            break
        idx = _corrected_index(header, row)
        time_s = pick_float(row, idx, "Time") or 0.0

        parsed.append(
            _ParsedEvent(
                action=action_name,
                kind=kind,
                time_s=time_s,
                day=time_s / SECONDS_PER_DAY,
                citizen_id=pick(row, idx, "Citizen"),
                skill=_clean_name(pick(row, idx, *SKILL_COLUMNS)),
                level=pick_float(row, idx, *LEVEL_COLUMNS),
            )
        )
        consumed += 1

    history.per_action_counts[action_name] = (
        history.per_action_counts.get(action_name, 0) + consumed
    )
    return consumed


def _label(cid: str, name_map: dict[str, str]) -> str:
    """id -> display name, `Citizen #<id>` fallback, blank stays blank."""
    if not cid:
        return ""
    if not _INT_RE.match(cid):
        return cid  # already a name, or an artifact we leave verbatim
    name = name_map.get(cid)
    return name if name is not None else f"Citizen #{cid}"


# Kinds that count as a "level up" for the top-levelers leaderboard and the
# per-citizen level-up tally.
_LEVELUP_KINDS = {"specialty_levelup", "character_levelup"}


def _citizen_dict(name: str, events: list[_ParsedEvent]) -> dict[str, Any]:
    """Roll one citizen's chronological events into a trajectory card dict.

    Derived summaries (professions, currently-held specialties, character level,
    level-up count) see *every* event; only the shipped timeline is capped.
    """
    ordered = sorted(events, key=lambda e: e.time_s)

    professions: list[str] = []
    seen_prof: set[str] = set()
    # Specialty gained/lost bookkeeping: a specialty is "current" if its last
    # gain/loss event was a gain. Level is the highest level seen for it.
    spec_held: dict[str, bool] = {}
    spec_level: dict[str, float] = {}
    character_level: float | None = None
    level_ups = 0

    for e in ordered:
        if e.kind == "profession" and e.skill and e.skill not in seen_prof:
            seen_prof.add(e.skill)
            professions.append(e.skill)
        elif e.kind == "specialty" and e.skill:
            spec_held[e.skill] = True
            if e.level is not None:
                spec_level[e.skill] = max(spec_level.get(e.skill, 0.0), e.level)
        elif e.kind == "specialty_loss" and e.skill:
            spec_held[e.skill] = False
        elif e.kind == "specialty_levelup":
            level_ups += 1
            if e.skill and e.level is not None:
                spec_level[e.skill] = max(spec_level.get(e.skill, 0.0), e.level)
        elif e.kind == "character_levelup":
            level_ups += 1
            if e.level is not None:
                character_level = (
                    e.level if character_level is None else max(character_level, e.level)
                )

    held_specialties = [(s, spec_level.get(s)) for s, held in spec_held.items() if held]
    held_specialties.sort(key=lambda sl: (-(sl[1] or 0.0), sl[0]))
    specialties = [
        {"name": s, "pretty": prettify_eco_name(s), "level": lvl} for s, lvl in held_specialties
    ]

    # Newest events ship in the timeline (aggregates already saw them all).
    tail = ordered[-MAX_TIMELINE_PER_CITIZEN:]
    timeline = [
        {
            "day": int(e.day),
            "time": e.time_s,
            "kind": e.kind,
            "skill": e.skill,
            "pretty": prettify_eco_name(e.skill) if e.skill else "",
            "level": e.level,
        }
        for e in reversed(tail)  # newest first
    ]

    return {
        "name": name,
        "eventCount": len(events),
        "firstDay": int(ordered[0].day),
        "lastDay": int(ordered[-1].day),
        "characterLevel": character_level,
        "levelUpCount": level_ups,
        "professions": [{"name": p, "pretty": prettify_eco_name(p)} for p in professions],
        "specialties": specialties,
        "timeline": timeline,
    }


def _specialties_taken(
    first_gains: dict[str, _ParsedEvent],
    ever_taken: dict[str, set[str]],
    per_citizen: dict[str, list[_ParsedEvent]],
) -> list[dict[str, Any]]:
    """One row per specialty anyone has gained, over every event (no citizen cap).

    ``holders`` is citizens whose last gain/loss for it was a gain, so a dropped
    specialty keeps its row (it was taken) with fewer holders.
    """
    holders: dict[str, int] = defaultdict(int)
    for events in per_citizen.values():
        held: dict[str, bool] = {}
        for e in sorted(events, key=lambda ev: ev.time_s):
            if e.skill and e.kind == "specialty":
                held[e.skill] = True
            elif e.skill and e.kind == "specialty_loss":
                held[e.skill] = False
        for skill, is_held in held.items():
            if is_held:
                holders[skill] += 1
    rows: list[dict[str, Any]] = [
        {
            "name": skill,
            "pretty": prettify_eco_name(skill),
            "firstDay": int(first.day),
            "takenBy": len(ever_taken.get(skill, ())),
            "holders": holders.get(skill, 0),
        }
        for skill, first in first_gains.items()
    ]
    rows.sort(key=lambda r: (-r["takenBy"], r["name"]))
    return rows


def build_history(
    parsed: list[_ParsedEvent],
    history: ProgressionHistory,
    name_map: dict[str, str],
) -> None:
    """Resolve ids and roll `parsed` up into trajectories + trends + leaderboards."""
    history.total_events = len(parsed)

    # Server-wide per-day trend buckets, keyed by event kind.
    trend_buckets: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    by_specialty: dict[str, int] = defaultdict(int)
    by_profession: dict[str, int] = defaultdict(int)
    class_completions: dict[str, int] = defaultdict(int)
    first_specialty_gains: dict[str, _ParsedEvent] = {}
    per_citizen: dict[str, list[_ParsedEvent]] = defaultdict(list)
    unnamed_gains = 0
    ever_taken: dict[str, set[str]] = defaultdict(set)

    for e in parsed:
        trend_buckets[e.kind][int(e.day)] += 1
        if e.kind == "specialty" and not e.skill:
            unnamed_gains += 1
        if e.kind == "specialty" and e.skill:
            by_specialty[e.skill] += 1
            if e.citizen_id:
                ever_taken[e.skill].add(e.citizen_id)
            first = first_specialty_gains.get(e.skill)
            if first is None or e.time_s < first.time_s:
                first_specialty_gains[e.skill] = e
        elif e.kind == "profession" and e.skill:
            by_profession[e.skill] += 1
        elif e.kind == "class" and e.skill:
            class_completions[e.skill] += 1
        if e.citizen_id:
            per_citizen[e.citizen_id].append(e)

    history.trends = {
        kind: [(float(day), float(count)) for day, count in sorted(days.items())]
        for kind, days in trend_buckets.items()
    }
    history.by_specialty = sorted(by_specialty.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    history.by_profession = sorted(
        by_profession.items(), key=lambda kv: (kv[1], kv[0]), reverse=True
    )
    history.class_completions = sorted(
        class_completions.items(), key=lambda kv: (kv[1], kv[0]), reverse=True
    )
    history.first_specialty_gains = [
        {
            "skill": event.skill,
            "pretty": prettify_eco_name(event.skill),
            "day": int(event.day),
            "time": event.time_s,
        }
        for event in sorted(first_specialty_gains.values(), key=lambda event: event.time_s)
    ]

    history.unnamed_specialty_gains = unnamed_gains
    history.specialties_taken = _specialties_taken(first_specialty_gains, ever_taken, per_citizen)

    # Per-citizen trajectory cards, busiest first, capped.
    citizen_cards = [
        _citizen_dict(_label(cid, name_map), events) for cid, events in per_citizen.items()
    ]
    citizen_cards.sort(key=lambda c: c["eventCount"], reverse=True)
    if len(citizen_cards) > MAX_CITIZENS:
        history.warnings.append(
            f"citizens truncated to busiest {MAX_CITIZENS} of {len(citizen_cards)} "
            "(trends + leaderboards cover all)"
        )
        citizen_cards = citizen_cards[:MAX_CITIZENS]
    history.citizens = citizen_cards

    history.top_levelers = sorted(
        ((c["name"], c["levelUpCount"]) for c in citizen_cards if c["levelUpCount"]),
        key=lambda kv: kv[1],
        reverse=True,
    )


async def _discover_daily_series(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    warnings: list[str],
) -> dict[str, list[tuple[float, float]]]:
    """Best-effort fetch of the progression daily series via flatlist discovery.

    We don't hard-code series names (they weren't confirmable live, and drift
    across Eco versions), so we scan `/datasets/flatlist` for progression-y
    names and fold whatever `/datasets/get` returns, capped. Any failure is a
    non-fatal empty result — the action-derived trends stand on their own.
    """
    # Imported lazily to avoid a hard climate.py dependency at module import.
    from .climate import _fetch_dataset, _fetch_dataset_flatlist

    try:
        flatlist = await _fetch_dataset_flatlist(client, base_url, headers)
    except httpx.HTTPError as exc:
        warnings.append(f"progression series: flatlist {type(exc).__name__}")
        return {}
    if not flatlist:
        return {}

    matches = [
        name
        for name in flatlist
        if any(kw in str(name).lower() for kw in SERIES_DISCOVERY_KEYWORDS)
    ]
    series: dict[str, list[tuple[float, float]]] = {}
    # A generous day window — the exporter clamps to the real span anyway.
    for name in matches:
        if len(series) >= MAX_DISCOVERED_SERIES:
            break
        dataset = await _fetch_dataset(client, base_url, name, day_end=365, headers=headers)
        if dataset.points:
            series[str(name)] = dataset.points
    return series


async def fetch_history(
    base_url: str | None = None,
    api_key: str | None = None,
    cache_ttl_s: float = DEFAULT_CACHE_TTL_S,
    client: httpx.AsyncClient | None = None,
) -> ProgressionHistory:
    """Stream all progression action CSVs and fold them into a single history.

    `client` is injectable so tests can hand in a pre-stubbed httpx client. When
    omitted we build one with a 30 s timeout — late-cycle CSVs take a beat.
    """
    normalized = _normalize_admin_base(base_url)
    key = _cache_key(normalized, api_key)
    if cache_ttl_s > 0:
        cached = _progression_cache.get(key)
        if cached is not None:
            return _history_from_dict(cached)

    history = ProgressionHistory(fetched_at_iso=_now_iso(), source_base_url=normalized)
    headers = {"X-API-Key": api_key} if api_key else {}
    parsed: list[_ParsedEvent] = []

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0))
    try:
        for action in PROGRESSION_ACTION_TYPES:
            url = f"{normalized}/api/v1/exporter/actions?actionName={action}"
            try:
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
                        consumed = parse_progression_rows(
                            action, batch, history, parsed, max_rows=remaining
                        )
                        remaining -= consumed
                        if remaining <= 0:
                            break
                        batch = [header]
                if header is not None and len(batch) > 1 and remaining > 0:
                    parse_progression_rows(action, batch, history, parsed, max_rows=remaining)
                # Record fetched-but-empty so the UI tells "empty" from "errored".
                history.per_action_counts.setdefault(action, 0)
            except httpx.HTTPStatusError as e:
                history.warnings.append(f"{action}: HTTP {e.response.status_code}")
            except httpx.HTTPError as e:
                history.warnings.append(f"{action}: {type(e).__name__}: {e}")

        name_map: dict[str, str] = {}
        if parsed:
            name_map = await fetch_citizen_name_map(http, normalized, headers, history.warnings)
        build_history(parsed, history, name_map)

        # Best-effort daily series only when the action stream showed activity —
        # a dead server shouldn't pay the flatlist round-trip.
        if parsed:
            history.daily_series = await _discover_daily_series(
                http, normalized, headers, history.warnings
            )
    finally:
        if owns_client:
            await http.aclose()

    if cache_ttl_s > 0:
        _progression_cache[key] = history.cache_dict()
    return history


def _history_from_dict(data: dict[str, Any]) -> ProgressionHistory:
    """Rehydrate a cached history dict back into a ProgressionHistory."""
    return ProgressionHistory(
        fetched_at_iso=data["fetchedAtISO"],
        source_base_url=data["sourceBaseUrl"],
        total_events=int(data["totalEvents"]),
        per_action_counts=dict(data.get("perActionCounts", {})),
        citizens=list(data.get("citizens", [])),
        trends={
            kind: [(float(d), float(c)) for d, c in points]
            for kind, points in data.get("trends", {}).items()
        },
        by_specialty=[(n, int(c)) for n, c in data.get("bySpecialty", [])],
        by_profession=[(n, int(c)) for n, c in data.get("byProfession", [])],
        class_completions=[(n, int(c)) for n, c in data.get("classCompletions", [])],
        top_levelers=[(n, int(c)) for n, c in data.get("topLevelers", [])],
        first_specialty_gains=list(data.get("firstSpecialtyGains", [])),
        specialties_taken=list(data.get("specialtiesTaken", [])),
        unnamed_specialty_gains=int(data.get("unnamedSpecialtyGains", 0)),
        daily_series={
            name: [(float(d), float(v)) for d, v in points]
            for name, points in data.get("dailySeries", {}).items()
        },
        warnings=list(data.get("warnings", [])),
    )


# Human labels for the normalized event kinds — shared by the card + markdown.
KIND_LABELS: dict[str, str] = {
    "profession": "professions gained",
    "specialty": "specialties gained",
    "specialty_loss": "specialties dropped",
    "specialty_levelup": "specialty level-ups",
    "character_levelup": "character level-ups",
    "class": "classes completed",
    "enroll": "enrollments",
}


def history_template_context(
    history: ProgressionHistory,
    top_citizens: int = 12,
    top_skills: int = 10,
    timeline_rows: int = 8,
) -> dict[str, Any]:
    """Shape for the MCP `_meta.ui` Jinja card. Product UX is the SPA — this
    card stays a compact summary: headline counts, a few trajectories, and the
    most-gained specialties."""
    kind_totals = [
        (KIND_LABELS.get(kind, kind), sum(int(c) for _, c in points))
        for kind, points in history.trends.items()
    ]
    kind_totals.sort(key=lambda kv: kv[1], reverse=True)

    specialties = history.by_specialty[:top_skills]
    max_spec = max((c for _, c in specialties), default=0) or 1

    citizens = []
    for c in history.citizens[:top_citizens]:
        recent = c["timeline"][:timeline_rows]
        citizens.append(
            {
                "name": c["name"],
                "characterLevel": c["characterLevel"],
                "levelUpCount": c["levelUpCount"],
                "professions": [p["pretty"] for p in c["professions"]],
                "specialties": [s["pretty"] for s in c["specialties"]],
                "recent": [
                    {
                        "day": ev["day"],
                        "label": KIND_LABELS.get(ev["kind"], ev["kind"]),
                        "skill": ev["pretty"],
                        "level": ev["level"],
                    }
                    for ev in recent
                ],
            }
        )

    return {
        "empty": history.total_events == 0,
        "fetched_at_iso": history.fetched_at_iso,
        "source_base_url": history.source_base_url,
        "total_events": history.total_events,
        "kind_totals": kind_totals,
        "top_specialties": [
            {
                "name": name,
                "pretty": prettify_eco_name(name),
                "count": count,
                "pct": (count / max_spec) * 100.0,
            }
            for name, count in specialties
        ],
        "top_levelers": history.top_levelers[:top_skills],
        "citizens": citizens,
        "warnings": list(history.warnings),
    }


def history_markdown(history: ProgressionHistory) -> str:
    """Compact markdown summary for MCP hosts without the SPA / card."""
    if history.total_events == 0:
        return (
            f"**Progression history** — no progression events recorded yet "
            f"({history.source_base_url})."
        )
    lines = [
        f"**Progression history** — {history.total_events:,} skill events "
        f"(`{history.source_base_url}`)",
        "",
    ]
    kind_totals = sorted(
        (
            (KIND_LABELS.get(kind, kind), sum(int(c) for _, c in points))
            for kind, points in history.trends.items()
        ),
        key=lambda kv: kv[1],
        reverse=True,
    )
    if kind_totals:
        summary = ", ".join(f"{count:,} {label}" for label, count in kind_totals if count)
        lines.append(f"- {summary}")
    if history.by_specialty:
        top = ", ".join(
            f"{prettify_eco_name(name)} ({count:,})" for name, count in history.by_specialty[:5]
        )
        lines.append(f"- Most-gained specialties: {top}")
    if history.top_levelers:
        top = ", ".join(f"{name} ({count:,})" for name, count in history.top_levelers[:3])
        lines.append(f"- Busiest levelers: {top}")
    if history.class_completions:
        top = ", ".join(
            f"{prettify_eco_name(name)} ({count:,})"
            for name, count in history.class_completions[:3]
        )
        lines.append(f"- Classes completed: {top}")
    for w in history.warnings:
        lines.append(f"- ⚠ {w}")
    return "\n".join(lines)
