"""Currency & money-supply visibility - the DiscordLink `Currency` /
`Currencies` surface, owned on the website.

DiscordLink ships two currency commands this module meets for everything
exportable today (see ``docs/datasets/currency.md`` for the probe):

* ``Currencies`` - the roster of live currencies, split into **minted/backed**
  and **personal/credit**, ranked by trade activity.
* ``Currency <name>`` - one currency's trade count, issuance (minted amount),
  and holder list.

The **top-holder list** is served live by the stores/economy exporter mod
(``mods/stores``, eco-app#58): it reads per-account currency balances from the
in-process ``CurrencyManager`` and joins account owners to names via the same
``UserManager`` access the jobs mod uses for ``/api/v1/citizens``. That surface
is best-effort - when the mod DLL is not deployed on the target server the
endpoint 404s and the per-currency report degrades to
``HOLDERS_UNAVAILABLE_NOTE`` rather than faking a list. Everything else is
reachable from the admin dataset catalog.

Four slices, each absence-tolerant (empty is a valid state, not an error):

1. **Money-supply series via ``/datasets/get``** - ``ActiveCurrencies`` (count),
   ``TradesInLast7Days`` (rolling trade value), ``PersonalWealthInDefaultCurrency``
   and ``GovernmentHoldingsInDefaultCurrency`` (aggregate holdings). Same
   fan-out + 200/500-tolerant pattern as ``climate.fetch_climate``.

2. **Currency roster + issuance via the action exporter** - stream-parse
   ``CreateCurrency`` (the full roster + founder) and ``MintCurrency`` (which
   currencies are backed, and by how much). A currency that mints is
   **minted/backed**; one that only appears via creation/trade is
   **personal/credit**.

3. **Per-currency trade volume via ``CurrencyTrade``** - fold the trade rows by
   currency name when the exporter carries that column, giving each currency a
   trade count and traded volume that mirror what ``Currency <name>`` reports.

Column names are keyed off the CSV header with candidate fallbacks (the
exporter's schema drifts across Eco versions and mods), matching how
``crafting.py`` and ``climate.py`` already read the exporter.
"""

from __future__ import annotations

import asyncio
import csv
import difflib
import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
from cachetools import TTLCache

# ---------------------------------------------------------------------------
# Dataset + action names (confirmed live on Eco 0.13.0.4 via the public
# /datasets/flatlist catalog - see docs/datasets/currency.md).
# ---------------------------------------------------------------------------

# ContinuousValue series pulled from /datasets/get. Each is absence-tolerant.
ACTIVE_CURRENCIES_DATASET = "ActiveCurrencies"
TRADES_7D_DATASET = "TradesInLast7Days"
PERSONAL_WEALTH_DATASET = "PersonalWealthInDefaultCurrency"
GOVERNMENT_HOLDINGS_DATASET = "GovernmentHoldingsInDefaultCurrency"

MONEY_SUPPLY_DATASETS: tuple[str, ...] = (
    ACTIVE_CURRENCIES_DATASET,
    TRADES_7D_DATASET,
    PERSONAL_WEALTH_DATASET,
    GOVERNMENT_HOLDINGS_DATASET,
)

# Action exporters (CSV). CreateCurrency names every currency; MintCurrency
# names the backed ones; CurrencyTrade carries per-trade volume.
CREATE_CURRENCY_ACTION = "CreateCurrency"
MINT_CURRENCY_ACTION = "MintCurrency"
CURRENCY_TRADE_ACTION = "CurrencyTrade"

# CSV column candidates, tried in order against the header row. The exporter's
# column naming drifts across Eco versions / mods, so we key defensively.
_CURRENCY_NAME_KEYS: tuple[str, ...] = ("Currency", "CurrencyName", "CurrencyType", "Name")
_AMOUNT_KEYS: tuple[str, ...] = (
    "CurrencyAmount",
    "MoneyAmount",
    "Amount",
    "MintedAmount",
    "TotalPrice",
    "Price",
    "Value",
    "Count",
)
_CREATOR_KEYS: tuple[str, ...] = ("Citizen", "Creator", "Founder", "Player", "User")

# A CurrencyTrade row's currency cell holds the Currency object's id, not its
# name (eco-app#217). A bare integer in a name column is that id.
_NUMERIC_ID_RE = re.compile(r"^\d+$")

# The stores/economy exporter mod (mods/stores, eco-app#58) serves per-account
# currency balances live from CurrencyManager at this path, under the same
# admin-token surface as the action exporter. Best-effort: an absent mod (DLL
# not yet deployed) 404s and the report degrades to HOLDERS_UNAVAILABLE_NOTE.
CURRENCY_HOLDINGS_PATH = "/api/v1/currency-holdings"

# How many holders the per-currency report keeps. The mod truncates its own
# list too; we re-cap so the payload stays lean regardless of the mod's cap.
_MAX_HOLDERS = int(os.environ.get("ECO_CURRENCY_MAX_HOLDERS", "15"))

# Shown on the per-currency report when the holdings endpoint is unreachable -
# the exporter mod is not deployed on this server yet (the DLL lands at the next
# restart, out of band). Not faked, matching the AGENTS.md pull-everything rule.
HOLDERS_UNAVAILABLE_NOTE = (
    "Live top-holder balances need the stores/economy exporter mod running on "
    "the server (eco-app#58); it is not reachable right now."
)

