"""`get_stores` by a store's given name, and the one-line answer to "who owns it".

Trade history knows a store by its owner and its block type, never by the name its
owner gave it ("Fern & Co"). Only the live exporter carries that name, so the
`store` argument is resolved through the live roster first, as `get_trades` does
(`trade_history.py`), and the matched stores are joined back to their history rows
by location. COI-3407.
"""

from __future__ import annotations

from typing import Any

import httpx

from .crafting import _normalize_admin_base
from .store_match import RosterStore, StoreMatch, resolve_stores
from .trade_history import _live_stores, coordinates, roster_from_stores

MAX_REPLY_CHARS = 280


async def live_roster(base_url: str | None, api_key: str | None) -> list[RosterStore] | None:
    """The live named stores, or None when the exporter does not serve them."""
    normalized = _normalize_admin_base(base_url)
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as http:
        live = await _live_stores(http, normalized, api_key)
    return None if live is None else roster_from_stores(live, [], {})


def narrow_to_roster(payload: dict[str, Any], query: str, roster: list[RosterStore]) -> StoreMatch:
    """Keep the history rows of the stores `query` names. A miss leaves the payload alone."""
    match = resolve_stores(query, roster)
    if not match.matched:
        return match
    places = {s.location for s in match.matched if s.location}
    owners = {s.owner.lower() for s in match.matched if s.owner}
    stores = [
        row
        for row in payload.get("stores") or []
        if coordinates(str(row.get("storeKey") or "").split("|", 1)[0]) in places
    ]
    traders = [
        row for row in payload.get("traders") or [] if str(row.get("name")).lower() in owners
    ]
    payload["stores"], payload["traders"] = stores, traders
    payload.setdefault("warnings", []).append(
        f"store {query!r} matched {', '.join(s.label for s in match.matched)} in the live "
        f"store list; showing its {len(stores)} history row(s) and {len(traders)} owner row(s)"
    )
    return match


def owner_reply(match: StoreMatch, payload: dict[str, Any]) -> str | None:
    """One line naming the owner, or None when the question needs the model.

    Null unless exactly one store was named outright: a near-name or a tie is not an
    answer to give without saying so. A store with no history row is not said to have no
    trades: the directory keeps only the top stores by value, so a missing row proves nothing.
    """
    if len(match.matched) != 1 or match.resolved_from:
        return None
    store = match.matched[0]
    if not store.owner:
        return None
    line = f"{store.name or store.label} is owned by {store.owner}."
    rows = payload.get("stores") or []
    if len(rows) == 1:
        trades = int(rows[0].get("tradeCount") or 0)
        line += (
            f" {trades:,} trades on record, the latest on day {int(rows[0].get('lastDay') or 0)}."
        )
    return line if len(line) <= MAX_REPLY_CHARS else None
