"""Trade & store logistics engine — cheapest source, best resale, arbitrage
spreads, and supply-gap finder (eco-app#51).

The price-intelligence (`market.py`, eco-app#49) and store/trader directory
(`stores.py`, eco-app#50) siblings *describe* the market — prices over time,
who runs which shop. This engine *reasons over* it: given an item it says where
to **buy** it cheapest, where to **sell** stock for the most, which items carry
a buy-low-sell-high **spread** across two stores, and which items have demand
but no supply — the "what should a new store stock" board. DiscordLink only ever
lists trades, so this whole module has no DiscordLink equivalent; it is the
clearest "exceed" in the epic (eco-app#37).

No-reset spine:

* The authoritative input is the **current shelf** — the live store-offer
  exporter (`mods/stores`, `/api/v1/stores`, eco-app#55): each store's offers as
  `(item, buying, price, quantity)`. Those offers are `source="live"`.
* That exporter is reset-gated, so today it may be absent. The engine degrades
  to a **history-derived** shelf reconstructed from the trades ledger
  (eco-app#6): per store, per item, per side, the **recent-median** trade price
  stands in for the live asking price and is marked `source="history"`. It works
  today and sharpens automatically when the live shelf lands — where both exist,
  the live offer wins per `(store, item, currency, side)`.

Design:

* **One normalized unit.** Both inputs fold into a list of :class:`ShelfOffer`,
  and :func:`build_logistics` is a pure function over that list — the four
  boards (cheapest / resale / arbitrage / supply-gap) unit-test with no HTTP.
* **Side is the store's perspective**, matching the DTO and DiscordLink:
  ``side="sell"`` = the store sells (a player **buys** from it → cheapest-source
  board), ``side="buy"`` = the store buys (a player **sells** to it →
  best-resale board).
* **Honest depth.** A spread needs two *different* stores. Empty and single-
  store markets report "not enough market depth" rather than inventing a spread
  from one shop's own quote.
"""

from __future__ import annotations

import os
import statistics
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import httpx

from .crafting import _normalize_admin_base, _now_iso, prettify_eco_name
from .trades import SECONDS_PER_DAY, _label, fetch_parsed_trades

# ---------------------------------------------------------------------------
# Tuning knobs — named constants with sane seeds. Each is env-overridable so
# the boards can be re-tuned against real early-cycle data without a redeploy.
# ---------------------------------------------------------------------------

# An arbitrage spread must clear BOTH gates. The percent gate keeps cheap items
# (a Board at 0.60) from firing on a rounding wobble; the absolute gate keeps
# pricey items from firing on a thin ratio. Seeds: 15% of the buy-in price and
# 0.5 currency absolute — raise them if the board fills with marginal spreads.
ARBITRAGE_MIN_SPREAD_PCT = float(os.environ.get("ECO_LOGI_MIN_SPREAD_PCT", "15.0"))
ARBITRAGE_MIN_ABS_SPREAD = float(os.environ.get("ECO_LOGI_MIN_ABS_SPREAD", "0.5"))

# A spread needs at least this many DISTINCT stores in a market — a seller and a
# separate buyer. Below it there is no market, only one shop quoting itself, so
# arbitrage stays empty and the report says "not enough market depth". Seed 2.
MIN_MARKET_DEPTH = int(os.environ.get("ECO_LOGI_MIN_DEPTH", "2"))

# A demanded item (has buy orders) sold by at most this many distinct stores is
# a supply gap. Seed 1 → zero sellers is "no supply", a lone monopolist is still
# "thin supply" worth a competitor.
SUPPLY_GAP_MAX_SELLERS = int(os.environ.get("ECO_LOGI_GAP_MAX_SELLERS", "1"))

# A cheapest sell price this far above the item's in-game median flags an
# over-priced item a new store could undercut. Seed 30% over median.
OVERPRICED_PCT = float(os.environ.get("ECO_LOGI_OVERPRICED_PCT", "30.0"))

# History offers use the recent-median of trades within this many in-game days
# of the store-item's last trade, so a stale opening price doesn't anchor the
# "current" asking price. Falls back to the full history when the window is bare.
HISTORY_RECENT_DAYS = float(os.environ.get("ECO_LOGI_RECENT_DAYS", "7.0"))

# Payload bounds — the SPA table is browsable, not exhaustive.
TOP_PER_ITEM = int(os.environ.get("ECO_LOGI_TOP_PER_ITEM", "6"))
TOP_ROWS = int(os.environ.get("ECO_LOGI_TOP_ROWS", "40"))

# Supply gaps are the valued board (Kai, eco-app#95: "great — I want 20+"), so
# they get their own, deliberately generous cap instead of sharing the price
# boards' TOP_ROWS. The SPA renders the whole list, so this is what caps how many
# gaps a busy server can surface.
SUPPLY_GAP_ROWS = int(os.environ.get("ECO_LOGI_GAP_ROWS", "40"))

# A named-store query (`store`) lists that store's whole shelf, so it needs its
# own caps: the stores returned, and shelf lines per store. The line count that
# was cut is reported, never silent (COI-758).
STORE_ROWS = int(os.environ.get("ECO_LOGI_STORE_ROWS", "10"))
STORE_SHELF_LINES = int(os.environ.get("ECO_LOGI_STORE_SHELF_LINES", "60"))


