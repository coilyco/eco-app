"""Per-item market price intelligence, built on the trades ledger (eco-app#49).

The trades ledger (eco-app#6) reconstructs every attributable `CurrencyTrade`
and derives a light per-item price-over-time series (mean unit price per day).
This module goes a layer deeper for the price axis the DiscordLink-replacement
epic asks for (eco-app#37): a **per-item, per-currency** price series with
median / min / max / volume per daily bucket, a short-vs-long-window trend
verdict. The real-world FRED benchmark rides beside it as `commodityBenchmark`, attached by
the tool handler from `commodity_benchmark.py`.

Design notes:

* **Consumes #6, never re-parses.** `fetch_market` calls
  `trades.fetch_ledger` and folds its normalized row model — the same
  streamed-CSV / shifted-column / `BoughtOrSold`-decode corrections apply for
  free, and we never hit the exporter twice. `build_market` is a pure function
  over ledger row dicts so the price math unit-tests without any HTTP.
* **Per-currency separation.** A bucket can be thin, and two currencies for
  the same item are two different price stories, so `(item, currency)` is the
  unit of a market. A trade with no currency (barter) never enters a price
  series.
* **Median, not mean.** A single fat-fingered trade shouldn't move the line,
  so the per-bucket statistic is the median unit price (min / max / volume are
  reported alongside so the spread is visible).
* **Trend = short window vs long window.** The most-recent `SHORT_WINDOW_DAYS`
  of bucket medians against the trailing `LONG_WINDOW_DAYS`. Under a flat band
  (`FLAT_BAND_PCT`) it reads flat; with fewer than two daily buckets it reads
  "insufficient" rather than inventing a direction. Early-cycle reality: many
  items have a handful of trades, so "insufficient" is the common, honest case.
"""

from __future__ import annotations

import os
import statistics
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import httpx

from .crafting import prettify_eco_name
from .trades import fetch_ledger

# Trend → glyph, shared by the card context and the markdown summary.
_TREND_ARROW = {"rising": "▲", "falling": "▼", "flat": "▬", "insufficient": "·"}
_TREND_ARROW_ASCII = {"rising": "↑", "falling": "↓", "flat": "→", "insufficient": "·"}

# How many (item, currency) markets to keep, ranked by trade count. Keeps the
# payload lean the same way the ledger caps its price series.
TOP_MARKETS = int(os.environ.get("ECO_MARKET_TOP", "24"))

# Trend windows, in in-game days. Short = the recent read; long = the trailing
# baseline it's measured against. Overridable for tests / tuning.
SHORT_WINDOW_DAYS = int(os.environ.get("ECO_MARKET_SHORT_DAYS", "3"))
LONG_WINDOW_DAYS = int(os.environ.get("ECO_MARKET_LONG_DAYS", "14"))

# Percent band inside which a short-vs-long delta reads "flat" rather than
# rising / falling. Thin early-cycle data is noisy; a 5% band avoids crying
# "rising" over a single quiet day.
FLAT_BAND_PCT = float(os.environ.get("ECO_MARKET_FLAT_BAND", "5.0"))


@dataclass
class PriceBucket:
    """One in-game day of trades for a single (item, currency) market."""

    day: int
    median: float
    minimum: float
    maximum: float
    volume: float  # units traded that day (sum of NumberOfItems)
    trades: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "median": self.median,
            "min": self.minimum,
            "max": self.maximum,
            "volume": self.volume,
            "trades": self.trades,
        }


@dataclass
class ItemMarket:
    """Price intelligence for one (item, currency) pair."""

    item: str
    currency: str
    buckets: list[PriceBucket]
    median_price: float
    latest_price: float
    latest_day: int
    trend: str  # rising | falling | flat | insufficient
    trend_delta_pct: float | None
    short_median: float | None
    long_median: float | None
    total_volume: float
    total_trades: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "item": self.item,
            "itemPretty": prettify_eco_name(self.item),
            "currency": self.currency,
            "buckets": [b.to_dict() for b in self.buckets],
            "medianPrice": self.median_price,
            "latestPrice": self.latest_price,
            "latestDay": self.latest_day,
            "trend": self.trend,
            "trendDeltaPct": self.trend_delta_pct,
            "shortMedian": self.short_median,
            "longMedian": self.long_median,
            "totalVolume": self.total_volume,
            "totalTrades": self.total_trades,
        }