# Per-process cache. Currency state moves slowly; a 45s TTL matches the economy
# card and stops the iframe-on-refresh case from hammering the admin endpoint.
_CACHE_TTL_S = float(os.environ.get("ECO_CURRENCY_CACHE_TTL", "45"))
_currency_cache: TTLCache[str, CurrencySnapshot] = TTLCache(maxsize=64, ttl=_CACHE_TTL_S)

# Action exporter row cap - same defensive bound crafting.py / climate.py use.
_MAX_ROWS_PER_ACTION = int(os.environ.get("ECO_CURRENCY_MAX_ROWS", "200000"))


@dataclass
class CurrencyHolder:
    """One account's balance in a currency - a row of the top-holders table.

    ``holder`` is the resolved owner citizen name, or ``None`` for a
    government/company account with no single owner (the ``account`` name
    carries those). Sourced from the stores/economy exporter mod (eco-app#58).
    """

    account: str
    holder: str | None
    balance: float


@dataclass
class CurrencyRecord:
    """One currency's reachable facts. JSON-serialized via the snapshot."""

    name: str
    is_minted: bool = False
    minted_amount: float = 0.0
    mint_events: int = 0
    trade_count: int = 0
    trade_volume: float = 0.0
    created_by: str | None = None
    # The in-game Currency id, from the exporter mod's holdings surface. The
    # action exporter keys CurrencyTrade rows by this, not by name (eco-app#217).
    currency_id: str | None = None
    # True while this record exists only because a trade row named an id no
    # roster entry claims — an id, not a currency name, and labelled as such.
    unresolved_id: bool = False
    # Top-holder balances from the exporter mod (eco-app#58). ``holders_reachable``
    # stays False until the endpoint answers for this currency, so the report can
    # tell "no holders yet" apart from "mod not deployed".
    holders_reachable: bool = False
    total_holdings: float = 0.0
    accounts_counted: int = 0
    top_holders: list[CurrencyHolder] = field(default_factory=list)


@dataclass
class CurrencySnapshot:
    """Raw fetch result. JSON-serializable via :meth:`to_dict`."""

    fetched_at_iso: str
    source_base_url: str
    info: dict[str, Any]
    days_elapsed: int
    admin_ok: bool
    # A refused read is not an absent feature. 404/405 mean the server does not
    # expose an action; 401 means our token was rejected. See #266.
    admin_reads_refused: bool = False
    # Money-supply series (each [(t, v), ...], possibly empty).
    active_currencies_series: list[tuple[float, float]] = field(default_factory=list)
    trades_7d_series: list[tuple[float, float]] = field(default_factory=list)
    personal_wealth_series: list[tuple[float, float]] = field(default_factory=list)
    government_holdings_series: list[tuple[float, float]] = field(default_factory=list)
    # Per-currency roster, keyed by currency name.
    currencies: dict[str, CurrencyRecord] = field(default_factory=dict)
    # Currency id -> display name, from the exporter mod's holdings surface.
    # The action exporter keys CurrencyTrade rows by id (eco-app#217).
    currency_id_to_name: dict[str, str] = field(default_factory=dict)
    # Whether each action's currency column was present (so an empty roster can
    # be explained: "the exporter didn't carry a Currency column" vs "no events").
    trade_rows_total: int = 0
    trade_currency_column_seen: bool = False
    # Whether the stores/economy exporter's holdings endpoint answered at all
    # (eco-app#58). False means the mod is not deployed on this server, so the
    # per-currency report shows HOLDERS_UNAVAILABLE_NOTE instead of a list.
    holders_reachable: bool = False
    # Currency-relevant dataset names the catalog exposes - surfaced so an
    # empty card is debuggable without server logs.
    available_currency_datasets: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def record(self, name: str) -> CurrencyRecord:
        rec = self.currencies.get(name)
        if rec is None:
            rec = CurrencyRecord(name=name)
            self.currencies[name] = rec
        return rec

    def to_dict(self) -> dict[str, Any]:
        return {
            "fetchedAtISO": self.fetched_at_iso,
            "sourceBaseUrl": self.source_base_url,
            "daysElapsed": self.days_elapsed,
            "adminOk": self.admin_ok,
            "activeCurrenciesSeries": [[t, v] for t, v in self.active_currencies_series],
            "trades7dSeries": [[t, v] for t, v in self.trades_7d_series],
            "personalWealthSeries": [[t, v] for t, v in self.personal_wealth_series],
            "governmentHoldingsSeries": [[t, v] for t, v in self.government_holdings_series],
            "currencies": [
                {
                    "name": r.name,
                    "isMinted": r.is_minted,
                    "mintedAmount": r.minted_amount,
                    "mintEvents": r.mint_events,
                    "tradeCount": r.trade_count,
                    "tradeVolume": r.trade_volume,
                    "createdBy": r.created_by,
                    "holdersReachable": r.holders_reachable,
                    "totalHoldings": round(r.total_holdings, 2) if r.holders_reachable else None,
                    "accountsCounted": r.accounts_counted if r.holders_reachable else None,
                    "topHolders": [
                        {"account": h.account, "holder": h.holder, "balance": round(h.balance, 2)}
                        for h in r.top_holders
                    ],
                }
                for r in self.currencies.values()
            ],
            "tradeRowsTotal": self.trade_rows_total,
            "tradeCurrencyColumnSeen": self.trade_currency_column_seen,
            "holdersReachable": self.holders_reachable,
            "availableCurrencyDatasets": list(self.available_currency_datasets),
            "warnings": list(self.warnings),
        }


