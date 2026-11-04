"""Matching a player's words to a store: containment first, a near name after.

Shared by the shelf board (`logistics.py`, COI-3279) and trade history
(`trade_history.py`, COI-3316), so a store asked for by a swapped word or a typo
resolves the same way in both. Pure: no HTTP, no clock.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

# A name nothing contains is tried again as a near one. A lone near store, or one
# clearly nearer than the rest, is answered as that store.
NEAR_MIN_EDIT = 0.85
NEAR_MARGIN = 0.2
NEAR_MAX_CHARS = 80
NEAR_SUGGESTIONS = 3


def norm_store(text: str) -> str:
    """Fold a store name or owner handle to a comparison key: lowercase letters
    and digits only, so `@Owner1` and `fern & co` both meet `owner1's Fern & Co`."""
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


def levenshtein(a: str, b: str) -> int:
    """Edit distance between two short strings."""
    if a == b:
        return 0
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        row = [i]
        for j, cb in enumerate(b, 1):
            row.append(min(previous[j] + 1, row[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = row
    return previous[-1]


def store_words(text: str) -> list[str]:
    """Words of a store name or handle; the `s` of a possessive is not one."""
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 1 or w.isdigit()]


def store_closeness(query: str, candidate: str) -> float:
    """How near a query is to one store name, 0 when it is not near at all.

    Near is a one-letter typo in the whole name, or two thirds of the shorter name's
    words shared (a word within one letter counting as shared). A single shared word
    is never near: that is a containment match's job, and it already failed.
    """
    q, c = store_words(query), store_words(candidate)
    fq, fc = "".join(q), "".join(c)
    if not fq or not fc or len(fq) > NEAR_MAX_CHARS or len(fc) > NEAR_MAX_CHARS:
        return 0.0
    edit = 1 - levenshtein(fq, fc) / max(len(fq), len(fc))
    spent, shared = list(c), 0
    for word in q:
        for i, other in enumerate(spent):
            one_off = len(word) >= 5 and len(other) >= 5 and levenshtein(word, other) <= 1
            if word == other or one_off:
                shared += 1
                del spent[i]
                break
    enough = shared >= 2 and shared * 3 >= 2 * min(len(q), len(c))
    overlap = shared / max(len(q), len(c)) if enough else 0.0
    return max(edit if edit >= NEAR_MIN_EDIT else 0.0, overlap)


@dataclass(frozen=True)
class RosterStore:
    """One store as the live exporter names it. `location` is `x,y,z` block
    coordinates, the form a trade row's ActionLocation takes, or empty when the
    exporter could not read it."""

    name: str
    owner: str
    location: str = ""

    @property
    def label(self) -> str:
        if self.owner and self.name:
            return f"{self.owner}'s {self.name}"
        return self.name or self.owner


@dataclass
class StoreMatch:
    """What a store query resolved to. `resolved_from` is the query when the match
    was a near one; `suggestions` holds equally near stores that were not guessed."""

    matched: list[RosterStore]
    resolved_from: str | None = None
    suggestions: list[tuple[float, RosterStore]] | None = None


def contains_match(store: RosterStore, want: str) -> bool:
    return any(want in norm_store(text) for text in (store.name, store.label, store.owner))


def resolve_stores(query: str, roster: Iterable[RosterStore]) -> StoreMatch:
    """Stores whose name, label or owner contain the query; failing that, the near one."""
    stores = list(roster)
    want = norm_store(query)
    if not want:
        return StoreMatch(matched=[])
    exact = [s for s in stores if contains_match(s, want)]
    if exact:
        return StoreMatch(matched=exact)
    scored = sorted(
        (
            (
                max(
                    store_closeness(query, s.name),
                    store_closeness(query, s.label),
                    store_closeness(query, s.owner),
                ),
                s,
            )
            for s in stores
        ),
        key=lambda pair: (-pair[0], pair[1].label.lower()),
    )
    near = [pair for pair in scored if pair[0] > 0][:NEAR_SUGGESTIONS]
    if near and (len(near) == 1 or near[0][0] - near[1][0] >= NEAR_MARGIN):
        return StoreMatch(matched=[near[0][1]], resolved_from=query.strip())
    return StoreMatch(matched=[], suggestions=near or None)
