"""Civics & governance surface — fold the civic action exporters + daily
series into an elections / turnout / demographics / settlements report.

The DiscordLink-parity epic (eco-app#37) wants elections, votes, laws, and
demographics on the website instead of Discord embeds. The live civic
endpoints (`/api/v1/elections*`, `/api/v1/laws`) already feed the
`get_government` org-chart card — that's the *current-state* snapshot
(who holds which title, which laws are active right now). This module is the
*history + trend* half: it consumes the civic **action exporters** the server
already ships (one CSV row per civic event) plus a handful of civics/people
daily-count series, so no game restart and no new C# mod.

Two data planes, both already live on the server:

* **Action rows** — `GET /api/v1/exporter/actions?actionName=<Name>` for the
  civic events (see `CIVICS_ACTION_TYPES`). One row per event: a `Vote` cast,
  a `StartElection`, a `BecomeCitizen`, a `SettlementFounded`, etc. `Time` is
  seconds since cycle start (in-game day = `Time / 86400`, the species-CSV
  convention). `Citizen` is a numeric in-game id, joined to a display name via
  the jobs mod's `/api/v1/citizens` surface (`Citizen #<id>` fallback,
  eco-app#5). Rows occasionally carry an undeclared extra tool column that
  shifts every later field; `crafting._corrected_index` absorbs it, so we key
  off the header and never fixed positions.
* **Daily series** — `GET /datasets/get?dataset=<Name>` for the civic counters
  (see `CIVICS_SERIES`). The same names double as datasets on the server (an
  action dataset also exposes a daily count series), so these give turnout /
  demographic / settlement counts *over time* for the trend charts. Fetched
  best-effort: an unknown or empty series is skipped, never fatal.

Everything degrades on the Day-3 sparse state: "no civic events yet" is a
valid report, not an error. Laws-in-effect are **not** derivable from the
action stream (there is no per-law event) — the civics card cross-links the
`get_government` / `/server` law surface for those rather than
fabricating them.

Cache: an in-process `TTLCache` keyed per (base_url, api_key_hash), mirroring
`trades._trades_cache`. The report is viewed in bursts; a short TTL keeps us
off the admin endpoint without going stale.
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
)

# --- Civic action exporters (one CSV row per event) ------------------------
#
# Grouped by the surface they feed. Every name here is an action dataset on
# the live server's `/datasets/flatlist`; the exporter endpoint streams the
# row-level CSV. A disabled / missing action 401/404s and is skipped rather
# than fatal — a partial civic picture is still useful.
ELECTION_ACTIONS: tuple[str, ...] = (
    "StartElection",
    "Vote",
    "DidntVote",
    "JoinOrLeaveElection",
    "WonElection",
    "LostElection",
)
DEMOGRAPHIC_ACTIONS: tuple[str, ...] = (
    "BecomeCitizen",
    "LeaveCitizenship",
    "ResidencyChanged",
    "DemographicChange",
)
SETTLEMENT_ACTIONS: tuple[str, ...] = (
    "SettlementFounded",
    "PlaceNewSettlementFoundation",
    "StartHomestead",
)
CIVICS_ACTION_TYPES: tuple[str, ...] = ELECTION_ACTIONS + DEMOGRAPHIC_ACTIONS + SETTLEMENT_ACTIONS

# --- Civics & people daily-count series (via /datasets/get) ----------------
#
# The trend half: daily counts of the civic actions above, giving turnout /
# demographic / settlement movement *over time* for the charts. These names
# resolve as datasets on the server (an action dataset also exposes a daily
# count series). Fetched
# best-effort — an unknown or empty series is skipped, never fatal.
CIVICS_SERIES: tuple[str, ...] = (
    "Vote",
    "DidntVote",
    "BecomeCitizen",
    "LeaveCitizenship",
    "ResidencyChanged",
    "DemographicChange",
    "SettlementFounded",
    "StartHomestead",
    "StartElection",
    "WonElection",
)

DEFAULT_CACHE_TTL_S = float(os.environ.get("ECO_CIVICS_CACHE_TTL", "60"))

# Per-action safety valve, same rationale as trades: 500k rows is well past
# any realistic late-cycle civic log and still sub-second to fold.
MAX_ROWS_PER_ACTION = int(os.environ.get("ECO_CIVICS_MAX_ROWS", "500000"))

# How many individual events we keep per surface for the browsable lists (the
# aggregates always see every row; these caps only bound the shipped lists).
MAX_EVENTS = int(os.environ.get("ECO_CIVICS_EVENTS", "400"))

# Upper bound on the daily-series day range we request. Cycles seldom run past
# a few hundred in-game days; 3650 (10 in-game years) is comfortably generous
# and the endpoint just returns whatever data exists within it.
SERIES_DAY_END = int(os.environ.get("ECO_CIVICS_SERIES_DAY_END", "3650"))

# In-game day length in real seconds — matches the species population CSV's
# `seconds / 86400` convention and the trades ledger's day index.
SECONDS_PER_DAY = 86400.0

_civics_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=64, ttl=DEFAULT_CACHE_TTL_S)


def _cache_key(base_url: str, api_key: str | None) -> str:
    token = hashlib.sha256((api_key or "").encode()).hexdigest()[:12]
    return f"{base_url}|{token}"


# Per-action candidate columns for the "subject" of a civic event — the thing
# the event is *about* (an election's name, a settlement's name, a demographic
# label). The exact civic CSV headers weren't captured live this cycle
# (eco-app#7), so we scan a generous candidate list header-keyed and take the
# first that yields a real name. A miss just leaves the subject blank; the
# citizen actor + per-type counts carry the report regardless.
_SUBJECT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "StartElection": ("ElectionName", "Election", "Name", "Title", "Proposition"),
    "Vote": ("ElectionName", "Election", "Name", "Title", "Proposition"),
    "DidntVote": ("ElectionName", "Election", "Name", "Title", "Proposition"),
    "JoinOrLeaveElection": ("ElectionName", "Election", "Name", "Title"),
    "WonElection": ("ElectionName", "Election", "Name", "Title", "Position"),
    "LostElection": ("ElectionName", "Election", "Name", "Title", "Position"),
    "BecomeCitizen": ("SettlementName", "Settlement", "Name"),
    "LeaveCitizenship": ("SettlementName", "Settlement", "Name"),
    "ResidencyChanged": ("SettlementName", "Settlement", "Property", "Name"),
    "DemographicChange": ("Demographic", "DemographicName", "Name"),
    "SettlementFounded": ("SettlementName", "Settlement", "Name"),
    "PlaceNewSettlementFoundation": ("SettlementName", "Settlement", "Name"),
    "StartHomestead": ("SettlementName", "Settlement", "Name"),
}

# Columns naming the acting citizen, best-effort across civic action shapes.
_CITIZEN_CANDIDATES: tuple[str, ...] = ("Citizen", "Voter", "Player", "User", "Founder")


@dataclass
class _CivicEvent:
    """One civic event before id->name resolution. Ids stay numeric here."""

    action: str
    time_s: float
    day: float
    citizen_id: str
    subject: str
    location: str


@dataclass
class CivicsReport:
    """Civic history + trend surface. JSON-serializable (camelCase `to_dict`)."""

    fetched_at_iso: str
    source_base_url: str
    total_events: int = 0
    # action -> rows folded (0 = fetched-but-empty).
    per_action_counts: dict[str, int] = field(default_factory=dict)

    # --- Elections & turnout ---
    elections_started: int = 0
    elections_won: int = 0
    elections_lost: int = 0
    votes_cast: int = 0
    abstentions: int = 0
    # Votes whose actor id the citizens join could not name. Counted, never
    # ranked — an unresolved id on the voter roll was an invented player (#223).
    unresolved_voter_ids: int = 0
    # Recent StartElection events: {subject, proposer, day}.
    recent_elections: list[dict[str, Any]] = field(default_factory=list)
    # Recent WonElection events: {subject, winner, day}.
    recent_outcomes: list[dict[str, Any]] = field(default_factory=list)
    # (name, votes) most-active voters, heaviest first.
    top_voters: list[tuple[str, int]] = field(default_factory=list)

    # --- Demographics ---
    # Event counts — these reconcile with perActionCounts. The civic exports
    # repeat whole runs of identical rows (a day-19 joined block appeared three
    # times verbatim), so an event count is not a headcount: Sirens reported
    # citizensGained 371 on a server that has seen 165 distinct players ever
    # (#224). The distinct counts below are the headcount.
    citizens_gained: int = 0
    citizens_lost: int = 0
    distinct_citizens_gained: int = 0
    distinct_citizens_lost: int = 0
    # Exact-duplicate demographic rows dropped from `recent_demographics`.
    duplicate_demographic_events: int = 0
    residency_moves: int = 0
    demographic_changes: int = 0
    # Recent BecomeCitizen / LeaveCitizenship events: {name, day, kind}.
    recent_demographics: list[dict[str, Any]] = field(default_factory=list)

    # --- Settlements ---
    # A founding and a foundation placement are different events: the stake is
    # a precursor that may never become a settlement. Summing them reported 17
    # settlements on a server with 5 (#225), so they are counted separately.
    settlements_founded: int = 0
    settlement_foundations_placed: int = 0
    homesteads_started: int = 0
    # Recent settlement / homestead events: {subject, founder, day, kind}.
    recent_settlements: list[dict[str, Any]] = field(default_factory=list)

    # --- Trend (daily-count series) ---
    # series name -> [(day, value), ...] sorted by day. Empty series omitted.
    trend: dict[str, list[tuple[float, float]]] = field(default_factory=dict)

    # Actions whose exporter could not be read (401, 404, transport error).
    # A scalar derived only from these is unmeasured, not zero (#259).
    unavailable_actions: list[str] = field(default_factory=list)

    warnings: list[str] = field(default_factory=list)

    @property
    def admin_available(self) -> bool:
        """Whether any civic exporter answered at all.

        `get_region` carries this state too; without it a
        caller could not tell a quiet server from a rejected admin key (#259).
        """
        return len(self.unavailable_actions) < len(CIVICS_ACTION_TYPES)

    def _measured(self, *actions: str) -> bool:
        """True when at least one action backing a scalar was actually read."""
        unavailable = set(self.unavailable_actions)
        return any(action not in unavailable for action in actions)

    def _opt(self, value: int, *actions: str) -> int | None:
        """A count, or None when every action behind it failed to fetch."""
        return value if self._measured(*actions) else None

    @property
    def turnout_rate(self) -> float | None:
        """Votes cast / (cast + abstained), or None when nobody was eligible."""
        if not self._measured("Vote", "DidntVote"):
            return None
        total = self.votes_cast + self.abstentions
        return (self.votes_cast / total) if total else None

    @property
    def net_citizens(self) -> int | None:
        if not self._measured("BecomeCitizen", "LeaveCitizenship"):
            return None
        return self.citizens_gained - self.citizens_lost

    @property
    def net_distinct_citizens(self) -> int | None:
        """Net headcount — distinct people, not repeated events (#224)."""
        if not self._measured("BecomeCitizen", "LeaveCitizenship"):
            return None
        return self.distinct_citizens_gained - self.distinct_citizens_lost

    def to_dict(self) -> dict[str, Any]:
        rate = self.turnout_rate
        return {
            "view": "eco_civics",
            "fetchedAtISO": self.fetched_at_iso,
            "sourceBaseUrl": self.source_base_url,
            "totalEvents": self.total_events if self.admin_available else None,
            "perActionCounts": dict(self.per_action_counts),
            # Whether any civic exporter answered. Without this a rejected admin
            # key and a genuinely quiet server were indistinguishable, and the
            # zeros below read as a measurement (#259).
            "adminAvailable": self.admin_available,
            "unavailableActions": list(self.unavailable_actions),
            "measurementNote": (
                "A null scalar means its exporter could not be read; a zero means the "
                "exporter answered and recorded no events. `unavailableActions` names "
                "the exporters behind every null. Note that `trend` is sourced "
                "separately and can carry data while these scalars are null."
            )
            if self.unavailable_actions
            else "",
            "electionsStarted": self._opt(self.elections_started, "StartElection"),
            "electionsWon": self._opt(self.elections_won, "WonElection"),
            "electionsLost": self._opt(self.elections_lost, "LostElection"),
            "votesCast": self._opt(self.votes_cast, "Vote"),
            "abstentions": self._opt(self.abstentions, "DidntVote"),
            "turnoutRate": rate,
            "recentElections": list(self.recent_elections),
            "recentOutcomes": list(self.recent_outcomes),
            "topVoters": [[n, c] for n, c in self.top_voters],
            "citizensGained": self._opt(self.citizens_gained, "BecomeCitizen"),
            "citizensLost": self._opt(self.citizens_lost, "LeaveCitizenship"),
            "netCitizens": self.net_citizens,
            # People, not events. `citizensGained` counts BecomeCitizen rows,
            # which the exporter repeats; these count who (#224).
            "distinctCitizensGained": self._opt(self.distinct_citizens_gained, "BecomeCitizen"),
            "distinctCitizensLost": self._opt(self.distinct_citizens_lost, "LeaveCitizenship"),
            "netDistinctCitizens": self.net_distinct_citizens,
            "duplicateDemographicEvents": self.duplicate_demographic_events,
            "demographicsNote": (
                "citizensGained / citizensLost count exporter events and reconcile with "
                "perActionCounts. The exporter repeats identical rows, so use "
                "distinctCitizensGained / distinctCitizensLost for a headcount."
            ),
            "residencyMoves": self._opt(self.residency_moves, "ResidencyChanged"),
            "demographicChanges": self._opt(self.demographic_changes, "DemographicChange"),
            "recentDemographics": list(self.recent_demographics),
            "settlementsFounded": self._opt(self.settlements_founded, "SettlementFounded"),
            "settlementFoundationsPlaced": self._opt(
                self.settlement_foundations_placed, "PlaceNewSettlementFoundation"
            ),
            "homesteadsStarted": self._opt(self.homesteads_started, "StartHomestead"),
            "recentSettlements": list(self.recent_settlements),
            "trend": {name: [[d, v] for d, v in points] for name, points in self.trend.items()},
            "warnings": list(self.warnings),
        }


def parse_civic_rows(
    action_name: str,
    rows: Iterable[list[str]],
    parsed: list[_CivicEvent],
    per_action_counts: dict[str, int],
    warnings: list[str],
    max_rows: int = MAX_ROWS_PER_ACTION,
) -> int:
    """Fold one civic action's CSV rows into `parsed` (raw ids) + bump the count.

    Returns the number of data rows consumed (excluding the header). Header-
    keyed and defensive: `_corrected_index` absorbs any undeclared extra tool
    column so picks land on the right value even on shifted rows.
    """
    it = iter(rows)
    try:
        header = next(it)
    except StopIteration:
        return 0

    col = {name: i for i, name in enumerate(header)}
    subject_cols = _SUBJECT_CANDIDATES.get(action_name, ("Name",))

    def pick(row: list[str], idx: list[int], *candidates: str) -> str:
        for c in candidates:
            j = col.get(c)
            if j is not None and idx[j] < len(row):
                v = row[idx[j]].strip()
                if v:
                    return v
        return ""

    consumed = 0
    for row in it:
        if not row:
            continue
        if consumed >= max_rows:
            warnings.append(f"{action_name}: truncated at {max_rows} rows (late-cycle size cap)")
            break
        idx = _corrected_index(header, row)

        try:
            time_s = float(pick(row, idx, "Time") or "0")
        except ValueError:
            time_s = 0.0

        parsed.append(
            _CivicEvent(
                action=action_name,
                time_s=time_s,
                day=time_s / SECONDS_PER_DAY,
                citizen_id=pick(row, idx, *_CITIZEN_CANDIDATES),
                # Kept raw: these columns key by id on the live exports, and
                # `_clean_name` blanked every one of them (#223). `_subject`
                # resolves or reports the id at build time.
                subject=pick(row, idx, *subject_cols),
                location=pick(row, idx, "ActionLocation", "Position"),
            )
        )
        consumed += 1

    per_action_counts[action_name] = per_action_counts.get(action_name, 0) + consumed
    return consumed


def _actor(cid: str, name_map: dict[str, str]) -> tuple[str | None, str | None]:
    """Resolve an actor id to ``(display name, unresolved id)``.

    Exactly one of the two is ever set. An id the citizens join does not know
    is **not** a person, and formatting it as ``Citizen #<id>`` invented one:
    on Sirens the unresolved ids turned out to be *election title* ids
    (456767 = "La Croisée des Bois Mayor"), so the proposer list and the voter
    roll gained players who do not exist (#223). An id we cannot name is
    reported as an id.
    """
    if not cid:
        return None, None
    if not _INT_RE.match(cid):
        return cid, None  # already a name, or an artifact we leave verbatim
    name = name_map.get(cid)
    if name is not None:
        return name, None
    return None, cid


def _subject(raw: str, name_map: dict[str, str]) -> tuple[str | None, str | None]:
    """Resolve an event's subject the same way, ``(name, unresolved id)``.

    Every `subject` / `settlement` field came back as an empty string because
    `_clean_name` blanks a bare number and the civic exports key these columns
    by id. Blank told the reader "no subject"; the truth was "an id we did not
    resolve" (#223).
    """
    value = (raw or "").strip()
    if not value:
        return None, None
    # A position triple is a genuine misalignment artifact; drop it.
    if "," in value and _NONSENSE_KEY_RE.match(value):
        return None, None
    return _actor(value, name_map)


def build_report(
    parsed: list[_CivicEvent],
    report: CivicsReport,
    name_map: dict[str, str],
) -> None:
    """Resolve ids and roll `parsed` up into the report's counts + event lists."""
    report.total_events = len(parsed)

    voter_counts: dict[str, int] = defaultdict(int)
    # Distinct people behind the demographic event stream, and the exact
    # duplicate rows we drop from the browsable list (#224).
    gained_people: set[str] = set()
    lost_people: set[str] = set()
    seen_demographics: set[tuple[str, int, bool, str]] = set()
    # Newest first so the "recent" lists lead with the latest events.
    ordered = sorted(parsed, key=lambda e: e.time_s, reverse=True)

    for e in ordered:
        # An id the citizens join cannot name is reported as an id, never as a
        # "Citizen #<id>" person (#223).
        name, actor_id = _actor(e.citizen_id, name_map)
        subject, subject_id = _subject(e.subject, name_map)
        day = int(e.day)
        if e.action == "StartElection":
            report.elections_started += 1
            if len(report.recent_elections) < MAX_EVENTS:
                report.recent_elections.append(
                    {
                        "subject": subject,
                        "subjectId": subject_id,
                        "proposer": name,
                        "proposerId": actor_id,
                        "day": day,
                    }
                )
        elif e.action == "WonElection":
            report.elections_won += 1
            if len(report.recent_outcomes) < MAX_EVENTS:
                report.recent_outcomes.append(
                    {
                        "subject": subject,
                        "subjectId": subject_id,
                        "winner": name,
                        "winnerId": actor_id,
                        "day": day,
                    }
                )
        elif e.action == "LostElection":
            report.elections_lost += 1
        elif e.action == "Vote":
            report.votes_cast += 1
            if name:
                voter_counts[name] += 1
            elif actor_id:
                # Counted, but never ranked as a person — an unresolved id on
                # the voter roll inflated turnout with a non-existent player.
                report.unresolved_voter_ids += 1
        elif e.action == "DidntVote":
            report.abstentions += 1
        elif e.action in ("BecomeCitizen", "LeaveCitizenship"):
            joined = e.action == "BecomeCitizen"
            if joined:
                report.citizens_gained += 1
                gained_people.add(e.citizen_id or f"?{len(gained_people)}")
            else:
                report.citizens_lost += 1
                lost_people.add(e.citizen_id or f"?{len(lost_people)}")
            # The exporter repeats whole runs of identical rows — a day-19
            # joined block appeared three times verbatim — so the same person,
            # day and kind is one event to show, not three (#224).
            row_key = (e.citizen_id, day, joined, e.subject)
            if row_key in seen_demographics:
                report.duplicate_demographic_events += 1
            elif len(report.recent_demographics) < MAX_EVENTS:
                seen_demographics.add(row_key)
                report.recent_demographics.append(
                    {
                        "name": name,
                        "nameId": actor_id,
                        "day": day,
                        "kind": "joined" if joined else "left",
                        "settlement": subject,
                        "settlementId": subject_id,
                    }
                )
        elif e.action == "ResidencyChanged":
            report.residency_moves += 1
        elif e.action == "DemographicChange":
            report.demographic_changes += 1
        elif e.action in ("SettlementFounded", "PlaceNewSettlementFoundation", "StartHomestead"):
            kind = {
                "SettlementFounded": "settlement",
                # A staked foundation, not a settlement. It may never become one.
                "PlaceNewSettlementFoundation": "foundation",
                "StartHomestead": "homestead",
            }[e.action]
            if e.action == "SettlementFounded":
                report.settlements_founded += 1
            elif e.action == "PlaceNewSettlementFoundation":
                report.settlement_foundations_placed += 1
            else:
                report.homesteads_started += 1
            if len(report.recent_settlements) < MAX_EVENTS:
                report.recent_settlements.append(
                    {
                        "subject": subject,
                        "subjectId": subject_id,
                        "founder": name,
                        "founderId": actor_id,
                        "day": day,
                        "kind": kind,
                    }
                )

    report.distinct_citizens_gained = len(gained_people)
    report.distinct_citizens_lost = len(lost_people)
    if report.duplicate_demographic_events:
        report.warnings.append(
            f"{report.duplicate_demographic_events} duplicate demographic event(s) were "
            "dropped from recentDemographics; the exporter repeats identical rows. "
            "citizensGained counts events, distinctCitizensGained counts people "
            "(eco-app#224)."
        )

    report.top_voters = sorted(voter_counts.items(), key=lambda kv: kv[1], reverse=True)
    if report.unresolved_voter_ids:
        report.warnings.append(
            f"{report.unresolved_voter_ids} vote(s) name an actor id the citizens join could "
            "not resolve; they are counted in votesCast but excluded from topVoters rather "
            "than listed as a 'Citizen #<id>' player (eco-app#223)."
        )


@dataclass
class CivicsFetch:
    """Raw fetch result — parsed civic events + series + the id->name map."""

    normalized_base_url: str
    parsed: list[_CivicEvent] = field(default_factory=list)
    series: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    name_map: dict[str, str] = field(default_factory=dict)
    per_action_counts: dict[str, int] = field(default_factory=dict)
    # Actions whose exporter could not be read at all, so a caller can tell an
    # unmeasured scalar from a measured zero (#259).
    unavailable_actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _parse_series_points(data: Any) -> list[tuple[float, float]]:
    """Tolerate the several shapes `/datasets/get` returns for a series.

    Mirrors the `_fetch_dataset` parsers in currency.py and climate.py: a list
    of `{Time, Value}` dicts, a list of `[time, value]` pairs, or a
    `{"Times": [...], "Values": [...]}` wrapper.
    """
    out: list[tuple[float, float]] = []
    if isinstance(data, list):
        for pt in data:
            if isinstance(pt, dict):
                t = pt.get("Time", pt.get("time"))
                v = pt.get("Value", pt.get("value"))
            elif isinstance(pt, list | tuple) and len(pt) >= 2:
                t, v = pt[0], pt[1]
            else:
                continue
            if t is None or v is None:
                continue
            try:
                out.append((float(t), float(v)))
            except (TypeError, ValueError):
                continue
    elif isinstance(data, dict):
        times = data.get("Times")
        values = data.get("Values")
        if isinstance(times, list) and isinstance(values, list):
            for t, v in zip(times, values, strict=False):
                try:
                    out.append((float(t), float(v)))
                except (TypeError, ValueError):
                    continue
        else:
            for pt in data.get("Points") or []:
                try:
                    out.append((float(pt["Time"]), float(pt["Value"])))
                except (KeyError, TypeError, ValueError):
                    continue
    return out


async def _fetch_series(
    client: httpx.AsyncClient,
    base: str,
    name: str,
    headers: dict[str, str],
) -> list[tuple[float, float]]:
    """Fetch a single civics daily-count series; [] on any non-200 / shape surprise.

    Best-effort by design — an unknown series name or an empty one is normal
    on the Day-3 sparse state, not an error. Points are converted to
    (in-game-day, value) so the trend chart's x-axis matches the ledger's day
    index.
    """
    try:
        r = await client.get(
            f"{base}/datasets/get",
            params={"dataset": name, "dayStart": 0, "dayEnd": max(SERIES_DAY_END, 1)},
            headers=headers,
        )
        if r.status_code != 200:
            return []
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return []
    points = _parse_series_points(data)
    # Times are seconds since cycle start (daily samples); collapse to day.
    return [(t / SECONDS_PER_DAY, v) for t, v in points]


async def fetch_civics_raw(
    base_url: str | None = None,
    api_key: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> CivicsFetch:
    """Stream the civic action CSVs + daily series and resolve citizen names.

    `client` is injectable so tests can hand in a pre-stubbed httpx client.
    When omitted we build one with a 30 s timeout — late-cycle CSVs take a beat.
    """
    normalized = _normalize_admin_base(base_url)
    headers = {"X-API-Key": api_key} if api_key else {}
    parsed: list[_CivicEvent] = []
    per_action_counts: dict[str, int] = {}
    unavailable_actions: list[str] = []
    warnings: list[str] = []

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0))
    try:
        for action in CIVICS_ACTION_TYPES:
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
                        consumed = parse_civic_rows(
                            action, batch, parsed, per_action_counts, warnings, max_rows=remaining
                        )
                        remaining -= consumed
                        if remaining <= 0:
                            break
                        batch = [header]
                if header is not None and len(batch) > 1 and remaining > 0:
                    parse_civic_rows(
                        action, batch, parsed, per_action_counts, warnings, max_rows=remaining
                    )
                # Record fetched-but-empty so the UI tells "empty" from "errored".
                per_action_counts.setdefault(action, 0)
            except httpx.HTTPStatusError as e:
                warnings.append(f"{action}: HTTP {e.response.status_code}")
                unavailable_actions.append(action)
            except httpx.HTTPError as e:
                warnings.append(f"{action}: {type(e).__name__}: {e}")
                unavailable_actions.append(action)

        # Daily-count series for the trend charts, best-effort. Empty series are
        # dropped so the payload only carries lines worth drawing.
        series: dict[str, list[tuple[float, float]]] = {}
        for name in CIVICS_SERIES:
            points = await _fetch_series(http, normalized, name, headers)
            if points:
                series[name] = points

        name_map: dict[str, str] = {}
        if parsed:
            name_map = await fetch_citizen_name_map(http, normalized, headers, warnings)
    finally:
        if owns_client:
            await http.aclose()

    return CivicsFetch(
        normalized_base_url=normalized,
        parsed=parsed,
        series=series,
        name_map=name_map,
        per_action_counts=per_action_counts,
        unavailable_actions=unavailable_actions,
        warnings=warnings,
    )


async def fetch_civics(
    base_url: str | None = None,
    api_key: str | None = None,
    cache_ttl_s: float = DEFAULT_CACHE_TTL_S,
    client: httpx.AsyncClient | None = None,
) -> CivicsReport:
    """Stream the civic exporters + series and fold them into a single report."""
    normalized = _normalize_admin_base(base_url)
    key = _cache_key(normalized, api_key)
    if cache_ttl_s > 0:
        cached = _civics_cache.get(key)
        if cached is not None:
            return _report_from_dict(cached)

    fetch = await fetch_civics_raw(base_url=base_url, api_key=api_key, client=client)
    report = CivicsReport(fetched_at_iso=_now_iso(), source_base_url=fetch.normalized_base_url)
    report.per_action_counts = dict(fetch.per_action_counts)
    report.unavailable_actions = list(fetch.unavailable_actions)
    report.warnings = list(fetch.warnings)
    report.trend = dict(fetch.series)
    build_report(fetch.parsed, report, fetch.name_map)

    if cache_ttl_s > 0:
        _civics_cache[key] = report.to_dict()
    return report


def _report_from_dict(data: dict[str, Any]) -> CivicsReport:
    """Rehydrate a cached report dict back into a CivicsReport."""

    def _int(key: str) -> int:
        """Cached nulls mean "not measured"; the zero is re-nulled on output."""
        return int(data.get(key) or 0)

    report = CivicsReport(
        fetched_at_iso=data["fetchedAtISO"],
        source_base_url=data["sourceBaseUrl"],
        unavailable_actions=list(data.get("unavailableActions", [])),
        total_events=_int("totalEvents"),
        per_action_counts=dict(data.get("perActionCounts", {})),
        elections_started=_int("electionsStarted"),
        elections_won=_int("electionsWon"),
        elections_lost=_int("electionsLost"),
        votes_cast=_int("votesCast"),
        abstentions=_int("abstentions"),
        recent_elections=list(data.get("recentElections", [])),
        recent_outcomes=list(data.get("recentOutcomes", [])),
        top_voters=[(n, int(c)) for n, c in data.get("topVoters", [])],
        citizens_gained=_int("citizensGained"),
        citizens_lost=_int("citizensLost"),
        distinct_citizens_gained=_int("distinctCitizensGained"),
        distinct_citizens_lost=_int("distinctCitizensLost"),
        duplicate_demographic_events=_int("duplicateDemographicEvents"),
        residency_moves=_int("residencyMoves"),
        demographic_changes=_int("demographicChanges"),
        recent_demographics=list(data.get("recentDemographics", [])),
        settlements_founded=_int("settlementsFounded"),
        settlement_foundations_placed=_int("settlementFoundationsPlaced"),
        homesteads_started=_int("homesteadsStarted"),
        recent_settlements=list(data.get("recentSettlements", [])),
        trend={
            name: [(float(d), float(v)) for d, v in points]
            for name, points in data.get("trend", {}).items()
        },
        warnings=list(data.get("warnings", [])),
    )
    return report


def civics_template_context(
    report: CivicsReport,
    top_voters: int = 8,
    recent: int = 8,
) -> dict[str, Any]:
    """Shape for the MCP `_meta.ui` Jinja card. Product UX is the SPA — this
    card is only the in-chat fragment, so it stays a compact summary."""
    rate = report.turnout_rate
    max_voter = max((c for _, c in report.top_voters[:top_voters]), default=0) or 1
    return {
        "empty": report.total_events == 0,
        "fetched_at_iso": report.fetched_at_iso,
        "source_base_url": report.source_base_url,
        "total_events": report.total_events,
        "elections_started": report.elections_started,
        "elections_won": report.elections_won,
        "votes_cast": report.votes_cast,
        "abstentions": report.abstentions,
        "turnout_pct": (rate * 100.0) if rate is not None else None,
        "citizens_gained": report.citizens_gained,
        "citizens_lost": report.citizens_lost,
        "net_citizens": report.net_citizens,
        "residency_moves": report.residency_moves,
        "settlements_founded": report.settlements_founded,
        "settlement_foundations_placed": report.settlement_foundations_placed,
        "homesteads_started": report.homesteads_started,
        "recent_elections": report.recent_elections[:recent],
        "recent_outcomes": report.recent_outcomes[:recent],
        "recent_settlements": report.recent_settlements[:recent],
        "top_voters": [
            {"name": n, "count": c, "pct": (c / max_voter) * 100.0}
            for n, c in report.top_voters[:top_voters]
        ],
        "warnings": list(report.warnings),
    }


def civics_markdown(report: CivicsReport) -> str:
    """Compact markdown summary for MCP hosts without the SPA / card."""
    if not report.admin_available:
        # "No civic events" is a plausible-looking answer for a quiet server,
        # which is exactly why an auth failure must not be reported as one.
        return (
            f"**Civics & governance** — could not read any civic exporter on "
            f"`{report.source_base_url}`, so nothing here was measured. "
            f"({'; '.join(report.warnings[:3])})"
        )
    if report.total_events == 0:
        return f"**Civics & governance** — no civic events recorded yet ({report.source_base_url})."
    lines = [
        f"**Civics & governance** — {report.total_events:,} civic events "
        f"(`{report.source_base_url}`)",
        "",
    ]
    rate = report.turnout_rate
    if report.votes_cast or report.abstentions:
        turnout = f" ({rate * 100:.0f}% turnout)" if rate is not None else ""
        lines.append(
            f"- Turnout: {report.votes_cast:,} votes cast, "
            f"{report.abstentions:,} abstentions{turnout}"
        )
    if report.elections_started or report.elections_won:
        lines.append(
            f"- Elections: {report.elections_started:,} started, {report.elections_won:,} won"
        )
    if report.citizens_gained or report.citizens_lost:
        lines.append(
            # Lead with the headcount; the event totals repeat rows (#224).
            f"- Demographics: +{report.distinct_citizens_gained:,} / "
            f"-{report.distinct_citizens_lost:,} citizens "
            f"(net {report.net_distinct_citizens or 0:+,}) from "
            f"{report.citizens_gained + report.citizens_lost:,} events, "
            f"{report.residency_moves:,} residency moves"
        )
    if (
        report.settlements_founded
        or report.settlement_foundations_placed
        or report.homesteads_started
    ):
        lines.append(
            f"- Settlements: {report.settlements_founded:,} founded, "
            f"{report.settlement_foundations_placed:,} foundations staked, "
            f"{report.homesteads_started:,} homesteads"
        )
    for w in report.warnings:
        lines.append(f"- ⚠ {w}")
    return "\n".join(lines)