def _admin_base(server: str | None, default_base: str) -> str:
    """Resolve a user-supplied server hint to an admin base URL.

    Same accepted shapes as the rest of the toolset: bare host, host:port,
    full URL, or ``None`` to use the configured default.
    """
    if not server:
        return default_base.rstrip("/")
    s = server.strip()
    if not s:
        return default_base.rstrip("/")
    if "://" not in s:
        s = f"http://{s}"
    parsed = urlparse(s)
    host = parsed.hostname or ""
    port = parsed.port or 3001
    return f"{parsed.scheme or 'http'}://{host}:{port}".rstrip("/")


# ---------------------------------------------------------------------------
# Series fetch (/datasets/get) - shares the parsing shapes with climate.py but
# is duplicated here to keep this module free of a climate dependency.
# ---------------------------------------------------------------------------

_DATASET_TIME_KEYS: tuple[str, ...] = ("Time", "time", "_id", "Id", "T")


def _parse_dataset_point(pt: Any) -> tuple[float, float] | None:
    """Coerce one ``/datasets/get`` point to ``(time, value)``.

    Tolerates the shapes Eco 0.13 returns: ``{"Time": t, "Value": v}``,
    ``{"_id": t, "Value": v}``, ``{"_id": t, "<ShortName>": v}`` (value under
    the stat's short-name key), and flat ``[t, v]`` pairs. ``None`` drops the
    point.
    """
    if isinstance(pt, list | tuple) and len(pt) >= 2:
        try:
            return float(pt[0]), float(pt[1])
        except (TypeError, ValueError):
            return None
    if not isinstance(pt, dict):
        return None
    time_key: str | None = None
    t_raw: Any = None
    for k in _DATASET_TIME_KEYS:
        if k in pt:
            time_key = k
            t_raw = pt[k]
            break
    if time_key is None:
        return None
    v_raw: Any = pt.get("Value", pt.get("value"))
    if v_raw is None:
        for k, val in pt.items():
            if k == time_key:
                continue
            if isinstance(val, int | float) and not isinstance(val, bool):
                v_raw = val
                break
    if v_raw is None:
        return None
    try:
        return float(t_raw), float(v_raw)
    except (TypeError, ValueError):
        return None


def _parse_parallel_arrays(data: dict[str, Any]) -> list[tuple[float, float]]:
    """Zip an Eco 0.13 ``{"Times": [...], "Values": [...]}`` payload."""
    times = data.get("Times") or data.get("times")
    values = data.get("Values") or data.get("values")
    if not isinstance(times, list) or not isinstance(values, list):
        return []
    pairs: list[tuple[float, float]] = []
    for t_raw, v_raw in zip(times, values, strict=False):
        try:
            pairs.append((float(t_raw), float(v_raw)))
        except (TypeError, ValueError):
            continue
    return pairs


async def _fetch_dataset(
    client: httpx.AsyncClient,
    base: str,
    name: str,
    day_end: int,
    headers: dict[str, str],
) -> list[tuple[float, float]]:
    """GET ``/datasets/get`` for one series. Returns ``[]`` on any non-200."""
    try:
        r = await client.get(
            f"{base}/datasets/get",
            params={"dataset": name, "dayStart": 0, "dayEnd": max(day_end, 1)},
            headers=headers,
        )
        if r.status_code != 200:
            return []
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return []
    out: list[tuple[float, float]] = []
    if isinstance(data, list):
        for pt in data:
            parsed = _parse_dataset_point(pt)
            if parsed is not None:
                out.append(parsed)
    elif isinstance(data, dict):
        out.extend(_parse_parallel_arrays(data))
    return out


async def _fetch_dataset_flatlist(
    client: httpx.AsyncClient, base: str, headers: dict[str, str]
) -> list[str]:
    """GET ``/datasets/flatlist`` - the catalog of all dataset stat names.

    Normalizes the two shapes Eco returns (``list[dict]`` with metadata on
    0.13, ``list[str]`` on older builds) to a plain list of names. ``[]`` on
    any non-200 or unexpected shape.
    """
    try:
        r = await client.get(f"{base}/datasets/flatlist", headers=headers)
        if r.status_code != 200:
            return []
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return []
    if isinstance(data, dict):
        for key in ("datasets", "Datasets", "values", "Values"):
            v = data.get(key)
            if isinstance(v, list):
                data = v
                break
        else:
            return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for entry in data:
        if isinstance(entry, str):
            if entry:
                out.append(entry)
        elif isinstance(entry, dict):
            name = entry.get("Name") or entry.get("name")
            if name:
                out.append(str(name))
    return out


# ---------------------------------------------------------------------------
# Action exporter (CSV) - roster, issuance, trade volume.
# ---------------------------------------------------------------------------


