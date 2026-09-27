"""Cycle- and upgrade-stage-relative price norms from the #eco-trades history.

Builds `data/eco_trades_norms.json.gz` from the same raw dump as the baseline
(`just trades-baseline norms <dump>`). Kai's spec, method and caveats:
docs/price-history.md. teable:coilyco-gaming/eco-app#6215.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta

from eco_mcp_app.norms import STAGES, upgrade_stage

# Every cycle's players mint their own currencies, so these two never mark one.
NOT_A_CURRENCY = frozenset({"Barter", "unknown"})
WINDOW_ACTIVE_DAYS = 7
TURNOVER_MIN = 0.5
MIN_GAP_DAYS = 3
MIN_WINDOW_LINES = 50
LONG_GAP_DAYS = 14
MIN_CYCLE_DAYS = 21
BASKET_MIN_OBS = 5
BASKET_CYCLE_SHARE = 0.75


def _ts(o: dict) -> datetime:
    return datetime.fromisoformat(o["ts"]).astimezone(UTC)


def _turnover(before: Counter[str], after: Counter[str]) -> float | None:
    """The lesser of the share of `before` in currencies absent from `after`, and the
    share of `after` in currencies absent from `before`. Names recur across cycles
    (personal credits, reused mints), so it compares neighbours, not global spans."""
    nb, na = sum(before.values()), sum(after.values())
    if nb < MIN_WINDOW_LINES or na < MIN_WINDOW_LINES:
        return None
    gone = sum(n for c, n in before.items() if c not in after) / nb
    new = sum(n for c, n in after.items() if c not in before) / na
    return min(gone, new)


def segment(obs: list[dict], latest_cycle: int) -> list[dict]:
    """Split the history into cycles at idle gaps and currency turnover. Method and
    thresholds: docs/price-history.md."""
    by_day: dict[date, Counter[str]] = defaultdict(Counter)
    active_set: set[date] = set()
    for o in obs:
        d = _ts(o).date()
        active_set.add(d)
        if o["cur"] not in NOT_A_CURRENCY:
            by_day[d][o["cur"]] += 1
    if not active_set:
        return []
    active = sorted(active_set)

    def window(days: list[date]) -> Counter[str]:
        total: Counter[str] = Counter()
        for d in days:
            total.update(by_day.get(d, Counter()))
        return total

    def unbroken(days: list[date]) -> list[date]:
        # A window stops at an idle gap, so it never compares across a reset.
        for k in range(1, len(days)):
            if (days[k] - days[k - 1]).days - 1 >= MIN_GAP_DAYS:
                return days[:k]
        return days

    w = WINDOW_ACTIVE_DAYS
    # (rank, start day, method, turnover, idle days). Lower rank is chosen first.
    candidates = []
    for j in range(1, len(active)):
        before = unbroken(active[max(0, j - w) : j][::-1])[::-1]
        score = _turnover(window(before), window(unbroken(active[j : j + w])))
        idle = (active[j] - active[j - 1]).days - 1
        if idle >= LONG_GAP_DAYS:
            candidates.append((0, active[j], "activity-gap", score, idle))
        elif idle >= MIN_GAP_DAYS and score is not None and score >= TURNOVER_MIN:
            candidates.append((1, active[j], "activity-gap+currency-turnover", score, idle))
        elif score is not None and score >= TURNOVER_MIN:
            candidates.append((2, active[j], "currency-turnover", score, idle))
    chosen: list[tuple] = []
    for cand in sorted(candidates, key=lambda c: (c[0], -(c[3] or 0))):
        if all(abs((cand[1] - c[1]).days) >= MIN_CYCLE_DAYS for c in chosen):
            chosen.append(cand)
    edges = [(0, active[0], "first-trade", None, None), *sorted(chosen, key=lambda c: c[1])]
    cycles = []
    for i, (_, b, method, score, idle) in enumerate(edges):
        stop = edges[i + 1][1] if i + 1 < len(edges) else active[-1] + timedelta(days=1)
        cycles.append(
            {
                "cycle": latest_cycle - (len(edges) - 1 - i),
                "start": b.isoformat(),
                "end": max(d for d in active if b <= d < stop).isoformat(),
                "boundary": method,
                "turnover": round(score, 3) if score is not None else None,
                "idleDaysBefore": idle,
            }
        )
    return cycles


def assign(obs: list[dict], cycles: list[dict]) -> None:
    """Tag every observation with its cycle and the upgrade stage reached so far."""
    starts = [(date.fromisoformat(c["start"]), c) for c in cycles]
    reached: dict[int, int] = defaultdict(int)
    onsets: dict[int, dict[int, datetime]] = defaultdict(dict)
    for o in sorted(obs, key=_ts):
        when = _ts(o)
        cycle = next(c for s, c in reversed(starts) if s <= when.date())
        n = cycle["cycle"]
        stage = upgrade_stage(o["item"])
        if stage > reached[n]:
            reached[n] = stage
            onsets[n].setdefault(stage, when)
        o["cycle"], o["stage"] = n, reached[n]
    for c in cycles:
        begin = datetime.combine(date.fromisoformat(c["start"]), datetime.min.time(), UTC)
        c["stageOnsetDay"] = {
            STAGES[s]: round((t - begin).total_seconds() / 86400, 1)
            for s, t in sorted(onsets[c["cycle"]].items())
        }


def _q(xs: list[float], q: float) -> float:
    if len(xs) == 1:
        return xs[0]
    return statistics.quantiles(xs, n=4, method="inclusive")[0 if q < 0.5 else 2]


def stats(units: list[float]) -> dict:
    xs = sorted(units)
    return {
        "n": len(xs),
        "median": round(statistics.median(xs), 4),
        "p25": round(_q(xs, 0.25), 4),
        "p75": round(_q(xs, 0.75), 4),
    }


def _geomean(xs: list[float]) -> float:
    return math.exp(statistics.fmean(math.log(x) for x in xs))


def basket_index(obs: list[dict], primary: dict[int, str]) -> tuple[dict[int, float], list[str]]:
    """Each cycle's price level in its primary currency, chained over a common basket
    so a missing item does not move it. 1.0 is the level of a typical cycle."""
    medians: dict[str, dict[int, float]] = defaultdict(dict)
    groups: dict[tuple[str, int], list[float]] = defaultdict(list)
    for o in obs:
        if primary.get(o["cycle"]) == o["cur"]:
            groups[(o["item"], o["cycle"])].append(o["unit"])
    for (item, cycle), units in groups.items():
        m = statistics.median(units)
        if len(units) >= BASKET_MIN_OBS and m > 0:
            medians[item][cycle] = m
    need = math.ceil(BASKET_CYCLE_SHARE * len(primary))
    basket = sorted(i for i, by_cycle in medians.items() if len(by_cycle) >= need)
    ratios: dict[int, list[float]] = defaultdict(list)
    for item in basket:
        level = _geomean(list(medians[item].values()))
        for cycle, m in medians[item].items():
            ratios[cycle].append(m / level)
    return {c: round(_geomean(r), 4) for c, r in ratios.items()}, basket


def norms(obs: list[dict], latest_cycle: int) -> dict:
    cycles = segment(obs, latest_cycle)
    assign(obs, cycles)
    per_cycle_cur: dict[int, Counter] = defaultdict(Counter)
    for o in obs:
        if o["cur"] not in NOT_A_CURRENCY:
            per_cycle_cur[o["cycle"]][o["cur"]] += 1
    primary = {c: cur.most_common(1)[0][0] for c, cur in per_cycle_cur.items()}
    index, basket = basket_index(obs, primary)
    for c in cycles:
        c["primaryCurrency"] = primary.get(c["cycle"])
        c["basketIndex"] = index.get(c["cycle"])
        c["observations"] = sum(per_cycle_cur[c["cycle"]].values())

    cyc: dict[tuple, list[float]] = defaultdict(list)
    stg: dict[tuple, list[float]] = defaultdict(list)
    for o in obs:
        cyc[(o["item"], o["cycle"], o["cur"])].append(o["unit"])
        stg[(o["item"], o["cycle"], o["stage"], o["cur"])].append(o["unit"])
    items: dict[str, dict] = defaultdict(lambda: {"cycles": {}, "crossCycle": {}})
    for (item, cycle, cur), units in cyc.items():
        slot = items[item]["cycles"].setdefault(str(cycle), {"byCurrency": {}, "stages": {}})
        slot["byCurrency"][cur] = stats(units)
    cross: dict[tuple[str, int], list[tuple[float, int]]] = defaultdict(list)
    for (item, cycle, stage, cur), units in stg.items():
        slot = items[item]["cycles"][str(cycle)]["stages"].setdefault(STAGES[stage], {})
        slot[cur] = stats(units)
        if primary.get(cycle) == cur and index.get(cycle):
            cross[(item, stage)].append((statistics.median(units) / index[cycle], len(units)))
    for (item, stage), points in cross.items():
        items[item]["crossCycle"][STAGES[stage]] = {
            "median": round(statistics.median(p for p, _ in points), 4),
            "cycles": len(points),
            "n": sum(n for _, n in points),
        }
    order = {name: i for i, name in enumerate(STAGES)}
    for entry in items.values():
        entry["crossCycle"] = dict(sorted(entry["crossCycle"].items(), key=lambda kv: order[kv[0]]))
        for slot in entry["cycles"].values():
            slot["stages"] = dict(sorted(slot["stages"].items(), key=lambda kv: order[kv[0]]))
    return {
        "cycles": cycles,
        "basket": basket,
        "items": {k: items[k] for k in sorted(items, key=str.lower)},
    }
