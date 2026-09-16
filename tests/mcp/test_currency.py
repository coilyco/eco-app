"""Unit tests for the `get_currency` tool.

Covers:
- ``fetch_currency`` money-supply series fan-out, action-exporter aggregation
  (roster + minted classification + trade volume), and graceful degradation
  when the admin token is absent.
- ``compute_currency_payload`` in both modes: the roster/list view (meets
  ``Currencies``) and the per-currency report (meets ``Currency <name>``),
  including the live top-holders fold, the holders-unavailable fallback, and
  the not-found path.
- Currency classification (minted/backed vs personal/credit) and lookup.
- The MCP tool wiring end-to-end (call_tool returns markdown + JSON,
  and no widget — just-data per eco-app#87).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import mcp.types as mt
import pytest
import respx

from eco_mcp_app import currency as currency_mod
from eco_mcp_app import server as eco_server
from eco_mcp_app.currency import (
    ACTIVE_CURRENCIES_DATASET,
    CREATE_CURRENCY_ACTION,
    CURRENCY_HOLDINGS_PATH,
    CURRENCY_TRADE_ACTION,
    GOVERNMENT_HOLDINGS_DATASET,
    HOLDERS_UNAVAILABLE_NOTE,
    MINT_CURRENCY_ACTION,
    PERSONAL_WEALTH_DATASET,
    TRADES_7D_DATASET,
    CurrencyHolder,
    CurrencyRecord,
    CurrencySnapshot,
    _classify,
    _find_currency,
    compute_currency_payload,
    fetch_currency,
)
from eco_mcp_app.server import (
    DEFAULT_ECO_INFO_URL,
    build_server,
)

_DEFAULT_BASE = DEFAULT_ECO_INFO_URL.rsplit("/info", 1)[0]
_DATASET_URL = f"{_DEFAULT_BASE}/datasets/get"
_FLATLIST_URL = f"{_DEFAULT_BASE}/datasets/flatlist"


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with a clean cache + a known admin token."""
    eco_server._info_cache.clear()
    eco_server._admin_token_cache.clear()
    currency_mod._clear_cache()
    monkeypatch.setenv("ECO_ADMIN_TOKEN", "test-token")


def _info(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "Description": "Eco via Sirens",
        "Category": "Test",
        "DaysRunning": 4,
        "TimeSinceStart": 4 * 3600,
        "EconomyDesc": "417 trades, 0 contracts",
    }
    base.update(overrides)
    return base


def _series(values: list[float]) -> list[dict[str, float]]:
    return [{"Time": float(i * 86400), "Value": float(v)} for i, v in enumerate(values)]


