"""Trades ledger — pull detailed CurrencyTrade / BarterTrade rows.

Aggregate time-series counters exist elsewhere, but the action exporter ships
*every individual trade*:
`/api/v1/exporter/actions?actionName=CurrencyTrade` returns one CSV row per
trade with columns::

    BankAccount, Currency, CurrencyAmount, NumberOfItems, BoughtOrSold,
    ShopOwner, Buyer, Seller, WorldObjectItem, ItemUsed, Citizen,
    ActionLocation, Count, Time

This module reconstructs a row-level ledger — who sold what to whom, for how
much, where, when — plus the aggregates that fall out of it almost for free:
top buyers / sellers by currency, per-currency volume, and a per-item
price-over-time series (unit price = CurrencyAmount / NumberOfItems, bucketed
by in-game day). `BarterTrade` shares the endpoint shape but is currency-free
(empty this cycle); we fetch it and fold whatever columns it exposes.

The server retains individual actions only for its detail window. Older
``TradeAction`` rows are per-citizen hourly rollups: ``Count`` is the number
of merged events, while party/item/store fields are one representative event.
``CurrencyAmount`` and ``NumberOfItems`` carry the action's
``SumInAggregateGrouping`` semantics and remain useful as unattributed
totals, but their ratio is not an item's price. Therefore ``Count > 1`` rows
never enter party, item, store, or price attribution. They do contribute to
the overall event count and currency totals, with an explicit warning for the
SPA and MCP clients (eco-app#132).

Design notes, mostly cribbed from the crafting atlas (eco-app#5):

* **Streaming.** CurrencyTrade grows without bound late-cycle, so we
  stream-parse via `crafting._stream_csv_rows` + a batched fold rather than
  buffering the whole body.
* **Defensive parsing.** Exporter rows occasionally carry an undeclared extra
  tool column that shifts every later field; `crafting._corrected_index`
  absorbs it. We key off the header, never fixed positions.
* **Numeric ids.** Buyer / Seller / ShopOwner / Citizen are numeric in-game
  ids. We join them to names via the jobs mod's `/api/v1/citizens` surface
  (`crafting.fetch_citizen_name_map`), falling back to `Citizen #<id>`.
* **BoughtOrSold.** An undecoded enum — live values 32 / 33. Best-effort
  decode below (heuristic, see eco-app#6); the who-sold-to-whom direction is
  read from the Buyer / Seller columns regardless, so a wrong decode never
  corrupts the ledger, only the secondary "direction" label.
* **Time.** Integer seconds since cycle start (same convention as the species
  population CSV, `species.py`) — in-game day = seconds / 86400.

Cache: an in-process `TTLCache` keyed per (base_url, api_key_hash). The ledger
is viewed in bursts; a short TTL keeps us off the admin endpoint without going
stale.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import statistics
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

# Both trade actions share the `/api/v1/exporter/actions` endpoint. CurrencyTrade
# is the money leg; BarterTrade is item-for-item (empty this cycle, folded
# best-effort).
TRADE_ACTION_TYPES = ("CurrencyTrade", "BarterTrade")

DEFAULT_CACHE_TTL_S = float(os.environ.get("ECO_TRADES_CACHE_TTL", "60"))

# Per-action safety valve, same rationale as crafting: 500k rows is ~50 MB of
# CSV, well past the late-cycle estimate and still sub-second to fold.
MAX_ROWS_PER_ACTION = int(os.environ.get("ECO_TRADES_MAX_ROWS", "500000"))

# We keep the aggregates over every parsed row but only ship this many
# individual rows to the client — the SPA table is browsable, not exhaustive.
# Newest trades win the cap.
MAX_LEDGER_ROWS = int(os.environ.get("ECO_TRADES_LEDGER_ROWS", "4000"))

# How many top items get a price-over-time series (keeps the payload lean).
TOP_PRICE_ITEMS = int(os.environ.get("ECO_TRADES_PRICE_ITEMS", "12"))

# In-game day length in real seconds, matching the species population CSV's
# `seconds / 86400` convention (species.py). The dataset survey's daily samples
# sit 86400s apart, so this keeps the ledger's day index aligned with them.
SECONDS_PER_DAY = 86400.0

# BoughtOrSold is an undecoded enum from Eco's GameActions source. Live values
# were 32 and 33; this is a best-effort decode (the Eco source wasn't reachable
# from the build container to confirm the polarity). It only labels a secondary
# "direction" chip — the authoritative buyer/seller comes from those columns —
# so a wrong guess never corrupts the ledger. See eco-app#6.
BOUGHT_OR_SOLD = {"32": "buy", "33": "sell"}

_trades_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=64, ttl=DEFAULT_CACHE_TTL_S)


def _cache_key(base_url: str, api_key: str | None) -> str:
    token = hashlib.sha256((api_key or "").encode()).hexdigest()[:12]
    return f"{base_url}|{token}"


@dataclass
class TradesLedger:
    """Row-level trades surface + derived aggregates. JSON-serializable."""

    fetched_at_iso: str
    source_base_url: str
    # Actual trade events: detail rows count one, hourly rollup rows contribute
    # their merged Count. This is intentionally not the number of CSV rows.
    total_trades: int = 0
    # Detail rows whose party/item/store fields remain attributable.
    detailed_trades: int = 0
    # Older hourly-rollup CSV rows and the merged events they represent. Their
    # currency totals are safe, but party/item/store attribution is not.
    rollup_rows: int = 0
    rollup_trades: int = 0
    # Day of the newest exporter row, rollups included (None = no rows). The freshness
    # check compares it with the cycle clock (ledger_freshness.py, COI-2067).
    newest_trade_day: float | None = None
    # CurrencyTrade / BarterTrade -> rows folded (0 = fetched-but-empty).
    per_type_counts: dict[str, int] = field(default_factory=dict)
    # Individual trades, newest first, capped at MAX_LEDGER_ROWS. Each dict is
    # already camelCase for the SPA (see `_row_dict`).
    trades: list[dict[str, Any]] = field(default_factory=list)
    total_currency_volume: float = 0.0
    # (item_id, trade_count, currency_volume), heaviest currency first.
    by_item: list[tuple[str, int, float]] = field(default_factory=list)
    # (currency_name, total_amount), heaviest first.
    by_currency: list[tuple[str, float]] = field(default_factory=list)
    # (name, currency_spent) and (name, currency_earned), heaviest first.
    top_buyers: list[tuple[str, float]] = field(default_factory=list)
    top_sellers: list[tuple[str, float]] = field(default_factory=list)
    # item_id -> [(day, mean_unit_price)] sorted by day, for the top items.
    price_series: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def exporter_rows(self) -> int:
        """CSV rows folded. This is what ``per_type_counts`` sums to."""
        return self.detailed_trades + self.rollup_rows

    def counts_note(self) -> str:
        """One sentence reconciling every trade count in the payload (#221).

        The sweep found four numbers presented as trade counts with nothing to
        say why they differ — `perTypeCounts` (3,672) reconciled with none of
        `totalTrades` (22,891), `rollupTrades` (22,365), or `detailedTrades`
        (526), while sitting in the same object as the last of them.
        """
        return (
            f"perTypeCounts counts exporter rows, not trades: {self.exporter_rows:,} rows = "
            f"{self.detailed_trades:,} detail rows (one trade each) + {self.rollup_rows:,} "
            f"hourly rollup rows covering {self.rollup_trades:,} merged trades, so the ledger "
            f"reports {self.total_trades:,} trade events. Eco's own /info counter "
            "(the economy line of get_server_status) counts a different population "
            "again and will not match."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fetchedAtISO": self.fetched_at_iso,
            "sourceBaseUrl": self.source_base_url,
            "totalTrades": self.total_trades,
            "detailedTrades": self.detailed_trades,
            "rollupRows": self.rollup_rows,
            "rollupTrades": self.rollup_trades,
            "newestTradeDay": self.newest_trade_day,
            "perTypeCounts": dict(self.per_type_counts),
            # Every count in one place, each labelled with its unit, so a
            # reader never has to guess which of four numbers to believe.
            "counts": {
                "exporterRows": self.exporter_rows,
                "tradeEvents": self.total_trades,
                "detailedRows": self.detailed_trades,
                "rollupRows": self.rollup_rows,
                "rollupTrades": self.rollup_trades,
                "note": self.counts_note(),
            },
            "trades": list(self.trades),
            "totalCurrencyVolume": self.total_currency_volume,
            "byItem": [[n, c, v] for n, c, v in self.by_item],
            "byCurrency": [[n, v] for n, v in self.by_currency],
            "topBuyers": [[n, v] for n, v in self.top_buyers],
            "topSellers": [[n, v] for n, v in self.top_sellers],
            "priceSeries": {
                item: [[d, p] for d, p in points] for item, points in self.price_series.items()
            },
            "warnings": list(self.warnings),
        }


@dataclass
class _ParsedTrade:
    """One trade before id->name resolution. Ids stay numeric here."""

    trade_type: str
    time_s: float
    day: float
    buyer_id: str
    seller_id: str
    shop_owner_id: str
    item: str
    quantity: float
    currency: str
    currency_amount: float
    unit_price: float | None
    store: str
    location: str
    direction: str
    # ``TradeAction`` rows with Count > 1 are server-side hourly rollups. The
    # exported event count and aggregate-safe numeric totals remain useful,
    # but labels describe only a representative event (eco-app#132).
    event_count: int
    is_rollup: bool


def _clean_name(value: str | None) -> str:
    """Drop values that are really positions / bare numbers where a name belongs.

    Misaligned exporter rows sometimes push a position triple or number into an
    item / store column; those are never legitimate ids, so we blank them out
    rather than render them (mirrors crafting's fold-time guard, eco-app#5).
    """
    v = (value or "").strip()
    if not v or _NONSENSE_KEY_RE.match(v):
        return ""
    return v


# A CurrencyTrade row's currency cell holds the Currency object's id, not its
# name (eco-app#217).
_NUMERIC_ID_RE = re.compile(r"^\d+$")


def _currency_cell(value: str) -> str:
    """Keep a trade row's currency cell, id or name.

    Unlike item / store columns, a bare number here is the real value: the
    exporter keys CurrencyTrade rows by the Currency object's id. Blanking it
    as a misalignment artifact dropped the currency from every detailed row
    (eco-app#217). Positions (``"1,2,3"``) are still artifacts and go.
    """
    v = (value or "").strip()
    if not v or ("," in v and _NONSENSE_KEY_RE.match(v)):
        return ""
    return v


def resolve_parsed_currencies(parsed: list[_ParsedTrade], id_to_name: dict[str, str]) -> int:
    """Rewrite id-valued currency cells to display names, in place (#217).

    Applied to the shared parsed rows before anything folds them, so the
    ledger, the store directory, and the market boards all inherit the fix from
    one pass. The map comes from the stores/economy exporter mod's holdings
    surface, the only source pairing a Currency id with its name.

    Returns the number of rows left unresolved. Those keep their raw id — the
    volume is real — rather than being blanked or renamed to a guess.
    """
    unresolved = 0
    for trade in parsed:
        if not trade.currency:
            continue
        name = id_to_name.get(trade.currency)
        if name:
            trade.currency = name
        elif _NUMERIC_ID_RE.match(trade.currency):
            unresolved += 1
    return unresolved


def parse_trade_rows(
    action_name: str,
    rows: Iterable[list[str]],
    ledger: TradesLedger,
    parsed: list[_ParsedTrade],
    max_rows: int = MAX_ROWS_PER_ACTION,
) -> int:
    """Fold one action's CSV rows into `parsed` (raw ids) and bump per-type count.

    Returns the number of data rows consumed (excluding the header). Aggregates
    that need id->name resolution are computed later, once every action folds.
    """
    it = iter(rows)
    try:
        header = next(it)
    except StopIteration:
        return 0

    col = {name: i for i, name in enumerate(header)}

    def pick(row: list[str], idx: list[int], *candidates: str) -> str:
        for c in candidates:
            j = col.get(c)
            if j is not None and idx[j] < len(row):
                v = row[idx[j]].strip()
                if v:
                    return v
        return ""

    def pick_float(row: list[str], idx: list[int], *candidates: str) -> float:
        raw = pick(row, idx, *candidates)
        try:
            parsed = float(raw)
        except ValueError:
            return 0.0
        # ``float("inf")`` / ``float("nan")`` parse without raising; a non-finite
        # currency/quantity here feeds ``inf`` into the unit-price division and
        # ultimately 500s ``JSONResponse`` (eco-app#83). Treat it as absent.
        return parsed if math.isfinite(parsed) else 0.0

    consumed = 0
    for row in it:
        if not row:
            continue
        if consumed >= max_rows:
            ledger.warnings.append(
                f"{action_name}: truncated at {max_rows} rows (late-cycle size cap)"
            )
            break
        idx = _corrected_index(header, row)

        time_s = pick_float(row, idx, "Time")
        quantity = pick_float(row, idx, "NumberOfItems")
        currency_amount = pick_float(row, idx, "CurrencyAmount")
        # Count is the event count on an individual trade and the merged event
        # count on an older hourly rollup. Some best-effort Barter exports omit
        # it, where a CSV row still proves one event.
        event_count = max(int(pick_float(row, idx, "Count")), 1)
        is_rollup = event_count > 1
        unit_price = currency_amount / quantity if quantity > 0 and currency_amount else None
        raw_dir = pick(row, idx, "BoughtOrSold")

        parsed.append(
            _ParsedTrade(
                trade_type=action_name,
                time_s=time_s,
                day=time_s / SECONDS_PER_DAY,
                buyer_id=pick(row, idx, "Buyer"),
                seller_id=pick(row, idx, "Seller"),
                shop_owner_id=pick(row, idx, "ShopOwner"),
                item=_clean_name(pick(row, idx, "ItemUsed")),
                quantity=quantity,
                # The exporter writes the Currency object's *id* here, not its
                # name. `_clean_name` treats a bare number as a misalignment
                # artifact and blanks it, which is why every one of the 526
                # detailed rows reported `currency: ""` while the payload also
                # said the currency column was present (eco-app#217). Keep the
                # raw value; `resolve_currency_names` turns it into a name once
                # the id map is available.
                currency=_currency_cell(pick(row, idx, "Currency")),
                currency_amount=currency_amount,
                unit_price=unit_price,
                store=_clean_name(pick(row, idx, "WorldObjectItem")),
                location=pick(row, idx, "ActionLocation"),
                direction=BOUGHT_OR_SOLD.get(raw_dir, raw_dir),
                event_count=event_count,
                is_rollup=is_rollup,
            )
        )
        consumed += 1

    ledger.per_type_counts[action_name] = ledger.per_type_counts.get(action_name, 0) + consumed
    return consumed


def _label(cid: str, name_map: dict[str, str]) -> str:
    """id -> display name, `Citizen #<id>` fallback, blank stays blank."""
    if not cid:
        return ""
    if not _INT_RE.match(cid):
        return cid  # already a name, or an artifact we leave verbatim
    name = name_map.get(cid)
    return name if name is not None else f"Citizen #{cid}"


def _row_dict(t: _ParsedTrade, name_map: dict[str, str]) -> dict[str, Any]:
    """A single ledger row, camelCase, with ids resolved to names."""
    return {
        "tradeType": t.trade_type,
        "time": t.time_s,
        "day": t.day,
        "buyer": _label(t.buyer_id, name_map),
        "seller": _label(t.seller_id, name_map),
        "shopOwner": _label(t.shop_owner_id, name_map),
        "item": t.item,
        "quantity": t.quantity,
        "currency": t.currency,
        "currencyAmount": t.currency_amount,
        "unitPrice": t.unit_price,
        "store": t.store,
        "location": t.location,
        "direction": t.direction,
        "eventCount": t.event_count,
        "aggregated": t.is_rollup,
    }


def build_ledger(
    parsed: list[_ParsedTrade], ledger: TradesLedger, name_map: dict[str, str]
) -> None:
    """Resolve ids and roll `parsed` up into the ledger's rows + aggregates."""
    ledger.total_trades = sum(t.event_count for t in parsed)
    ledger.detailed_trades = sum(1 for t in parsed if not t.is_rollup)
    ledger.rollup_rows = sum(1 for t in parsed if t.is_rollup)
    ledger.rollup_trades = sum(t.event_count for t in parsed if t.is_rollup)
    ledger.newest_trade_day = max((t.day for t in parsed), default=None)

    by_item_count: dict[str, int] = defaultdict(int)
    by_item_volume: dict[str, float] = defaultdict(float)
    by_currency: dict[str, float] = defaultdict(float)
    buyers: dict[str, float] = defaultdict(float)
    sellers: dict[str, float] = defaultdict(float)
    # item -> day -> [unit prices], collapsed to a per-day mean below.
    price_points: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))

    for t in parsed:
        # CurrencyAmount is summed by CurrencyTrade's aggregation contract, so
        # gross and per-currency volume retain all history. The party/item/store
        # fields are representative on a rollup and must never be attributed.
        ledger.total_currency_volume += t.currency_amount
        if t.currency:
            by_currency[t.currency] += t.currency_amount
        if t.is_rollup:
            continue
        if t.item:
            by_item_count[t.item] += 1
            by_item_volume[t.item] += t.currency_amount
        if t.currency_amount:
            if t.buyer_id:
                buyers[_label(t.buyer_id, name_map)] += t.currency_amount
            if t.seller_id:
                sellers[_label(t.seller_id, name_map)] += t.currency_amount
        if t.item and t.unit_price is not None:
            price_points[t.item][int(t.day)].append(t.unit_price)

    ledger.by_item = sorted(
        ((item, by_item_count[item], by_item_volume[item]) for item in by_item_count),
        key=lambda r: (r[2], r[1]),
        reverse=True,
    )
    ledger.by_currency = sorted(by_currency.items(), key=lambda kv: kv[1], reverse=True)
    ledger.top_buyers = sorted(buyers.items(), key=lambda kv: kv[1], reverse=True)
    ledger.top_sellers = sorted(sellers.items(), key=lambda kv: kv[1], reverse=True)

    # Price-over-time for the busiest items only. Per (item, day) we take the
    # mean unit price so a single outlier trade doesn't spike the line.
    top_items = [item for item, _, _ in ledger.by_item[:TOP_PRICE_ITEMS]]
    for item in top_items:
        days = price_points.get(item)
        if not days:
            continue
        series = [(float(day), statistics.fmean(prices)) for day, prices in sorted(days.items())]
        if series:
            ledger.price_series[item] = series

    # Newest attributable trades first, capped. Rollup rows never appear in the
    # row-level ledger: their labels would fabricate party/item attribution.
    ordered = sorted((t for t in parsed if not t.is_rollup), key=lambda t: t.time_s, reverse=True)
    if len(ordered) > MAX_LEDGER_ROWS:
        ledger.warnings.append(
            f"ledger truncated to newest {MAX_LEDGER_ROWS} of {len(ordered)} detailed trades "
            "(aggregates cover all)"
        )
        ordered = ordered[:MAX_LEDGER_ROWS]
    ledger.trades = [_row_dict(t, name_map) for t in ordered]
    if ledger.rollup_rows:
        ledger.warnings.append(
            f"{ledger.rollup_trades} older trades arrive as {ledger.rollup_rows} hourly "
            "rollups; party, item, store, and unit-price views cover detailed rows only. "
            "Overall currency volume includes their summed amounts (eco-app#132)"
        )


@dataclass
class ParsedTradeFetch:
    """Raw fetch result — parsed rows + the id->name map, before aggregation.

    Shared handoff between :func:`fetch_ledger` and the store / trader
    directory (`stores.py`, eco-app#50): both fold the *same* streamed rows into
    different shapes, so the network + parse + citizen-join happens once here.
    """

    normalized_base_url: str
    parsed: list[_ParsedTrade] = field(default_factory=list)
    name_map: dict[str, str] = field(default_factory=dict)
    per_type_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # Currency ids left unresolved because the exporter mod is absent or older
    # than the id field. Reported, never guessed at. See eco-app#217.
    unresolved_currency_rows: int = 0


async def fetch_parsed_trades(
    base_url: str | None = None,
    api_key: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> ParsedTradeFetch:
    """Stream both trade action CSVs and resolve citizen names, no aggregation.

    This is the shared fetch spine — `fetch_ledger` folds the result into a
    row-level ledger, `stores.build_directory` folds the same rows into store /
    trader profiles (eco-app#50). `client` is injectable so tests can hand in a
    pre-stubbed httpx client. When omitted we build one with a 30 s timeout —
    late-cycle CSVs take a beat.
    """
    normalized = _normalize_admin_base(base_url)
    headers = {"X-API-Key": api_key} if api_key else {}
    # A throwaway ledger used only as the mutable sink `parse_trade_rows`
    # expects (its per_type_counts + warnings); its aggregates are never built.
    scratch = TradesLedger(fetched_at_iso="", source_base_url=normalized)
    parsed: list[_ParsedTrade] = []

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0))
    try:
        for action in TRADE_ACTION_TYPES:
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
                        consumed = parse_trade_rows(
                            action, batch, scratch, parsed, max_rows=remaining
                        )
                        remaining -= consumed
                        if remaining <= 0:
                            break
                        batch = [header]
                if header is not None and len(batch) > 1 and remaining > 0:
                    parse_trade_rows(action, batch, scratch, parsed, max_rows=remaining)
                # Record fetched-but-empty so the UI tells "empty" from "errored".
                scratch.per_type_counts.setdefault(action, 0)
            except httpx.HTTPStatusError as e:
                scratch.warnings.append(f"{action}: HTTP {e.response.status_code}")
            except httpx.HTTPError as e:
                scratch.warnings.append(f"{action}: {type(e).__name__}: {e}")

        name_map: dict[str, str] = {}
        unresolved_currency_rows = 0
        if parsed:
            name_map = await fetch_citizen_name_map(http, normalized, headers, scratch.warnings)
        # The exporter keys trades by Currency id; only the stores/economy
        # exporter mod knows the names (eco-app#217). Resolve here, on the
        # shared rows, so the ledger, the store directory and the market boards
        # all inherit one join. Skipped entirely when no row carries an id —
        # a server whose export already names currencies costs no extra call.
        if any(_NUMERIC_ID_RE.match(t.currency) for t in parsed if t.currency):
            from .currency import fetch_currency_id_map

            id_map = await fetch_currency_id_map(http, normalized, headers)
            unresolved_currency_rows = resolve_parsed_currencies(parsed, id_map)
            if unresolved_currency_rows:
                scratch.warnings.append(
                    f"{unresolved_currency_rows:,} trade row(s) name a currency id the "
                    "stores/economy exporter mod (eco-app#58) did not resolve to a name; "
                    "they keep the raw id rather than being dropped or guessed at"
                )
    finally:
        if owns_client:
            await http.aclose()

    return ParsedTradeFetch(
        normalized_base_url=normalized,
        parsed=parsed,
        name_map=name_map,
        per_type_counts=scratch.per_type_counts,
        warnings=scratch.warnings,
        unresolved_currency_rows=unresolved_currency_rows,
    )


async def fetch_ledger(
    base_url: str | None = None,
    api_key: str | None = None,
    cache_ttl_s: float = DEFAULT_CACHE_TTL_S,
    client: httpx.AsyncClient | None = None,
) -> TradesLedger:
    """Stream both trade action CSVs and fold them into a single ledger.

    `client` is injectable so tests can hand in a pre-stubbed httpx client. When
    omitted we build one with a 30 s timeout — late-cycle CSVs take a beat.
    """
    normalized = _normalize_admin_base(base_url)
    key = _cache_key(normalized, api_key)
    if cache_ttl_s > 0:
        cached = _trades_cache.get(key)
        if cached is not None:
            return _ledger_from_dict(cached)

    fetch = await fetch_parsed_trades(base_url=base_url, api_key=api_key, client=client)
    ledger = TradesLedger(fetched_at_iso=_now_iso(), source_base_url=fetch.normalized_base_url)
    ledger.per_type_counts = dict(fetch.per_type_counts)
    ledger.warnings = list(fetch.warnings)
    build_ledger(fetch.parsed, ledger, fetch.name_map)

    if cache_ttl_s > 0:
        _trades_cache[key] = ledger.to_dict()
    return ledger


def _ledger_from_dict(data: dict[str, Any]) -> TradesLedger:
    """Rehydrate a cached ledger dict back into a TradesLedger."""
    return TradesLedger(
        fetched_at_iso=data["fetchedAtISO"],
        source_base_url=data["sourceBaseUrl"],
        total_trades=int(data["totalTrades"]),
        detailed_trades=int(data.get("detailedTrades", data["totalTrades"])),
        rollup_rows=int(data.get("rollupRows", 0)),
        rollup_trades=int(data.get("rollupTrades", 0)),
        newest_trade_day=data.get("newestTradeDay"),
        per_type_counts=dict(data.get("perTypeCounts", {})),
        trades=list(data.get("trades", [])),
        total_currency_volume=float(data.get("totalCurrencyVolume", 0.0)),
        by_item=[(n, int(c), float(v)) for n, c, v in data.get("byItem", [])],
        by_currency=[(n, float(v)) for n, v in data.get("byCurrency", [])],
        top_buyers=[(n, float(v)) for n, v in data.get("topBuyers", [])],
        top_sellers=[(n, float(v)) for n, v in data.get("topSellers", [])],
        price_series={
            item: [(float(d), float(p)) for d, p in points]
            for item, points in data.get("priceSeries", {}).items()
        },
        warnings=list(data.get("warnings", [])),
    )


def ledger_template_context(
    ledger: TradesLedger,
    top_items: int = 12,
    top_traders: int = 8,
    recent: int = 15,
) -> dict[str, Any]:
    """Shape for the MCP `_meta.ui` Jinja card. Product UX is the SPA — this
    card is only the in-chat fragment, so it stays a compact summary."""
    from .crafting import prettify_eco_name

    items = ledger.by_item[:top_items]
    max_item = max((c for _, c, _ in items), default=0) or 1

    def _trader_rows(rows: list[tuple[str, float]]) -> list[dict[str, Any]]:
        top = rows[:top_traders]
        max_amt = max((a for _, a in top), default=0.0) or 1.0
        return [{"name": n, "amount": a, "pct": (a / max_amt) * 100.0} for n, a in top]

    return {
        "empty": ledger.total_trades == 0,
        "fetched_at_iso": ledger.fetched_at_iso,
        "source_base_url": ledger.source_base_url,
        "total_trades": ledger.total_trades,
        "detailed_trades": ledger.detailed_trades,
        "rollup_rows": ledger.rollup_rows,
        "rollup_trades": ledger.rollup_trades,
        "total_currency_volume": ledger.total_currency_volume,
        "per_type_counts": [(n, c) for n, c in ledger.per_type_counts.items() if c],
        "top_items": [
            {
                "name": name,
                "pretty": prettify_eco_name(name),
                "count": count,
                "volume": volume,
                "pct": (count / max_item) * 100.0,
            }
            for name, count, volume in items
        ],
        "top_buyers": _trader_rows(ledger.top_buyers),
        "top_sellers": _trader_rows(ledger.top_sellers),
        "by_currency": ledger.by_currency,
        "recent": [
            {
                "seller": r["seller"] or "—",
                "buyer": r["buyer"] or "—",
                "item": prettify_eco_name(r["item"]) if r["item"] else "—",
                "quantity": r["quantity"],
                "currency": r["currency"],
                "currency_amount": r["currencyAmount"],
                "day": int(r["day"]),
            }
            for r in ledger.trades[:recent]
        ],
        "warnings": list(ledger.warnings),
    }


def ledger_markdown(ledger: TradesLedger) -> str:
    """Compact markdown summary for MCP hosts without the SPA / card."""
    if ledger.total_trades == 0:
        return f"**Trades ledger** — no trades recorded yet ({ledger.source_base_url})."
    lines = [
        f"**Trades ledger** — {ledger.total_trades:,} trade events from "
        f"{ledger.exporter_rows:,} exporter rows "
        f"({ledger.total_currency_volume:,.0f} total currency, `{ledger.source_base_url}`)",
        "",
    ]
    if ledger.top_sellers:
        top = ", ".join(f"{name} ({amt:,.0f})" for name, amt in ledger.top_sellers[:3])
        lines.append(f"- Top sellers: {top}")
    if ledger.by_item:
        from .crafting import prettify_eco_name

        top = ", ".join(
            f"{prettify_eco_name(item)} ({count:,})" for item, count, _ in ledger.by_item[:5]
        )
        lines.append(f"- Most-traded items: {top}")
    if ledger.by_currency:
        top = ", ".join(f"{cur} ({amt:,.0f})" for cur, amt in ledger.by_currency[:3])
        lines.append(f"- Currencies: {top}")
    for w in ledger.warnings:
        lines.append(f"- ⚠ {w}")
    return "\n".join(lines)
