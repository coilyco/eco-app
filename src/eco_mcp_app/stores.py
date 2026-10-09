"""Store & trader directory — history-derived shop profiles (eco-app#50).

DiscordLink's ``Trades <name>`` resolves a search term to a store or a player
and lists that store's live offers, but it never surfaces **who owns the
store** (a confirmed gap in its ``FormatTrades``). eco-app can do the join and
exceed it: the ``CurrencyTrade`` exporter the server already ships carries
``ShopOwner``, ``Buyer``, ``Seller`` and ``ActionLocation`` on every row, so we
can reconstruct, from history alone:

* **Store profiles** — keyed by store (``ActionLocation`` + ``ShopOwner``):
  items traded, buy vs sell mix, price points, total volume + value, unique
  counterparties, and recency (last trade). ``ShopOwner`` resolves to a citizen
  name, falling back to ``Citizen #<id>`` exactly as the crafting atlas does.
* **Trader profiles** — per citizen: what they buy and sell, top items, volume,
  and the stores they operate. This is the history-derived answer to
  ``Trades <player>``.

The buy-vs-sell split is read structurally, **not** from the undecoded
``BoughtOrSold`` enum: for a given store the ``ShopOwner`` is either the
``Seller`` (the store sold — a sell offer filled) or the ``Buyer`` (the store
bought — a buy offer filled). Reading it off the party columns means a wrong
``BoughtOrSold`` decode never corrupts the mix (same defensiveness the ledger
applies, eco-app#6).

The one thing history cannot give is the **current shelf snapshot** — what is
listed right now, at what price. That is the reset-gated stores exporter mod
(``mods/stores``, a sibling issue); once it lands this directory upgrades the
profiles to live. Until then, every profile is derived from trades that already
happened, and an empty history is a valid state, not an error.

We do not re-fetch or re-parse the CSVs: `trades.fetch_parsed_trades` is the
shared spine, so the network + parse + citizen-join happens once and both the
ledger and this directory fold the same rows.
"""

from __future__ import annotations

import os
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import httpx
from cachetools import TTLCache

from .crafting import _normalize_admin_base, _now_iso, prettify_eco_name
from .trades import DEFAULT_CACHE_TTL_S, _label, fetch_parsed_trades

# Same short TTL rationale as the ledger: the directory is viewed in bursts and
# store history moves slowly, so a brief cache keeps us off the admin endpoint.
_CACHE_TTL_S = float(os.environ.get("ECO_STORES_CACHE_TTL", str(DEFAULT_CACHE_TTL_S)))
_stores_cache: TTLCache[str, StoreDirectory] = TTLCache(maxsize=64, ttl=_CACHE_TTL_S)

# Payload bounds — the SPA table is browsable, not exhaustive. Serialize the top
# stores / traders by value; a truncation warning keeps the drop visible rather
# than silent (AGENTS.md pull-everything rule).
MAX_STORES = int(os.environ.get("ECO_STORES_MAX", "200"))
MAX_TRADERS = int(os.environ.get("ECO_STORES_MAX_TRADERS", "200"))
# Per-profile detail caps (keeps each row lean).
TOP_ITEMS_PER_PROFILE = int(os.environ.get("ECO_STORES_TOP_ITEMS", "12"))
TOP_COUNTERPARTIES = int(os.environ.get("ECO_STORES_TOP_COUNTERPARTIES", "10"))

# Real seconds per in-game day — mirrors trades.SECONDS_PER_DAY so a store's
# `lastDay` lines up with the ledger's day index.
SECONDS_PER_DAY = 86400.0


def _cache_key(base_url: str, api_key: str | None) -> str:
    import hashlib

    token = hashlib.sha256((api_key or "").encode()).hexdigest()[:12]
    return f"{base_url}|{token}"


# ---------------------------------------------------------------------------
# Output model — JSON-serializable, camelCase for the SPA. Cached in-process as
# the object (like currency.py), so no rehydration path is needed.
# ---------------------------------------------------------------------------