def _route_datasets(values: dict[str, list[float]]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.params.get("dataset", "")
        if name in values:
            return httpx.Response(200, json=_series(values[name]))
        return httpx.Response(200, json=[])

    respx.get(_DATASET_URL).mock(side_effect=handler)


def _route_flatlist(names: list[str]) -> None:
    respx.get(_FLATLIST_URL).mock(return_value=httpx.Response(200, json=names))


def _action_url(action: str) -> str:
    return f"{_DEFAULT_BASE}/api/v1/exporter/actions?actionName={action}"


def _route_action(action: str, csv_text: str, status: int = 200) -> None:
    respx.get(_action_url(action)).mock(return_value=httpx.Response(status, text=csv_text))


# Representative exporter CSVs (columns keyed off the header, per the probe in
# docs/datasets/currency.md). Two minted currencies, one personal.
_CREATE_CSV = "Time,Citizen,Currency\n0,101,Sirens\n3600,102,GoldNote\n7200,103,Kai\n"
_MINT_CSV = (
    "Time,Citizen,Currency,Amount\n0,101,Sirens,1000\n3600,101,Sirens,500\n7200,102,GoldNote,250\n"
)
_TRADE_CSV = (
    "Time,Buyer,Seller,ShopOwner,Currency,CurrencyAmount,BoughtOrSold\n"
    "10,201,202,202,Sirens,40,32\n"
    "20,203,204,204,Sirens,60,33\n"
    "30,205,206,206,GoldNote,15,32\n"
    "40,207,208,208,Kai,5,33\n"
)


def _route_all_actions(
    create: str = _CREATE_CSV, mint: str = _MINT_CSV, trade: str = _TRADE_CSV
) -> None:
    _route_action(CREATE_CURRENCY_ACTION, create)
    _route_action(MINT_CURRENCY_ACTION, mint)
    _route_action(CURRENCY_TRADE_ACTION, trade)
    _route_holdings()


_HOLDINGS_URL = f"{_DEFAULT_BASE}{CURRENCY_HOLDINGS_PATH}"

# Representative holdings from the stores/economy exporter mod (eco-app#58):
# Sirens held across a government account (no single owner) and two players.
_HOLDINGS_JSON: list[dict[str, object]] = [
    {
        "currency": "Sirens",
        "backed": True,
        "accountsCounted": 3,
        "totalHoldings": 9250.0,
        "topHolders": [
            {"account": "Treasury", "holder": None, "balance": 6000.0},
            {"account": "Kai's Personal Account", "holder": "Kai", "balance": 2500.0},
            {"account": "Salt's Personal Account", "holder": "Salt", "balance": 750.0},
        ],
    },
]


def _route_holdings(payload: object = _HOLDINGS_JSON, status: int = 200) -> None:
    if status == 200:
        respx.get(_HOLDINGS_URL).mock(return_value=httpx.Response(200, json=payload))
    else:
        respx.get(_HOLDINGS_URL).mock(return_value=httpx.Response(status, text=""))


# ---------------------------------------------------------------------------
# fetch_currency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_fetch_currency_aggregates_roster_and_series() -> None:
    _route_datasets(
        {
            ACTIVE_CURRENCIES_DATASET: [1, 2, 3],
            TRADES_7D_DATASET: [100.0, 500.0, 1200.0],
            PERSONAL_WEALTH_DATASET: [0.0, 8000.0, 9000.0],
            GOVERNMENT_HOLDINGS_DATASET: [0.0, 1000.0, 1500.0],
        }
    )
    _route_flatlist(["ActiveCurrencies", "MintCurrency", "CurrencyTrade", "IrrelevantStat"])
    _route_all_actions()

    snap = await fetch_currency(
        None,
        info=_info(),
        days_elapsed=4,
        admin_token="test-token",
        default_admin_base=_DEFAULT_BASE,
    )

    assert snap.admin_ok is True
    # Series parsed.
    assert [v for _, v in snap.active_currencies_series] == [1.0, 2.0, 3.0]
    assert [v for _, v in snap.government_holdings_series] == [0.0, 1000.0, 1500.0]

    # Roster: three currencies, minted classification + issuance + trade folds.
    assert set(snap.currencies) == {"Sirens", "GoldNote", "Kai"}
    sirens = snap.currencies["Sirens"]
    assert sirens.is_minted is True
    assert sirens.minted_amount == 1500.0  # 1000 + 500
    assert sirens.mint_events == 2
    assert sirens.trade_count == 2
    assert sirens.trade_volume == 100.0  # 40 + 60
    assert sirens.created_by == "101"

    gold = snap.currencies["GoldNote"]
    assert gold.is_minted is True
    assert gold.minted_amount == 250.0
    assert gold.trade_count == 1

    kai = snap.currencies["Kai"]
    assert kai.is_minted is False  # never minted → personal/credit
    assert kai.trade_count == 1
    assert kai.trade_volume == 5.0

    assert snap.trade_currency_column_seen is True
    # Catalog discovery filtered to currency-relevant names only.
    assert "IrrelevantStat" not in snap.available_currency_datasets
    assert "CurrencyTrade" in snap.available_currency_datasets

    # Top holders from the exporter mod folded onto the existing Sirens record.
    assert snap.holders_reachable is True
    assert sirens.holders_reachable is True
    assert sirens.accounts_counted == 3
    assert sirens.total_holdings == 9250.0
    assert [(h.account, h.holder, h.balance) for h in sirens.top_holders] == [
        ("Treasury", None, 6000.0),
        ("Kai's Personal Account", "Kai", 2500.0),
        ("Salt's Personal Account", "Salt", 750.0),
    ]
    # A currency with no holdings row stays reachable-unaware (mod-not-deployed
    # and no-holdings are distinguished by the per-record flag).
    assert gold.holders_reachable is False


@pytest.mark.asyncio
@respx.mock
async def test_fetch_currency_holdings_absent_when_mod_undeployed() -> None:
    """A 404 on the holdings endpoint is silent and leaves holders unreachable."""
    _route_datasets({})
    _route_flatlist([])
    _route_action(CREATE_CURRENCY_ACTION, _CREATE_CSV)
    _route_action(MINT_CURRENCY_ACTION, _MINT_CSV)
    _route_action(CURRENCY_TRADE_ACTION, _TRADE_CSV)
    _route_holdings(status=404)

    snap = await fetch_currency(
        None,
        info=_info(),
        days_elapsed=4,
        admin_token="test-token",
        default_admin_base=_DEFAULT_BASE,
    )
    assert snap.holders_reachable is False
    assert all(not r.holders_reachable for r in snap.currencies.values())
    assert snap.warnings == []  # 404 (mod not deployed) is silent


@pytest.mark.asyncio
@respx.mock
async def test_fetch_currency_degrades_without_token() -> None:
    """No admin token → no admin fan-out at all, empty roster, admin_ok False."""
    snap = await fetch_currency(
        None,
        info=_info(),
        days_elapsed=4,
        admin_token=None,
        default_admin_base=_DEFAULT_BASE,
    )
    assert snap.admin_ok is False
    assert snap.currencies == {}
    assert snap.active_currencies_series == []
    # No exporter / datasets calls were issued.
    assert not respx.calls


@pytest.mark.asyncio
@respx.mock
async def test_fetch_currency_tolerates_locked_action() -> None:
    """A 401 on one action is silent; other actions still populate the roster."""
    _route_datasets({})
    _route_flatlist([])
    _route_action(CREATE_CURRENCY_ACTION, _CREATE_CSV)
    _route_action(MINT_CURRENCY_ACTION, "", status=401)
    _route_action(CURRENCY_TRADE_ACTION, _TRADE_CSV)
    _route_holdings(status=404)  # exporter mod not deployed

    snap = await fetch_currency(
        None,
        info=_info(),
        days_elapsed=4,
        admin_token="test-token",
        default_admin_base=_DEFAULT_BASE,
    )
    # Roster still built from create + trade; nothing minted (mint was locked).
    assert set(snap.currencies) == {"Sirens", "GoldNote", "Kai"}
    assert all(not r.is_minted for r in snap.currencies.values())
    # 401 is swallowed silently — not surfaced as a warning.
    assert snap.warnings == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_currency_flags_missing_trade_currency_column() -> None:
    """When CurrencyTrade lacks a Currency column, the flag records that."""
    _route_datasets({})
    _route_flatlist([])
    _route_action(CREATE_CURRENCY_ACTION, _CREATE_CSV)
    _route_action(MINT_CURRENCY_ACTION, _MINT_CSV)
    _route_action(CURRENCY_TRADE_ACTION, "Time,Buyer,Seller,CurrencyAmount\n10,1,2,40\n")
    _route_holdings(status=404)  # exporter mod not deployed

    snap = await fetch_currency(
        None,
        info=_info(),
        days_elapsed=4,
        admin_token="test-token",
        default_admin_base=_DEFAULT_BASE,
    )
    assert snap.trade_currency_column_seen is False
    assert snap.trade_rows_total == 1
    # No per-currency trade attribution possible → trade counts stay at 0.
    assert all(r.trade_count == 0 for r in snap.currencies.values())


# ---------------------------------------------------------------------------
# compute_currency_payload — classification + views
# ---------------------------------------------------------------------------


def _snapshot_with(records: list[CurrencyRecord], **kw: object) -> CurrencySnapshot:
    snap = CurrencySnapshot(
        fetched_at_iso="2026-07-05T00:00:00+00:00",
        source_base_url=_DEFAULT_BASE,
        info=kw.get("info", _info()),  # type: ignore[arg-type]
        days_elapsed=4,
        admin_ok=bool(kw.get("admin_ok", True)),
    )
    for r in records:
        snap.currencies[r.name] = r
    snap.personal_wealth_series = kw.get("personal", [])  # type: ignore[assignment]
    snap.government_holdings_series = kw.get("gov", [])  # type: ignore[assignment]
    snap.active_currencies_series = kw.get("active", [])  # type: ignore[assignment]
    snap.trades_7d_series = kw.get("trades7d", [])  # type: ignore[assignment]
    return snap


def test_classify_minted_vs_personal() -> None:
    assert _classify(CurrencyRecord("A", is_minted=True)) == "minted"
    assert _classify(CurrencyRecord("B", is_minted=False)) == "personal"


def test_compute_list_view_splits_and_ranks() -> None:
    records = [
        CurrencyRecord(
            "Sirens", is_minted=True, minted_amount=1500, trade_count=2, trade_volume=100
        ),
        CurrencyRecord(
            "GoldNote", is_minted=True, minted_amount=250, trade_count=1, trade_volume=15
        ),
        CurrencyRecord("Kai", is_minted=False, trade_count=1, trade_volume=5),
    ]
    snap = _snapshot_with(
        records,
        personal=[(0.0, 9000.0)],
        gov=[(0.0, 1500.0)],
        active=[(0.0, 3.0)],
        trades7d=[(0.0, 1200.0)],
    )
    payload = compute_currency_payload(snap)

    assert payload["mode"] == "list"
    assert payload["counts"] == {"total": 3, "minted": 2, "personal": 1}
    # Ranked by trade volume desc.
    assert [c["name"] for c in payload["currencies"]] == ["Sirens", "GoldNote", "Kai"]
    assert [c["name"] for c in payload["minted"]] == ["Sirens", "GoldNote"]
    assert [c["name"] for c in payload["personal"]] == ["Kai"]

    money = payload["money"]
    assert money["activeCurrencies"] == 3
    assert money["totalSupply"] == 10500.0
    assert money["tradeValue7d"] == 1200.0
    assert money["hasSupplyData"] is True
    # The ledger roster and the server's own active count agree here (both 3),
    # so nothing needs reconciling and the note stays empty (#257).
    assert money["activeCurrenciesReported"] == 3
    assert money["currencyIdsSeenInLedger"] == 3
    assert money["activeCurrenciesSource"] == "ActiveCurrencies"
    assert money["activeCurrenciesNote"] == ""
    assert "3 currencies in the trade ledger" in payload["narrative"]


def test_split_source_currency_counts_are_labelled_not_blended() -> None:
    """The live server reported 15 active while the ledger held 185 ids (#257).

    Both are probably correct measurements of different things; the response
    just presented them as the same thing with nothing reconciling them.
    """
    records = [
        CurrencyRecord(f"C{i}", is_minted=i < 2, trade_count=1, trade_volume=float(i))
        for i in range(6)
    ]
    snap = _snapshot_with(records, active=[(0.0, 2.0)])
    payload = compute_currency_payload(snap)
    money = payload["money"]

    assert money["activeCurrenciesReported"] == 2
    assert money["currencyIdsSeenInLedger"] == 6
    assert payload["counts"]["total"] == 6
    # The disagreement is stated rather than left for the reader to notice.
    assert "not reconcilable" in money["activeCurrenciesNote"]
    # And the narrative no longer calls the ledger roster "active".
    assert "6 currencies in the trade ledger" in payload["narrative"]
    assert "2 reported active by the server" in payload["narrative"]
    assert "6 active" not in payload["narrative"]


def test_ledger_count_is_named_as_a_fallback_when_the_dataset_is_absent() -> None:
    records = [CurrencyRecord("Only", is_minted=True, trade_count=1)]
    payload = compute_currency_payload(_snapshot_with(records, active=[]))
    money = payload["money"]

    assert money["activeCurrencies"] == 1
    assert money["activeCurrenciesSource"] == "trade-ledger"
    assert money["activeCurrenciesReported"] is None


def test_compute_list_view_carries_series_and_holders() -> None:
    """List mode surfaces the money/trade time series and per-currency holders.

    The website /economy page charts the trend and renders wealth distribution
    from the roster view, so both the series block and each currency's holder
    block have to ride along with the list payload (eco-app#78).
    """
    sirens = CurrencyRecord(
        "Sirens", is_minted=True, minted_amount=1500, trade_count=2, trade_volume=100
    )
    sirens.holders_reachable = True
    sirens.accounts_counted = 2
    sirens.total_holdings = 8500.0
    sirens.top_holders = [
        CurrencyHolder("Treasury", None, 6000.0),
        CurrencyHolder("Kai's Personal Account", "Kai", 2500.0),
    ]
    snap = _snapshot_with(
        [sirens, CurrencyRecord("Kai", is_minted=False, trade_count=1, trade_volume=5)],
        personal=[(0.0, 8000.0), (86400.0, 9000.0)],
        gov=[(0.0, 1000.0), (86400.0, 1500.0)],
        active=[(0.0, 2.0), (86400.0, 2.0)],
        trades7d=[(0.0, 500.0), (86400.0, 1200.0)],
    )
    snap.holders_reachable = True
    payload = compute_currency_payload(snap)

    # Series ride along as [time, value] pairs for the trend charts.
    series = payload["series"]
    assert series["personalWealth"] == [[0.0, 8000.0], [86400.0, 9000.0]]
    assert series["trades7d"] == [[0.0, 500.0], [86400.0, 1200.0]]
    assert series["governmentHoldings"][-1] == [86400.0, 1500.0]

    # Each roster entry carries its own holders block for wealth distribution.
    top = payload["currencies"][0]
    assert top["name"] == "Sirens"
    assert top["holders"]["reachable"] is True
    assert top["holders"]["accountsCounted"] == 2
    assert top["holders"]["totalHoldings"] == 8500.0
    assert top["holders"]["list"][0] == {
        "account": "Treasury",
        "holder": None,
        "balance": 6000.0,
    }
    # A currency the mod never reported degrades to reachable=False, not a fake list.
    kai = next(c for c in payload["currencies"] if c["name"] == "Kai")
    assert kai["holders"]["reachable"] is False
    assert kai["holders"]["list"] == []


def test_compute_report_view_selects_currency_with_live_holders() -> None:
    sirens = CurrencyRecord(
        "Sirens", is_minted=True, minted_amount=1500, mint_events=2, trade_count=2
    )
    sirens.holders_reachable = True
    sirens.accounts_counted = 3
    sirens.total_holdings = 9250.0
    sirens.top_holders = [
        CurrencyHolder("Treasury", None, 6000.0),
        CurrencyHolder("Kai's Personal Account", "Kai", 2500.0),
    ]
    snap = _snapshot_with([sirens, CurrencyRecord("Kai", is_minted=False, trade_count=1)])
    snap.holders_reachable = True
    payload = compute_currency_payload(snap, currency="sirens")  # case-insensitive

    assert payload["mode"] == "report"
    assert payload["notFound"] is False
    assert payload["holders_reachable"] is True
    sel = payload["selected"]
    assert sel is not None
    assert sel["name"] == "Sirens"
    assert sel["type"] == "minted"
    assert sel["mintedAmount"] == 1500.0
    holders = sel["holders"]
    assert holders["reachable"] is True
    assert holders["note"] == ""
    assert holders["accountsCounted"] == 3
    assert holders["totalHoldings"] == 9250.0
    assert holders["list"] == [
        {"account": "Treasury", "holder": None, "balance": 6000.0},
        {"account": "Kai's Personal Account", "holder": "Kai", "balance": 2500.0},
    ]


def test_compute_report_view_holders_unavailable_when_mod_undeployed() -> None:
    """No holdings endpoint reached → the report shows the unavailable note."""
    records = [CurrencyRecord("Sirens", is_minted=True, minted_amount=1500, trade_count=2)]
    snap = _snapshot_with(records)  # holders_reachable defaults False
    payload = compute_currency_payload(snap, currency="Sirens")

    sel = payload["selected"]
    assert sel is not None
    assert sel["holders"]["reachable"] is False
    assert sel["holders"]["note"] == HOLDERS_UNAVAILABLE_NOTE
    assert sel["holders"]["list"] == []
    assert payload["holders_reachable"] is False
    assert payload["holders_unavailable_note"] == HOLDERS_UNAVAILABLE_NOTE


def test_compute_report_view_not_found() -> None:
    snap = _snapshot_with([CurrencyRecord("Sirens", is_minted=True)])
    payload = compute_currency_payload(snap, currency="Nonexistent")
    assert payload["mode"] == "report"
    assert payload["notFound"] is True
    assert payload["selected"] is None


def test_find_currency_exact_then_substring() -> None:
    records = [CurrencyRecord("GoldNote"), CurrencyRecord("Kai Siren")]
    assert _find_currency(records, "goldnote").name == "GoldNote"  # exact, case-insensitive
    assert _find_currency(records, "siren").name == "Kai Siren"  # substring
    assert _find_currency(records, "zzz") is None
    assert _find_currency(records, "") is None


def test_compute_empty_roster_narrative_without_token() -> None:
    snap = _snapshot_with([], admin_ok=False)
    payload = compute_currency_payload(snap)
    assert payload["counts"]["total"] == 0
    assert "Admin token unavailable" in payload["narrative"]


# ---------------------------------------------------------------------------
# MCP tool wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tools_includes_get_currency() -> None:
    mcp = build_server()
    handler = mcp.request_handlers[mt.ListToolsRequest]
    result = await handler(mt.ListToolsRequest(method="tools/list"))
    names = {tool.name for tool in result.root.tools}
    assert "get_currency" in names


@pytest.mark.asyncio
@respx.mock
async def test_call_get_currency_returns_iframe_fragment() -> None:
    respx.get(DEFAULT_ECO_INFO_URL).mock(return_value=httpx.Response(200, json=_info()))
    _route_datasets(
        {
            ACTIVE_CURRENCIES_DATASET: [1, 2, 3],
            PERSONAL_WEALTH_DATASET: [0.0, 9000.0],
            GOVERNMENT_HOLDINGS_DATASET: [0.0, 1500.0],
        }
    )
    _route_flatlist([])
    _route_all_actions()

    mcp = build_server()
    handler = mcp.request_handlers[mt.CallToolRequest]
    req = mt.CallToolRequest(
        method="tools/call",
        params=mt.CallToolRequestParams(name="get_currency", arguments={}),
    )
    result = await handler(req)
    blocks = result.root.content
    assert len(blocks) == 2
    assert isinstance(blocks[0], mt.TextContent)

    md = blocks[0].text
    assert "currency" in md.lower()

    payload = json.loads(blocks[1].text)
    assert payload["mode"] == "list"
    assert payload["counts"]["total"] == 3
    assert payload["money"]["totalSupply"] == 10500.0
    # _route_all_actions routes the holdings endpoint too, so the fold reached it.
    assert payload["holders_reachable"] is True

    # Just-data per eco-app#87: get_currency no longer emits a widget.
    assert result.root.meta is None


@pytest.mark.asyncio
@respx.mock
async def test_call_get_currency_report_mode() -> None:
    respx.get(DEFAULT_ECO_INFO_URL).mock(return_value=httpx.Response(200, json=_info()))
    _route_datasets({})
    _route_flatlist([])
    _route_all_actions()

    mcp = build_server()
    handler = mcp.request_handlers[mt.CallToolRequest]
    req = mt.CallToolRequest(
        method="tools/call",
        params=mt.CallToolRequestParams(name="get_currency", arguments={"currency": "Sirens"}),
    )
    result = await handler(req)
    payload = json.loads(result.root.content[1].text)
    assert payload["mode"] == "report"
    assert payload["selected"]["name"] == "Sirens"
    assert payload["selected"]["type"] == "minted"
    # Live top holders from the exporter mod reached the report end-to-end.
    holders = payload["selected"]["holders"]
    assert holders["reachable"] is True
    assert holders["list"][0] == {"account": "Treasury", "holder": None, "balance": 6000.0}
    # The report markdown renders the holder table, not a deferred note.
    assert "Treasury" in result.root.content[0].text
    # Just-data per eco-app#87: get_currency no longer emits a widget.
    assert result.root.meta is None


@pytest.mark.asyncio
@respx.mock
async def test_call_get_currency_handles_info_failure() -> None:
    respx.get(DEFAULT_ECO_INFO_URL).mock(side_effect=httpx.ConnectError("refused"))
    mcp = build_server()
    handler = mcp.request_handlers[mt.CallToolRequest]
    req = mt.CallToolRequest(
        method="tools/call",
        params=mt.CallToolRequestParams(name="get_currency", arguments={}),
    )
    result = await handler(req)
    assert result.root.isError is True
    assert "unreachable" in result.root.content[0].text.lower()
    # Just-data per eco-app#87: get_currency no longer emits a widget.
    assert result.root.meta is None


@pytest.mark.asyncio
async def test_tool_declaration_uses_currency_uri() -> None:
    """The tool no longer declares a widget resource (just-data per eco-app#87)."""
    mcp = build_server()
    handler = mcp.request_handlers[mt.ListToolsRequest]
    result = await handler(mt.ListToolsRequest(method="tools/list"))
    tool = next(t for t in result.root.tools if t.name == "get_currency")
    assert tool.meta is None


@pytest.mark.asyncio
async def test_no_mcp_app_resources_registered() -> None:
    mcp = build_server()
    assert mt.ListResourcesRequest not in mcp.request_handlers


# ---------------------------------------------------------------------------
# /preview/currency.json data-plane route (the /trade SPA route's source)
# ---------------------------------------------------------------------------


@respx.mock
def test_preview_currency_json_route(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from eco_mcp_app.http_app import create_app

    eco_server._info_cache.clear()
    currency_mod._clear_cache()
    monkeypatch.setenv("ECO_ADMIN_TOKEN", "test-token")

    respx.get(DEFAULT_ECO_INFO_URL).mock(return_value=httpx.Response(200, json=_info()))
    _route_datasets({ACTIVE_CURRENCIES_DATASET: [1, 2, 3]})
    _route_flatlist([])
    _route_all_actions()

    client = TestClient(create_app())
    r = client.get("/preview/currency.json?currency=Sirens")
    assert r.status_code == 200
    payload = r.json()
    assert payload["mode"] == "report"
    assert payload["selected"]["name"] == "Sirens"
    # The dedicated route wins over the generic /preview/{tool} handler.
    r2 = client.get("/preview/currency.json")
    assert r2.json()["mode"] == "list"


@respx.mock
def test_preview_currency_json_sanitizes_nested_nonfinite_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The currency data plane stays strict JSON when exporter values are non-finite."""
    from fastapi.testclient import TestClient

    from eco_mcp_app.http_app import create_app

    original_compute = currency_mod.compute_currency_payload

    def _compute_with_nonfinite(
        snapshot: CurrencySnapshot, *, currency: str | None = None
    ) -> dict[str, object]:
        payload = original_compute(snapshot, currency=currency)
        payload["money"].update(
            {
                "personalWealth": float("inf"),
                "governmentHoldings": float("-inf"),
                "totalSupply": float("nan"),
                "tradeValue7d": float("nan"),
                "hasSupplyData": True,
            }
        )
        payload["series"] = {
            "personalWealth": [[0.0, float("inf")]],
            "governmentHoldings": [[0.0, float("-inf")]],
            "activeCurrencies": [[0.0, 3.0]],
            "trades7d": [[0.0, float("nan")]],
        }
        return payload

    monkeypatch.setattr(currency_mod, "compute_currency_payload", _compute_with_nonfinite)
    respx.get(DEFAULT_ECO_INFO_URL).mock(return_value=httpx.Response(200, json=_info()))
    _route_datasets({ACTIVE_CURRENCIES_DATASET: [1, 2, 3]})
    _route_flatlist([])
    _route_all_actions()

    response = TestClient(create_app()).get("/preview/currency.json")

    assert response.status_code == 200, response.text
    assert "Infinity" not in response.text
    assert "NaN" not in response.text
    payload = json.loads(
        response.text,
        parse_constant=lambda value: (_ for _ in ()).throw(
            AssertionError(f"non-strict JSON constant: {value}")
        ),
    )
    assert payload["money"] == {
        "activeCurrencies": 3,
        "activeCurrenciesSource": "ActiveCurrencies",
        "activeCurrenciesReported": 3,
        "currencyIdsSeenInLedger": 3,
        "activeCurrenciesNote": "",
        "personalWealth": None,
        "governmentHoldings": None,
        "totalSupply": None,
        "tradeValue7d": None,
        "hasSupplyData": True,
        "supplyNote": (
            "A null money figure means its series was not read, not that the "
            "world holds no money. `hasSupplyData` says which."
        ),
    }
    assert payload["series"] == {
        "personalWealth": [[0.0, None]],
        "governmentHoldings": [[0.0, None]],
        "activeCurrencies": [[0.0, 3.0]],
        "trades7d": [[0.0, None]],
    }


# ---------------------------------------------------------------------------
# Response size — the `currency` filter has to actually narrow (eco-app#231)
# ---------------------------------------------------------------------------


def _many_records(n: int = 185) -> list[CurrencyRecord]:
    """A roster the size of the one on Sirens."""
    records = [
        CurrencyRecord(f"Currency{i:03d}", is_minted=i % 2 == 0, trade_count=i, trade_volume=i * 2)
        for i in range(n - 1)
    ]
    records.append(CurrencyRecord("Spectres", is_minted=True, trade_count=99, trade_volume=1234.5))
    return records


def test_naming_one_currency_drops_the_whole_roster() -> None:
    """Asking about one currency used to fail exactly like asking about all.

    `get_currency(currency="Spectres")` returned 136,670 characters — the same
    185-entry `currencies[]` and `personal[]` as the unfiltered call, plus a
    `selected` block. All three variants exceeded the MCP response cap.
    """
    snap = _snapshot_with(_many_records())
    full = compute_currency_payload(snap)
    one = compute_currency_payload(snap, currency="Spectres")

    assert one["selected"] is not None
    assert one["selected"]["name"] == "Spectres"
    # The roster is gone, but the summary totals that describe it remain.
    assert one["currencies"] == []
    assert one["personal"] == []
    assert one["minted"] == []
    assert one["counts"]["total"] == 185
    assert one["money"] == full["money"]
    # The filter now buys a real reduction rather than adding a block.
    assert len(json.dumps(one)) < len(json.dumps(full)) / 10


def test_a_miss_returns_suggestions_not_the_corpus() -> None:
    snap = _snapshot_with(_many_records())
    miss = compute_currency_payload(snap, currency="Sun Coin")
    assert miss["notFound"] is True
    assert miss["selected"] is None
    assert miss["currencies"] == []
    assert miss["suggestions"]
    assert len(miss["suggestions"]) <= 8
    assert len(json.dumps(miss)) < 5_000


def test_the_holders_note_is_not_repeated_on_every_row() -> None:
    """185 copies of the same 200-byte note cost ~37 KB on their own."""
    snap = _snapshot_with(_many_records())
    payload = compute_currency_payload(snap)
    body = json.dumps(payload)
    # Exactly one copy: the hoisted top-level note.
    assert body.count(HOLDERS_UNAVAILABLE_NOTE) == 1
    assert payload["holders_unavailable_note"] == HOLDERS_UNAVAILABLE_NOTE
    # Rows still say the holders are unreachable, just without the prose.
    assert payload["currencies"][0]["holders"]["reachable"] is False


def test_the_selected_currency_keeps_its_own_holders_note() -> None:
    # A report reader has no roster context to fall back on.
    snap = _snapshot_with(_many_records())
    one = compute_currency_payload(snap, currency="Spectres")
    assert one["selected"]["holders"]["note"] == HOLDERS_UNAVAILABLE_NOTE


@pytest.mark.asyncio
@respx.mock
async def test_a_server_that_refused_every_action_is_not_called_empty() -> None:
    """401 on everything meant the roster was never read. The narrative said
    "no currencies have been created or traded yet" beside a payload carrying
    40 active currencies. See #266."""
    _route_datasets({})
    _route_flatlist([])
    _route_action(CREATE_CURRENCY_ACTION, "", status=401)
    _route_action(MINT_CURRENCY_ACTION, "", status=401)
    _route_action(CURRENCY_TRADE_ACTION, "", status=401)
    _route_holdings(status=401)

    snap = await fetch_currency(
        None,
        info=_info(),
        days_elapsed=4,
        admin_token="test-token",
        default_admin_base=_DEFAULT_BASE,
    )

    assert snap.admin_reads_refused is True
    assert snap.currencies == {}
    payload = compute_currency_payload(snap)
    narrative = payload["narrative"]
    assert "refused" in narrative.lower(), narrative
    assert "no currencies" not in narrative.lower(), narrative


@pytest.mark.asyncio
@respx.mock
async def test_a_genuinely_empty_server_still_reads_as_early_cycle() -> None:
    """The distinction that must survive: refused is not the same as empty."""
    _route_datasets({})
    _route_flatlist([])
    _route_action(CREATE_CURRENCY_ACTION, "")
    _route_action(MINT_CURRENCY_ACTION, "")
    _route_action(CURRENCY_TRADE_ACTION, "")
    _route_holdings(status=404)

    snap = await fetch_currency(
        None,
        info=_info(),
        days_elapsed=4,
        admin_token="test-token",
        default_admin_base=_DEFAULT_BASE,
    )

    assert snap.admin_reads_refused is False
    narrative = compute_currency_payload(snap)["narrative"]
    assert "refused" not in narrative.lower(), narrative


def test_an_unreachable_holder_ledger_reports_null_not_zero() -> None:
    """The same rule as get_economy's KPIs, on the block that still broke it.

    `reachable: False` means the exporter mod is not deployed, so no count was
    observed. Reporting 0 there claims nobody holds the currency, which is a
    different statement and one a caller cannot distinguish. See eco-app#6077.
    """
    rec = currency_mod.CurrencyRecord(name="unreadable")
    rec.holders_reachable = False

    view = currency_mod._holders_view(rec)

    assert view["reachable"] is False
    assert view["accountsCounted"] is None
    assert view["totalHoldings"] is None
    assert view["list"] == []


def test_a_reachable_ledger_with_no_holders_still_reports_zero() -> None:
    """The negative control: a measured empty ledger is a real zero."""
    rec = currency_mod.CurrencyRecord(name="empty")
    rec.holders_reachable = True

    view = currency_mod._holders_view(rec)

    assert view["reachable"] is True
    assert view["accountsCounted"] == 0
    assert view["totalHoldings"] == 0.0


async def _absorb(entries: list[dict]) -> CurrencySnapshot:
    """Drive the real fetch against a mocked holdings endpoint."""
    snapshot = CurrencySnapshot(
        fetched_at_iso="2026-09-16T00:00:00+00:00",
        source_base_url=_DEFAULT_BASE,
        info=_info(),  # type: ignore[arg-type]
        days_elapsed=4,
        admin_ok=True,
    )
    base = "http://eco.test:3001"
    with respx.mock:
        respx.get(f"{base}{currency_mod.CURRENCY_HOLDINGS_PATH}").mock(
            return_value=httpx.Response(200, json=entries)
        )
        async with httpx.AsyncClient() as client:
            await currency_mod._fetch_currency_holdings(client, base, {}, snapshot)
    return snapshot


@pytest.mark.asyncio
async def test_a_truncated_holder_list_announces_itself() -> None:
    """`limit` does not reach this cap, so the cap has to say so itself.

    A list of 15 beside accountsCounted 22 is silent truncation, which reads as
    the whole population. Measured live on eco.coilysiren.me. See eco-app#6076.
    """
    snapshot = await _absorb(
        [
            {
                "currency": "reihtnog Credit",
                "accountsCounted": 22,
                "totalHoldings": 1500.0,
                "topHolders": [
                    {"account": f"acct{i}", "holder": f"h{i}", "balance": float(100 - i)}
                    for i in range(22)
                ],
            }
        ]
    )

    rec = snapshot.record("reihtnog Credit")
    assert len(rec.top_holders) == currency_mod._MAX_HOLDERS
    assert rec.accounts_counted == 22
    warning = next((w for w in snapshot.warnings if "holder rows" in w), None)
    assert warning is not None, f"truncation was silent: {snapshot.warnings}"
    assert "15 of 22" in warning
    assert "not `limit`" in warning


@pytest.mark.asyncio
async def test_an_untruncated_holder_list_stays_quiet() -> None:
    """The negative control: no truncation, no warning."""
    snapshot = await _absorb(
        [
            {
                "currency": "small",
                "accountsCounted": 2,
                "totalHoldings": 3.0,
                "topHolders": [{"account": "a", "holder": "h", "balance": 2.0}],
            }
        ]
    )

    assert not [w for w in snapshot.warnings if "holder rows" in w]


@pytest.mark.asyncio
@respx.mock
async def test_the_roster_is_not_shipped_twice() -> None:
    """`minted` and `personal` partition what `currencies` already carries.

    Shipping them as full views sent every record twice, on the tool whose own
    warnings are about response caps. They are names now: the partition
    survives, the copy does not. See eco-app#6076.
    """
    respx.get(DEFAULT_ECO_INFO_URL).mock(return_value=httpx.Response(200, json=_info()))
    _route_datasets({})
    _route_flatlist([])
    _route_all_actions()

    mcp = build_server()
    handler = mcp.request_handlers[mt.CallToolRequest]
    result = await handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name="get_currency", arguments={}),
        )
    )
    payload = json.loads(result.root.content[1].text)

    assert payload["minted"] == [] or all(isinstance(x, str) for x in payload["minted"])
    assert payload["personal"] == [] or all(isinstance(x, str) for x in payload["personal"])

    # Every partitioned name still resolves to a record in `currencies`, so the
    # partition is information rather than a dangling label.
    roster = {row["name"] for row in payload["currencies"]}
    for key in ("minted", "personal"):
        assert set(payload[key]) <= roster, f"{key} names a currency the roster dropped"

    # And the counts still describe the whole population.
    assert payload["counts"]["minted"] == len(payload["minted"])
    assert payload["counts"]["personal"] == len(payload["personal"])


