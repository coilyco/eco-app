"""Trade history for one store over a time window, and the window every answer names.

A member asks "what sold the most at <store> in the last 2 days" or "who is the top
buyer of <item>", and an answer that does not say which stretch of trades it covers
gives a different number each time it is asked (COI-3316). So every ledger answer
carries a `window`, and the store, item and window filters rebuild the aggregates over
the trades they select instead of describing the whole ledger.

A trade row names no store, only where it happened (`ActionLocation`, block coordinates)
and who owned the shop. The store's given name lives in the live exporter, so a named
store is joined to its trades through that location. Only detailed trades take part:
older hourly rollups carry no attributable store, item or party (eco-app#132).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

import httpx

from .crafting import _normalize_admin_base, _now_iso
from .ledger_window import WindowRequest, describe_window, resolve_bounds
from .store_match import RosterStore, StoreMatch, resolve_stores
from .trades import (
    TradesLedger,
    _cache_key,
    _label,
    _ParsedTrade,
    _trades_cache,
    build_ledger,
    fetch_parsed_trades,
)

_COORD = re.compile(r"-?\d+")


def coordinates(text: str) -> str:
    """`x,y,z` from either side's spelling of a block position, empty when it has none."""
    numbers = _COORD.findall(text or "")
    return ",".join(numbers) if len(numbers) >= 3 else ""


def live_location(value: Any) -> str:
    if isinstance(value, dict):
        try:
            return ",".join(str(round(float(value[axis]))) for axis in ("x", "y", "z"))
        except (KeyError, TypeError, ValueError):
            return ""
    return coordinates(value) if isinstance(value, str) else ""


def roster_from_stores(
    live: Iterable[dict[str, Any]], parsed: Iterable[_ParsedTrade], name_map: dict[str, str]
) -> list[RosterStore]:
    """Live named stores, then every shop the trades show that the live list lacks, by owner."""
    roster: list[RosterStore] = []
    seen_locations: set[str] = set()
    for store in live:
        location = live_location(store.get("location"))
        roster.append(
            RosterStore(
                name=(store.get("name") or "").strip(),
                owner=(store.get("owner") or "").strip(),
                location=location,
            )
        )
        if location:
            seen_locations.add(location)
    history: dict[str, str] = {}
    for t in parsed:
        if t.is_rollup or not t.shop_owner_id:
            continue
        location = coordinates(t.location)
        if location and location not in seen_locations and location not in history:
            history[location] = _label(t.shop_owner_id, name_map)
    roster.extend(RosterStore(name="", owner=owner, location=loc) for loc, owner in history.items())
    return roster


def _belongs(trade: _ParsedTrade, stores: list[RosterStore], name_map: dict[str, str]) -> bool:
    here = coordinates(trade.location)
    owner = _label(trade.shop_owner_id, name_map) if trade.shop_owner_id else ""
    for store in stores:
        if store.location:
            if here == store.location:
                return True
        elif owner and owner.lower() == store.owner.lower():
            return True
    return False


def select_trades(
    parsed: Iterable[_ParsedTrade],
    name_map: dict[str, str],
    *,
    start: float | None,
    end: float | None,
    stores: list[RosterStore] | None,
    item_ok: Callable[[str], bool] | None,
) -> list[_ParsedTrade]:
    """The detailed trades inside the window, at the matched stores, for the item."""
    chosen: list[_ParsedTrade] = []
    for t in parsed:
        if t.is_rollup:
            continue
        if start is not None and t.day < start:
            continue
        if end is not None and t.day > end:
            continue
        if item_ok is not None and not item_ok(t.item):
            continue
        if stores is not None and not _belongs(t, stores, name_map):
            continue
        chosen.append(t)
    return chosen


def _store_filter(query: str, match: StoreMatch, source: str) -> dict[str, Any]:
    block: dict[str, Any] = {
        "query": query,
        "rosterSource": source,
        "matched": [
            {"name": s.name or None, "owner": s.owner or None, "label": s.label}
            for s in match.matched
        ],
        "resolvedFrom": match.resolved_from,
        "suggestions": [
            {"label": s.label, "closeness": round(score, 2)} for score, s in match.suggestions or []
        ],
    }
    return block