def _item_view(
    item: str, count: int, volume: float, qty: float, prices: list[float]
) -> dict[str, Any]:
    return {
        "item": item,
        "pretty": prettify_eco_name(item) if item else item,
        "tradeCount": count,
        "volume": round(volume, 2),
        "quantity": round(qty, 2),
        "avgUnitPrice": round(statistics.fmean(prices), 4) if prices else None,
    }


@dataclass
class StoreProfile:
    """One store's history-derived profile. Keyed by (location, owner id)."""

    store_key: str
    owner: str
    owner_id: str
    location: str
    store_object: str
    trade_count: int
    total_volume: float
    sell_count: int  # store sold (owner == seller): sell offers filled
    buy_count: int  # store bought (owner == buyer): buy offers filled
    unique_counterparties: int
    last_day: float
    currencies: list[tuple[str, float]] = field(default_factory=list)
    top_items: list[dict[str, Any]] = field(default_factory=list)
    top_counterparties: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        obj = prettify_eco_name(self.store_object) if self.store_object else "Store"
        return f"{self.owner}'s {obj}" if self.owner else obj

    def to_dict(self) -> dict[str, Any]:
        return {
            "storeKey": self.store_key,
            "label": self.label,
            "owner": self.owner,
            "ownerId": self.owner_id,
            "location": self.location,
            "storeObject": self.store_object,
            "storeObjectPretty": (
                prettify_eco_name(self.store_object) if self.store_object else ""
            ),
            "tradeCount": self.trade_count,
            "totalVolume": round(self.total_volume, 2),
            # 185 currencies exist on Sirens, so a single volume scalar is only
            # meaningful when the store trades in one of them. Say which case
            # this is rather than leaving the reader to add unlike units (#236).
            "mixedCurrencyVolume": len(self.currencies) > 1,
            "sellCount": self.sell_count,
            "buyCount": self.buy_count,
            "uniqueCounterparties": self.unique_counterparties,
            "lastDay": round(self.last_day, 2),
            # The per-currency breakout `totalVolume` flattens.
            "currencies": [[c, round(v, 2)] for c, v in self.currencies],
            "topItems": list(self.top_items),
            "topCounterparties": list(self.top_counterparties),
        }


@dataclass
class TraderProfile:
    """One citizen's trade history — what they buy, sell, and the stores they run."""

    name: str
    citizen_id: str
    trade_count: int
    total_volume: float
    sell_volume: float
    buy_volume: float
    unique_counterparties: int
    last_day: float
    stores_operated: list[dict[str, Any]] = field(default_factory=list)
    top_sells: list[dict[str, Any]] = field(default_factory=list)
    top_buys: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "citizenId": self.citizen_id,
            "tradeCount": self.trade_count,
            "totalVolume": round(self.total_volume, 2),
            "sellVolume": round(self.sell_volume, 2),
            "buyVolume": round(self.buy_volume, 2),
            "uniqueCounterparties": self.unique_counterparties,
            "lastDay": round(self.last_day, 2),
            "storesOperated": list(self.stores_operated),
            "topSells": list(self.top_sells),
            "topBuys": list(self.top_buys),
        }