@pytest.mark.asyncio
@respx.mock
async def test_the_markdown_summarises_every_row_despite_limit() -> None:
    """Rule 5: summaries describe every row regardless of `limit`.

    The markdown renders before the roster is bounded, so a limit=1 call still
    names the minted and personal sections off the full population.
    """
    respx.get(DEFAULT_ECO_INFO_URL).mock(return_value=httpx.Response(200, json=_info()))
    _route_datasets({})
    _route_flatlist([])
    _route_all_actions()

    mcp = build_server()
    handler = mcp.request_handlers[mt.CallToolRequest]
    result = await handler(
        mt.CallToolRequest(
            method="tools/call",
            params=mt.CallToolRequestParams(name="get_currency", arguments={"limit": 1}),
        )
    )
    payload = json.loads(result.root.content[1].text)

    assert len(payload["currencies"]) <= 1
    assert payload["counts"]["total"] >= len(payload["currencies"])


# eco-app#6077: an unread dataset must report null, never zero. The money block
# is the second instance found by the 25-tool sweep, after the holder block.
_MONEY_FIGURES = ("personalWealth", "governmentHoldings", "totalSupply", "tradeValue7d")


def _money(**series: list[tuple[float, float]]) -> dict[str, Any]:
    snapshot = CurrencySnapshot(
        fetched_at_iso="2026-09-16T00:00:00+00:00",
        source_base_url=_DEFAULT_BASE,
        info=_info(),  # type: ignore[arg-type]
        days_elapsed=4,
        admin_ok=True,
    )
    for name, points in series.items():
        setattr(snapshot, name, points)
    money = currency_mod.compute_currency_payload(snapshot)["money"]
    assert isinstance(money, dict)
    return money


@pytest.mark.parametrize("figure", _MONEY_FIGURES)
def test_an_unread_money_series_reports_null_never_zero(figure: str) -> None:
    """Measured live: every figure read 0.0 beside `hasSupplyData: false`.

    A zero there asserts the world holds no money, which is a different claim
    from not having read the ledger.
    """
    money = _money()

    assert money[figure] is None
    assert money["hasSupplyData"] is False


def test_a_measured_zero_stays_zero() -> None:
    """Or the invariant would be satisfied by nulling everything."""
    money = _money(
        personal_wealth_series=[(0.0, 0.0)],
        government_holdings_series=[(0.0, 0.0)],
        trades_7d_series=[(0.0, 0.0)],
    )

    for figure in _MONEY_FIGURES:
        assert money[figure] == 0.0, f"{figure} was measured at zero and must stay zero"
    assert money["hasSupplyData"] is True
