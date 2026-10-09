"""Real-world commodity benchmark for `get_market`: a FRED series beside an item's price history.

`get_market` attaches one optional field, `commodityBenchmark`, when its `item` filter names
an Eco good that maps to a FRED series. This module is everything behind that field and
nothing else: the item-to-series map, the FRED key lookup, a SQLite response cache, and the
cadence-aware percent-change math. It judges nothing. The in-game side of "is this price
fair" is the price norm on every priced row (`norms.py`), so this field only supplies the
real-world number the norm cannot.

Design notes:

- **Null is a state, not a zero.** An unmapped item, a missing key, a FRED fault or an empty
  series all return no benchmark. `fetch_benchmark` hands back a warning for every one of
  those except an unmapped item, which is the ordinary case for most Eco goods and would
  put a warning on nearly every filtered call.
- **FRED cadence matters.** Copper, wheat, iron and board are monthly and WTI oil is daily.
  A "7-day change" against a monthly series is either zero or noise, so the change keys
  follow the series' own frequency.
- **Key lookup.** `FRED_API_KEY` from the environment wins, then SSM
  `/eco-mcp-app/fred-api-key` through boto3 pinned to `us-east-1` (the AWS CLI default of
  `us-west-2` fails with `ParameterNotFound` silently).
- **Cache.** `~/.cache/eco-mcp-app/fred.sqlite`, 6 hours for observations and 7 days for
  series metadata, keyed by series id. `ECO_MCP_CACHE_DIR` relocates it.
- **Errors never carry the URL.** An httpx error string embeds the request URL, query
  string and `api_key` included, so a warning names the status code or exception class only.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

FRED_BASE_URL = "https://api.stlouisfed.org/fred"

# Eco item -> FRED series. `display_unit` is what FRED's observations are denominated in.
# `proxy_note` is set where the series is a stand-in for the good, so the field says so
# instead of letting a reader take an ore price for an ingot price (#234).
ITEM_MAP: dict[str, dict[str, str]] = {
    "Copper": {
        "series_id": "PCOPPUSDM",
        "eco_item": "CopperIngot",
        "display_name": "copper",
        "display_unit": "USD / metric ton",
    },
    "Wheat": {
        "series_id": "PWHEAMTUSDM",
        "eco_item": "Wheat",
        "display_name": "wheat",
        "display_unit": "USD / metric ton",
    },
    "Board": {
        "series_id": "WPU0811",
        "eco_item": "Board",
        "display_name": "lumber (PPI)",
        "display_unit": "PPI index",
        "proxy_note": "A producer price index for lumber, not a price per unit.",
    },
    "Iron": {
        "series_id": "PIORECRUSDM",
        "eco_item": "IronIngot",
        "display_name": "iron ore",
        "display_unit": "USD / metric ton",
        "proxy_note": "Iron ore, a different good from the ingot, earlier in the production chain.",
    },
    "Oil": {
        "series_id": "DCOILWTICO",
        "eco_item": "Oil",
        "display_name": "WTI crude oil",
        "display_unit": "USD / bbl",
    },
}

# Case-insensitive aliases: the map key, the in-game item id, and a few shorthands.
_ITEM_ALIASES: dict[str, str] = {"lumber": "Board", "crude": "Oil"}
for _key, _meta in ITEM_MAP.items():
    _ITEM_ALIASES[_key.lower()] = _key
    _ITEM_ALIASES[_meta["eco_item"].lower()] = _key


def resolve_item(item: str | None) -> str | None:
    """The `ITEM_MAP` key for `item`, or None. The exporter's `Item` suffix is ignored."""
    if not item:
        return None
    stem = item.strip().lower()
    if stem.endswith("item") and len(stem) > len("item"):
        stem = stem[: -len("item")]
    return _ITEM_ALIASES.get(stem)


_SSM_PARAM = "/eco-mcp-app/fred-api-key"
_SSM_REGION = "us-east-1"

# Process-level cache: SSM calls are not free and the key does not rotate mid-process.
_fred_api_key: str | None = None


def get_fred_api_key() -> str | None:
    """The FRED API key, or None when neither the environment nor SSM supplies one."""
    global _fred_api_key
    if _fred_api_key is not None:
        return _fred_api_key
    env = os.environ.get("FRED_API_KEY")
    if env:
        _fred_api_key = env.strip()
        return _fred_api_key
    try:
        import boto3  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        client = boto3.client("ssm", region_name=_SSM_REGION)
        resp = client.get_parameter(Name=_SSM_PARAM, WithDecryption=True)
        _fred_api_key = resp["Parameter"]["Value"].strip()
        return _fred_api_key
    except Exception as e:  # SSM must never take the tool down
        logger.warning("SSM fred-api-key lookup failed: %s", type(e).__name__)
        return None