@dataclass
class StoreDirectory:
    """Store + trader directories derived from trade history. JSON-serializable."""

    fetched_at_iso: str
    source_base_url: str
    total_trades: int = 0
    per_type_counts: dict[str, int] = field(default_factory=dict)
    # Detail rows vs hourly-rollup rows, so `perTypeCounts` (which counts rows)
    # can be reconciled against `totalTrades` (which counts events). See #221.
    detailed_rows: int = 0
    rollup_rows: int = 0
    rollup_trades: int = 0
    stores: list[StoreProfile] = field(default_factory=list)
    traders: list[TraderProfile] = field(default_factory=list)
    total_stores: int = 0
    total_traders: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "view": "eco_stores",
            "fetchedAtISO": self.fetched_at_iso,
            "sourceBaseUrl": self.source_base_url,
            "totalTrades": self.total_trades,
            "perTypeCounts": dict(self.per_type_counts),
            "counts": {
                "exporterRows": self.detailed_rows + self.rollup_rows,
                "tradeEvents": self.total_trades,
                "detailedRows": self.detailed_rows,
                "rollupRows": self.rollup_rows,
                "rollupTrades": self.rollup_trades,
                "note": (
                    "perTypeCounts counts exporter rows, not trades; totalTrades counts trade "
                    "events, with each hourly rollup row standing for its merged Count. "
                    "Eco's own /info counter (the economy line of get_server_status) counts "
                    "a different population again and will not match."
                ),
            },
            # Reading a store's totalVolume requires knowing whether it mixes
            # currencies; the per-store flag says so, this names the rule (#236).
            "volumeNote": (
                "totalVolume sums currency amounts across every currency a store traded in. "
                "Where mixedCurrencyVolume is true it adds unlike units — read `currencies` "
                "for the per-currency breakout instead."
            ),
            "stores": [s.to_dict() for s in self.stores],
            "traders": [t.to_dict() for t in self.traders],
            "totalStores": self.total_stores,
            "totalTraders": self.total_traders,
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# Aggregation — pure, folds a parsed-trade list into the two directories.
# ---------------------------------------------------------------------------


@dataclass
class _StoreAcc:
    """Mutable per-store accumulator, materialized into a StoreProfile."""

    owner_id: str
    location: str
    store_objects: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    trade_count: int = 0
    total_volume: float = 0.0
    sell_count: int = 0
    buy_count: int = 0
    last_time: float = 0.0
    counterparties: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    currencies: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    # item -> [count, volume, qty, [unit prices]]
    items: dict[str, list[Any]] = field(
        default_factory=lambda: defaultdict(lambda: [0, 0.0, 0.0, []])
    )


@dataclass
class _TraderAcc:
    """Mutable per-citizen accumulator, materialized into a TraderProfile."""

    citizen_id: str
    trade_count: int = 0
    sell_volume: float = 0.0
    buy_volume: float = 0.0
    last_time: float = 0.0
    counterparties: set[str] = field(default_factory=set)
    stores: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    # item -> [count, volume, qty, [unit prices]]
    sells: dict[str, list[Any]] = field(
        default_factory=lambda: defaultdict(lambda: [0, 0.0, 0.0, []])
    )
    buys: dict[str, list[Any]] = field(
        default_factory=lambda: defaultdict(lambda: [0, 0.0, 0.0, []])
    )


def _fold_item(bucket: list[Any], qty: float, volume: float, unit_price: float | None) -> None:
    bucket[0] += 1
    bucket[1] += volume
    bucket[2] += qty
    if unit_price is not None:
        bucket[3].append(unit_price)


def _top_items(items: dict[str, list[Any]], limit: int) -> list[dict[str, Any]]:
    ranked = sorted(items.items(), key=lambda kv: (kv[1][1], kv[1][0]), reverse=True)
    return [_item_view(item, c, vol, qty, prices) for item, (c, vol, qty, prices) in ranked[:limit]]