def _norm_item(name: str) -> str:
    """Fold an item id or display name to a comparison key.

    Lowercase, drop every non-alphanumeric, drop a trailing ``item``. Dropping
    punctuation and spaces is what lets a human phrasing meet an internal key:
    the filter used to compare against the key alone, and internal keys have no
    spaces, so *every* query written the way a person writes it — "Wooden Hull
    Planks" — returned zero (#247).
    """
    stem = "".join(ch for ch in (name or "").lower() if ch.isalnum())
    if stem.endswith("item") and len(stem) > len("item"):
        stem = stem[: -len("item")]
    return stem


def _norm_store(text: str) -> str:
    """Fold a store name or owner handle to a comparison key: lowercase letters
    and digits only, so `@Elizabeth1337` and `charred corns & more` both meet the
    label `elizabeth1337's Charred Corns & More` (COI-758)."""
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


def _store_matches(offer: ShelfOffer, want_store: str) -> bool:
    """True when a normalized query is part of this offer's store label or owner."""
    return want_store in _norm_store(offer.store_label) or want_store in _norm_store(offer.owner)


def _item_matches(offer: ShelfOffer, want_item: str) -> bool:
    """True when a normalized query names this offer's item.

    Checks the internal key *and* the display name the same payload returns —
    matching only the key made `itemPretty` unusable as an input even though it
    is what the API hands the caller (#247). A trailing "s" is trimmed from the
    query so a singular phrasing still finds a plural key.
    """
    candidates = (_norm_item(offer.item), _norm_item(offer.item_display))
    if any(want_item in c for c in candidates if c):
        return True
    singular = want_item[:-1] if want_item.endswith("s") else want_item
    return bool(singular) and any(singular in c for c in candidates if c)


# ---------------------------------------------------------------------------
# Normalized offer — the one unit both the live shelf and trade history produce.
# ---------------------------------------------------------------------------


@dataclass
class ShelfOffer:
    """One store's offer for one item on one side. JSON-serializable."""

    store_key: str
    store_label: str
    owner: str
    item: str  # item type name, the programmatic key (e.g. "IronIngotItem")
    item_display: str
    currency: str
    side: str  # "sell" = store sells (player buys) | "buy" = store buys (player sells)
    price: float  # per-unit, in `currency`
    quantity: float  # stock (sell) or amount still wanted (buy)
    source: str  # "live" | "history"
    last_day: float | None = None  # freshness of a history offer; None = live/current
    # Store metadata the live exporter carries (dto.md). Kept off `to_dict`, which
    # is one offer line, and surfaced once per store on the `stores` board.
    store_name: str = ""
    location: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "store": self.store_label,
            "owner": self.owner,
            "storeKey": self.store_key,
            "item": self.item,
            "itemPretty": self.item_display,
            "currency": self.currency,
            "side": self.side,
            "price": round(self.price, 4),
            "quantity": round(self.quantity, 2),
            "source": self.source,
            "lastDay": round(self.last_day, 2) if self.last_day is not None else None,
        }


# ---------------------------------------------------------------------------
# Report model — the four boards + provenance. JSON-serializable for the SPA.
# ---------------------------------------------------------------------------


@dataclass
class LogisticsReport:
    fetched_at_iso: str
    source_base_url: str
    live: bool = False  # was a live shelf consulted (vs history-only)?
    total_offers: int = 0
    total_stores: int = 0
    cheapest: list[dict[str, Any]] = field(default_factory=list)
    resale: list[dict[str, Any]] = field(default_factory=list)
    arbitrage: list[dict[str, Any]] = field(default_factory=list)
    supply_gaps: list[dict[str, Any]] = field(default_factory=list)
    market_summaries: list[dict[str, Any]] = field(default_factory=list)
    # The shelf of each store a `store` query matched. `store_query` echoes the
    # query and `stores_matched` is None without one, so 0 reads as a miss and
    # never as an empty store (COI-758).
    stores: list[dict[str, Any]] = field(default_factory=list)
    store_query: str | None = None
    stores_matched: int | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "view": "logistics",
            "fetchedAtISO": self.fetched_at_iso,
            "sourceBaseUrl": self.source_base_url,
            "live": self.live,
            "totalOffers": self.total_offers,
            "totalStores": self.total_stores,
            "cheapest": list(self.cheapest),
            "resale": list(self.resale),
            "arbitrage": list(self.arbitrage),
            "supplyGaps": list(self.supply_gaps),
            "marketSummaries": list(self.market_summaries),
            "storeQuery": self.store_query,
            "storesMatched": self.stores_matched,
            "stores": list(self.stores),
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# The engine — pure over a list of ShelfOffer.
# ---------------------------------------------------------------------------


def _passes_filters(
    offer: ShelfOffer,
    want_item: str | None,
    want_currency: str | None,
    want_store: str | None = None,
) -> bool:
    if want_store is not None and not _store_matches(offer, want_store):
        return False
    if want_item is not None and not _item_matches(offer, want_item):
        return False
    if want_currency is not None and offer.currency.lower() != want_currency:
        return False
    return True