async def _live_stores(
    http: httpx.AsyncClient, normalized: str, api_key: str | None
) -> list[dict[str, Any]] | None:
    """The live store list, or None when the exporter does not serve it."""
    headers = {"X-API-Key": api_key} if api_key else {}
    try:
        resp = await http.get(f"{normalized}/api/v1/stores", headers=headers)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    return data if isinstance(data, list) else None


async def fetch_trade_history(
    base_url: str | None = None,
    api_key: str | None = None,
    *,
    store: str | None = None,
    window: WindowRequest | None = None,
    item_ok: Callable[[str], bool] | None = None,
    item_label: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> TradesLedger:
    """A ledger rebuilt over the trades a store, window and item select.

    Every aggregate (top buyers, by item, by units) describes that selection, and the
    ledger carries the `window` and `storeFilter` that say so. The anchor of a relative
    window is the newest trade on record.
    """
    window = window or WindowRequest()
    normalized = _normalize_admin_base(base_url)
    cache_key = f"{_cache_key(normalized, api_key)}:parsed"
    cached = _trades_cache.get(cache_key)
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0))
    try:
        if cached is not None:
            fetch = cached["fetch"]
        else:
            fetch = await fetch_parsed_trades(base_url=base_url, api_key=api_key, client=http)
            _trades_cache[cache_key] = {"fetch": fetch}
        live = await _live_stores(http, normalized, api_key) if store else None
    finally:
        if owns_client:
            await http.aclose()

    detailed = [t for t in fetch.parsed if not t.is_rollup]
    newest = max((t.day for t in detailed), default=None)
    start, end = resolve_bounds(window, newest)

    stores: list[RosterStore] | None = None
    store_block: dict[str, Any] | None = None
    notes: list[str] = []
    if store:
        roster = roster_from_stores(live or [], fetch.parsed, fetch.name_map)
        match = resolve_stores(store, roster)
        stores = match.matched
        source = "live store list and trade owners" if live is not None else "trade owners only"
        store_block = _store_filter(store, match, source)
        if live is None:
            notes.append(
                "the live store list is unavailable, so a store is matched by its owner's "
                "handle only, not by its given name"
            )
        if not match.matched:
            notes.append(f"no store matched {store!r}, so no trades are shown")
        elif match.resolved_from:
            notes.append(
                f"no store is named {store!r}; showing the closest, {match.matched[0].label}"
            )
        if match.suggestions and not match.matched:
            notes.append("closest names: " + "; ".join(s.label for _, s in match.suggestions))

    chosen = select_trades(
        fetch.parsed, fetch.name_map, start=start, end=end, stores=stores, item_ok=item_ok
    )
    ledger = TradesLedger(fetched_at_iso=_now_iso(), source_base_url=fetch.normalized_base_url)
    ledger.per_type_counts = dict(fetch.per_type_counts)
    ledger.warnings = list(fetch.warnings)
    build_ledger(chosen, ledger, fetch.name_map)
    ledger.newest_trade_day = newest
    ledger.scoped_before_item = (
        len(
            select_trades(
                fetch.parsed, fetch.name_map, start=start, end=end, stores=stores, item_ok=None
            )
        )
        if item_ok is not None
        else None
    )
    ledger.window = describe_window(window, start, end, chosen, newest)
    ledger.store_filter = store_block
    scope: list[str] = []
    if store:
        scope.append("the matched store")
    if item_label:
        scope.append(f"item {item_label}")
    if window.asked:
        scope.append(ledger.window["label"])
    ledger.aggregate_scope = (
        "topBuyers, topSellers, byItem, byItemUnits and the totals describe only: "
        + ", ".join(scope)
        if scope
        else None
    )
    ledger.warnings.extend(notes)
    return ledger