@dataclass
class MarketIntelligence:
    """Per-item price surface + provenance. JSON-serializable for the SPA."""

    fetched_at_iso: str
    source_base_url: str
    total_trades: int = 0
    markets: list[ItemMarket] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "view": "market",
            "fetchedAtISO": self.fetched_at_iso,
            "sourceBaseUrl": self.source_base_url,
            "totalTrades": self.total_trades,
            "markets": [m.to_dict() for m in self.markets],
            "warnings": list(self.warnings),
        }


def _normalize_item(name: str) -> str:
    """Fold an item id to a comparison key: lowercase, drop a trailing `item`.

    The exporter ships `IronIngotItem` where a caller says `IronIngot`, so
    comparing the folded keys (`ironingot`) needs no hand-maintained alias table.
    """
    stem = (name or "").strip().lower()
    if stem.endswith("item") and len(stem) > len("item"):
        stem = stem[: -len("item")]
    return stem


def _classify_trend(
    bucket_medians: list[tuple[int, float]],
    *,
    short_days: int,
    long_days: int,
    flat_band_pct: float,
) -> tuple[str, float | None, float | None, float | None]:
    """Short-window vs long-window median delta → (label, delta%, short, long).

    `bucket_medians` is `(day, median)` sorted ascending. Fewer than two
    distinct days can't establish a trend, so we say "insufficient" rather than
    fabricate a direction from a single point.
    """
    if len(bucket_medians) < 2:
        return "insufficient", None, None, None
    latest_day = bucket_medians[-1][0]
    short_vals = [m for d, m in bucket_medians if d > latest_day - short_days]
    long_vals = [m for d, m in bucket_medians if d > latest_day - long_days]
    if not short_vals or not long_vals:
        return "insufficient", None, None, None
    short_med = statistics.median(short_vals)
    long_med = statistics.median(long_vals)
    if long_med == 0:
        return "flat", None, short_med, long_med
    delta = (short_med - long_med) / long_med * 100.0
    if delta > flat_band_pct:
        label = "rising"
    elif delta < -flat_band_pct:
        label = "falling"
    else:
        label = "flat"
    return label, delta, short_med, long_med