def build_logistics(
    offers: Iterable[ShelfOffer],
    *,
    medians: dict[tuple[str, str], float] | None = None,
    item: str | None = None,
    currency: str | None = None,
    store: str | None = None,
    top_per_item: int = TOP_PER_ITEM,
    top_rows: int = TOP_ROWS,
    top_gap_rows: int = SUPPLY_GAP_ROWS,
    min_spread_pct: float = ARBITRAGE_MIN_SPREAD_PCT,
    min_abs_spread: float = ARBITRAGE_MIN_ABS_SPREAD,
    min_depth: int = MIN_MARKET_DEPTH,
    gap_max_sellers: int = SUPPLY_GAP_MAX_SELLERS,
    overpriced_pct: float = OVERPRICED_PCT,
) -> LogisticsReport:
    """Fold normalized shelf offers into the four logistics boards.

    Pure — no HTTP, no clock (the caller stamps `fetched_at_iso`). `medians`
    keys the in-game reference median per ``(item, currency)`` so the supply-gap
    board can flag over-priced items; omit it and the over-priced signal simply
    doesn't fire. `item` / `currency` narrow every board (`item` matches on the
    normalized key so `Iron` finds `IronIngotItem`). `store` matches part of a
    store's label or its owner handle and also fills the `stores` shelf board.
    """
    want_item = _norm_item(item) if item else None
    want_store = _norm_store(store) if store else None
    want_store = want_store or None
    want_currency = currency.strip().lower() if currency else None
    medians = medians or {}

    # Keep the last-writer-wins offer per (store, item, currency, side): a live
    # offer overwrites a history one for the same shelf line (see `_merge`), so
    # by here the list is already deduped; we just bucket by market.
    priced = [o for o in offers if o.price > 0]
    # Kept so an empty filtered result can tell a lookup miss from a dead
    # market — see the warning below (#247).
    total_before_filter = len(priced)
    kept = [o for o in priced if _passes_filters(o, want_item, want_currency, want_store)]

    # (item, currency) -> {"sell": [...], "buy": [...]}
    markets: dict[tuple[str, str], dict[str, list[ShelfOffer]]] = defaultdict(
        lambda: {"sell": [], "buy": []}
    )
    all_stores: set[str] = set()
    for o in kept:
        markets[(o.item, o.currency)][o.side].append(o)
        all_stores.add(o.store_key)

    report = LogisticsReport(fetched_at_iso="", source_base_url="")
    report.total_offers = len(kept)
    report.total_stores = len(all_stores)

    cheapest: list[dict[str, Any]] = []
    resale: list[dict[str, Any]] = []
    arbitrage: list[dict[str, Any]] = []
    supply_gaps: list[dict[str, Any]] = []
    market_summaries: list[dict[str, Any]] = []

    for (item_name, cur), sides in markets.items():
        all_sells = sorted(sides["sell"], key=lambda o: o.price)  # cheapest first
        # A shelf line with nothing on it is not a place to buy (COI-2045: a 0-stock
        # Barter line outranked a 185-stock Spectres line). Every board below reads
        # `sells`, the buyable lines. `sold_out` only feeds the explicit sold-out row.
        sells = [o for o in all_sells if o.quantity > 0]
        sold_out = [o for o in all_sells if o.quantity <= 0]
        buys = sorted(sides["buy"], key=lambda o: o.price, reverse=True)  # best paid first
        pretty = prettify_eco_name(item_name) if item_name else item_name
        median = medians.get((item_name, cur))

        market_summaries.append(
            {
                "item": item_name,
                "itemPretty": pretty,
                "currency": cur,
                "sellerCount": len({o.store_key for o in sells}),
                "buyerCount": len({o.store_key for o in buys}),
                "supplyQty": round(sum(o.quantity for o in sells), 2),
                "demandQty": round(sum(o.quantity for o in buys), 2),
                "cheapestSell": round(sells[0].price, 4) if sells else None,
                "bestBuy": round(buys[0].price, 4) if buys else None,
                "sources": sorted({o.source for o in sells + buys}),
            }
        )

        # --- Cheapest source: where to BUY (stores selling), cheapest first ---
        if sells:
            cheapest.append(
                {
                    "item": item_name,
                    "itemPretty": pretty,
                    "currency": cur,
                    "sellerCount": len({o.store_key for o in sells}),
                    "soldOutCount": len({o.store_key for o in sold_out}),
                    "cheapest": round(sells[0].price, 4),
                    "offers": [o.to_dict() for o in sells[:top_per_item]],
                }
            )
        elif sold_out:
            # Every seller is empty. Say so with null, never a 0 price or a
            # zero-stock store dressed as the pick; `soldOutNote` is what the
            # reply template renders (reply_templates.py).
            cheapest.append(
                {
                    "item": item_name,
                    "itemPretty": pretty,
                    "currency": cur,
                    "sellerCount": 0,
                    "soldOutCount": len({o.store_key for o in sold_out}),
                    "cheapest": None,
                    "offers": [],
                    "soldOutOffers": [o.to_dict() for o in sold_out[:top_per_item]],
                    "soldOutNote": (
                        f"Every store selling {pretty} for {cur} is out of stock right now."
                        if cur
                        else f"Every store selling {pretty} is out of stock right now."
                    ),
                }
            )

        # --- Best resale: where to SELL (stores buying), highest paid first ---
        if buys:
            resale.append(
                {
                    "item": item_name,
                    "itemPretty": pretty,
                    "currency": cur,
                    "buyerCount": len({o.store_key for o in buys}),
                    "best": round(buys[0].price, 4),
                    "offers": [o.to_dict() for o in buys[:top_per_item]],
                }
            )

        # --- Arbitrage: buy cheapest (a store's sell) then sell dearest (a
        # different store's buy). Needs depth AND two distinct stores. ---
        distinct = len({o.store_key for o in sides["sell"] + sides["buy"]})
        if sells and buys and distinct >= min_depth:
            buy_from = sells[0]  # player buys here (store's cheapest sell offer)
            sell_to = buys[0]  # player sells here (store's highest buy offer)
            if buy_from.store_key != sell_to.store_key:
                spread = sell_to.price - buy_from.price
                spread_pct = (spread / buy_from.price) * 100.0 if buy_from.price else 0.0
                if spread >= min_abs_spread and spread_pct >= min_spread_pct:
                    volume = min(buy_from.quantity, sell_to.quantity)
                    arbitrage.append(
                        {
                            "item": item_name,
                            "itemPretty": pretty,
                            "currency": cur,
                            "spread": round(spread, 4),
                            "spreadPct": round(spread_pct, 1),
                            "volume": round(volume, 2),
                            "opportunity": round(spread * volume, 2),
                            "storeCount": distinct,
                            "buyFrom": buy_from.to_dict(),
                            "sellTo": sell_to.to_dict(),
                        }
                    )

        # --- Supply gap: demand (buy orders) with thin/absent supply, or a
        # cheapest sell sitting well above the in-game median. ---
        seller_count = len({o.store_key for o in sells})
        buyer_count = len({o.store_key for o in buys})
        gap = _supply_gap_row(
            item_name,
            pretty,
            cur,
            sells,
            buys,
            seller_count,
            buyer_count,
            median,
            gap_max_sellers,
            overpriced_pct,
        )
        if gap is not None:
            supply_gaps.append(gap)

    # Rank each board. Cheapest/resale by best price achievable; arbitrage by
    # opportunity size (spread * movable volume, both of what the issue asks to
    # rank on); supply gaps by unmet demand quantity.
    # Buyable rows by price, then sold-out rows, so `cheapest[0]` is a store with
    # stock whenever any market has one.
    cheapest.sort(key=lambda r: (r["cheapest"] is None, r["cheapest"] or 0.0))
    resale.sort(key=lambda r: r["best"], reverse=True)
    arbitrage.sort(key=lambda r: (r["opportunity"], r["spread"]), reverse=True)
    supply_gaps.sort(key=lambda r: (_GAP_RANK.get(r["reason"], 0), r["demandQty"]), reverse=True)

    report.cheapest = cheapest[:top_rows]
    report.resale = resale[:top_rows]
    report.arbitrage = arbitrage[:top_rows]
    report.supply_gaps = supply_gaps[:top_gap_rows]
    report.market_summaries = sorted(
        market_summaries,
        key=lambda row: (
            row["supplyQty"] + row["demandQty"],
            row["sellerCount"] + row["buyerCount"],
        ),
        reverse=True,
    )

    matched_keys: set[str] = set()
    if want_store is not None:
        matched_keys = {o.store_key for o in priced if _store_matches(o, want_store)}
        report.store_query = (store or "").strip()
        report.stores_matched = len(matched_keys)
        _fill_store_board(report, priced, kept, matched_keys, want_item, want_currency)
        # Arbitrage and supply gaps compare stores, and one store's slice of the
        # market cannot say either truthfully (its own buy order would read as
        # unmet demand), so they are left out rather than shown misleading.
        report.arbitrage = []
        report.supply_gaps = []
        if matched_keys:
            report.warnings.append(
                "store filter set: arbitrage and supply gaps need the whole market, so "
                "they are left out. Drop store to see them."
            )

    # Honest depth note — the whole point of the "single-store" acceptance case.
    if want_store is not None and not matched_keys:
        # A store name nobody has is a lookup miss. It is never the same fact as a
        # store with nothing on its shelves, so say which, and name real stores.
        known = sorted({o.store_label for o in priced})
        sample = ", ".join(known[:8])
        report.warnings.append(
            f"no store matched store={store!r}. "
            + (
                f"{len(known):,} store(s) have priced offers, for example: {sample}. "
                if known
                else "No store has a priced offer at all yet. "
            )
            + "This is a lookup miss, not an empty store. Try part of the store's name "
            "or its owner's handle."
        )
    elif report.total_offers == 0 and want_store is not None:
        # The store exists, so the item or currency filter is what emptied it.
        report.warnings.append(
            f"{len(matched_keys):,} store(s) matched store={store!r} but none has an offer "
            "matching the item or currency filter. Drop it to see the whole shelf."
        )
    elif report.total_offers == 0:
        # "Nothing is for sale" and "your filter matched nothing" are different
        # facts, and only one of them is knowable from an empty result. Saying
        # the first when the second was true is what let a Discord agent tell a
        # member, fluently and wrongly, that zero wooden hull planks existed on
        # a server with 913 of them on one shelf (#247).
        if want_item is not None and total_before_filter > 0:
            report.warnings.append(
                f"no offers matched item={item!r} — the item name was not recognized. "
                f"{total_before_filter:,} offer(s) exist across the market unfiltered, so "
                "this is a lookup miss, not an empty market. Try the internal item key "
                "(e.g. WoodenHullPlanksItem) or a shorter fragment."
            )
        elif want_currency is not None and total_before_filter > 0:
            report.warnings.append(
                f"no offers matched currency={currency!r}; {total_before_filter:,} offer(s) "
                "exist in other currencies, so this is a filter miss, not an empty market."
            )
        else:
            report.warnings.append("no shelf offers or priced trade history yet — nothing to route")
    elif report.total_stores < min_depth and want_store is None:
        report.warnings.append(
            f"not enough market depth for arbitrage: need ≥{min_depth} distinct stores, "
            f"saw {report.total_stores} (cheapest-source / best-resale still shown)"
        )
    return report