async def _stream_csv_rows(
    client: httpx.AsyncClient, url: str, headers: dict[str, str]
) -> AsyncIterator[list[str]]:
    """Yield CSV rows (header included) from a streaming response."""
    async with client.stream("GET", url, headers=headers) as resp:
        resp.raise_for_status()
        pending: list[str] = []
        async for line in resp.aiter_lines():
            pending.append(line)
            if len(pending) >= 256:
                for row in csv.reader(pending):
                    yield row
                pending.clear()
        if pending:
            for row in csv.reader(pending):
                yield row


def _pick(row: list[str], col: dict[str, int], keys: tuple[str, ...]) -> str | None:
    """First non-empty value among ``keys`` in a header-keyed CSV row."""
    for k in keys:
        if k in col and col[k] < len(row):
            v = row[col[k]].strip()
            if v:
                return v
    return None


async def _aggregate_currency_action(
    client: httpx.AsyncClient,
    base: str,
    action_name: str,
    headers: dict[str, str],
    snapshot: CurrencySnapshot,
) -> tuple[int, str | None]:
    """Stream one currency action CSV and fold it into the snapshot roster.

    Returns ``(rows_consumed, warning_or_none)``. A non-2xx becomes a warning
    string (surfaced on the card) rather than an exception - a single locked-
    down action shouldn't blank the whole card.

    Per action:
      * ``CreateCurrency`` - register the currency (name + founder id).
      * ``MintCurrency``   - mark it minted, add the minted amount.
      * ``CurrencyTrade``  - add trade count + traded volume by currency.
    """
    url = f"{base}/api/v1/exporter/actions?actionName={action_name}"
    rows_consumed = 0
    try:
        header: list[str] | None = None
        col: dict[str, int] = {}
        currency_col_present = False
        async for row in _stream_csv_rows(client, url, headers):
            if header is None:
                header = row
                col = {name: i for i, name in enumerate(header)}
                currency_col_present = any(k in col for k in _CURRENCY_NAME_KEYS)
                continue
            if rows_consumed >= _MAX_ROWS_PER_ACTION:
                snapshot.warnings.append(
                    f"{action_name}: truncated at {_MAX_ROWS_PER_ACTION} rows (late-cycle cap)"
                )
                break
            rows_consumed += 1
            name = _pick(row, col, _CURRENCY_NAME_KEYS)
            amount_s = _pick(row, col, _AMOUNT_KEYS)
            try:
                amount = float(amount_s) if amount_s is not None else 0.0
            except ValueError:
                amount = 0.0
            if action_name == CURRENCY_TRADE_ACTION:
                snapshot.trade_rows_total += 1
                if name:
                    rec = snapshot.record(name)
                    rec.trade_count += 1
                    rec.trade_volume += amount
            elif action_name == MINT_CURRENCY_ACTION:
                if name:
                    rec = snapshot.record(name)
                    rec.is_minted = True
                    rec.mint_events += 1
                    rec.minted_amount += amount
            elif action_name == CREATE_CURRENCY_ACTION:
                if name:
                    rec = snapshot.record(name)
                    if rec.created_by is None:
                        rec.created_by = _pick(row, col, _CREATOR_KEYS)
        if action_name == CURRENCY_TRADE_ACTION and header is not None:
            snapshot.trade_currency_column_seen = currency_col_present
        return rows_consumed, None
    except httpx.HTTPStatusError as e:
        return 0, f"{action_name}: HTTP {e.response.status_code}"
    except httpx.HTTPError as e:
        return 0, f"{action_name}: {type(e).__name__}"


# ---------------------------------------------------------------------------
# Top holders (stores/economy exporter mod) - the one Currency <name> piece the
# action exporters cannot reconstruct. See eco-app#58 and mods/stores.
# ---------------------------------------------------------------------------


async def _fetch_currency_holdings(
    client: httpx.AsyncClient, base: str, headers: dict[str, str], snapshot: CurrencySnapshot
) -> None:
    """Fold the exporter mod's per-currency top holders into the roster.

    Best-effort, mirroring the citizens join in ``crafting.py``: a missing mod
    (401/404/405, the DLL not deployed) leaves ``holders_reachable`` False so the
    report shows ``HOLDERS_UNAVAILABLE_NOTE``; other failures append a non-fatal
    warning. A currency seen here but absent from the action roster is created,
    so a holder-only currency still surfaces. See eco-app#58.
    """
    url = f"{base}{CURRENCY_HOLDINGS_PATH}"
    try:
        r = await client.get(url, headers=headers)
        if r.status_code != 200:
            if r.status_code not in (401, 404, 405):
                snapshot.warnings.append(f"currency-holdings: HTTP {r.status_code}")
            return
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        snapshot.warnings.append(f"currency-holdings: {type(e).__name__}")
        return

    if not isinstance(data, list):
        return

    snapshot.holders_reachable = True
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = entry.get("currency")
        if not name:
            continue
        rec = snapshot.record(str(name))
        # The join key CurrencyTrade rows carry (eco-app#217). Recording it here
        # is what lets `_resolve_currency_ids` fold id-keyed trade totals onto
        # the named currency after every action has folded.
        currency_id = entry.get("id")
        if currency_id not in (None, ""):
            rec.currency_id = str(currency_id)
            snapshot.currency_id_to_name[str(currency_id)] = rec.name
        rec.holders_reachable = True
        rec.accounts_counted = _as_int(entry.get("accountsCounted"))
        rec.total_holdings = _as_float(entry.get("totalHoldings"))
        holders: list[CurrencyHolder] = []
        for h in entry.get("topHolders") or []:
            if not isinstance(h, dict):
                continue
            account = h.get("account")
            if not account:
                continue
            holder = h.get("holder")
            holders.append(
                CurrencyHolder(
                    account=str(account),
                    holder=str(holder) if holder else None,
                    balance=_as_float(h.get("balance")),
                )
            )
        holders.sort(key=lambda x: x.balance, reverse=True)
        rec.top_holders = holders[:_MAX_HOLDERS]
        # Say so. accountsCounted keeps the real total either way, but a list of
        # 15 beside a count of 22 is silent truncation. See eco-app#6076.
        if len(holders) > _MAX_HOLDERS:
            snapshot.warnings.append(
                f"{rec.name}: showing {_MAX_HOLDERS:,} of {len(holders):,} holder "
                f"rows; this cap is ECO_CURRENCY_MAX_HOLDERS, not `limit`"
            )


