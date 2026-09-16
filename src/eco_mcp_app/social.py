"""Community activity surface from play, arrivals, and reputation exports.

The Eco action exporter ships the three inputs this surface needs alongside the
economic actions, so no new C# mod is required:

* ``Play`` records activity ticks.
* ``FirstLogin`` records new arrivals.
* ``ReputationTransfer`` records who gives reputation to whom.

``ChatSent`` is deliberately not ingested. Player-authored chat has a different
privacy and retention boundary from server-observed activity, and eco-app#185
removes that data plane entirely.

Player identities remain redacted on the public surface. Numeric citizen ids
are joined through the jobs mod's ``/api/v1/citizens`` endpoint, then exposed as
stable non-reversible ``player-<hash>`` handles. Names in the clear require both
the ``reveal_names`` tool argument and the ``ECO_SOCIAL_ALLOW_NAMES`` deployment
gate.
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

PLAY_ACTION = "Play"
FIRST_LOGIN_ACTION = "FirstLogin"
REPUTATION_ACTION = "ReputationTransfer"
SOCIAL_ACTION_TYPES = (PLAY_ACTION, FIRST_LOGIN_ACTION, REPUTATION_ACTION)

DEFAULT_CACHE_TTL_S = float(os.environ.get("ECO_SOCIAL_CACHE_TTL", "60"))
MAX_ROWS_PER_ACTION = int(os.environ.get("ECO_SOCIAL_MAX_ROWS", "500000"))
MAX_NEW_ARRIVALS = int(os.environ.get("ECO_SOCIAL_ARRIVALS", "60"))
# The reputation graph grows with the square of the citizen count, so it is the
# other array here that runs away. See eco-app#6076.
MAX_REPUTATION_EDGES = int(os.environ.get("ECO_SOCIAL_REPUTATION_EDGES", "120"))
SECONDS_PER_DAY = 86400.0
NAMES_ALLOW_ENV = "ECO_SOCIAL_ALLOW_NAMES"

_ACTIVITY_CITIZEN = ("Citizen", "Player", "User")
# The live exporter emits the `Reputation*`-prefixed names first in each tuple;
# the bare names behind them are older/other shapes kept for compatibility. The
# prefixed names lead because a generic `Citizen` / `Player` column in this
# export need not be the giver (#260).
_REP_GIVER = (
    "ReputationSender",
    "Citizen",
    "Giver",
    "GiverCitizen",
    "FromCitizen",
    "Sender",
    "Player",
)
_REP_RECEIVER = (
    "ReputationReceiver",
    "ReceiverCitizen",
    "Receiver",
    "TargetCitizen",
    "Target",
    "ToCitizen",
    "ReceiverPlayer",
)
_REP_AMOUNT = (
    "ReputationAmountTransferred",
    "Amount",
    "Reputation",
    "ReputationAmount",
    "Value",
    "Delta",
)
# Eco records the magnitude and the direction of a reputation transfer in two
# columns. Without the sign, praise and dogpiling both read as positive weight.
_REP_SIGN = ("ReputationTransferredSign", "ReputationSign", "Sign")
_NEGATIVE_SIGNS = frozenset({"-", "-1", "-1.0", "negative", "neg", "down", "false"})

_social_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=64, ttl=DEFAULT_CACHE_TTL_S)


def names_allowed() -> bool:
    """Return whether the deployment explicitly allows clear player names."""
    return (os.environ.get(NAMES_ALLOW_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


def effective_show_names(reveal_names: bool) -> bool:
    """Require both the caller request and the deployment gate."""
    return bool(reveal_names) and names_allowed()


def hash_handle(identity: str) -> str:
    """Return a stable, non-reversible public handle for one identity."""
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]
    return f"player-{digest}"


def _cache_key(base_url: str, api_key: str | None, show_names: bool) -> str:
    token = hashlib.sha256((api_key or "").encode()).hexdigest()[:12]
    return f"{base_url}|{token}|{int(show_names)}"


@dataclass
class _RepEdge:
    time_s: float
    day: float
    giver_id: str
    receiver_id: str
    amount: float


@dataclass
class _ActivityEvent:
    time_s: float
    day: float
    citizen_id: str
    kind: str


@dataclass
class SocialSurface:
    """Redaction-aware community activity surface."""

    fetched_at_iso: str
    source_base_url: str
    redacted: bool = True
    per_type_counts: dict[str, int] = field(default_factory=dict)
    total_reputation_transfers: int = 0
    total_first_logins: int = 0
    total_play_events: int = 0
    play_by_day: list[tuple[int, int]] = field(default_factory=list)
    first_logins_by_day: list[tuple[int, int]] = field(default_factory=list)
    new_arrivals: list[dict[str, Any]] = field(default_factory=list)
    reputation_edges: list[dict[str, Any]] = field(default_factory=list)
    top_reputation_givers: list[tuple[str, float]] = field(default_factory=list)
    # Header of the ReputationTransfer export, recorded so an unrecognised
    # giver / receiver column can be fixed from the warning alone (#227).
    reputation_columns_seen: list[str] = field(default_factory=list)
    top_reputation_receivers: list[tuple[str, float]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fetchedAtISO": self.fetched_at_iso,
            "sourceBaseUrl": self.source_base_url,
            "redacted": self.redacted,
            "perTypeCounts": dict(self.per_type_counts),
            "totalReputationTransfers": self.total_reputation_transfers,
            "totalFirstLogins": self.total_first_logins,
            "totalPlayEvents": self.total_play_events,
            "playByDay": [[d, c] for d, c in self.play_by_day],
            "firstLoginsByDay": [[d, c] for d, c in self.first_logins_by_day],
            "newArrivals": list(self.new_arrivals),
            "reputationEdges": list(self.reputation_edges),
            "topReputationGivers": [[n, a] for n, a in self.top_reputation_givers],
            "reputationColumnsSeen": list(self.reputation_columns_seen),
            "topReputationReceivers": [[n, a] for n, a in self.top_reputation_receivers],
            "warnings": list(self.warnings),
        }


def _mk_pick(header: list[str]) -> Any:
    """Build a header-keyed cell picker with row realignment."""
    col = {name: i for i, name in enumerate(header)}

    def pick(row: list[str], idx: list[int], *candidates: str) -> str:
        for candidate in candidates:
            column = col.get(candidate)
            if column is not None and idx[column] < len(row):
                value = row[idx[column]].strip()
                if value:
                    return value
        return ""

    return pick


def _clean_id(value: str) -> str:
    """Keep plausible citizen ids or names and reject row-shift artifacts."""
    cleaned = (value or "").strip()
    if not cleaned:
        return ""
    if _INT_RE.match(cleaned):
        return cleaned
    if _NONSENSE_KEY_RE.match(cleaned):
        return ""
    return cleaned


def _apply_sign(amount: float, sign: str) -> float:
    """Apply a separate direction column to a magnitude-only amount.

    The exporter splits a reputation transfer into an unsigned
    ``ReputationAmountTransferred`` and a ``ReputationTransferredSign``. An
    amount that already carries its own sign is left alone, so a future export
    that folds the two together does not get negated twice.
    """
    token = (sign or "").strip().lower()
    if not token or amount < 0:
        return amount
    if token in _NEGATIVE_SIGNS or token.startswith("-"):
        return -amount
    return amount


def parse_reputation_rows(
    rows: Iterable[list[str]],
    surface: SocialSurface,
    edges: list[_RepEdge],
    max_rows: int = MAX_ROWS_PER_ACTION,
) -> int:
    """Fold ``ReputationTransfer`` rows into raw directed edges."""
    iterator = iter(rows)
    try:
        header = next(iterator)
    except StopIteration:
        return 0
    pick = _mk_pick(header)
    # Remember what the export actually offers. When none of the candidate
    # giver columns match, naming the columns that *are* there is the whole
    # difference between "extend the list" and "go probe the live server"
    # (#227).
    surface.reputation_columns_seen = [c for c in header if c]

    consumed = 0
    for row in iterator:
        if not row:
            continue
        if consumed >= max_rows:
            surface.warnings.append(
                f"{REPUTATION_ACTION}: truncated at {max_rows} rows (late-cycle size cap)"
            )
            break
        idx = _corrected_index(header, row)
        try:
            time_s = float(pick(row, idx, "Time") or "0")
        except ValueError:
            time_s = 0.0
        try:
            amount = float(pick(row, idx, *_REP_AMOUNT) or "0")
        except ValueError:
            amount = 0.0
        amount = _apply_sign(amount, pick(row, idx, *_REP_SIGN))
        edges.append(
            _RepEdge(
                time_s=time_s,
                day=time_s / SECONDS_PER_DAY,
                giver_id=_clean_id(pick(row, idx, *_REP_GIVER)),
                receiver_id=_clean_id(pick(row, idx, *_REP_RECEIVER)),
                amount=amount,
            )
        )
        consumed += 1

    surface.per_type_counts[REPUTATION_ACTION] = (
        surface.per_type_counts.get(REPUTATION_ACTION, 0) + consumed
    )
    return consumed


def parse_activity_rows(
    action_name: str,
    rows: Iterable[list[str]],
    surface: SocialSurface,
    events: list[_ActivityEvent],
    max_rows: int = MAX_ROWS_PER_ACTION,
) -> int:
    """Fold ``Play`` or ``FirstLogin`` rows into raw activity events."""
    iterator = iter(rows)
    try:
        header = next(iterator)
    except StopIteration:
        return 0
    pick = _mk_pick(header)
    kind = "firstlogin" if action_name == FIRST_LOGIN_ACTION else "play"

    consumed = 0
    for row in iterator:
        if not row:
            continue
        if consumed >= max_rows:
            surface.warnings.append(
                f"{action_name}: truncated at {max_rows} rows (late-cycle size cap)"
            )
            break
        idx = _corrected_index(header, row)
        try:
            time_s = float(pick(row, idx, "Time") or "0")
        except ValueError:
            time_s = 0.0
        events.append(
            _ActivityEvent(
                time_s=time_s,
                day=time_s / SECONDS_PER_DAY,
                citizen_id=_clean_id(pick(row, idx, *_ACTIVITY_CITIZEN)),
                kind=kind,
            )
        )
        consumed += 1

    surface.per_type_counts[action_name] = surface.per_type_counts.get(action_name, 0) + consumed
    return consumed


class _Redactor:
    def __init__(self, name_map: dict[str, str], show_names: bool) -> None:
        self.name_map = name_map
        self.show_names = show_names

    def label(self, citizen_id: str) -> str:
        if not citizen_id:
            return ""
        if not _INT_RE.match(citizen_id):
            return citizen_id if self.show_names else hash_handle(citizen_id)
        identity = self.name_map.get(citizen_id) or f"Citizen #{citizen_id}"
        return identity if self.show_names else hash_handle(identity)


def build_surface(
    surface: SocialSurface,
    edges: list[_RepEdge],
    activity: list[_ActivityEvent],
    name_map: dict[str, str],
    show_names: bool,
) -> None:
    """Resolve identities and roll raw activity into public aggregates."""
    redactor = _Redactor(name_map, show_names)
    surface.redacted = not show_names

    play_day: dict[int, int] = defaultdict(int)
    login_day: dict[int, int] = defaultdict(int)
    for event in activity:
        if event.kind == "firstlogin":
            login_day[int(event.day)] += 1
        else:
            play_day[int(event.day)] += 1
    surface.total_play_events = sum(1 for event in activity if event.kind == "play")
    surface.total_first_logins = sum(1 for event in activity if event.kind == "firstlogin")
    surface.play_by_day = sorted(play_day.items())
    surface.first_logins_by_day = sorted(login_day.items())
    arrivals = sorted(
        (event for event in activity if event.kind == "firstlogin"),
        key=lambda event: event.time_s,
        reverse=True,
    )
    # Say so. This was the one silent truncation in the tool suite: a caller
    # read 60 rows with no way to know 124 existed. See #267.
    if len(arrivals) > MAX_NEW_ARRIVALS:
        surface.warnings.append(
            f"newArrivals: showing {MAX_NEW_ARRIVALS:,} of {len(arrivals):,} rows, "
            "most recent first (totalFirstLogins counts every one)"
        )
        arrivals = arrivals[:MAX_NEW_ARRIVALS]
    surface.new_arrivals = [
        {"label": redactor.label(event.citizen_id) or "-", "day": int(event.day)}
        for event in arrivals
    ]

    surface.total_reputation_transfers = len(edges)
    edge_amount: dict[tuple[str, str], float] = defaultdict(float)
    edge_count: dict[tuple[str, str], int] = defaultdict(int)
    given: dict[str, float] = defaultdict(float)
    received: dict[str, float] = defaultdict(float)
    for edge in edges:
        giver = redactor.label(edge.giver_id)
        receiver = redactor.label(edge.receiver_id)
        if not giver or not receiver:
            continue
        edge_amount[(giver, receiver)] += edge.amount
        edge_count[(giver, receiver)] += 1
        given[giver] += edge.amount
        received[receiver] += edge.amount

    if edges and not edge_amount:
        no_giver = sum(1 for edge in edges if not edge.giver_id)
        no_receiver = sum(1 for edge in edges if not edge.receiver_id)
        missing = "giver" if no_giver >= no_receiver else "receiver"
        columns = _REP_GIVER if missing == "giver" else _REP_RECEIVER
        seen = ", ".join(surface.reputation_columns_seen) or "none"
        surface.warnings.append(
            f"{REPUTATION_ACTION}: {len(edges):,} transfer(s) parsed but the "
            f"{missing} column was not recognized (tried {', '.join(columns)}; "
            f"the export carries: {seen}). Add the right column to the candidate "
            "list in social.py to light the reputation graph up."
        )

    ranked: list[dict[str, Any]] = sorted(
        (
            {
                "source": source,
                "target": target,
                "amount": edge_amount[(source, target)],
                "count": edge_count[(source, target)],
            }
            for source, target in edge_amount
        ),
        key=lambda edge: abs(edge["amount"]),
        reverse=True,
    )
    # Say so, the same way newArrivals does. Largest by absolute amount first,
    # so a truncated graph keeps the edges that carry the weight.
    if len(ranked) > MAX_REPUTATION_EDGES:
        surface.warnings.append(
            f"reputationEdges: showing {MAX_REPUTATION_EDGES:,} of {len(ranked):,} "
            "rows, largest by amount first"
        )
        ranked = ranked[:MAX_REPUTATION_EDGES]
    surface.reputation_edges = ranked
    surface.top_reputation_givers = sorted(given.items(), key=lambda row: row[1], reverse=True)
    surface.top_reputation_receivers = sorted(
        received.items(), key=lambda row: row[1], reverse=True
    )


async def fetch_social(
    base_url: str | None = None,
    api_key: str | None = None,
    reveal_names: bool = False,
    cache_ttl_s: float = DEFAULT_CACHE_TTL_S,
    client: httpx.AsyncClient | None = None,
) -> SocialSurface:
    """Fetch the three community activity exports and fold one surface."""
    show_names = effective_show_names(reveal_names)
    normalized = _normalize_admin_base(base_url)
    key = _cache_key(normalized, api_key, show_names)
    if cache_ttl_s > 0:
        cached = _social_cache.get(key)
        if cached is not None:
            return _surface_from_dict(cached)

    surface = SocialSurface(fetched_at_iso=_now_iso(), source_base_url=normalized)
    headers = {"X-API-Key": api_key} if api_key else {}
    edges: list[_RepEdge] = []
    activity: list[_ActivityEvent] = []

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0))
    try:
        for action in SOCIAL_ACTION_TYPES:
            url = f"{normalized}/api/v1/exporter/actions?actionName={action}"
            try:
                await _stream_one_action(http, url, headers, action, surface, edges, activity)
                surface.per_type_counts.setdefault(action, 0)
            except httpx.HTTPStatusError as error:
                surface.warnings.append(f"{action}: HTTP {error.response.status_code}")
            except httpx.HTTPError as error:
                surface.warnings.append(f"{action}: {type(error).__name__}: {error}")

        name_map: dict[str, str] = {}
        if edges or activity:
            name_map = await fetch_citizen_name_map(http, normalized, headers, surface.warnings)
    finally:
        if owns_client:
            await http.aclose()

    build_surface(surface, edges, activity, name_map, show_names)
    if cache_ttl_s > 0:
        _social_cache[key] = surface.to_dict()
    return surface


async def _stream_one_action(
    http: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    action: str,
    surface: SocialSurface,
    edges: list[_RepEdge],
    activity: list[_ActivityEvent],
) -> None:
    """Stream one CSV action in bounded batches and dispatch its parser."""
    remaining = MAX_ROWS_PER_ACTION
    header: list[str] | None = None
    batch: list[list[str]] = []

    def fold(rows: list[list[str]], budget: int) -> int:
        if action == REPUTATION_ACTION:
            return parse_reputation_rows(rows, surface, edges, max_rows=budget)
        return parse_activity_rows(action, rows, surface, activity, max_rows=budget)

    async for row in _stream_csv_rows(http, url, headers):
        if header is None:
            header = row
            batch = [row]
            continue
        batch.append(row)
        if len(batch) >= 1024:
            consumed = fold(batch, remaining)
            remaining -= consumed
            if remaining <= 0:
                return
            batch = [header]
    if header is not None and len(batch) > 1 and remaining > 0:
        fold(batch, remaining)


def _surface_from_dict(data: dict[str, Any]) -> SocialSurface:
    return SocialSurface(
        fetched_at_iso=data["fetchedAtISO"],
        source_base_url=data["sourceBaseUrl"],
        redacted=bool(data.get("redacted", True)),
        per_type_counts=dict(data.get("perTypeCounts", {})),
        total_reputation_transfers=int(data.get("totalReputationTransfers", 0)),
        total_first_logins=int(data.get("totalFirstLogins", 0)),
        total_play_events=int(data.get("totalPlayEvents", 0)),
        play_by_day=[(int(day), int(count)) for day, count in data.get("playByDay", [])],
        first_logins_by_day=[
            (int(day), int(count)) for day, count in data.get("firstLoginsByDay", [])
        ],
        new_arrivals=list(data.get("newArrivals", [])),
        reputation_edges=list(data.get("reputationEdges", [])),
        top_reputation_givers=[
            (name, float(amount)) for name, amount in data.get("topReputationGivers", [])
        ],
        top_reputation_receivers=[
            (name, float(amount)) for name, amount in data.get("topReputationReceivers", [])
        ],
        warnings=list(data.get("warnings", [])),
    )


def social_template_context(
    surface: SocialSurface,
    top_rep: int = 8,
    top_edges: int = 12,
    recent: int = 12,
) -> dict[str, Any]:
    """Shape a compact community summary for MCP hosts."""

    def rep_rows(rows: list[tuple[str, float]]) -> list[dict[str, Any]]:
        top = rows[:top_rep]
        max_amount = max((abs(amount) for _, amount in top), default=0.0) or 1.0
        return [
            {"name": name, "amount": amount, "pct": (abs(amount) / max_amount) * 100.0}
            for name, amount in top
        ]

    empty = (
        surface.total_reputation_transfers == 0
        and surface.total_first_logins == 0
        and surface.total_play_events == 0
    )
    return {
        "empty": empty,
        "redacted": surface.redacted,
        "fetched_at_iso": surface.fetched_at_iso,
        "source_base_url": surface.source_base_url,
        "total_reputation_transfers": surface.total_reputation_transfers,
        "total_first_logins": surface.total_first_logins,
        "total_play_events": surface.total_play_events,
        "per_type_counts": [
            (name, count) for name, count in surface.per_type_counts.items() if count
        ],
        "rep_givers": rep_rows(surface.top_reputation_givers),
        "rep_receivers": rep_rows(surface.top_reputation_receivers),
        "rep_edges": surface.reputation_edges[:top_edges],
        "new_arrivals": surface.new_arrivals[:recent],
        "warnings": list(surface.warnings),
    }


def social_markdown(surface: SocialSurface) -> str:
    """Return a compact markdown summary for non-SPA MCP hosts."""
    if (
        surface.total_reputation_transfers == 0
        and surface.total_first_logins == 0
        and surface.total_play_events == 0
    ):
        return f"**Community activity** - no activity recorded yet ({surface.source_base_url})."
    posture = "names redacted" if surface.redacted else "names shown (operator)"
    lines = [
        f"**Community activity** - {surface.total_play_events:,} play events, "
        f"{surface.total_reputation_transfers:,} reputation transfers, "
        f"{surface.total_first_logins:,} new arrivals, {posture} "
        f"(`{surface.source_base_url}`)",
        "",
    ]
    if surface.top_reputation_receivers:
        top = ", ".join(
            f"{name} ({amount:,.0f})" for name, amount in surface.top_reputation_receivers[:5]
        )
        lines.append(f"- Most-repped: {top}")
    if surface.new_arrivals:
        lines.append(f"- New arrivals: {len(surface.new_arrivals)} recent (from FirstLogin)")
    for warning in surface.warnings:
        lines.append(f"- Warning: {warning}")
    return "\n".join(lines)