def build_directory(
    fetch: Any,
    *,
    max_stores: int = MAX_STORES,
    max_traders: int = MAX_TRADERS,
) -> StoreDirectory:
    """Fold a `ParsedTradeFetch` into store + trader directories.

    Ids resolve to names via the fetch's citizen map (`Citizen #<id>` fallback).
    A store is keyed by (`ActionLocation`, `ShopOwner`); a trader is any citizen
    seen as buyer or seller. The buy/sell split is read off the party columns —
    owner-as-seller is a store sale, owner-as-buyer a store purchase — so it
    never depends on the undecoded `BoughtOrSold` enum.
    """
    name_map = fetch.name_map
    rollups = [t for t in fetch.parsed if t.is_rollup]
    directory = StoreDirectory(
        fetched_at_iso=_now_iso(),
        source_base_url=fetch.normalized_base_url,
        # Count remains a valid merged-event total, but store/trader labels on
        # hourly TradeAction rollups are only representative (eco-app#132).
        total_trades=sum(t.event_count for t in fetch.parsed),
        per_type_counts=dict(fetch.per_type_counts),
        detailed_rows=sum(1 for t in fetch.parsed if not t.is_rollup),
        rollup_rows=len(rollups),
        rollup_trades=sum(t.event_count for t in rollups),
        warnings=list(fetch.warnings),
    )
    if rollups:
        directory.warnings.append(
            f"{sum(t.event_count for t in rollups)} older trades arrive as {len(rollups)} "
            "hourly rollups; store and trader profiles use detailed rows only (eco-app#132)"
        )

    stores: dict[str, _StoreAcc] = {}
    traders: dict[str, _TraderAcc] = {}

    def _trader(cid: str) -> _TraderAcc:
        acc = traders.get(cid)
        if acc is None:
            acc = _TraderAcc(citizen_id=cid)
            traders[cid] = acc
        return acc

    for t in fetch.parsed:
        if t.is_rollup:
            continue
        # --- store leg: needs a shop owner + a place to be a "store" ---
        if t.shop_owner_id:
            key = f"{t.location}|{t.shop_owner_id}"
            store = stores.get(key)
            if store is None:
                store = _StoreAcc(owner_id=t.shop_owner_id, location=t.location)
                stores[key] = store
            store.trade_count += 1
            store.total_volume += t.currency_amount
            store.last_time = max(store.last_time, t.time_s)
            if t.store:
                store.store_objects[t.store] += 1
            if t.currency:
                store.currencies[t.currency] += t.currency_amount
            if t.item:
                _fold_item(store.items[t.item], t.quantity, t.currency_amount, t.unit_price)
            # Owner is the seller -> the store sold; owner is the buyer -> bought.
            counterparty_id = ""
            if t.shop_owner_id == t.seller_id:
                store.sell_count += 1
                counterparty_id = t.buyer_id
            elif t.shop_owner_id == t.buyer_id:
                store.buy_count += 1
                counterparty_id = t.seller_id
            else:
                # Owner is neither party on this row (rare / misaligned) — count
                # the buyer as the counterparty so the metric degrades, not drops.
                counterparty_id = t.buyer_id or t.seller_id
            if counterparty_id and counterparty_id != t.shop_owner_id:
                store.counterparties[_label(counterparty_id, name_map)] += t.currency_amount

        # --- trader legs: seller earns, buyer spends ---
        if t.seller_id:
            acc = _trader(t.seller_id)
            acc.trade_count += 1
            acc.sell_volume += t.currency_amount
            acc.last_time = max(acc.last_time, t.time_s)
            if t.item:
                _fold_item(acc.sells[t.item], t.quantity, t.currency_amount, t.unit_price)
            if t.buyer_id and t.buyer_id != t.seller_id:
                acc.counterparties.add(t.buyer_id)
            if t.shop_owner_id == t.seller_id and t.shop_owner_id:
                acc.stores[f"{t.location}|{t.shop_owner_id}"] += t.currency_amount
        if t.buyer_id and t.buyer_id != t.seller_id:
            acc = _trader(t.buyer_id)
            acc.trade_count += 1
            acc.buy_volume += t.currency_amount
            acc.last_time = max(acc.last_time, t.time_s)
            if t.item:
                _fold_item(acc.buys[t.item], t.quantity, t.currency_amount, t.unit_price)
            if t.seller_id:
                acc.counterparties.add(t.seller_id)
            if t.shop_owner_id == t.buyer_id and t.shop_owner_id:
                acc.stores[f"{t.location}|{t.shop_owner_id}"] += t.currency_amount

    directory.total_stores = len(stores)
    directory.total_traders = len(traders)
    directory.stores = _materialize_stores(stores, name_map, max_stores, directory)
    directory.traders = _materialize_traders(traders, name_map, max_traders, directory)
    return directory