async def fetch_currency_id_map(
    client: httpx.AsyncClient, base: str, headers: dict[str, str]
) -> dict[str, str]:
    """Currency id -> display name, from the exporter mod's holdings surface.

    The one source that pairs the id the action exporter writes into
    CurrencyTrade rows with the name a player recognises (eco-app#217).
    Best-effort by design: an absent or older mod yields an empty map, and the
    ledger keeps raw ids rather than inventing names.
    """
    url = f"{base}{CURRENCY_HOLDINGS_PATH}"
    try:
        r = await client.get(url, headers=headers)
        if r.status_code != 200:
            return {}
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return {}
    if not isinstance(data, list):
        return {}
    out: dict[str, str] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        cid, name = entry.get("id"), entry.get("currency")
        if cid not in (None, "") and name:
            out[str(cid)] = str(name)
    return out


def _resolve_currency_ids(snapshot: CurrencySnapshot) -> None:
    """Fold id-keyed trade totals onto the currency they belong to (#217).

    The action exporter writes CurrencyTrade rows keyed by the Currency
    object's id, while CreateCurrency writes names, so the two never joined:
    18 id-named phantom currencies carried all 3,668 trades and the 167 real
    ones reported zero. The exporter mod's holdings surface supplies the
    id -> name map (see ``CurrencyHoldingsDto.Id``); this merges on it.

    An id with no mapping is kept rather than dropped — the volume is real —
    but flagged ``unresolved_id`` so nothing presents a bare number as a
    currency name, and a warning says how much of the ledger is affected.
    """
    id_to_name = snapshot.currency_id_to_name
    unresolved_trades = 0
    unresolved_ids: list[str] = []

    for key in list(snapshot.currencies):
        rec = snapshot.currencies.get(key)
        if rec is None or not _NUMERIC_ID_RE.match(rec.name):
            continue
        target_name = id_to_name.get(rec.name)
        if target_name is None or target_name == rec.name:
            if rec.trade_count:
                unresolved_trades += rec.trade_count
                unresolved_ids.append(rec.name)
            rec.unresolved_id = True
            continue
        target = snapshot.record(target_name)
        target.trade_count += rec.trade_count
        target.trade_volume += rec.trade_volume
        target.mint_events += rec.mint_events
        target.minted_amount += rec.minted_amount
        target.is_minted = target.is_minted or rec.is_minted
        if target.created_by is None:
            target.created_by = rec.created_by
        del snapshot.currencies[key]

    if unresolved_trades:
        shown = ", ".join(sorted(unresolved_ids)[:5])
        more = "" if len(unresolved_ids) <= 5 else f" (+{len(unresolved_ids) - 5} more)"
        snapshot.warnings.append(
            f"{unresolved_trades:,} trade(s) are attributed to currency ids no roster entry "
            f"claims ({shown}{more}). The exporter keys trades by id; names come from the "
            "stores/economy exporter mod (eco-app#58), so an old or absent mod leaves these "
            "unresolved. They are listed with unresolvedId set, not as currency names."
        )


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


