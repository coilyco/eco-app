"""Unit tests for the `get_economy` tool.

Covers: dataset fan-out + KPI computation (happy path), Day-3 empty-data
paths, admin token absent → empty-state branch, classification thresholds,
and the MCP tool wiring end-to-end.

Uses respx to mock both /info and /datasets/get, mirroring the pattern in
tests/test_fetch_eco_info.py and the sibling eco-spec-tracker repo.
"""

from __future__ import annotations

import json

import httpx
import mcp.types as mt
import pytest
import respx

from eco_mcp_app import server as eco_server
from eco_mcp_app.server import (
    DEFAULT_ECO_INFO_URL,
    ECONOMY_DATASETS,
    build_server,
    compute_economy_payload,
    fetch_economy,
)

# Base URL of the default Eco server, derived the same way server.py derives it.
_DEFAULT_BASE = DEFAULT_ECO_INFO_URL.rsplit("/info", 1)[0]
_DATASET_URL = f"{_DEFAULT_BASE}/datasets/get"


@pytest.fixture(autouse=True)
def _clear_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test gets a clean slate for info + economy caches + admin token."""
    eco_server._info_cache.clear()
    eco_server._economy_cache.clear()
    eco_server._admin_token_cache.clear()
    # An explicit env var sidesteps SSM/boto3 — tests must never reach AWS.
    monkeypatch.setenv("ECO_ADMIN_TOKEN", "test-token")


def _info_body(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "Description": "Eco via Sirens",
        "Category": "Test",
        "DaysRunning": 3,
        "TimeSinceStart": 3 * 3600,
        "EconomyDesc": "524 trades, 0 contracts",
        "TotalCulture": 142.0,
    }
    base.update(overrides)
    return base


def _series_points(vals: list[float]) -> list[dict[str, float]]:
    """Dataset format matches the live endpoint: list of {Time, Value}."""
    return [{"Time": float(i), "Value": float(v)} for i, v in enumerate(vals)]


def _mock_all_datasets(values: dict[str, list[float]]) -> None:
    """Mock /datasets/get, routing by the `dataset` query param."""

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.params.get("dataset", "")
        pts = _series_points(values.get(name, []))
        return httpx.Response(200, json=pts)

    respx.get(_DATASET_URL).mock(side_effect=handler)


# ---------------------------------------------------------------------------
# fetch_economy: wiring + degradation paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_fetch_economy_sends_admin_token_header() -> None:
    info_route = respx.get(DEFAULT_ECO_INFO_URL).mock(
        return_value=httpx.Response(200, json=_info_body())
    )
    _mock_all_datasets({name: [1, 2, 3] for name in ECONOMY_DATASETS})

    raw = await fetch_economy()
    # 1 /info + 14 dataset fan-out calls.
    assert info_route.called
    dataset_calls = [c for c in respx.calls if str(c.request.url).startswith(_DATASET_URL)]
    assert len(dataset_calls) == len(ECONOMY_DATASETS)
    for call in dataset_calls:
        assert call.request.headers.get("X-API-Key") == "test-token"
    assert raw["admin_ok"] is True
    assert raw["days_elapsed"] == 3
    assert set(raw["series"].keys()) == set(ECONOMY_DATASETS)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_economy_empty_without_admin_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """No token → /datasets/get is never called; each series is an empty list."""
    monkeypatch.delenv("ECO_ADMIN_TOKEN", raising=False)
    # Also defeat any ambient SSM/boto3 by making _load_admin_token see no env.
    eco_server._admin_token_cache.clear()
    monkeypatch.setattr(eco_server, "_load_admin_token", lambda: None)

    respx.get(DEFAULT_ECO_INFO_URL).mock(return_value=httpx.Response(200, json=_info_body()))
    ds_route = respx.get(_DATASET_URL).mock(return_value=httpx.Response(200, json=[]))

    raw = await fetch_economy()
    assert raw["admin_ok"] is False
    assert not ds_route.called
    assert all(pts == [] for pts in raw["series"].values())


@pytest.mark.asyncio
@respx.mock
async def test_fetch_economy_tolerates_per_dataset_500() -> None:
    """A single 500 from one dataset must not blow up the rest of the card."""
    respx.get(DEFAULT_ECO_INFO_URL).mock(return_value=httpx.Response(200, json=_info_body()))

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.params.get("dataset", "")
        if name == "TransferMoney":
            return httpx.Response(500, text="No stat named X was found")
        return httpx.Response(200, json=_series_points([1, 2, 3]))

    respx.get(_DATASET_URL).mock(side_effect=handler)

    raw = await fetch_economy()
    # The failed dataset is named as unread rather than silently reported as an
    # empty (and therefore zero-valued) series (#261).
    assert "TransferMoney" not in raw["series"]
    assert "TransferMoney" in raw["datasets_unavailable"]
    # A healthy series is still populated.
    assert raw["series"]["PayWages"] == [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]
    assert "PayWages" not in raw["datasets_unavailable"]


@pytest.mark.asyncio
@respx.mock
async def test_fetch_economy_caches_within_ttl() -> None:
    info_route = respx.get(DEFAULT_ECO_INFO_URL).mock(
        return_value=httpx.Response(200, json=_info_body())
    )
    _mock_all_datasets({name: [] for name in ECONOMY_DATASETS})

    await fetch_economy()
    await fetch_economy()
    await fetch_economy()
    # Economy cache short-circuits the second + third calls entirely.
    assert info_route.call_count == 1


# ---------------------------------------------------------------------------
# compute_economy_payload: KPI math + classification
# ---------------------------------------------------------------------------


def _raw(
    series: dict[str, list[int]] | dict[str, list[float]] | None = None,
    days: int = 3,
    econ_desc: str = "524 trades, 0 contracts",
    admin_ok: bool = True,
) -> dict[str, object]:
    pts = {
        name: [(float(i), float(v)) for i, v in enumerate(series.get(name, []))] if series else []
        for name in ECONOMY_DATASETS
    }
    return {
        "info": {
            "Description": "Eco",
            "Category": "Test",
            "EconomyDesc": econ_desc,
            "TotalCulture": 100.0,
            "_sourceUrl": DEFAULT_ECO_INFO_URL,
        },
        "days_elapsed": days,
        "series": pts,
        "admin_ok": admin_ok,
    }


def test_compute_healthy_classification_on_day3_zero_contracts() -> None:
    """The real Day-3 shape: 524 trades, no contracts, no loans → healthy."""
    payload = compute_economy_payload(_raw())
    k = payload["kpis"]
    assert payload["health"] == "healthy"
    assert k["trades_total"] == 524
    assert k["trades_per_day"] == round(524 / 3, 1)
    # Nothing resolved → there is no rate to report. A 0.0 here would read as a
    # measured "no defaults" when the truth is "no loans at all" (#261).
    assert k["loan_default_rate"] is None
    assert k["contract_completion_ratio"] is None
    # The counts behind those rates were measured, so they stay real zeros.
    assert k["contracts_posted"] == 0
    assert k["loans_offered"] == 0
    # Narrative says what was observed instead of dressing zeros up as health.
    assert "no loans resolved" in payload["narrative"]
    assert "no contract activity recorded" in payload["narrative"]
    assert "0.0% contracts completed" not in payload["narrative"]


def test_unread_datasets_yield_null_kpis_not_zeros() -> None:
    """A dataset the fetch could not read must not become a confident zero."""
    raw = _raw()
    raw["series"] = {}
    raw["datasets_unavailable"] = list(ECONOMY_DATASETS)
    payload = compute_economy_payload(raw)
    k = payload["kpis"]

    assert k["wages_total"] is None
    assert k["taxes_paid"] is None
    assert k["contracts_posted"] is None
    assert k["loans_offered"] is None
    assert k["net_tax_flow"] is None
    assert payload["datasets_unavailable"] == list(ECONOMY_DATASETS)
    assert "unavailable" in payload["narrative"]
    # Unknown credit conditions must never be read as a boom.
    assert payload["health"] != "booming"


def test_govt_funds_reads_the_same_dataset_as_get_currency() -> None:
    """get_economy and get_currency must agree on the treasury balance (#258)."""
    raw = _raw(
        series={
            eco_server.currency_mod.GOVERNMENT_HOLDINGS_DATASET: [100, 500, 87912],
            "PayTax": [150],
            "ReceiveGovernmentFunds": [30],
        }
    )
    k = compute_economy_payload(raw)["kpis"]

    # A level dataset reports its current value, not the sum of its samples.
    assert k["govt_funds"] == 87912.0
    assert k["govt_funds_source"] == eco_server.currency_mod.GOVERNMENT_HOLDINGS_DATASET
    # The flow out of the treasury stays a separate, still-summed number.
    assert k["govt_funds_received"] == 30.0
    assert k["net_tax_flow"] == 120.0


def test_level_datasets_stay_out_of_sparks() -> None:
    """Treasury balance is not economic volatility, so it cannot win a spark slot."""
    raw = _raw(
        series={
            eco_server.currency_mod.GOVERNMENT_HOLDINGS_DATASET: [1, 900, 5, 4000],
            "PropertyTransfer": [1, 2, 1, 3],
        }
    )
    names = [s["name"] for s in compute_economy_payload(raw)["sparks"]]
    assert eco_server.currency_mod.GOVERNMENT_HOLDINGS_DATASET not in names
    assert "PropertyTransfer" in names


def test_compute_stressed_on_high_default_rate() -> None:
    series = {
        "DefaultedOnLoanOrBond": [3, 3, 3],  # total 9
        "RepaidLoanOrBond": [1],  # total 1 → default rate 90%
    }
    payload = compute_economy_payload(_raw(series=series))
    assert payload["health"] == "stressed"
    assert payload["kpis"]["loan_default_rate"] == 90.0


def test_compute_stressed_on_high_contract_failure() -> None:
    series = {
        "CompletedContract": [1],
        "FailedContract": [5, 5],  # failure rate > 90%
    }
    payload = compute_economy_payload(_raw(series=series))
    assert payload["health"] == "stressed"
    assert payload["kpis"]["contract_failure_rate"] > 30.0


def test_compute_booming_on_activity_growth() -> None:
    """Two weeks of runtime, trailing-week activity >20% above the prior week,
    zero loan defaults → booming. Guards against the old tautological WoW that
    made `booming` unreachable."""
    # 15 daily points: 8 quiet days (10/day) then 7 busy days (30/day). The
    # trailing 7-day window lands squarely on the busy tail.
    series = {"TransferMoney": [10] * 8 + [30] * 7}
    payload = compute_economy_payload(_raw(series=series, days=15))
    wow = payload["kpis"]["trades_wow_pct"]
    assert wow is not None and wow >= 20.0
    assert payload["health"] == "booming"


def test_compute_wow_none_before_two_weeks() -> None:
    """WoW is undefined until two full weeks of runtime — a young server must
    never read `booming`."""
    series = {"TransferMoney": [10, 20, 30, 40, 50]}
    payload = compute_economy_payload(_raw(series=series, days=5))
    assert payload["kpis"]["trades_wow_pct"] is None
    assert payload["health"] != "booming"


def test_compute_wow_stressed_beats_booming() -> None:
    """A high default rate classifies `stressed` even when activity is growing —
    the stressed check must take precedence."""
    series = {
        "TransferMoney": [10] * 8 + [40] * 7,  # strong WoW growth
        "DefaultedOnLoanOrBond": [9],  # total 9
        "RepaidLoanOrBond": [1],  # → 90% default rate
    }
    payload = compute_economy_payload(_raw(series=series, days=15))
    assert payload["kpis"]["trades_wow_pct"] is not None
    assert payload["health"] == "stressed"


def test_compute_healthy_when_both_zero() -> None:
    """Day-3 reality: zero contracts, zero loans must not trigger `stressed`."""
    payload = compute_economy_payload(_raw())
    assert payload["health"] == "healthy"


def test_compute_net_tax_flow_signed() -> None:
    series = {
        "PayTax": [100, 50],  # total 150 in
        "ReceiveGovernmentFunds": [30],  # total 30 out
    }
    payload = compute_economy_payload(_raw(series=series))
    assert payload["kpis"]["net_tax_flow"] == 120.0


def test_compute_sparks_pick_most_volatile() -> None:
    """Sparklines prefer high-stddev series (normalized by mean)."""
    series = {
        "PayWages": [10, 10, 10, 10],  # flat → stddev 0
        "TransferMoney": [1, 100, 5, 800],  # spiky
        "PayTax": [5, 5, 5, 5],  # flat
        "PropertyTransfer": [1, 2, 1, 3],  # mildly spiky
    }
    payload = compute_economy_payload(_raw(series=series))
    names = [s["name"] for s in payload["sparks"]]
    # The flat series should NOT be first; the spiky one should.
    assert names[0] == "TransferMoney"
    # The structured result retains only data needed by API consumers.
    assert "svg" not in payload["sparks"][0]


def test_compute_sparks_skips_empty_series() -> None:
    """Series with <2 points are excluded (would only render a flat baseline)."""
    payload = compute_economy_payload(_raw())
    assert payload["sparks"] == []


def test_compute_handles_missing_economy_desc() -> None:
    """No EconomyDesc on /info → trades_total is 0, not a crash."""
    payload = compute_economy_payload(_raw(econ_desc=""))
    assert payload["kpis"]["trades_total"] == 0
    assert payload["kpis"]["trades_per_day"] == 0.0


# ---------------------------------------------------------------------------
# MCP tool wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tools_includes_get_economy() -> None:
    mcp = build_server()
    handler = mcp.request_handlers[mt.ListToolsRequest]
    result = await handler(mt.ListToolsRequest(method="tools/list"))
    names = {tool.name for tool in result.root.tools}
    assert "get_economy" in names


@pytest.mark.asyncio
@respx.mock
async def test_call_get_economy_returns_htmx_fragment() -> None:
    respx.get(DEFAULT_ECO_INFO_URL).mock(return_value=httpx.Response(200, json=_info_body()))
    _mock_all_datasets({name: [1.0, 2.0, 3.0] for name in ECONOMY_DATASETS})

    mcp = build_server()
    handler = mcp.request_handlers[mt.CallToolRequest]
    req = mt.CallToolRequest(
        method="tools/call",
        params=mt.CallToolRequestParams(name="get_economy", arguments={}),
    )
    result = await handler(req)
    blocks = result.root.content
    assert len(blocks) == 2
    # Block 0: markdown fallback. Block 1: JSON. Just-data per eco-app#87:
    # get_economy no longer emits a widget, so there is no HTML fragment.
    assert isinstance(blocks[0], mt.TextContent)
    assert isinstance(blocks[1], mt.TextContent)

    # Markdown mentions the narrative health.
    assert "economic health" in blocks[0].text.lower()

    # JSON is parseable and carries the computed KPIs.
    payload = json.loads(blocks[1].text)
    assert payload["health"] in {"healthy", "booming", "stressed"}
    assert payload["kpis"]["trades_total"] == 524

    # Just-data per eco-app#87: get_economy no longer emits a widget.
    assert result.root.meta is None


@pytest.mark.asyncio
@respx.mock
async def test_call_get_economy_handles_info_failure() -> None:
    respx.get(DEFAULT_ECO_INFO_URL).mock(side_effect=httpx.ConnectError("refused"))

    mcp = build_server()
    handler = mcp.request_handlers[mt.CallToolRequest]
    req = mt.CallToolRequest(
        method="tools/call",
        params=mt.CallToolRequestParams(name="get_economy", arguments={}),
    )
    result = await handler(req)
    # Error path: isError=True, plain-text error block, and no widget
    # (just-data per eco-app#87).
    assert result.root.isError is True
    assert "unreachable" in result.root.content[0].text.lower()
    assert result.root.meta is None


@pytest.mark.asyncio
async def test_a_shape_we_cannot_parse_is_unmeasured_not_zero() -> None:
    """A 200 whose body yields no parsed points is not an empty dataset. It is
    a dataset we could not read, and reporting 0 for it made get_economy
    contradict get_currency about a funded treasury. See #266."""
    import httpx

    from eco_mcp_app.server import _fetch_dataset

    async def handler(request: httpx.Request) -> httpx.Response:
        # 200, non-empty, and in none of the shapes the parser knows.
        return httpx.Response(200, json={"unexpectedEnvelope": [{"t": 1, "v": 2}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        points = await _fetch_dataset(client, "http://e", "AnyDataset", 5, {})

    assert points is None, "an unreadable body was reported as a measured empty series"


@pytest.mark.asyncio
async def test_a_genuinely_empty_series_stays_measured() -> None:
    """The distinction that has to survive: nothing happened is not the same
    as nothing was read."""
    import httpx

    from eco_mcp_app.server import _fetch_dataset

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        points = await _fetch_dataset(client, "http://e", "AnyDataset", 5, {})

    assert points == [], "a genuinely empty dataset was reported as unreadable"


@pytest.mark.asyncio
async def test_rows_that_all_fail_to_parse_are_unmeasured() -> None:
    """Every row skipped means the shape is wrong, not that the server is idle."""
    import httpx

    from eco_mcp_app.server import _fetch_dataset

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"when": 1, "howMuch": 2}, {"when": 2}])

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        points = await _fetch_dataset(client, "http://e", "AnyDataset", 5, {})

    assert points is None


def _kpis_with_all_datasets_read() -> dict[str, object]:
    """Baseline: every dataset present and carrying real activity."""
    raw = _raw({name: [1.0, 2.0, 3.0] for name in ECONOMY_DATASETS})
    return compute_economy_payload(raw)["kpis"]


def test_withholding_any_dataset_yields_null_never_zero() -> None:
    """The invariant, over every dataset rather than one case per tool.

    Three commits fixed this shape one tool at a time without converging, so
    the rule is asserted as a property: a KPI that moves when its dataset goes
    unread must move to None. A 0.0 is a measured "no activity" and cannot be
    told apart from "never looked" by any caller. See eco-app#6077.
    """
    baseline = _kpis_with_all_datasets_read()

    for withheld in ECONOMY_DATASETS:
        present = {name: [1.0, 2.0, 3.0] for name in ECONOMY_DATASETS if name != withheld}
        raw = _raw(present)
        raw["series"] = {
            name: [(float(i), float(v)) for i, v in enumerate(vals)]
            for name, vals in present.items()
        }
        payload = compute_economy_payload(raw)

        assert withheld in payload["datasets_unavailable"], (
            f"{withheld} was not read and is missing from datasets_unavailable, "
            "so a caller cannot tell which nulls it produced"
        )

        for key, was in baseline.items():
            now = payload["kpis"][key]
            if now == was:
                continue
            assert now is None, (
                f"withholding {withheld} moved kpis[{key}] from {was!r} to {now!r}. "
                "An unread dataset must yield None: a zero asserts the server "
                "reported no activity, which is a different claim"
            )


def test_a_measured_zero_stays_zero() -> None:
    """The negative control, or the rule above is satisfied by nulling everything."""
    raw = _raw({name: [0.0, 0.0] for name in ECONOMY_DATASETS})
    kpis = compute_economy_payload(raw)["kpis"]

    assert kpis["wages_total"] == 0.0
    assert kpis["taxes_paid"] == 0.0
    assert compute_economy_payload(raw)["datasets_unavailable"] == []