def build_market(
    rows: Iterable[dict[str, Any]],
    *,
    top_markets: int = TOP_MARKETS,
    short_window_days: int = SHORT_WINDOW_DAYS,
    long_window_days: int = LONG_WINDOW_DAYS,
    flat_band_pct: float = FLAT_BAND_PCT,
    item: str | None = None,
    currency: str | None = None,
) -> list[ItemMarket]:
    """Fold ledger row dicts into per-(item, currency) price intelligence.

    `rows` are `trades.TradesLedger.trades` shaped dicts (camelCase, ids already
    resolved). Rows without an item, currency, or usable unit price never enter
    a price series. `item` / `currency` narrow the result (case-insensitive;
    `item` matches on the normalized key so `Iron` finds `IronIngotItem`).
    """
    want_item = _normalize_item(item) if item else None
    want_currency = currency.strip().lower() if currency else None

    # (item, currency) -> day -> list[unit_price], plus a parallel volume tally.
    prices: dict[tuple[str, str], dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    volume: dict[tuple[str, str], dict[int, float]] = defaultdict(lambda: defaultdict(float))

    for r in rows:
        item_name = (r.get("item") or "").strip()
        cur = (r.get("currency") or "").strip()
        unit_price = r.get("unitPrice")
        if not item_name or not cur or unit_price is None:
            continue
        try:
            price = float(unit_price)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        # Substring on the normalized key so a coarse filter ("Iron") catches
        # "IronIngotItem". `in_game_reference` matches exactly instead, so the
        # fair-value bridge never over-matches a partial name.
        if want_item is not None and want_item not in _normalize_item(item_name):
            continue
        if want_currency is not None and cur.lower() != want_currency:
            continue
        try:
            day = int(float(r.get("day") or 0))
        except (TypeError, ValueError):
            day = 0
        qty = r.get("quantity") or 0.0
        try:
            qty = float(qty)
        except (TypeError, ValueError):
            qty = 0.0
        key = (item_name, cur)
        prices[key][day].append(price)
        volume[key][day] += qty

    markets: list[ItemMarket] = []
    for (item_name, cur), by_day in prices.items():
        buckets: list[PriceBucket] = []
        all_prices: list[float] = []
        for day in sorted(by_day):
            day_prices = by_day[day]
            all_prices.extend(day_prices)
            buckets.append(
                PriceBucket(
                    day=day,
                    median=statistics.median(day_prices),
                    minimum=min(day_prices),
                    maximum=max(day_prices),
                    volume=volume[(item_name, cur)][day],
                    trades=len(day_prices),
                )
            )
        bucket_medians = [(b.day, b.median) for b in buckets]
        trend, delta, short_med, long_med = _classify_trend(
            bucket_medians,
            short_days=short_window_days,
            long_days=long_window_days,
            flat_band_pct=flat_band_pct,
        )
        markets.append(
            ItemMarket(
                item=item_name,
                currency=cur,
                buckets=buckets,
                median_price=statistics.median(all_prices),
                latest_price=buckets[-1].median,
                latest_day=buckets[-1].day,
                trend=trend,
                trend_delta_pct=delta,
                short_median=short_med,
                long_median=long_med,
                total_volume=sum(b.volume for b in buckets),
                total_trades=sum(b.trades for b in buckets),
            )
        )

    markets.sort(key=lambda m: (m.total_trades, m.total_volume), reverse=True)
    if top_markets > 0:
        markets = markets[:top_markets]
    return markets


async def fetch_market(
    base_url: str | None = None,
    api_key: str | None = None,
    *,
    item: str | None = None,
    currency: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> MarketIntelligence:
    """Fetch the trades ledger and fold it into price intelligence.

    Reuses `trades.fetch_ledger` (and its cache) wholesale — no second HTTP
    round-trip, no re-parse. The ledger's own truncation / partial-failure
    warnings propagate so the caller can tell "thin" from "capped".
    """
    ledger = await fetch_ledger(base_url=base_url, api_key=api_key, client=client)
    markets = build_market(ledger.trades, item=item, currency=currency)
    warnings = list(ledger.warnings)
    warnings.extend(_explain_empty_market(ledger.trades, markets, item=item, currency=currency))
    return MarketIntelligence(
        fetched_at_iso=ledger.fetched_at_iso,
        source_base_url=ledger.source_base_url,
        total_trades=ledger.total_trades,
        markets=markets,
        warnings=warnings,
    )


def _explain_empty_market(
    rows: list[dict[str, Any]],
    markets: list[ItemMarket],
    *,
    item: str | None,
    currency: str | None,
) -> list[str]:
    """Say why a market query came back empty (#218).

    `get_market` returned `markets: []` for every query, filtered or not, while
    reporting `totalTrades: 22891` in the same payload — so the tool looked
    like it was reaching data and finding nothing in it. The real cause was
    upstream: a price series needs an item, a currency and a usable unit price,
    and the currency-id join failure (#217) left every row's currency blank.
    An empty answer should name the missing ingredient rather than leave the
    caller to guess between "no data for that item" and "the tool is broken".
    """
    if markets or not rows:
        return []
    missing_currency = sum(1 for r in rows if not (r.get("currency") or "").strip())
    missing_item = sum(1 for r in rows if not (r.get("item") or "").strip())
    missing_price = sum(1 for r in rows if r.get("unitPrice") is None)
    if missing_currency == len(rows):
        return [
            f"No markets built: all {len(rows):,} ledger rows are missing a currency, so no "
            "price series can be keyed. See the currency attribution note (eco-app#217)."
        ]
    if missing_price == len(rows):
        return [
            f"No markets built: none of the {len(rows):,} ledger rows carry a usable unit "
            "price (quantity or currency amount absent)."
        ]
    if missing_item == len(rows):
        return [f"No markets built: none of the {len(rows):,} ledger rows name an item."]
    if item or currency:
        parts = []
        if item:
            parts.append(f"item={item!r}")
        if currency:
            parts.append(f"currency={currency!r}")
        wanted = " and ".join(parts)
        return [
            f"No markets matched {wanted} across {len(rows):,} ledger rows — the filter "
            "excluded everything priced."
        ]
    return [
        f"No markets built from {len(rows):,} ledger rows: every row lacked an item, a "
        "currency, or a usable unit price."
    ]


def price_map(markets: Iterable[ItemMarket]) -> dict[str, float]:
    """Collapse per-(item, currency) markets into one median price per item id.

    The cost engine (eco-app#98 C) needs a single "what does this leaf cost"
    number per item, but an item can trade in several currencies. We keep the
    **busiest** market for each item (most trades, tie-broken by volume) — its
    median is the dominant-currency price, the same "pick the busiest market"
    rule `in_game_reference` uses for the fair-value bridge. Keyed on the raw
    Eco item id (`IronIngotItem`); `cost._Resolver` also matches the normalized
    stem, so a recipe ingredient resolves either way.
    """
    best: dict[str, ItemMarket] = {}
    for m in markets:
        cur = best.get(m.item)
        if cur is None or (m.total_trades, m.total_volume) > (cur.total_trades, cur.total_volume):
            best[m.item] = m
    return {item: m.median_price for item, m in best.items()}


async def fetch_price_map(
    base_url: str | None = None,
    api_key: str | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, float]:
    """Fetch the ledger and reduce it to an item → median price map.

    Like `fetch_market` but **uncapped** (`top_markets=0`) and flattened to one
    price per item, the shape the cost engine wants. Reuses `fetch_ledger`'s
    cache, so a `/preview/recipes.json?cost=1` call rides the same fetch the
    `/trade` market view already warmed.
    """
    ledger = await fetch_ledger(base_url=base_url, api_key=api_key, client=client)
    markets = build_market(ledger.trades, top_markets=0)
    return price_map(markets)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def market_template_context(
    intel: MarketIntelligence, *, top: int = 12, spark_points: int = 24
) -> dict[str, Any]:
    """Shape for the MCP `_meta.ui` Jinja card. Product UX is the SPA; this
    fragment is the compact in-chat summary, so it ships only the top markets
    and a coarse sparkline of each one's recent bucket medians."""
    markets = intel.markets[:top]

    def _spark(buckets: list[PriceBucket]) -> list[dict[str, Any]]:
        pts = buckets[-spark_points:]
        vals = [b.median for b in pts]
        lo = min(vals) if vals else 0.0
        hi = max(vals) if vals else 0.0
        span = (hi - lo) or 1.0
        # 0..100 height for a CSS bar; a flat line sits mid-height.
        return [
            {"day": b.day, "pct": ((b.median - lo) / span) * 100.0 if hi != lo else 50.0}
            for b in pts
        ]

    return {
        "empty": len(markets) == 0,
        "fetched_at_iso": intel.fetched_at_iso,
        "source_base_url": intel.source_base_url,
        "total_trades": intel.total_trades,
        "markets": [
            {
                "item": m.item,
                "pretty": prettify_eco_name(m.item),
                "currency": m.currency,
                "median_price": m.median_price,
                "latest_price": m.latest_price,
                "trend": m.trend,
                "trend_arrow": _TREND_ARROW.get(m.trend, "·"),
                "trend_delta_pct": m.trend_delta_pct,
                "total_volume": m.total_volume,
                "total_trades": m.total_trades,
                "spark": _spark(m.buckets),
            }
            for m in markets
        ],
        "warnings": list(intel.warnings),
    }


def market_markdown(intel: MarketIntelligence) -> str:
    """Compact markdown summary for MCP hosts without the SPA / card."""
    if not intel.markets:
        return f"**Market price intelligence** — no priced trades yet ({intel.source_base_url})."
    lines = [
        f"**Market price intelligence** — {len(intel.markets)} item markets "
        f"from {intel.total_trades:,} trades (`{intel.source_base_url}`)",
        "",
    ]
    for m in intel.markets[:8]:
        delta = f" ({m.trend_delta_pct:+.0f}%)" if m.trend_delta_pct is not None else ""
        lines.append(
            f"- {prettify_eco_name(m.item)}: median "
            f"{m.median_price:,.2f} {m.currency}/unit "
            f"{_TREND_ARROW_ASCII.get(m.trend, '·')} {m.trend}{delta} "
            f"· {m.total_trades:,} trades, {m.total_volume:,.0f} units"
        )
    for w in intel.warnings:
        lines.append(f"- ⚠ {w}")
    return "\n".join(lines)