async def fetch_currency(
    server: str | None,
    *,
    info: dict[str, Any],
    days_elapsed: int,
    admin_token: str | None,
    default_admin_base: str,
) -> CurrencySnapshot:
    """Fetch every reachable currency slice for the given server.

    The caller owns ``info`` (already retrieved via ``fetch_eco_info``) - we
    don't re-fetch /info here, matching ``climate.fetch_climate`` /
    ``ecoregion.gather_ecoregion_payload``. Never raises for admin-token
    problems: an absent token degrades to the public headline and an empty
    roster, and the card renders its "token unavailable" banner.
    """
    base = _admin_base(server, default_admin_base)

    cache_key = base
    cached = _currency_cache.get(cache_key)
    if cached is not None:
        return cached

    snapshot = CurrencySnapshot(
        fetched_at_iso=_now_iso(),
        source_base_url=base,
        info=dict(info or {}),
        days_elapsed=max(1, int(days_elapsed or 1)),
        admin_ok=bool(admin_token),
    )

    if not admin_token:
        _currency_cache[cache_key] = snapshot
        return snapshot

    headers = {"X-API-Key": admin_token}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        # Money-supply series fan out in parallel; each is 200/500-tolerant.
        (
            snapshot.active_currencies_series,
            snapshot.trades_7d_series,
            snapshot.personal_wealth_series,
            snapshot.government_holdings_series,
        ) = await asyncio.gather(
            _fetch_dataset(client, base, ACTIVE_CURRENCIES_DATASET, snapshot.days_elapsed, headers),
            _fetch_dataset(client, base, TRADES_7D_DATASET, snapshot.days_elapsed, headers),
            _fetch_dataset(client, base, PERSONAL_WEALTH_DATASET, snapshot.days_elapsed, headers),
            _fetch_dataset(
                client, base, GOVERNMENT_HOLDINGS_DATASET, snapshot.days_elapsed, headers
            ),
        )

        flatlist = await _fetch_dataset_flatlist(client, base, headers)
        kws = ("currenc", "mint", "trade", "wealth", "money", "holding")
        snapshot.available_currency_datasets = sorted(
            {name for name in flatlist if any(kw in name.lower() for kw in kws)}
        )

        # Actions folded sequentially (each CSV can be many MB late-cycle;
        # streaming them one at a time keeps peak memory bounded). Create
        # first so the roster exists before mint/trade decorate it.
        for action in (CREATE_CURRENCY_ACTION, MINT_CURRENCY_ACTION, CURRENCY_TRADE_ACTION):
            _rows, warn = await _aggregate_currency_action(client, base, action, headers, snapshot)
            # 401/404/405 just means "this server doesn't expose that action" -
            # silent. Other failures are worth surfacing on the card.
            if warn and "401" in warn:
                # Refused, not absent. Recorded rather than surfaced: a partial
                # 401 with a roster built from the other actions is not worth a
                # warning, but a server that refused everything must not be
                # described as having no currencies. See #266.
                snapshot.admin_reads_refused = True
            elif warn and not any(code in warn for code in ("404", "405")):
                snapshot.warnings.append(warn)

        # Top holders from the stores/economy exporter mod. Folded after the
        # roster so it decorates existing records (and adds any holder-only
        # currency). Best-effort - an undeployed mod leaves holders unreachable.
        await _fetch_currency_holdings(client, base, headers, snapshot)

    # The holdings surface is also where the currency id -> name map comes
    # from, so id-keyed trade totals can only be folded onto their real
    # currency once it has answered (eco-app#217).
    _resolve_currency_ids(snapshot)

    _currency_cache[cache_key] = snapshot
    return snapshot


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Payload computation - server.py renders the card from this shape. Pure, no IO.
# ---------------------------------------------------------------------------


def _series_last(pts: list[tuple[float, float]]) -> float | None:
    """Latest value of a level series, or None when it carries no sample.

    None rather than 0.0, because an empty series means the dataset was not
    read (no admin token, or the exporter refused), and a zero there asserts
    the world holds no money (eco-app#6077).
    """
    return float(pts[-1][1]) if pts else None