def _reset_api_key_cache() -> None:
    """Test hook: clears the process-level FRED key cache."""
    global _fred_api_key
    _fred_api_key = None


def default_cache_dir() -> Path:
    return Path(os.environ.get("ECO_MCP_CACHE_DIR", str(Path.home() / ".cache" / "eco-mcp-app")))


def _cache_db_path() -> Path:
    d = default_cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "fred.sqlite"


# Observations refresh every 6h, under the fastest FRED cadence. Metadata barely changes.
OBSERVATIONS_TTL_S = 6 * 60 * 60
METADATA_TTL_S = 7 * 24 * 60 * 60


def _open_cache() -> sqlite3.Connection:
    conn = sqlite3.connect(_cache_db_path())
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fred_cache (
            kind TEXT NOT NULL,
            series_id TEXT NOT NULL,
            fetched_at REAL NOT NULL,
            payload TEXT NOT NULL,
            PRIMARY KEY (kind, series_id)
        )
        """
    )
    return conn


def _cache_get(kind: str, series_id: str, ttl_s: float) -> Any | None:
    with _open_cache() as conn:
        row = conn.execute(
            "SELECT fetched_at, payload FROM fred_cache WHERE kind = ? AND series_id = ?",
            (kind, series_id),
        ).fetchone()
    if row is None:
        return None
    fetched_at, payload = row
    if (time.time() - fetched_at) > ttl_s:
        return None
    return json.loads(payload)


def _cache_put(kind: str, series_id: str, payload: Any) -> None:
    with _open_cache() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO fred_cache (kind, series_id, fetched_at, payload) "
            "VALUES (?, ?, ?, ?)",
            (kind, series_id, time.time(), json.dumps(payload)),
        )


async def _fetch_json(client: httpx.AsyncClient, path: str, params: dict[str, str]) -> dict:
    r = await client.get(f"{FRED_BASE_URL}{path}", params=params)
    r.raise_for_status()
    return r.json()


async def fetch_series_metadata(series_id: str, api_key: str) -> dict[str, Any]:
    """The FRED series record (we read `frequency_short`), cached for 7 days."""
    cached = _cache_get("meta", series_id, METADATA_TTL_S)
    if cached is not None:
        return cached
    async with httpx.AsyncClient(timeout=10.0) as client:
        data = await _fetch_json(
            client,
            "/series",
            {"series_id": series_id, "api_key": api_key, "file_type": "json"},
        )
    series = (data.get("seriess") or [{}])[0]
    _cache_put("meta", series_id, series)
    return series


async def fetch_observations(
    series_id: str, api_key: str, *, limit: int = 200
) -> list[dict[str, Any]]:
    """Observations newest-first, truncated to `limit` (about 16 years of monthly data)."""
    cached = _cache_get("obs", series_id, OBSERVATIONS_TTL_S)
    if cached is not None:
        return cached
    async with httpx.AsyncClient(timeout=10.0) as client:
        data = await _fetch_json(
            client,
            "/series/observations",
            {
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": str(limit),
            },
        )
    obs = data.get("observations") or []
    _cache_put("obs", series_id, obs)
    return obs


def _parse_obs_value(raw: str | None) -> float | None:
    # FRED encodes a missing value as "." even in a numeric column.
    if raw is None or raw == "." or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _clean_observations(obs: list[dict[str, Any]]) -> list[tuple[str, float]]:
    """(date, value) pairs oldest-first with missing values dropped."""
    cleaned: list[tuple[str, float]] = []
    for o in reversed(obs):
        v = _parse_obs_value(o.get("value", ""))
        if v is None:
            continue
        cleaned.append((o.get("date", ""), v))
    return cleaned


def _pct(new: float, old: float) -> float | None:
    if old == 0:
        return None
    return (new - old) / old * 100.0


def _pct_at_offset(obs: list[tuple[str, float]], offset: int) -> float | None:
    """Percent change between the newest observation and the one `offset` samples back."""
    if len(obs) <= offset:
        return None
    return _pct(obs[-1][1], obs[-1 - offset][1])


def _pct_by_days(obs: list[tuple[str, float]], days: int) -> float | None:
    """Percent change against the newest observation at least `days` older.

    Daily series have gaps (weekends, holidays), so an exact list offset is not "N days ago".
    """
    if len(obs) < 2:
        return None
    latest_date_s, latest_val = obs[-1]
    try:
        latest_date = datetime.fromisoformat(latest_date_s)
    except ValueError:
        return None
    for date_s, val in reversed(obs[:-1]):
        try:
            d = datetime.fromisoformat(date_s)
        except ValueError:
            continue
        if (latest_date - d).days >= days:
            return _pct(latest_val, val)
    return None


def latest_pct_changes(
    obs: list[tuple[str, float]], frequency: str
) -> tuple[dict[str, float | None], str]:
    """Cadence-appropriate percent changes and the cadence label that names them."""
    freq = (frequency or "").upper()
    if freq == "D":
        return (
            {
                "7d": _pct_by_days(obs, 7),
                "30d": _pct_by_days(obs, 30),
                "90d": _pct_by_days(obs, 90),
            },
            "daily",
        )
    if freq == "M":
        return (
            {
                "1m": _pct_at_offset(obs, 1),
                "3m": _pct_at_offset(obs, 3),
                "12m": _pct_at_offset(obs, 12),
            },
            "monthly",
        )
    if freq == "W":
        return (
            {
                "1w": _pct_at_offset(obs, 1),
                "4w": _pct_at_offset(obs, 4),
                "52w": _pct_at_offset(obs, 52),
            },
            "weekly",
        )
    return (
        {
            "prev": _pct_at_offset(obs, 1),
            "3-back": _pct_at_offset(obs, 3),
            "12-back": _pct_at_offset(obs, 12),
        },
        freq.lower() or "unknown",
    )


async def fetch_benchmark(item: str | None) -> tuple[dict[str, Any] | None, str | None]:
    """The `commodityBenchmark` value for `item` and the warning that explains a null.

    Returns `(None, None)` for an item with no FRED mapping, `(None, warning)` when the
    mapping exists but the key, the fetch or the series failed, and `(benchmark, None)`
    otherwise. Never raises: `get_market` must survive any FRED fault.
    """
    resolved = resolve_item(item)
    if resolved is None:
        return None, None
    meta = ITEM_MAP[resolved]
    series_id = meta["series_id"]
    prefix = f"Commodity benchmark for {resolved} unavailable"
    api_key = get_fred_api_key()
    if not api_key:
        return None, f"{prefix}: FRED API key not configured."
    try:
        obs_cached = _cache_get("obs", series_id, OBSERVATIONS_TTL_S) is not None
        meta_cached = _cache_get("meta", series_id, METADATA_TTL_S) is not None
        series_meta = await fetch_series_metadata(series_id, api_key)
        raw_obs = await fetch_observations(series_id, api_key)
    except httpx.HTTPStatusError as e:
        return None, f"{prefix}: FRED answered HTTP {e.response.status_code}."
    except (httpx.HTTPError, ValueError, sqlite3.Error, OSError) as e:
        return None, f"{prefix}: FRED request failed ({type(e).__name__})."
    frequency = series_meta.get("frequency_short") or ""
    cleaned = _clean_observations(raw_obs)
    if not cleaned:
        return None, f"{prefix}: FRED series {series_id} returned no observations."
    latest_date, latest_value = cleaned[-1]
    changes, changes_label = latest_pct_changes(cleaned, frequency)
    return {
        "source": "FRED",
        "requested": item,
        "item": resolved,
        "benchmarkedAs": meta["display_name"],
        "note": meta.get("proxy_note"),
        "seriesId": series_id,
        "displayUnit": meta["display_unit"],
        "frequency": frequency,
        "latestValue": latest_value,
        "latestDate": latest_date,
        "changes": changes,
        "changesLabel": changes_label,
        "cached": obs_cached and meta_cached,
    }, None


def benchmark_markdown(benchmark: dict[str, Any]) -> str:
    """One line for the text block of a `get_market` result."""
    changes = ", ".join(
        f"{v:+.1f}% over {k}" for k, v in benchmark["changes"].items() if v is not None
    )
    note = f" {benchmark['note']}" if benchmark.get("note") else ""
    return (
        f"- Real-world benchmark: {benchmark['benchmarkedAs']} (FRED {benchmark['seriesId']}) "
        f"{benchmark['latestValue']:,.2f} {benchmark['displayUnit']} on {benchmark['latestDate']}"
        f"{f', {changes}' if changes else ''}.{note}"
    )
