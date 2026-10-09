"""Is the trade ledger keeping up with the world clock? (COI-2067)

The ledger comes from Eco's action exporter and the cycle clock comes from `/info`, two
sources that fail independently. A ledger whose newest trade is days behind
`cycle.daysRunning` reads the same whether nobody traded (a quiet server) or the exporter
stopped writing, and until now nothing in the payload said which question to ask. This
module compares the two and writes the answer into a `ledgerFreshness` block plus one
leading warning, so a stalled exporter cannot hide behind a ledger that merely looks old.

Stateless on purpose: one call cannot tell quiet from stalled, but it can name Eco's own
trade counter beside the ledger so the second read can (see `lagging` below). Anything the
clock could not supply is `None`, never zero.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

# A ledger further behind the cycle clock than this is flagged. A day or two of silence is
# ordinary for a small server, so the warning stays neutral about the cause.
LAG_WARN_DAYS = float(os.environ.get("ECO_LEDGER_LAG_WARN_DAYS", "2"))

_TRADES_RE = re.compile(r"(\d+)\s*trade")


@dataclass(frozen=True)
class WorldClock:
    """What `/info` said about time and trades, or why it said nothing."""

    days_running: int | None
    trades_total: int | None
    error: str | None = None


def read_world_clock(info: dict[str, Any]) -> WorldClock:
    """`DaysRunning` and Eco's own trade counter from a `/info` body, `None` when absent."""
    raw_days = info.get("DaysRunning")
    days: int | None = None
    if raw_days is not None and raw_days != "":
        try:
            days = int(raw_days)
        except (TypeError, ValueError):
            days = None
    match = _TRADES_RE.search(str(info.get("EconomyDesc") or ""))
    return WorldClock(days_running=days, trades_total=int(match.group(1)) if match else None)


def assess(newest_trade_day: float | None, clock: WorldClock) -> tuple[dict[str, Any], str | None]:
    """The `ledgerFreshness` block and the leading warning, if one is owed.

    `status` is one of:

    * `current` - the newest trade is within `LAG_WARN_DAYS` of the cycle clock.
    * `lagging` - further behind. Quiet server or stalled exporter. If `infoTradesTotal`
      rises between two reads while `newestTradeDay` does not, the exporter is stalled.
    * `empty` - the ledger holds no trades.
    * `unverifiable` - the clock is missing (`/info` unreachable or sent no `DaysRunning`),
      so no lag can be computed. Said out loud rather than passed as `current`.
    """
    lag: float | None = None
    if newest_trade_day is not None and clock.days_running is not None:
        lag = round(max(clock.days_running - newest_trade_day, 0.0), 2)

    warning: str | None = None
    counter = (
        f"Eco's own trade counter reads {clock.trades_total:,}"
        if clock.trades_total is not None
        else "Eco's own trade counter is unavailable"
    )
    newest = f"day {newest_trade_day:.2f}" if newest_trade_day is not None else "none"

    if clock.days_running is None:
        status = "unverifiable"
        why = clock.error or "/info sent no DaysRunning"
        warning = (
            f"ledger freshness unverifiable: the cycle clock is unavailable ({why}). "
            f"The newest ledger trade is {newest}. This does not mean the ledger is current."
        )
    elif newest_trade_day is None:
        status = "empty"
        if clock.trades_total:
            warning = (
                f"the ledger holds no trades on cycle day {clock.days_running}, but {counter}. "
                "If that counter is not a leftover from before the cycle rolled, the exporter "
                "is not writing."
            )
    elif lag is not None and lag > LAG_WARN_DAYS:
        status = "lagging"
        warning = (
            f"the newest ledger trade is day {newest_trade_day:.2f} but the cycle is on day "
            f"{clock.days_running}, {lag:.1f} days behind. Either nobody has traded since or "
            f"the exporter stopped writing. {counter}: if it rises while the newest trade day "
            "stays put across two reads, the exporter is stalled."
        )
    else:
        status = "current"

    block = {
        "status": status,
        "newestTradeDay": None if newest_trade_day is None else round(newest_trade_day, 2),
        "daysRunning": clock.days_running,
        "lagDays": lag,
        "infoReachable": clock.error is None,
        "infoError": clock.error,
        "infoTradesTotal": clock.trades_total,
        "warnAfterDays": LAG_WARN_DAYS,
    }
    return block, warning


def apply_freshness(payload: dict[str, Any], clock: WorldClock) -> str | None:
    """Write `ledgerFreshness` into a ledger-shaped payload, its warning first.

    Returns the warning so the caller can put it in the markdown block too.
    """
    newest = payload.get("newestTradeDay")
    block, warning = assess(None if newest is None else float(newest), clock)
    payload["ledgerFreshness"] = block
    if warning is not None:
        payload.setdefault("warnings", []).insert(0, warning)
    return warning