def _round_or_none(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def _classify(rec: CurrencyRecord) -> str:
    """minted/backed vs personal/credit, the split ``Currencies`` shows.

    A currency that has minted is backed by a reserve (minted/backed);
    everything else is a personal credit currency (auto-issued per player,
    credit-based, no reserve).
    """
    return "minted" if rec.is_minted else "personal"


def _currency_view(rec: CurrencyRecord) -> dict[str, Any]:
    return {
        "name": rec.name,
        # A trade-only record whose "name" is really an unjoined Currency id
        # (eco-app#217). Consumers must not caption these as currencies.
        "currencyId": rec.currency_id or (rec.name if rec.unresolved_id else None),
        "unresolvedId": rec.unresolved_id,
        "type": _classify(rec),
        "isMinted": rec.is_minted,
        "mintedAmount": round(rec.minted_amount, 2),
        "mintEvents": rec.mint_events,
        "tradeCount": rec.trade_count,
        "tradeVolume": round(rec.trade_volume, 2),
        "createdBy": rec.created_by,
    }


def _holders_view(rec: CurrencyRecord, *, include_note: bool = True) -> dict[str, Any]:
    """The per-currency top-holder block (meets ``Currency <name>``'s holders).

    ``reachable`` False means the exporter mod is not deployed on the target
    server. ``include_note`` controls whether the row repeats the explanation:
    185 copies of the same 200-byte unavailability note cost ~37 KB, so list
    rows omit it and defer to the payload's top-level
    ``holders_unavailable_note`` (#231). The single ``selected`` currency keeps
    its own copy, because a report reader has no list context to fall back on.
    """
    if not rec.holders_reachable:
        return {
            "reachable": False,
            "note": HOLDERS_UNAVAILABLE_NOTE if include_note else "",
            # null, never 0: the ledger was unread, so no count was observed.
            # A 0 here reads as "nobody holds it". See eco-app#6077.
            "accountsCounted": None,
            "totalHoldings": None,
            "list": [],
        }
    return {
        "reachable": True,
        "note": "" if rec.top_holders else "No accounts hold this currency yet.",
        "accountsCounted": rec.accounts_counted,
        "totalHoldings": round(rec.total_holdings, 2),
        "list": [
            {"account": h.account, "holder": h.holder, "balance": round(h.balance, 2)}
            for h in rec.top_holders
        ],
    }


def _currency_view_full(rec: CurrencyRecord) -> dict[str, Any]:
    """List-mode currency view enriched with its live top-holder block.

    The roster view (the ``get_economy`` MCP card, and the money-supply
    strip on the website ``/trade`` page) renders wealth distribution per
    currency, so each entry carries ``holders`` (``_holders_view``) next to the
    issuance / trade facts. The report view builds the same shape inline for its
    single ``selected`` currency, so the two stay consistent.
    """
    return {**_currency_view(rec), "holders": _holders_view(rec, include_note=False)}


def _rank_key(rec: CurrencyRecord) -> tuple[float, float, float, str]:
    """Rank currencies by trade activity, then issuance, then name.

    Sorting on volume first, then count, then minted amount mirrors how
    ``Currencies`` surfaces "top" currencies - the most economically active
    float to the top regardless of type.
    """
    return (rec.trade_volume, float(rec.trade_count), rec.minted_amount, rec.name)


def compute_currency_payload(
    snapshot: CurrencySnapshot, *, currency: str | None = None
) -> dict[str, Any]:
    """Shape the snapshot for the Jinja card + JSON block.

    ``currency`` selects the per-currency report view (meets ``Currency
    <name>``); omitting it returns the roster view (meets ``Currencies``).
    Both share the money-supply header.
    """
    info = snapshot.info or {}
    records = sorted(snapshot.currencies.values(), key=_rank_key, reverse=True)

    # Two different measurements that were both being called "active
    # currencies", an order of magnitude apart, with nothing reconciling them
    # (#257). The server's own ActiveCurrencies dataset counts currencies the
    # game considers live; the ledger count is distinct currency ids that have
    # ever appeared in a trade. Keep both, name both, and never blend them.
    reported_active_raw = _series_last(snapshot.active_currencies_series)
    reported_active = int(reported_active_raw) if reported_active_raw is not None else 0
    ledger_ids = len(records)
    active_source = ACTIVE_CURRENCIES_DATASET if reported_active else "trade-ledger"
    active_now = reported_active or ledger_ids
    personal_wealth = _series_last(snapshot.personal_wealth_series)
    government_holdings = _series_last(snapshot.government_holdings_series)
    trade_value_7d = _series_last(snapshot.trades_7d_series)
    money_supply = (
        personal_wealth + government_holdings
        if personal_wealth is not None and government_holdings is not None
        else None
    )

    minted = [r for r in records if r.is_minted]
    personal = [r for r in records if not r.is_minted]

    # EconomyDesc headline ("417 trades, 0 contracts") - always public.
    economy_desc = str(info.get("EconomyDesc") or "")

    selected: dict[str, Any] | None = None
    not_found = False
    suggestions: list[str] = []
    if currency:
        match = _find_currency(records, currency)
        if match is None:
            not_found = True
            suggestions = _suggest_currencies(records, currency)
        else:
            selected = {
                **_currency_view(match),
                "holders": _holders_view(match),
            }

    money = {
        "activeCurrencies": active_now,
        "activeCurrenciesSource": active_source,
        # Named distinctly so a consumer reading `money` and a consumer reading
        # `counts` stop coming away with different pictures of the economy.
        "activeCurrenciesReported": reported_active or None,
        "currencyIdsSeenInLedger": ledger_ids,
        "activeCurrenciesNote": (
            f"`activeCurrenciesReported` is the server's own {ACTIVE_CURRENCIES_DATASET} "
            "reading — currencies the game counts as live. `currencyIdsSeenInLedger` "
            "(= `counts.total`) is distinct currency ids that have appeared in a trade, "
            "which includes currencies no longer active. They measure different things "
            "and are not reconcilable from this tool's data."
        )
        if reported_active and reported_active != ledger_ids
        else "",
        "personalWealth": _round_or_none(personal_wealth),
        "governmentHoldings": _round_or_none(government_holdings),
        "totalSupply": _round_or_none(money_supply),
        "tradeValue7d": _round_or_none(trade_value_7d),
        "hasSupplyData": bool(
            snapshot.personal_wealth_series or snapshot.government_holdings_series
        ),
        "supplyNote": (
            "A null money figure means its series was not read, not that the "
            "world holds no money. `hasSupplyData` says which."
        ),
    }

    return {
        "view": "eco_currency",
        "mode": "report" if currency else "list",
        "query": currency,
        "notFound": not_found,
        "server": {
            "description": info.get("Description", ""),
            "category": info.get("Category"),
            "sourceUrl": info.get("_sourceUrl"),
        },
        "days_elapsed": snapshot.days_elapsed,
        "admin_ok": snapshot.admin_ok,
        "economy_desc": economy_desc,
        "money": money,
        "narrative": _narrative(
            admin_ok=snapshot.admin_ok,
            reads_refused=snapshot.admin_reads_refused,
            records=records,
            money=money,
            available=snapshot.available_currency_datasets,
        ),
        # Money-supply + trade-volume series over in-game time, so the website
        # can chart the trend rather than only the latest reading. Each is a
        # list of [time, value]; empty when the admin token / dataset is absent.
        "series": {
            "personalWealth": [[t, v] for t, v in snapshot.personal_wealth_series],
            "governmentHoldings": [[t, v] for t, v in snapshot.government_holdings_series],
            "activeCurrencies": [[t, v] for t, v in snapshot.active_currencies_series],
            "trades7d": [[t, v] for t, v in snapshot.trades_7d_series],
        },
        # Report mode answers about one currency, so it ships `selected` and
        # the summary totals and drops the full roster. Shipping all 185 rows
        # (65 KB of `currencies` plus another 65 KB of `personal`) meant asking
        # about one currency failed exactly the way asking about all of them
        # did — and a miss returned the entire corpus too (#231).
        "currencies": [] if currency else [_currency_view_full(r) for r in records],
        "minted": [] if currency else [_currency_view_full(r) for r in minted],
        "personal": [] if currency else [_currency_view_full(r) for r in personal],
        "counts": {
            "total": len(records),
            "minted": len(minted),
            "personal": len(personal),
        },
        "selected": selected,
        # Only populated on a miss, so a caller can retry without pulling the
        # roster down to look up a spelling.
        "suggestions": suggestions,
        "holders_reachable": snapshot.holders_reachable,
        "holders_unavailable_note": HOLDERS_UNAVAILABLE_NOTE,
        "trade_currency_column_seen": snapshot.trade_currency_column_seen,
        "available_currency_datasets": list(snapshot.available_currency_datasets),
        "warnings": list(snapshot.warnings),
        "fetched_at_iso": snapshot.fetched_at_iso,
        "source_base_url": snapshot.source_base_url,
    }


def _suggest_currencies(records: list[CurrencyRecord], query: str, limit: int = 8) -> list[str]:
    """Names to try after a miss, so the caller need not pull the whole roster.

    Close spellings first, then the most economically active currencies, which
    is what someone who typed a half-remembered name most likely wanted.
    ``records`` arrives already ranked by activity.
    """
    names = [rec.name for rec in records]
    close = difflib.get_close_matches(query.strip(), names, n=limit, cutoff=0.6)
    out = list(close)
    for name in names:
        if len(out) >= limit:
            break
        if name not in out:
            out.append(name)
    return out


def _find_currency(records: list[CurrencyRecord], query: str) -> CurrencyRecord | None:
    """Case-insensitive currency lookup: exact match first, then substring."""
    q = query.strip().lower()
    if not q:
        return None
    for rec in records:
        if rec.name.lower() == q:
            return rec
    for rec in records:
        if q in rec.name.lower():
            return rec
    return None


def _narrative(
    *,
    admin_ok: bool,
    reads_refused: bool = False,
    records: list[CurrencyRecord],
    money: dict[str, Any],
    available: list[str],
) -> str:
    """One-sentence header summary, matching the economy/climate card voice."""
    if not admin_ok and not records:
        return (
            "Admin token unavailable - currency roster and money-supply series "
            "require ECO_ADMIN_TOKEN. Showing the public /info headline only."
        )
    if not records and reads_refused:
        # The server answered and refused us, so nothing is known about its
        # currencies. Saying there are none asserts a fact we never read.
        return (
            "Currency roster unreadable - the server refused this token (HTTP 401). "
            "Showing the public /info headline only."
        )
    if not records:
        if available:
            sample = ", ".join(f"`{n}`" for n in available[:5])
            return (
                f"Server exposes currency datasets ({sample}) but no currencies "
                "have been created or traded yet - early in the cycle."
            )
        return "No currencies created or traded yet - early in the cycle."
    minted = sum(1 for r in records if r.is_minted)
    personal = len(records) - minted
    # The roster size is currency ids seen in the ledger, which is not the
    # server's active-currency count and must not borrow the word (#257).
    noun = "currency" if len(records) == 1 else "currencies"
    bits = [f"{len(records)} {noun} in the trade ledger"]
    reported = money.get("activeCurrenciesReported")
    if reported and reported != len(records):
        bits.append(f"{reported} reported active by the server")
    bits.append(f"{minted} minted / {personal} personal")
    if money.get("hasSupplyData"):
        bits.append(f"money supply {money['totalSupply']:,.0f}")
    return "Currency market: " + ", ".join(bits) + "."


def _clear_cache() -> None:
    """Test hook - drop the per-process currency cache."""
    _currency_cache.clear()


__all__ = [
    "ACTIVE_CURRENCIES_DATASET",
    "CREATE_CURRENCY_ACTION",
    "CURRENCY_HOLDINGS_PATH",
    "CURRENCY_TRADE_ACTION",
    "GOVERNMENT_HOLDINGS_DATASET",
    "HOLDERS_UNAVAILABLE_NOTE",
    "MINT_CURRENCY_ACTION",
    "MONEY_SUPPLY_DATASETS",
    "PERSONAL_WEALTH_DATASET",
    "TRADES_7D_DATASET",
    "CurrencyHolder",
    "CurrencyRecord",
    "CurrencySnapshot",
    "compute_currency_payload",
    "fetch_currency",
]