def _materialize_stores(
    stores: dict[str, _StoreAcc],
    name_map: dict[str, str],
    max_stores: int,
    directory: StoreDirectory,
) -> list[StoreProfile]:
    ranked = sorted(
        stores.items(), key=lambda kv: (kv[1].total_volume, kv[1].trade_count), reverse=True
    )
    if len(ranked) > max_stores:
        directory.warnings.append(
            f"store directory truncated to top {max_stores} of {len(ranked)} stores by value"
        )
        ranked = ranked[:max_stores]
    out: list[StoreProfile] = []
    for key, acc in ranked:
        store_object = ""
        if acc.store_objects:
            store_object = max(acc.store_objects.items(), key=lambda kv: kv[1])[0]
        top_cps = sorted(acc.counterparties.items(), key=lambda kv: kv[1], reverse=True)
        out.append(
            StoreProfile(
                store_key=key,
                owner=_label(acc.owner_id, name_map),
                owner_id=acc.owner_id,
                location=acc.location,
                store_object=store_object,
                trade_count=acc.trade_count,
                total_volume=acc.total_volume,
                sell_count=acc.sell_count,
                buy_count=acc.buy_count,
                unique_counterparties=len(acc.counterparties),
                last_day=acc.last_time / SECONDS_PER_DAY,
                currencies=sorted(acc.currencies.items(), key=lambda kv: kv[1], reverse=True),
                top_items=_top_items(acc.items, TOP_ITEMS_PER_PROFILE),
                top_counterparties=[n for n, _ in top_cps[:TOP_COUNTERPARTIES]],
            )
        )
    return out


