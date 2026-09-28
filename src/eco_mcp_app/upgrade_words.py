"""Upgrade shorthand as members write it: `au3`, `SBU4`, `bu5`, `MU0`.

Kai's ask on teable:coilyco/eco-app#8425, spec by game-dev. B, A and M are the
Basic, Advanced and Modern tiers, an `s` prefix is the Scholars module, 1-4 is
that module, 0 is "none of this tier yet" and 5 is a specialist module converted
from a 4. The grammar and every mapping are in docs/price-history.md.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .norms import STAGES

TIERS = {"b": "Basic", "a": "Advanced", "m": "Modern"}
_TOKEN = re.compile(r"^(s)?([bam])u ?([0-5])$", re.I)
_IN_TEXT = re.compile(r"\b(s?[bam]u ?[0-5])\b", re.I)
_BEFORE = {"Basic": "none", "Advanced": "Basic 4", "Modern": "Advanced 4"}
# Words joining an item to its qualifier: "sell iron at au3".
_JOINERS = frozenset({"at", "on", "with", "for", "using", "in", "@"})


def parse(token: str) -> tuple[bool, str, int] | None:
    """(scholars, tier, n) for a whole shorthand token, else None."""
    m = _TOKEN.match(" ".join(token.split()))
    return (bool(m[1]), TIERS[m[2].lower()], int(m[3])) if m else None


def canonical(token: str) -> str:
    """`sau 3` -> `SAU3`, the spelling a reply quotes back."""
    return "".join(token.split()).upper()


def stage_for(token: str) -> str | None:
    """The ladder stage a shorthand names as a qualifier. A 0 is the stage before
    that tier, a 5 is that tier's 4, since a 5 is converted from a 4."""
    parsed = parse(token)
    if parsed is None:
        return None
    _, tier, n = parsed
    if n == 0:
        return _BEFORE[tier]
    return f"{tier} {min(n, 4)}"


def parse_stage(text: str | None) -> str | None:
    """A qualifier as given: shorthand, or a ladder name like `Advanced 2`."""
    words = " ".join((text or "").split())
    if not words:
        return None
    by_lower = {s.lower(): s for s in STAGES}
    return by_lower.get(words.lower()) or stage_for(words)


def generic_item(token: str) -> str | None:
    """`au3` -> `Advanced Upgrade 3`, `sbu4` -> `Scholars Basic Upgrade 4`."""
    parsed = parse(token)
    if parsed is None or not 1 <= parsed[2] <= 4:
        return None
    scholars, tier, n = parsed
    return f"{'Scholars ' if scholars else ''}{tier} Upgrade {n}"


def meaning(token: str) -> str | None:
    """Why a shorthand that names no item is not one: xu0, sxu0 and sxu5."""
    parsed = parse(token)
    if parsed is None:
        return None
    scholars, tier, n = parsed
    shown = canonical(token)
    if n == 0:
        return f"{shown} means no {tier} upgrade, it is not an item."
    if n == 5 and scholars:
        return f"{shown} is not an Eco module: specialist upgrades have no Scholars form."
    return None


# Each specialist module's tier, read by game-dev from the Eco 0.14 source at
# Kai's ask: a module's recipe consumes one tier-4 module, and that is its tier.
# The name is not (Advanced Masonry is MU5). Table and source: the decision
# comment on teable:coilyco/eco-app#8425. Refresh eco_autogen_data to 0.14 and
# autogen.py can read it, but the server's own mods stay a hand entry here.
SPECIALISTS: dict[str, tuple[str, ...]] = {
    "Basic": (
        "Basic Engineering", "Blacksmith", "Butchery", "Campfire Cooking", "Carpentry Basic",
        "Farming", "Fertilizers", "Gathering Basic", "Hunting", "Logging Basic",
        "Masonry Basic", "Milling", "Mining Basic", "Painting", "Paper Milling",
        "Shipwright Basic", "Smelting Basic", "Tailoring",
        "Animal Husbandry", "Beekeeping", "Fishing Reloaded",
    ),
    "Advanced": (
        "Advanced Baking", "Advanced Cooking", "Advanced Smelting", "Baking",
        "Blacksmith Advanced", "Carpentry Advanced", "Cooking", "Gathering Advanced",
        "Glassworking Advanced", "Logging Advanced", "Masonry Advanced", "Mechanics Advanced",
        "Mining Advanced", "Pottery", "Shipwright Advanced", "Smelting",
        "Mixology", "Advanced Mixology",
    ),
    "Modern": (
        "Advanced Masonry", "Composites", "Cutting Edge Cooking", "Electronics",
        "Glassworking Modern", "Industry", "Mechanics Modern", "Mining Modern", "Oil Drilling",
        "Tailoring Modern", "Biochemist",
    ),
}  # fmt: skip


