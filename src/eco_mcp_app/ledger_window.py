"""The time window a trade answer covers, and how a request for one is read (COI-3316).

Pure: no HTTP, no clock. Days are server days, the unit the exporter stamps on a trade.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol


class _Dated(Protocol):
    day: float


UNIT = "server days"
DETAIL_ONLY = "detailed trades only; older hourly rollups carry no store, item or buyer"


@dataclass(frozen=True)
class WindowRequest:
    """What the caller asked for. `days` counts back from the newest trade on record."""

    days: float | None = None
    since_day: float | None = None
    until_day: float | None = None

    @property
    def asked(self) -> bool:
        return not (self.days is None and self.since_day is None and self.until_day is None)


def _number(arguments: dict[str, Any], name: str) -> float | None:
    raw = arguments.get(name)
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number of server days, got {raw!r}") from None
    if value != value or value in (float("inf"), float("-inf")) or value < 0:
        raise ValueError(f"{name} must be a number of server days, 0 or more, got {raw!r}")
    return value


def parse_window(arguments: dict[str, Any]) -> WindowRequest:
    """Read `days`, `since_day` and `until_day`, refusing a combination that has no meaning."""
    days = _number(arguments, "days")
    since = _number(arguments, "since_day")
    until = _number(arguments, "until_day")
    if days is not None and days <= 0:
        raise ValueError("days must be more than 0")
    if days is not None and since is not None:
        raise ValueError("pass days (count back from the newest trade) or since_day, not both")
    if since is not None and until is not None and since > until:
        raise ValueError(f"since_day {since:g} is after until_day {until:g}")
    return WindowRequest(days=days, since_day=since, until_day=until)


def resolve_bounds(
    request: WindowRequest, newest_day: float | None
) -> tuple[float | None, float | None]:
    """The first and last server day a request covers, or None for an open end."""
    end = request.until_day
    if request.days is not None:
        anchor = end if end is not None else newest_day
        if anchor is None:
            return None, end
        return max(anchor - request.days, 0.0), anchor
    return request.since_day, end


def _day(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def describe_window(
    request: WindowRequest,
    start: float | None,
    end: float | None,
    chosen: Sequence[_Dated],
    newest_day: float | None,
) -> dict[str, Any]:
    """The window block every ledger answer carries, with a `label` a reply can cite."""
    first = min((t.day for t in chosen), default=None)
    last = max((t.day for t in chosen), default=None)
    if request.days is not None and end is not None and start is not None:
        label = f"the last {request.days:g} server days (days {_day(start)} to {_day(end)})"
    elif request.asked:
        left = f"day {_day(start)}" if start is not None else "the first trade on record"
        right = f"day {_day(end)}" if end is not None else "the newest trade"
        label = f"{left} to {right}"
    elif first is not None and last is not None:
        label = f"all detailed trades on record (days {_day(first)} to {_day(last)})"
    else:
        label = "no detailed trades on record"
    return {
        "label": label,
        "unit": UNIT,
        "requested": (
            {"days": request.days, "sinceDay": request.since_day, "untilDay": request.until_day}
            if request.asked
            else None
        ),
        "startDay": start,
        "endDay": end,
        "firstTradeDay": first,
        "lastTradeDay": last,
        "newestTradeOnRecordDay": newest_day,
        "basis": DETAIL_ONLY,
    }