def _fill_store_board(
    report: LogisticsReport,
    priced: list[ShelfOffer],
    kept: list[ShelfOffer],
    matched_keys: set[str],
    want_item: str | None,
    want_currency: str | None,
) -> None:
    """Fill `report.stores` with one shelf per store a `store` query matched.

    A live row lists every line the store has, zero stock included, because a
    named store with 0 of something is an answer. A history-only row of an owner
    who also has a live row is dropped, since the live exporter covers that
    owner and the history row would show the same shop at stale prices.
    """
    by_store: dict[str, list[ShelfOffer]] = defaultdict(list)
    for o in kept:
        by_store[o.store_key].append(o)
    meta: dict[str, ShelfOffer] = {}
    for o in priced:
        if o.store_key in matched_keys:
            meta.setdefault(o.store_key, o)
    live_owners = {
        meta[k].owner for k in matched_keys if meta[k].source == "live" and meta[k].owner
    }
    rows: list[dict[str, Any]] = []
    superseded = 0
    for key in matched_keys:
        first = meta[key]
        live = first.source == "live"
        if not live and first.owner in live_owners:
            superseded += 1
            continue
        lines = sorted(
            by_store.get(key, []), key=lambda o: (o.side != "sell", o.item_display.lower())
        )
        row: dict[str, Any] = {
            "store": first.store_label,
            "name": first.store_name or first.store_label,
            "owner": first.owner or None,
            "storeKey": key,
            "live": live,
            "location": first.location,
            "currencies": sorted({o.currency for o in lines if o.currency}),
            "sellLines": sum(1 for o in lines if o.side == "sell"),
            "buyLines": sum(1 for o in lines if o.side == "buy"),
            "shelfLines": len(lines),
            "shelf": [o.to_dict() for o in lines[:STORE_SHELF_LINES]],
        }
        notes: list[str] = []
        if len(lines) > STORE_SHELF_LINES:
            notes.append(f"shelf shows {STORE_SHELF_LINES:,} of {len(lines):,} lines")
        if not live:
            notes.append(
                "no live shelf for this store: prices are recent median trade prices and "
                "quantity is units traded, not stock"
            )
        if not lines:
            notes.append(
                "no line matches the item or currency filter"
                if want_item or want_currency
                else "no priced line"
            )
        if notes:
            row["note"] = "; ".join(notes)
        rows.append(row)
    rows.sort(key=lambda r: (-r["shelfLines"], r["store"].lower()))
    if len(rows) > STORE_ROWS:
        report.warnings.append(
            f"stores: showing {STORE_ROWS:,} of {len(rows):,} matching stores, "
            "most shelf lines first. Narrow the store name to see the rest."
        )
    report.stores = rows[:STORE_ROWS]
    if superseded:
        report.warnings.append(
            f"{superseded:,} history-only store(s) left out because their owner has a live shelf"
        )


