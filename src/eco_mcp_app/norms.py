"""The historical price norm, attached wherever a tool or page lists an item price.

Kai's decision on teable:coilyco/eco-app#8368. Reads `data/eco_trades_norms.json.gz`
(built by `scripts/trades_norms.py`) and compares a price against past cycles at
the live world's upgrade stage. Field contract and fallbacks: docs/price-history.md.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import statistics
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from functools import cache
from typing import Any

import httpx

from .crafting import _normalize_admin_base, prettify_eco_name
from .recipes import _bundled_data_path

UPGRADE = re.compile(r"^(?:Scholars )?(Basic|Advanced|Modern) Upgrade ([1-4])$")
TIERS = ("Basic", "Advanced", "Modern")
STAGES = ("none", *(f"{tier} {n}" for tier in TIERS for n in range(1, 5)))

# Fewer trades than this behind a stage bucket and the all-stage figure stands in.
MIN_N = 5
LIVE_TTL_S = 600.0
CAVEAT = "stage trails the tech level by the time from an upgrade's first craft to its first trade"
_NUMERIC = re.compile(r"^\d+$")
_CYCLE = re.compile(r"\bCycle\s+(\d+)", re.I)
_TAGS = re.compile(r"<[^>]*>")
_LVL = re.compile(r"\s*Lvl\s*(\d)")
# Recipe-graph items as `eco://vocab/items` entries: an item there with no trades is
# still an item, so it resolves with no price rather than as a miss (#8425).
Catalog = Sequence[dict[str, Any]]
# "mining bu5": a specialty word before a tier's 5 names one specialist module.
_SPECIALTY = re.compile(r"^(.+?)\s+([bam])u ?5$", re.I)
_SPECIALTY_AFTER = re.compile(r"^([bam])u ?5\s+(.+)$", re.I)
_DETERMINERS = frozenset({"a", "an", "the", "my", "some", "your", "our"})
# Names members use that no item carries. Checked like a name, plurals included.
_ALIASES = {"solar panel": "Solar Generator"}


def upgrade_stage(item: str) -> int:
    """1-12 for Basic 1 through Modern 4, Scholars folded in; 0 for anything else."""
    m = UPGRADE.match(item)
    return TIERS.index(m[1]) * 4 + int(m[2]) if m else 0


def _r(x: float) -> float:
    return round(x, 4)


@dataclass(frozen=True)
class LiveContext:
    """What the live world says about the norm: its cycle, stage, and currency names."""

    stage: str | None
    cycle: int | None
    currency_names: dict[str, str] = field(default_factory=dict)
    home: bool = True
    notes: tuple[str, ...] = ()
    # False when the ledger could not be read, so "no stage" is not "no upgrade yet".
    stage_known: bool = True


class Norms:
    """One lookup over the norms file, by item, currency and stage."""

    def __init__(self, data: dict[str, Any], item_ids: dict[str, str]) -> None:
        self.cycles = {c["cycle"]: c for c in data.get("cycles", [])}
        self.latest = max(self.cycles, default=None)
        self.items: dict[str, Any] = data.get("items", {})
        self.built = data.get("source", {})
        self._by_lower = {name.lower(): name for name in self.items}
        self._by_id = {i: n for i, n in item_ids.items() if n in self.items}
        self._aliases = {a: n for a, n in _ALIASES.items() if n in self.items}
        self.all_stages = {name: self._all_stages(name) for name in self.items}

    def _all_stages(self, name: str) -> dict[str, Any] | None:
        points = []
        for cycle, slot in self.items[name]["cycles"].items():
            meta = self.cycles.get(int(cycle), {})
            entry = slot["byCurrency"].get(meta.get("primaryCurrency") or "")
            if entry and meta.get("basketIndex"):
                points.append((entry["median"] / meta["basketIndex"], entry["n"]))
        if not points:
            return None
        return {
            "median": _r(statistics.median(p for p, _ in points)),
            "cycles": len(points),
            "n": sum(n for _, n in points),
        }

    def key(self, item: str | None) -> str | None:
        """The norms name for an Eco id (`LumberItem`) or a display name (`Lumber`)."""
        if not item:
            return None
        if item in self.items:
            return item
        if item in self._by_id:
            return self._by_id[item]
        # Eco ids spell tiers `BasicUpgradeLvl1Item`, where the trade feed says `Basic Upgrade 1`.
        pretty = _LVL.sub(r" \1", prettify_eco_name(item))
        return self._by_lower.get(item.lower()) or self._by_lower.get(pretty.lower())

    def stage_of(self, item: str) -> int:
        name = self.key(item)
        return upgrade_stage(name or prettify_eco_name(item))

    def reference_currency(self, ctx: LiveContext) -> str | None:
        return self.cycles.get(ctx.cycle or -1, {}).get("primaryCurrency")

    def lookup(
        self,
        item: str | None,
        price: float | None,
        currency: str | None,
        ctx: LiveContext,
    ) -> dict[str, Any]:
        """The per-object norm. `basis` and `n` are always present, the rest only
        when it has a value."""
        if not ctx.home:
            return {"basis": None, "n": 0, "fallback": "norms describe the Sirens server only"}
        name = self.key(item)
        if name is None:
            return {"basis": None, "n": 0, "fallback": "no trade history for this item"}
        out: dict[str, Any] = {}
        bucket = self.items[name]["crossCycle"].get(ctx.stage) if ctx.stage else None
        figure: dict[str, Any] | None
        if bucket and bucket["n"] >= MIN_N:
            out["basis"], figure = "stage", bucket
        else:
            out["basis"], figure = "all", self.all_stages[name]
            if ctx.stage is None and not ctx.stage_known:
                out["fallback"] = "the live stage is unknown, using all stages"
            elif ctx.stage is None:
                out["fallback"] = "no upgrade traded yet this cycle, using all stages"
            elif bucket:
                out["fallback"] = (
                    f"only {bucket['n']} trades at {ctx.stage} in past cycles, using all stages"
                )
            else:
                out["fallback"] = f"no trades at {ctx.stage} in past cycles, using all stages"
        if figure is None:
            out.update(basis=None, n=0)
        else:
            out.update(n=figure["n"], cycles=figure["cycles"])
            index = self.cycles.get(ctx.cycle or -1, {}).get("basketIndex")
            if index:
                out["referencePrice"] = _r(figure["median"] * index)
        cur_name = _currency_name(currency, ctx)
        if (
            price is not None
            and "referencePrice" in out
            and out["referencePrice"] > 0
            and cur_name is not None
            and cur_name == self.reference_currency(ctx)
        ):
            out["multiple"] = round(float(price) / out["referencePrice"], 2)
        if cur_name is not None:
            out.update(self._in_currency(name, cur_name, ctx.stage))
        return out

    def _in_currency(self, name: str, cur: str, stage: str | None) -> dict[str, Any]:
        """The most recent cycle this item traded in this currency, at the stage
        when that bucket is big enough, else the whole cycle."""
        cycles = self.items[name]["cycles"]
        for cycle in sorted(cycles, key=int, reverse=True):
            slot = cycles[cycle]
            whole = slot["byCurrency"].get(cur)
            if not whole:
                continue
            staged = slot["stages"].get(stage or "", {}).get(cur)
            pick = staged if staged and staged["n"] >= MIN_N else whole
            return {
                "median": pick["median"],
                "p25": pick["p25"],
                "p75": pick["p75"],
                "currency": cur,
                "currencyN": pick["n"],
                "cycle": int(cycle),
            }
        return {}

    def resolve(self, word: str | None) -> str | None:
        """One player word to one norms item, or None. Exact name or Eco id, then
        the singular of a plural, then a bare metal word as its bar. No fuzzy match:
        a wrong item priced confidently is worse than none (teable:coilyco/eco-app#8423)."""
        query = " ".join((word or "").split())
        if not query:
            return None
        bases = [query]
        # "a basic upgrade 4", "my bricks": a caller may pass the member's words
        # verbatim. Tried second, so The Grasshopper still resolves whole.
        head, _, rest = query.partition(" ")
        if rest and head.lower() in _DETERMINERS:
            bases.append(rest)
        for base in bases:
            if name := self.key(base) or self._resolve_form(base.lower(), self._by_lower):
                return name
        return None

    def _bases(self, word: str | None) -> list[str]:
        query = " ".join((word or "").split())
        if not query:
            return []
        bases = [query]
        head, _, rest = query.partition(" ")
        if rest and head.lower() in _DETERMINERS:
            bases.append(rest)
        return bases

    def _resolve_form(self, lowered: str, by_lower: dict[str, str]) -> str | None:
        forms = [lowered]
        if lowered.endswith("ies"):
            forms.append(lowered[:-3] + "y")
        if lowered.endswith("es"):
            forms.append(lowered[:-2])
        if lowered.endswith("s"):
            forms.append(lowered[:-1])
        for form in forms:
            if name := by_lower.get(form) or self._aliases.get(form):
                return name
            # Only the metals are named `<Word> Bar`, and players say "iron" for the bar.
            if " " not in form and (name := by_lower.get(f"{form} bar")):
                return name
        return None

    def find(self, word: str | None, catalog: Catalog = ()) -> tuple[str | None, dict[str, Any]]:
        """(item name, miss detail) for the whole phrase. The name is a norms item or,
        failing that, a recipe-catalog item with no trades. Upgrade shorthand is read
        first (teable:coilyco/eco-app#8425): `au3`, `mining bu5`, and the xu0/xu5 misses."""
        from . import upgrade_words as uw

        names = {
            form.lower(): e["name"]
            for e in catalog
            for form in (e["name"], e["id"], *e.get("aliases", []))
        }
        names.update(self._by_lower)
        for base in self._bases(word):
            if uw.parse(base):
                if (name := uw.generic_item(base)) and name in names.values():
                    return name, {}
                if note := uw.meaning(base):
                    return None, {"note": note}
                _, tier, _ = uw.parse(base) or (False, "", 0)
                # The specialty words, not full names: 21 Basic names would pass 280.
                listed = ", ".join(uw.specialties(tier))
                article = "an" if tier == "Advanced" else "a"
                note = f"{uw.canonical(base)} is {article} {tier} specialist: {listed}."
                full = sorted(f"{m} Upgrade" for m in uw.SPECIALISTS[tier])
                return None, {"note": note, "candidates": full}
            m = _SPECIALTY.match(base)
            after = _SPECIALTY_AFTER.match(base)
            if m or after:
                words, letter = (m[1], m[2]) if m else (after[2], after[1])  # type: ignore[index]
                modules = uw.specialist_modules(uw.TIERS[letter.lower()], words)
                if len(modules) == 1:
                    return modules[0], {}
                if modules:
                    return None, {"note": f"{base} could be either:", "candidates": modules}
            if name := self.key(base) or self._resolve_form(base.lower(), self._by_lower):
                return name, {}
            if name := self._resolve_form(base.lower(), names):
                return name, {}
            # Exact order won above, so a swap only reaches an item the exact order is not.
            if (swapped := uw.swapped_tier(base)) and (
                name := self._resolve_form(swapped.lower(), names)
            ):
                return name, {}
        return None, {}

    def first_traded_stage(self, name: str) -> int | None:
        """The lowest stage the item ever traded at, in any currency or barter.
        `crossCycle` holds primary-currency trades only, so it can read too high."""
        seen = {STAGES.index(s) for s in self.items[name]["crossCycle"]}
        for slot in self.items[name]["cycles"].values():
            seen.update(STAGES.index(s) for s in slot.get("stages", {}) if s in STAGES)
        return min(seen, default=None)

    def _stage_medians(self, name: str, floor: int | None) -> list[tuple[int, float, bool]]:
        """(stage index, basket-unit median, estimated) from the floor to Modern 4.

        Kai's rule on #8423: a guess beats silence, never below the floor. Method
        per Jev on a holdout backtest (docs/price-history.md): linear on the stage
        index between real medians, flat carry past the outermost one. A stage
        under MIN_N is estimated, and anchors only when no stage reaches MIN_N."""
        if floor is None:
            return []
        cross = self.items[name]["crossCycle"]
        buckets = [(STAGES.index(s), b) for s, b in cross.items() if s in STAGES]
        full = sorted((i, b["median"]) for i, b in buckets if b["n"] >= MIN_N)
        anchors = full or sorted((i, b["median"]) for i, b in buckets)
        if not anchors:
            return []
        real = {i for i, _ in full}
        out = []
        for i in range(floor, len(STAGES)):
            below = [a for a in anchors if a[0] <= i]
            above = [a for a in anchors if a[0] >= i]
            if below and above and below[-1][0] != above[0][0]:
                (lo, v_lo), (hi, v_hi) = below[-1], above[0]
                value = v_lo + (v_hi - v_lo) * (i - lo) / (hi - lo)
            else:
                value = (below[-1] if below else above[0])[1]
            out.append((i, value, i not in real))
        return out

    def vocabulary(self, catalog: Catalog = ()) -> list[dict[str, Any]]:
        """Every item `find` accepts, as `eco://vocab/priced-items` entries: norms items,
        recipe items with no trades, their shorthand, and the shorthand that names no
        item. A caller matching words then fills `item` exactly when this tool would
        resolve it. A plural needs no entry: callers fold s/es."""
        from . import upgrade_words as uw

        ids = {n: i for i, n in self._by_id.items()}
        ids.update({e["name"]: e["id"] for e in catalog if e["name"] not in ids})
        names = sorted(set(self.items) | {e["name"] for e in catalog})
        shorthand = uw.item_aliases(names)
        entries = []
        for name in names:
            aliases = [ids[name]] if name in ids else []
            aliases += shorthand.get(name, [])
            aliases += [a for a, n in self._aliases.items() if n == name]
            head, _, tail = name.rpartition(" ")
            if tail == "Bar" and head and " " not in head:
                aliases.append(head)
            entries.append({"id": ids.get(name, name), "name": name, "aliases": aliases})
        return entries + uw.pseudo_entries()

    def candidates(self, word: str | None, catalog: Catalog = ()) -> list[str]:
        """Item names holding the word whole, only when there are 2 to 5 of them."""
        query = " ".join((word or "").split())
        if not query:
            return []
        pattern = re.compile(rf"\b{re.escape(query)}\b", re.I)
        names = set(self.items) | {e["name"] for e in catalog}
        hits = sorted(name for name in names if pattern.search(name))
        return hits if 2 <= len(hits) <= 5 else []

    def price_by_stage(
        self,
        word: str | None,
        ctx: LiveContext,
        stage: str | None = None,
        catalog: Catalog = (),
    ) -> dict[str, Any]:
        """The cross-cycle median per upgrade stage for one item, in the live currency.

        Basis is Jev's pick on #8423: the cross-cycle stage median times the live
        cycle's basketIndex, the same basis as `referencePrice`. Sold and Bought
        lines pool, one observation per trade line."""
        from . import upgrade_words as uw

        name, miss = self.find(word, catalog)
        qualifier = stage
        # "iron at au3": the shorthand qualifies, the rest is the item (#8425).
        if name is None and not miss and qualifier is None and word:
            rest, token = uw.split_qualifier(word)
            if token:
                name, miss = self.find(rest, catalog)
                qualifier = token
        if name is None:
            missing: dict[str, Any] = {
                "query": word,
                "resolved": None,
                "candidates": miss.get("candidates", self.candidates(word, catalog)),
            }
            if "note" in miss:
                missing["note"] = miss["note"]
            return {**missing, "reply": _stage_reply(missing)}
        item_id = next((i for i, n in self._by_id.items() if n == name), None)
        item_id = item_id or next((e["id"] for e in catalog if e["name"] == name), None)
        out: dict[str, Any] = {
            "query": word,
            "resolved": name,
            "item": name,
            "itemId": item_id,
            "currency": self.reference_currency(ctx),
            "cycle": ctx.cycle,
            "liveStage": ctx.stage,
            "minN": MIN_N,
            "traded": name in self.items,
            "stages": [],
        }
        if qualifier is not None:
            out["stageQuery"] = qualifier
            out["stage"] = uw.parse_stage(qualifier)
            if uw.parse(qualifier):
                out["stageShorthand"] = uw.canonical(qualifier)
        if name not in self.items:
            out["reply"] = _stage_reply(out)
            return out
        index = self.cycles.get(ctx.cycle or -1, {}).get("basketIndex")
        if not ctx.home:
            out["note"] = "norms describe the Sirens server only"
            return out
        if not index:
            out["note"] = (
                f"cycle {ctx.cycle} is not in the norms file yet, so no median is in its currency"
            )
        floor = self.first_traded_stage(name)
        out["firstTradedStage"] = STAGES[floor] if floor is not None else None
        # Every trade line in any currency, so a reply with no median can say why.
        out["trades"] = sum(
            entry.get("n", 0)
            for slot in self.items[name]["cycles"].values()
            for entry in slot.get("byCurrency", {}).values()
        )
        for i, value, estimated in self._stage_medians(name, floor):
            bucket = self.items[name]["crossCycle"].get(STAGES[i]) or {}
            out["stages"].append(
                {
                    "stage": STAGES[i],
                    "median": round(value * index, 2) if index else None,
                    "estimated": estimated,
                    "n": bucket.get("n", 0),
                    "cycles": bucket.get("cycles", 0),
                }
            )
        out["reply"] = _stage_reply(out)
        return out

    def context(self, ctx: LiveContext) -> dict[str, Any]:
        """Payload-level fields every per-object norm shares."""
        ref = self.reference_currency(ctx)
        ref_id = next((i for i, n in ctx.currency_names.items() if n == ref), None)
        notes = list(ctx.notes)
        if ctx.home and ctx.cycle is not None and ctx.cycle not in self.cycles:
            notes.append(
                f"cycle {ctx.cycle} is not in the norms file yet, so there is no reference "
                "price; regenerate data/eco_trades_norms.json.gz"
            )
        return {
            "stage": ctx.stage,
            "cycle": ctx.cycle,
            "referenceCurrency": ref,
            "referenceCurrencyId": ref_id,
            "caveat": CAVEAT,
            "source": (
                f"#eco-trades {self.built.get('firstTrade')} to {self.built.get('lastTrade')}, "
                f"{self.built.get('observations', 0):,} trade lines"
            ),
            "notes": notes,
        }


def _currency_name(currency: str | None, ctx: LiveContext) -> str | None:
    if not currency or currency in ("Barter", "unknown"):
        return None
    if _NUMERIC.match(currency):
        return ctx.currency_names.get(currency)
    return currency


@cache
def load() -> Norms | None:
    path = _bundled_data_path("eco_trades_norms.json.gz")
    if path is None:
        return None
    data = json.loads(gzip.decompress(path.read_bytes()))
    ids: dict[str, str] = {}
    baseline = _bundled_data_path("eco_trades_baseline.json.gz")
    if baseline is not None:
        for name, entry in json.loads(gzip.decompress(baseline.read_bytes()))["items"].items():
            if entry.get("itemId"):
                ids[entry["itemId"]] = name
    return Norms(data, ids)


# ---------- live context ----------

_live_cache: dict[str, tuple[float, LiveContext]] = {}


async def _info(server: str | None) -> dict[str, Any]:
    from .server import fetch_eco_info

    return await fetch_eco_info(server)


def _admin_key() -> str | None:
    from .server import ADMIN_API_KEY_ENV, _get_admin_token

    return os.environ.get(ADMIN_API_KEY_ENV) or _get_admin_token()


async def _ledger_items(server: str | None) -> list[str]:
    from .trades import fetch_parsed_trades

    fetched = await fetch_parsed_trades(base_url=server, api_key=_admin_key())
    return [t.item for t in fetched.parsed if t.item]


async def _currency_names(server: str | None) -> dict[str, str]:
    from .currency import fetch_currency_id_map

    key = _admin_key()
    headers = {"X-API-Key": key} if key else {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        return await fetch_currency_id_map(client, _normalize_admin_base(server), headers)


async def live_context(
    server: str | None,
    *,
    fetch_info: Callable[[str | None], Awaitable[dict[str, Any]]] = _info,
    fetch_items: Callable[[str | None], Awaitable[list[str]]] = _ledger_items,
    fetch_currency_names: Callable[[str | None], Awaitable[dict[str, str]]] = _currency_names,
) -> LiveContext:
    """The live cycle and stage, cached for ten minutes per server. The stage is the
    highest upgrade in the current cycle's trade ledger."""
    home = server in (None, "") or _normalize_admin_base(server) == _normalize_admin_base(None)
    key = "" if home else _normalize_admin_base(server)
    hit = _live_cache.get(key)
    if hit and time.monotonic() - hit[0] < LIVE_TTL_S:
        return hit[1]
    norms = load()
    notes: list[str] = []
    cycle = norms.latest if norms else None
    try:
        info = await fetch_info(server)
        m = _CYCLE.search(_TAGS.sub("", str(info.get("Description") or "")))
        if m:
            cycle = int(m[1])
        else:
            notes.append("live cycle not named by the server, assuming the latest in the file")
    except Exception:  # a norm lookup must never break a price tool
        notes.append("server /info unreachable, assuming the latest cycle in the file")
    stage: str | None = None
    stage_known = False
    try:
        items = await fetch_items(server)
        if items:
            top = max((norms.stage_of(i) for i in items), default=0) if norms else 0
            stage, stage_known = (STAGES[top] if top else None), True
        else:
            notes.append("the trade ledger returned no rows, so the live stage is unknown")
    except Exception:  # a norm lookup must never break a price tool
        notes.append("trade ledger unreachable, so the live stage is unknown")
    try:
        names = await fetch_currency_names(server)
    except Exception:  # a norm lookup must never break a price tool
        names = {}
    if not names:
        notes.append("currency ids could not be named, so id-keyed prices get no multiple")
    ctx = LiveContext(
        stage=stage,
        cycle=cycle,
        currency_names=names,
        home=home,
        notes=tuple(notes),
        stage_known=stage_known,
    )
    _live_cache[key] = (time.monotonic(), ctx)
    return ctx


# ---------- payload annotation ----------

# Every tool that lists an item price, and where. A path is dot-separated keys, and
# `key[]` walks a list. The object at the path gets `norm`, pricing the named field
# (none when the field is ""). Item and currency come from the object, else from the
# nearest ancestor holding one. A priced object under a normed row for the same item
# (an offer under its item's row) shares that row's norm rather than repeating it.
PriceSpec = tuple[str, str]
PRICE_FIELDS: dict[str, tuple[PriceSpec, ...]] = {
    "get_trades": (("trades[]", "unitPrice"),),
    "get_market": (("markets[]", "medianPrice"),),
    "get_stores": (
        ("stores[].topItems[]", "avgUnitPrice"),
        ("traders[].topSells[]", "avgUnitPrice"),
        ("traders[].topBuys[]", "avgUnitPrice"),
    ),
    "find_trade": (
        ("cheapest[]", "cheapest"),
        ("resale[]", "best"),
        ("arbitrage[]", ""),
        ("supplyGaps[]", "buyPrice"),
        ("marketSummaries[]", "cheapestSell"),
    ),
    "price_recipe": (
        ("recipes[].cost", "perUnitCost"),
        ("recipes[].cost.ingredients[]", "unitCost"),
    ),
    "get_recipes": (
        ("recipes[].cost", "perUnitCost"),
        ("recipes[].cost.ingredients[]", "unitCost"),
    ),
    "fair_price": (("", "inGameMedian"),),
    # SPA data routes, not MCP tools. Their pages show the same prices.
    "/preview/item.json": (("", ""),),
    "/preview/price-history.json": (("", "distribution.median"),),
}

# Every other tool, named so a new tool has to be placed in one list or the other.
NO_PRICE_TOOLS = frozenset(
    {
        "list_public_servers",
        "get_server_status",
        "get_currency",
        "get_civics",
        "get_progression",
        "get_world",
        "get_economy",
        "get_map",
        "get_milestones",
        "get_species",
        "explain_item",
        "get_crafting_atlas",
        "get_region",
        "get_climate",
        "get_government",
        "get_skills",
        # Its stages are the norm on one basis. Attaching `norm` would add this
        # cycle's own median and quartiles beside them (teable:coilyco/eco-app#8423).
        "price_by_stage",
        "get_social",
    }
)

_ITEM_KEYS = ("item", "product")
_CURRENCY_KEYS = ("currency", "inGameCurrency")


def _get(obj: dict[str, Any], dotted: str) -> Any:
    for part in dotted.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)  # type: ignore[assignment]
    return obj


def _visit(node: Any, parts: list[str], inherited: tuple[str | None, str | None], fn: Any) -> None:
    if not isinstance(node, dict):
        return
    item = next((node[k] for k in _ITEM_KEYS if isinstance(node.get(k), str)), inherited[0])
    cur = next((node[k] for k in _CURRENCY_KEYS if isinstance(node.get(k), str)), inherited[1])
    if not parts:
        fn(node, item, cur)
        return
    head, rest = parts[0], parts[1:]
    if head.endswith("[]"):
        for child in node.get(head[:-2]) or []:
            _visit(child, rest, (item, cur), fn)
    else:
        _visit(node.get(head), rest, (item, cur), fn)


def annotate(route: str, payload: Any, ctx: LiveContext, norms: Norms | None = None) -> bool:
    """Attach `norm` to every priced object `route` lists, and `normContext` once.
    Returns whether the route lists prices at all."""
    specs = PRICE_FIELDS.get(route)
    norms = norms or load()
    if not specs or norms is None or not isinstance(payload, dict):
        return False

    for path, price_key in specs:

        def attach(obj: dict[str, Any], item: str | None, cur: str | None, key: str = price_key):
            price = _get(obj, key) if key else None
            obj["norm"] = norms.lookup(
                item, price if isinstance(price, int | float) else None, cur, ctx
            )

        _visit(payload, [p for p in path.split(".") if p], (None, None), attach)
    payload["normContext"] = norms.context(ctx)
    return True


# Keys that make an object a listed item price, for the check that nothing slips.
PRICE_KEYS = frozenset(
    {
        "unitPrice",
        "medianPrice",
        "avgUnitPrice",
        "price",
        "cheapest",
        "best",
        "buyPrice",
        "cheapestSell",
        "perUnitCost",
        "unitCost",
        "inGameMedian",
        "bestUnitPrice",
    }
)


def unnormed(payload: Any) -> list[str]:
    """Paths of priced objects, with an item of their own or an ancestor's, that
    carry no `norm`. The acceptance check: empty for every price-returning payload."""
    misses: list[str] = []

    def walk(node: Any, path: str, item: str | None, normed: str | None) -> None:
        if isinstance(node, list):
            for i, child in enumerate(node):
                walk(child, f"{path}[{i}]", item, normed)
            return
        if not isinstance(node, dict):
            return
        item = next((node[k] for k in _ITEM_KEYS if isinstance(node.get(k), str)), item)
        if "norm" in node:
            normed = item
        priced = any(isinstance(node.get(k), int | float) for k in PRICE_KEYS)
        if priced and item is not None and normed != item:
            misses.append(path or "$")
        for k, v in node.items():
            if k not in ("norm", "normContext"):
                walk(v, f"{path}.{k}", item, normed)

    walk(payload, "", None, None)
    return misses


def _stage_reply(payload: dict[str, Any]) -> str:
    """The one-line answer `{{reply}}` templates. Kept terse so an item with every
    stage traded still fits the 280-character template cap."""
    if payload.get("resolved") is None:
        text = payload.get("note") or f"Couldn't match {payload.get('query')!r} to one Eco item."
        note = payload.get("note") or ""
        if payload.get("candidates") and (not note or note.endswith(":")):
            joiner = " " if note else " Items with that word: "
            text += joiner + ", ".join(payload["candidates"]) + "."
        return str(text)
    if not payload.get("traded", True):
        return f"{payload['item']} has no recorded trades."
    if payload.get("note"):
        return f"{payload['item']}: {payload['note']}."
    if not payload["stages"]:
        # Traded, but only in currencies with no basket index to compare (#8425).
        count = payload.get("trades", 0)
        plural = "" if count == 1 else "s"
        return (
            f"{payload['item']} has {count} recorded trade{plural}, none in a comparable "
            "currency, so no median."
        )
    # Grouped by tier so the stage number alone repeats: every item then fits.
    # `~` marks an estimate. Its thin count stays in the payload, not the line.
    tiers: dict[str, list[str]] = {}
    for row in payload["stages"]:
        tier, _, number = row["stage"].partition(" ")
        tiers.setdefault(tier, []).append(f"{number} {_figure(row, short=True)}".lstrip())
    groups = [
        f"no upgrade {rows[0]}" if tier == "none" else f"{tier} " + ", ".join(rows)
        for tier, rows in tiers.items()
    ]
    floor = payload.get("firstTradedStage")
    tail = f" None before {floor}." if floor and floor != STAGES[0] else ""
    head = f"{payload['item']} median {payload['currency']} by stage (trades, ~est.): "
    lead = _stage_lead(payload)
    if lead is None:
        return head + ". ".join(groups) + "." + tail
    # A qualifier leads, and the ladder follows while the cap allows.
    text = lead
    for count in range(len(groups), 0, -1):
        longer = f"{lead} By stage (trades, ~est.): " + ". ".join(groups[:count]) + "."
        if len(longer + tail) <= 280:
            return longer + tail
    return text


def _figure(row: dict[str, Any], short: bool = False) -> str:
    if row["median"] is None:
        return "?"
    if row["estimated"]:
        short_form = f"~{row['median']:.2f}"
        return short_form if short else f"{short_form} estimated ({row['n']} trades)"
    return (
        f"{row['median']:.2f} ({row['n']})" if short else f"{row['median']:.2f} ({row['n']} trades)"
    )


def _stage_lead(payload: dict[str, Any]) -> str | None:
    """The qualifier's own line: its median, or why there is none."""
    if "stageQuery" not in payload:
        return None
    asked = payload.get("stage")
    shown = f" ({payload['stageShorthand']})" if payload.get("stageShorthand") else ""
    if asked is None:
        return f"{payload['stageQuery']!r} is not an upgrade stage."
    floor = payload.get("firstTradedStage")
    if floor and STAGES.index(asked) < STAGES.index(floor):
        return f"{payload['item']} was never traded before {floor}, so nothing at {asked}{shown}."
    row = next((r for r in payload["stages"] if r["stage"] == asked), None)
    if row is None or row["median"] is None:
        return f"{payload['item']} has no price at {asked}{shown}."
    return (
        f"{payload['item']} at {asked}{shown}: {_figure(row)} median {payload['currency']}, "
        "the market price at that world stage, not your cost to craft with that module."
    )


def price_by_stage_markdown(payload: dict[str, Any]) -> str:
    """The readable block for `price_by_stage`: one line per stage, nothing else priced."""
    if payload.get("resolved") is None or not payload.get("traded", True):
        return str(payload["reply"])
    head = f"**{payload['item']}**, median trade price per upgrade stage"
    lines = [f"{head} in {payload['currency']}"]
    if payload.get("note"):
        lines.append(payload["note"])
    if (lead := _stage_lead(payload)) is not None:
        lines.append(lead)
    for row in payload["stages"]:
        lines.append(f"- {row['stage']}: {_figure(row)}")
    floor = payload.get("firstTradedStage")
    if floor and floor != STAGES[0]:
        lines.append(f"Never traded before {floor}, so no price below it.")
    return "\n".join(lines)