def _specialty(module: str) -> str:
    """`Masonry Basic` -> `masonry`: the word a member puts before `bu5`."""
    return " ".join(w for w in module.split() if w not in TIERS.values()).lower()


def specialties(tier: str) -> list[str]:
    """The specialty words of one tier's modules, deduplicated, in table order."""
    return list(dict.fromkeys(_specialty(m) for m in SPECIALISTS[tier]))


def specialist_modules(tier: str, words: str) -> list[str]:
    """The module(s) `<words> <tier>5` names. A module named exactly the words wins
    (smelting au5 is Smelting, not Advanced Smelting). More than one is ambiguous."""
    key = " ".join(words.split()).lower()
    exact = [m for m in SPECIALISTS[tier] if m.lower() == key]
    loose = [m for m in SPECIALISTS[tier] if _specialty(m) == key]
    return [f"{m} Upgrade" for m in (exact or loose)]


def swapped_tier(name: str) -> str | None:
    """`Basic Gathering Upgrade` <-> `Gathering Basic Upgrade`: members say either
    order and the data holds one (Kai, via game-dev on #8425)."""
    words = name.split()
    if len(words) < 3 or words[-1].lower() not in ("upgrade", "upgrades"):
        return None
    rest = words[:-1]
    tiers = [i for i, w in enumerate(rest) if w.capitalize() in TIERS.values()]
    if len(tiers) != 1 or len(rest) < 2:
        return None
    if tiers[0] == 0:
        rest = rest[1:] + rest[:1]
    elif tiers[0] == len(rest) - 1:
        rest = rest[-1:] + rest[:-1]
    else:
        return None
    return " ".join([*rest, words[-1]])


def split_qualifier(text: str) -> tuple[str, str | None]:
    """("iron", "au3") from "iron at au3". A lone token, or text holding none,
    comes back whole with no qualifier."""
    words = " ".join(text.split())
    m = _IN_TEXT.search(words)
    if m is None:
        return words, None
    rest = (words[: m.start()] + " " + words[m.end() :]).split()
    while rest and rest[-1].lower() in _JOINERS:
        rest.pop()
    while rest and rest[0].lower() in _JOINERS:
        rest.pop(0)
    if not rest:
        return words, None
    return " ".join(rest), m[1]


def _forms(token: str) -> list[str]:
    """`au3` and `au 3`: members write both, and callers split on spaces."""
    return [token, f"{token[:-1]} {token[-1]}"]


def stage_vocabulary() -> list[dict[str, Any]]:
    """`eco://vocab/stages`: every ladder stage with the shorthand naming it."""
    aliases: dict[str, list[str]] = {s: [] for s in STAGES}
    for prefix in ("", "s"):
        for letter in TIERS:
            for n in range(6):
                token = f"{prefix}{letter}u{n}"
                if prefix and n == 5:
                    continue
                stage = stage_for(token)
                if stage:
                    aliases[stage] += _forms(token)
    return [{"id": s, "name": s, "aliases": aliases[s]} for s in STAGES]


def item_aliases(names: Iterable[str]) -> dict[str, list[str]]:
    """Shorthand aliases per item name: `au3` on Advanced Upgrade 3, and
    `mining bu5` on Mining Basic Upgrade."""
    present = set(names)
    out: dict[str, list[str]] = {}
    for prefix in ("", "s"):
        for letter in TIERS:
            for n in range(1, 5):
                token = f"{prefix}{letter}u{n}"
                name = generic_item(token)
                if name in present:
                    out.setdefault(name, []).extend(_forms(token))
    for name in present:
        # The other word order, unless it is an item in its own right (Masonry).
        swapped = swapped_tier(name)
        if swapped and swapped not in present and not name[-1].isdigit():
            out.setdefault(name, []).append(swapped)
    for letter, tier in TIERS.items():
        for words in {*specialties(tier), *(m.lower() for m in SPECIALISTS[tier])}:
            modules = specialist_modules(tier, words)
            if len(modules) == 1 and modules[0] in present:
                forms = _forms(f"{letter}u5")
                out.setdefault(modules[0], []).extend(f"{words} {f}" for f in forms)
                out[modules[0]].extend(f"{f} {words}" for f in forms)
    return out


def pseudo_entries() -> list[dict[str, Any]]:
    """Shorthand that names no single item (xu0, sxu0, sxu5, xu5), as entries a
    caller can pass back so the tool says what the shorthand means."""
    entries = []
    for prefix in ("", "s"):
        for letter in TIERS:
            for n in (0, 5):
                token = f"{prefix}{letter}u{n}"
                shown = token.upper()
                entries.append({"id": shown, "name": shown, "aliases": _forms(token)})
    return entries