# Supply-gap severity, for ranking: an unmet buy order beats a lone monopolist
# beats a merely over-priced shelf.
_GAP_RANK = {"no_supply": 3, "thin_supply": 2, "overpriced": 1}


def _supply_gap_row(
    item_name: str,
    pretty: str,
    cur: str,
    sells: list[ShelfOffer],
    buys: list[ShelfOffer],
    seller_count: int,
    buyer_count: int,
    median: float | None,
    gap_max_sellers: int,
    overpriced_pct: float,
) -> dict[str, Any] | None:
    """Classify one market's supply gap, or None if it is healthily supplied.

    Priority: no supply (demand, zero sellers) > thin supply (demand, ≤ N
    sellers) > over-priced (has sellers, cheapest sell well above median).
    """
    cheapest_sell = sells[0].price if sells else None
    best_buy = buys[0].price if buys else None
    demand_qty = sum(o.quantity for o in buys)
    supply_qty = sum(o.quantity for o in sells)

    reason: str | None = None
    over_pct: float | None = None
    if buyer_count > 0 and seller_count == 0:
        reason = "no_supply"
    elif buyer_count > 0 and seller_count <= gap_max_sellers:
        reason = "thin_supply"
    elif median and cheapest_sell is not None and median > 0:
        over_pct = (cheapest_sell - median) / median * 100.0
        if over_pct >= overpriced_pct:
            reason = "overpriced"

    if reason is None:
        return None
    return {
        "item": item_name,
        "itemPretty": pretty,
        "currency": cur,
        "reason": reason,
        "sellerCount": seller_count,
        "buyerCount": buyer_count,
        "demandQty": round(demand_qty, 2),
        "supplyQty": round(supply_qty, 2),
        "buyPrice": round(best_buy, 4) if best_buy is not None else None,
        "cheapestSell": round(cheapest_sell, 4) if cheapest_sell is not None else None,
        "median": round(median, 4) if median is not None else None,
        "overMedianPct": round(over_pct, 1) if over_pct is not None else None,
        # Who needs it — the buy-side owners folded per citizen with the amount
        # each still wants, biggest demand first. This is the "who should I sell
        # to" half of a supply gap: a gap is only actionable if you know who is
        # asking (eco-app#77).
        "buyers": _gap_buyers(buys),
    }