def _materialize_traders(
    traders: dict[str, _TraderAcc],
    name_map: dict[str, str],
    max_traders: int,
    directory: StoreDirectory,
) -> list[TraderProfile]:
    def _volume(acc: _TraderAcc) -> float:
        return acc.sell_volume + acc.buy_volume

    ranked = sorted(
        traders.items(), key=lambda kv: (_volume(kv[1]), kv[1].trade_count), reverse=True
    )
    if len(ranked) > max_traders:
        directory.warnings.append(
            f"trader directory truncated to top {max_traders} of {len(ranked)} traders by value"
        )
        ranked = ranked[:max_traders]
    out: list[TraderProfile] = []
    for cid, acc in ranked:
        stores_operated = sorted(acc.stores.items(), key=lambda kv: kv[1], reverse=True)
        out.append(
            TraderProfile(
                name=_label(cid, name_map),
                citizen_id=cid,
                trade_count=acc.trade_count,
                total_volume=_volume(acc),
                sell_volume=acc.sell_volume,
                buy_volume=acc.buy_volume,
                unique_counterparties=len(acc.counterparties),
                last_day=acc.last_time / SECONDS_PER_DAY,
                stores_operated=[
                    {"storeKey": k, "location": k.split("|", 1)[0], "volume": round(v, 2)}
                    for k, v in stores_operated
                ],
                top_sells=_top_items(acc.sells, TOP_ITEMS_PER_PROFILE),
                top_buys=_top_items(acc.buys, TOP_ITEMS_PER_PROFILE),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Fetch (cached) — reuses the ledger's shared fetch spine.
# ---------------------------------------------------------------------------


async def fetch_directory(
    base_url: str | None = None,
    api_key: str | None = None,
    cache_ttl_s: float = _CACHE_TTL_S,
    client: httpx.AsyncClient | None = None,
) -> StoreDirectory:
    """Fetch trade history once and fold it into store + trader directories.

    `client` is injectable for tests. Cached per (base, api-key) for a short TTL
    like the ledger — the directory is viewed in bursts.
    """
    normalized = _normalize_admin_base(base_url)
    key = _cache_key(normalized, api_key)
    if cache_ttl_s > 0:
        cached = _stores_cache.get(key)
        if cached is not None:
            return cached

    fetch = await fetch_parsed_trades(base_url=base_url, api_key=api_key, client=client)
    directory = build_directory(fetch)

    if cache_ttl_s > 0:
        _stores_cache[key] = directory
    return directory


def _clear_cache() -> None:
    """Test hook — drop the per-process directory cache."""
    _stores_cache.clear()


# ---------------------------------------------------------------------------
# Card / text shaping — the SPA owns product UX; these stay compact summaries.
# ---------------------------------------------------------------------------


def directory_template_context(
    directory: StoreDirectory, top_stores: int = 8, top_traders: int = 8
) -> dict[str, Any]:
    """Shape for the MCP `_meta.ui` Jinja card (compact in-chat summary)."""
    stores = directory.stores[:top_stores]
    max_store_vol = max((s.total_volume for s in stores), default=0.0) or 1.0
    traders = directory.traders[:top_traders]
    max_trader_vol = max((t.total_volume for t in traders), default=0.0) or 1.0

    return {
        "empty": directory.total_stores == 0 and directory.total_traders == 0,
        "fetched_at_iso": directory.fetched_at_iso,
        "source_base_url": directory.source_base_url,
        "total_trades": directory.total_trades,
        "total_stores": directory.total_stores,
        "total_traders": directory.total_traders,
        "stores": [
            {
                "label": s.label,
                "owner": s.owner,
                "store_object": prettify_eco_name(s.store_object) if s.store_object else "",
                "volume": s.total_volume,
                "trade_count": s.trade_count,
                "sell_count": s.sell_count,
                "buy_count": s.buy_count,
                "counterparties": s.unique_counterparties,
                "last_day": int(s.last_day),
                "top_items": [i["pretty"] for i in s.top_items[:3]],
                "pct": (s.total_volume / max_store_vol) * 100.0,
            }
            for s in stores
        ],
        "traders": [
            {
                "name": t.name,
                "volume": t.total_volume,
                "sell_volume": t.sell_volume,
                "buy_volume": t.buy_volume,
                "trade_count": t.trade_count,
                "stores": len(t.stores_operated),
                "last_day": int(t.last_day),
                "pct": (t.total_volume / max_trader_vol) * 100.0,
            }
            for t in traders
        ],
        "warnings": list(directory.warnings),
    }


def directory_markdown(directory: StoreDirectory) -> str:
    """Compact markdown summary for MCP hosts without the SPA / card."""
    if directory.total_stores == 0 and directory.total_traders == 0:
        return (
            f"**Store & trader directory** — no trade history recorded yet "
            f"({directory.source_base_url})."
        )
    lines = [
        f"**Store & trader directory** — {directory.total_stores:,} stores, "
        f"{directory.total_traders:,} traders from {directory.total_trades:,} trades "
        f"(`{directory.source_base_url}`)",
        "",
    ]
    if directory.stores:
        lines.append("**Top stores by value:**")
        for s in directory.stores[:5]:
            items = ", ".join(i["pretty"] for i in s.top_items[:3])
            extra = f" — {items}" if items else ""
            lines.append(
                f"- {s.label} ({s.trade_count:,} trades, {s.total_volume:,.0f} currency, "
                f"{s.unique_counterparties} counterparties){extra}"
            )
        lines.append("")
    if directory.traders:
        lines.append("**Top traders by value:**")
        for t in directory.traders[:5]:
            operated = f", runs {len(t.stores_operated)} store(s)" if t.stores_operated else ""
            lines.append(
                f"- {t.name} ({t.total_volume:,.0f} currency: "
                f"{t.sell_volume:,.0f} sold / {t.buy_volume:,.0f} bought{operated})"
            )
    for w in directory.warnings:
        lines.append(f"- ⚠ {w}")
    return "\n".join(lines)


__all__ = [
    "StoreDirectory",
    "StoreProfile",
    "TraderProfile",
    "build_directory",
    "directory_markdown",
    "directory_template_context",
    "fetch_directory",
]
