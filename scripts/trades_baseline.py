#!/usr/bin/env python3
"""Build `data/eco_trades_baseline.json.gz` from the whole #eco-trades Discord history.

A **maintenance** script, like `autogen_refresh.py`: the app reads the vendored
file and never fetches Discord itself. Two steps, so a rebuild does not refetch:

    just trades-baseline fetch  /tmp/eco-trades.jsonl   # page the channel, read-only
    just trades-baseline build  /tmp/eco-trades.jsonl   # aggregate into data/

`fetch` pages the channel backwards 100 messages at a time through the tailnet
Discord MCP (`mcporter call tailnet_coilyco_discord.list_channel-message`), so it
needs a host with that MCP configured. The raw dump carries player names and is
not committed. `build` needs only the dump.

Every DiscordLink trade embed carries "Sold" or "Bought" lines reading
"N X Item * unit = total", and a "Result" field naming the currency. One line is
one observation. Prices are set in player-made currencies that differ by cycle,
so every figure is kept per currency as well as pooled. See
data/eco_trades_baseline.SOURCE.txt for what the fields mean.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

CHANNEL_ID = "1300205194386079866"
REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO / "data" / "eco_trades_baseline.json.gz"
AUTOGEN = REPO / "data" / "eco_autogen_data.json.gz"

LINE = re.compile(
    r"^\s*(-?[\d,]*\.?\d+)\s+X\s+(.+?)\s+\*\s+(-?[\d,]*\.?\d+)\s+=\s+(-?[\d,]*\.?\d+)\s*$"
)
RESULT = re.compile(r"\*(?P<who>[^*]+)\*\s+(?:paid|gained)\s+-?[\d,]*\.?\d+\s+\*(?P<cur>[^*]+)\*")


def _page(before: str | None) -> list[dict]:
    args: dict = {"channel_id": CHANNEL_ID, "limit": 100}
    if before:
        args["before"] = before
    for attempt in range(6):
        cp = subprocess.run(
            [
                "mcporter",
                "call",
                "tailnet_coilyco_discord.list_channel-message",
                "--output",
                "json",
                "--args",
                json.dumps(args),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        try:
            body = json.loads(cp.stdout)
        except ValueError:
            body = None
        # An isError envelope is a dict with no "result" list, and iterating it once
        # wrote its keys into the dump as if they were messages.
        if isinstance(body, dict) and isinstance(body.get("result"), list):
            return body["result"]
        if isinstance(body, list):
            return body
        detail = (
            body["content"][0]["text"][:200]
            if isinstance(body, dict) and body.get("isError")
            else cp.stdout[:200]
        )
        print(f"retry {attempt} before={before}: {detail}", file=sys.stderr, flush=True)
        time.sleep(5 * (attempt + 1))
    raise SystemExit(f"gave up at before={before}")


def fetch(out: Path) -> None:
    before, total, pages = None, 0, 0
    with out.open("w") as fh:
        while msgs := _page(before):
            for m in msgs:
                fh.write(json.dumps(m) + "\n")
            total += len(msgs)
            pages += 1
            before = min(msgs, key=lambda m: int(m["id"]))["id"]
            print(
                f"page {pages} total {total} oldest {msgs[-1]['timestamp']}",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(1)
    print(json.dumps({"messages": total, "pages": pages}))


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _quantile(xs: list[float], q: float) -> float:
    if len(xs) == 1:
        return xs[0]
    return statistics.quantiles(xs, n=100, method="inclusive")[int(q * 100) - 1]


def _stats(obs: list[dict]) -> dict:
    units = sorted(o["unit"] for o in obs)
    qty = sum(o["qty"] for o in obs)

    def r(x: float) -> float:
        return round(x, 4)

    return {
        "observations": len(obs),
        "quantity": r(qty),
        "median": r(statistics.median(units)),
        "mean": r(statistics.fmean(units)),
        "weightedMean": r(sum(o["qty"] * o["unit"] for o in obs) / qty) if qty else None,
        "p25": r(_quantile(units, 0.25)),
        "p75": r(_quantile(units, 0.75)),
        "min": r(units[0]),
        "max": r(units[-1]),
        "firstSeen": min(o["ts"] for o in obs)[:10],
        "lastSeen": max(o["ts"] for o in obs)[:10],
    }


def _item_ids(autogen: Path) -> dict[str, str]:
    data = json.load(gzip.open(autogen))
    ids: dict[str, str] = {}
    for rec in data.get("recipes", []):
        for p in [rec.get("product"), *rec.get("byproducts", []), *rec.get("ingredients", [])]:
            if p and not p.get("isTag") and p.get("displayName"):
                ids.setdefault(p["displayName"].lower(), p["item"])
    return ids


def build(raw: Path, autogen: Path = AUTOGEN) -> tuple[dict, list[str]]:
    ids = _item_ids(autogen)
    seen: set[str] = set()
    messages = skipped = mismatch = 0
    bad: list[str] = []
    obs: list[dict] = []
    for line in raw.open():
        m = json.loads(line)
        if m["id"] in seen:
            continue
        seen.add(m["id"])
        messages += 1
        embeds = m.get("embeds") or []
        if not embeds:
            skipped += 1
            continue
        for e in embeds:
            fields = e.get("fields") or []
            cur = "unknown"
            for f in fields:
                if f["name"] == "Result":
                    rm = RESULT.search(f["value"])
                    if rm:
                        cur = rm["cur"].strip()
                    elif "No currency was exchanged" in f["value"]:
                        cur = "Barter"
            for f in fields:
                # "Bought (1)" and "Bought (2)" appear when one embed carries two baskets.
                side = f["name"].split(" ")[0]
                if side not in ("Sold", "Bought"):
                    continue
                for ln in f["value"].split("\n"):
                    if not ln.strip() or ln.strip().startswith("Total"):
                        continue
                    lm = LINE.match(ln)
                    if not lm:
                        bad.append(ln[:120])
                        continue
                    qty, item, unit, total = _num(lm[1]), lm[2].strip(), _num(lm[3]), _num(lm[4])
                    # The displayed unit is rounded to 2 places, so allow that rounding
                    # times the quantity.
                    if abs(qty * unit - total) > 0.005 * abs(qty) + 0.011:
                        mismatch += 1
                    obs.append(
                        {
                            "item": item,
                            "qty": qty,
                            "unit": unit,
                            "cur": cur,
                            "side": side.lower(),
                            "ts": m["timestamp"],
                        }
                    )
    by_item: dict[str, list[dict]] = defaultdict(list)
    currencies: dict[str, int] = defaultdict(int)
    for o in obs:
        by_item[o["item"]].append(o)
        currencies[o["cur"]] += 1
    items = {}
    for name in sorted(by_item, key=str.lower):
        group = by_item[name]
        by_cur: dict[str, list[dict]] = defaultdict(list)
        for o in group:
            by_cur[o["cur"]].append(o)
        items[name] = {
            "itemId": ids.get(name.lower()),
            "pooled": _stats(group),
            "sides": {s: sum(1 for o in group if o["side"] == s) for s in ("bought", "sold")},
            "byCurrency": {
                c: _stats(v) for c, v in sorted(by_cur.items(), key=lambda kv: -len(kv[1]))
            },
        }
    stamps = [o["ts"] for o in obs]
    out = {
        "_comment": (
            "Per-item price baselines from the full #eco-trades history. Generated, "
            "not authored: see eco_trades_baseline.SOURCE.txt. `pooled` mixes player "
            "currencies and is a rough level only, `byCurrency` is like-for-like."
        ),
        "source": {
            "channel": "#eco-trades (Sirens Discord)",
            "messages": messages,
            "messagesWithoutTradeEmbed": skipped,
            "observations": len(obs),
            "unparsedLines": len(bad),
            "totalMismatches": mismatch,
            "firstTrade": min(stamps)[:10] if stamps else None,
            "lastTrade": max(stamps)[:10] if stamps else None,
            "items": len(items),
            "itemsWithoutId": sum(1 for v in items.values() if not v["itemId"]),
            "currencies": dict(sorted(currencies.items(), key=lambda kv: -kv[1])),
        },
        "items": items,
    }
    return out, bad


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Build data/eco_trades_baseline.json.gz from #eco-trades."
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="page the channel into a raw JSONL dump")
    f.add_argument("raw", type=Path)
    b = sub.add_parser("build", help="aggregate a raw dump into the baseline file")
    b.add_argument("raw", type=Path)
    b.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    if args.cmd == "fetch":
        fetch(args.raw)
        return
    out, bad = build(args.raw)
    body = json.dumps(out, separators=(",", ":"), ensure_ascii=False).encode()
    # Gzipped like eco_autogen_data.json.gz: 3.9 MB of generated JSON nobody reads by hand.
    if args.output.suffix == ".gz":
        body = gzip.compress(body, compresslevel=9, mtime=0)
    args.output.write_bytes(body)
    print(json.dumps(out["source"], indent=1))
    if bad:
        print(f"{len(bad)} unparsed lines, first: {bad[:3]}", file=sys.stderr)


if __name__ == "__main__":
    main()