def _gap_buyers(buys: list[ShelfOffer], *, top: int = 8) -> list[dict[str, Any]]:
    """Fold buy-side offers into per-citizen demand rows, biggest want first.

    One citizen can post buy orders at more than one store, so demand folds by
    owner (falling back to the store label when a shop has no named owner) and
    sums the wanted quantity across their orders. The best (highest) price that
    citizen is offering rides along so the SPA can show what the demand pays.
    """
    folded: dict[str, dict[str, Any]] = {}
    for o in buys:
        key = o.owner or o.store_label
        row = folded.get(key)
        if row is None:
            folded[key] = {
                "owner": o.owner,
                "store": o.store_label,
                "quantity": o.quantity,
                "price": o.price,
            }
        else:
            row["quantity"] += o.quantity
            row["price"] = max(row["price"], o.price)
    ranked = sorted(folded.values(), key=lambda r: r["quantity"], reverse=True)
    return [
        {
            "owner": r["owner"],
            "store": r["store"],
            "quantity": round(r["quantity"], 2),
            "price": round(r["price"], 4),
        }
        for r in ranked[:top]
    ]


# ---------------------------------------------------------------------------
# History-derived shelf — recent-median reconstruction from the trades ledger.
# ---------------------------------------------------------------------------


def _history_offers(fetch: Any) -> tuple[list[ShelfOffer], dict[tuple[str, str], float]]:
    """Reconstruct a history-derived shelf + per-market in-game medians.

    A store's shelf line is `(location, owner, item, currency, side)` where the
    side is read structurally — owner-as-seller is a store *sell*, owner-as-buyer
    a store *buy* — so a wrong `BoughtOrSold` decode never flips a board (same
    defensiveness as `stores.py`). The offer price is the **recent-median** of
    that line's unit prices, so an opening-day price doesn't anchor "current".

    The medians map is the item's overall in-game median across *all* priced
    trades (shop-attributed or not), the reference the supply-gap board compares
    a cheapest sell against.
    """
    name_map = fetch.name_map

    # (store_key, owner_id, location, item, currency, side)
    #   -> {"prices": [(time_s, unit_price)], "qty": float, "objects": Counter}
    lines: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}
    # (item, currency) -> [unit_price] across all priced trades
    market_prices: dict[tuple[str, str], list[float]] = defaultdict(list)

    for t in fetch.parsed:
        # A TradeAction hourly rollup's item/store/party fields describe a
        # representative event. Its currency and quantity totals are useful to
        # the ledger, but its derived unit price is not a shelf quote.
        if t.is_rollup:
            continue
        if not t.item or not t.currency or t.unit_price is None or t.unit_price <= 0:
            continue
        market_prices[(t.item, t.currency)].append(t.unit_price)
        if not t.shop_owner_id:
            continue
        if t.shop_owner_id == t.seller_id:
            side = "sell"
        elif t.shop_owner_id == t.buyer_id:
            side = "buy"
        else:
            # Owner is neither party on this row — can't attribute a shelf side.
            continue
        key = (
            f"{t.location}|{t.shop_owner_id}",
            t.shop_owner_id,
            t.location,
            t.item,
            t.currency,
            side,
        )
        acc = lines.get(key)
        if acc is None:
            acc = {"prices": [], "qty": 0.0, "objects": defaultdict(int)}
            lines[key] = acc
        acc["prices"].append((t.time_s, t.unit_price))
        acc["qty"] += t.quantity
        if t.store:
            acc["objects"][t.store] += 1

    offers: list[ShelfOffer] = []
    for (store_key, owner_id, _location, item_name, cur, side), acc in lines.items():
        prices: list[tuple[float, float]] = acc["prices"]
        latest = max(t for t, _ in prices)
        window_start = latest - HISTORY_RECENT_DAYS * SECONDS_PER_DAY
        recent = [p for t, p in prices if t >= window_start] or [p for _, p in prices]
        objects: dict[str, int] = acc["objects"]
        store_object = max(objects.items(), key=lambda kv: kv[1])[0] if objects else ""
        owner = _label(owner_id, name_map)
        obj_pretty = prettify_eco_name(store_object) if store_object else "Store"
        label = f"{owner}'s {obj_pretty}" if owner else obj_pretty
        offers.append(
            ShelfOffer(
                store_key=store_key,
                store_label=label,
                owner=owner,
                item=item_name,
                item_display=prettify_eco_name(item_name),
                currency=cur,
                side=side,
                price=statistics.median(recent),
                quantity=acc["qty"],
                source="history",
                last_day=latest / SECONDS_PER_DAY,
                store_name=obj_pretty,
            )
        )

    medians = {k: statistics.median(v) for k, v in market_prices.items() if v}
    return offers, medians


# ---------------------------------------------------------------------------
# Live shelf — the reset-gated store-offer exporter (best-effort).
# ---------------------------------------------------------------------------


def parse_live_stores(stores: Iterable[dict[str, Any]]) -> list[ShelfOffer]:
    """Fold the `/api/v1/stores` DTO array into live shelf offers.

    Tolerates the nulls the DTO promises — an unowned store, a currency-less
    store, an orphaned (locationless) store — and skips barter / free shelves
    (a 0 price with no currency) the same way the price boards skip unpriced
    trades. See `mods/stores/docs/dto.md`.
    """
    offers: list[ShelfOffer] = []
    for store in stores:
        name = (store.get("name") or "").strip()
        owner = (store.get("owner") or "").strip()
        currency = (store.get("currency") or "").strip()
        store_key = f"live:{name}|{owner}"
        raw_location = store.get("location")
        location = raw_location if isinstance(raw_location, dict) else None
        label = f"{owner}'s {name}" if owner and name else (name or "Store")
        for offer in store.get("offers") or []:
            item_type = (offer.get("itemTypeName") or offer.get("item") or "").strip()
            if not item_type:
                continue
            try:
                price = float(offer.get("price") or 0.0)
                quantity = float(offer.get("quantity") or 0.0)
            except (TypeError, ValueError):
                continue
            if price <= 0:  # barter / free shelf — never a currency price point
                continue
            offers.append(
                ShelfOffer(
                    store_key=store_key,
                    store_label=label,
                    owner=owner,
                    item=item_type,
                    item_display=(offer.get("item") or prettify_eco_name(item_type)).strip(),
                    currency=currency,
                    side="buy" if offer.get("buying") else "sell",
                    price=price,
                    quantity=quantity,
                    source="live",
                    last_day=None,
                    store_name=name,
                    location=location,
                )
            )
    return offers


async def _fetch_live_offers(
    http: httpx.AsyncClient, normalized_base_url: str, api_key: str | None
) -> tuple[list[ShelfOffer], list[str]]:
    """Best-effort GET of the live shelf. Absence is normal (reset-gated), so a
    404 / connection error degrades to history rather than failing the tool."""
    url = f"{normalized_base_url}/api/v1/stores"
    headers = {"X-API-Key": api_key} if api_key else {}
    try:
        resp = await http.get(url, headers=headers)
        if resp.status_code == 404:
            return [], []  # exporter not deployed yet — history carries the day
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as e:
        return [], [f"live shelf: HTTP {e.response.status_code} (using history)"]
    except (httpx.HTTPError, ValueError) as e:
        return [], [f"live shelf: {type(e).__name__} (using history)"]
    if not isinstance(data, list):
        return [], ["live shelf: unexpected shape (using history)"]
    return parse_live_stores(data), []


def _merge(history: list[ShelfOffer], live: list[ShelfOffer]) -> list[ShelfOffer]:
    """Live offers win per `(store, item, currency, side)` shelf line; history
    fills every line the live shelf doesn't cover (or all of them, offline)."""
    by_line: dict[tuple[str, str, str, str], ShelfOffer] = {}
    for o in history:
        by_line[(o.store_key, o.item, o.currency, o.side)] = o
    for o in live:
        by_line[(o.store_key, o.item, o.currency, o.side)] = o
    return list(by_line.values())


async def fetch_logistics(
    base_url: str | None = None,
    api_key: str | None = None,
    *,
    item: str | None = None,
    currency: str | None = None,
    store: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> LogisticsReport:
    """Assemble the shelf (history + best-effort live) and fold it into the boards.

    History is the spine (`fetch_parsed_trades`, shared with the ledger and the
    directory — one fetch, one parse); the live shelf sharpens it when present.
    `client` is injectable for tests. A trades-exporter fault propagates to the
    caller (the tool renders the error card); the live shelf's own faults degrade
    to history and become warnings.
    """
    normalized = _normalize_admin_base(base_url)
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0))
    try:
        fetch = await fetch_parsed_trades(base_url=base_url, api_key=api_key, client=http)
        hist_offers, medians = _history_offers(fetch)
        live_offers, live_warnings = await _fetch_live_offers(http, normalized, api_key)
    finally:
        if owns_client:
            await http.aclose()

    offers = _merge(hist_offers, live_offers)
    report = build_logistics(offers, medians=medians, item=item, currency=currency, store=store)
    report.fetched_at_iso = _now_iso()
    report.source_base_url = normalized
    report.live = bool(live_offers)
    report.warnings = list(fetch.warnings) + live_warnings + report.warnings
    rollups = [t for t in fetch.parsed if t.is_rollup]
    if rollups:
        report.warnings.append(
            f"{sum(t.event_count for t in rollups)} older trades arrive as {len(rollups)} "
            "hourly rollups; history-derived offers use detailed rows only (eco-app#132)"
        )
    return report


# ---------------------------------------------------------------------------
# Card / text shaping — the SPA owns product UX; these stay compact summaries.
# ---------------------------------------------------------------------------

_GAP_LABEL = {
    "no_supply": "no supply",
    "thin_supply": "thin supply",
    "overpriced": "over-priced",
}


def logistics_template_context(report: LogisticsReport, *, top: int = 6) -> dict[str, Any]:
    """Shape for the MCP `_meta.ui` Jinja card (compact in-chat summary)."""
    return {
        "empty": report.total_offers == 0,
        "fetched_at_iso": report.fetched_at_iso,
        "source_base_url": report.source_base_url,
        "live": report.live,
        "total_offers": report.total_offers,
        "total_stores": report.total_stores,
        "resale": report.resale[:top],
        "arbitrage": report.arbitrage[:top],
        "supply_gaps": [
            {**g, "reasonLabel": _GAP_LABEL.get(g["reason"], g["reason"])}
            for g in report.supply_gaps[:top]
        ],
        "warnings": list(report.warnings),
    }


_MD_SHELF_LINES = 15


def _store_shelf_markdown(report: LogisticsReport) -> list[str]:
    lines = [
        f"**Store shelf for {report.store_query!r}** — {report.stores_matched} store(s) matched:"
    ]
    for st in report.stores:
        kind = "live shelf" if st["live"] else "history only"
        owner = f"owner {st['owner']}, " if st["owner"] else ""
        lines.append(f"- **{st['store']}** ({owner}{kind}, {st['shelfLines']} line(s))")
        for o in st["shelf"][:_MD_SHELF_LINES]:
            verb = "sells" if o["side"] == "sell" else "buys"
            amount = "in stock" if o["side"] == "sell" else "wanted"
            lines.append(
                f"  - {verb} {o['itemPretty']}: {o['quantity']:,.0f} {amount} "
                f"at {o['price']:,.2f} {o['currency']}"
            )
        if len(st["shelf"]) > _MD_SHELF_LINES:
            lines.append(f"  - ... {len(st['shelf']) - _MD_SHELF_LINES} more in the JSON block")
        if st.get("note"):
            lines.append(f"  - note: {st['note']}")
    lines.append("")
    return lines


def logistics_markdown(report: LogisticsReport) -> str:
    """Compact markdown summary for MCP hosts without the SPA / card."""
    src = "live shelf + history" if report.live else "history-derived"
    if report.store_query is not None and report.total_offers == 0:
        # A store miss gets its own words: "no shelf offers yet" would read as an
        # empty store (COI-758).
        head = f"**Store shelf** — no offers for store {report.store_query!r}."
        return "\n".join([head, "", *(f"- ⚠ {w}" for w in report.warnings)]).rstrip()
    if report.total_offers == 0:
        return (
            f"**Trade logistics** — no shelf offers or priced trade history yet "
            f"({report.source_base_url})."
        )
    lines = [
        f"**Trade logistics** — {report.total_offers:,} shelf offers across "
        f"{report.total_stores:,} stores ({src}, `{report.source_base_url}`)",
        "",
    ]
    if report.store_query is not None:
        lines.extend(_store_shelf_markdown(report))
    if report.resale:
        lines.append("**Best resale (where to sell):**")
        for r in report.resale[:5]:
            best = r["offers"][0]
            lines.append(
                f"- {r['itemPretty']}: {r['best']:,.2f} {r['currency']}/unit "
                f"at {best['store']} ({best['source']})"
            )
        lines.append("")
    if report.arbitrage:
        lines.append("**Arbitrage spreads (buy low, sell high):**")
        for r in report.arbitrage[:5]:
            lines.append(
                f"- {r['itemPretty']}: buy {r['buyFrom']['price']:,.2f} at "
                f"{r['buyFrom']['store']} → sell {r['sellTo']['price']:,.2f} at "
                f"{r['sellTo']['store']} = +{r['spread']:,.2f} {r['currency']} "
                f"({r['spreadPct']:+.0f}%, up to {r['volume']:,.0f} units)"
            )
        lines.append("")
    if report.supply_gaps:
        lines.append("**Supply gaps (what a store should stock):**")
        for g in report.supply_gaps[:5]:
            label = _GAP_LABEL.get(g["reason"], g["reason"])
            if g["reason"] == "overpriced":
                detail = (
                    f"cheapest {g['cheapestSell']:,.2f} vs median "
                    f"{g['median']:,.2f} (+{g['overMedianPct']:.0f}%)"
                )
            else:
                who = ", ".join(
                    f"{b['owner'] or b['store']} ({b['quantity']:,.0f})"
                    for b in g.get("buyers", [])[:3]
                )
                detail = (
                    f"{g['buyerCount']} buyer(s), {g['sellerCount']} seller(s), "
                    f"demand {g['demandQty']:,.0f}"
                )
                if who:
                    detail += f" — wanted by {who}"
            lines.append(f"- {g['itemPretty']} — {label}: {detail}")
        lines.append("")
    for w in report.warnings:
        lines.append(f"- ⚠ {w}")
    return "\n".join(lines).rstrip()


__all__ = [
    "LogisticsReport",
    "ShelfOffer",
    "build_logistics",
    "fetch_logistics",
    "logistics_markdown",
    "logistics_template_context",
    "parse_live_stores",
]
