"""Tech progression: how far up the upgrade ladder the server has crafted, and
which specialties its citizens have taken (COI-2090).

It replaced ``get_milestones``, whose per-goal culture bars read a different
current value on each row. Kai asked for "tech progression based on the highest
level upgrade created and specialization taken". It rides on ``get_progression``
as the ``techProgression`` key, so the tool count stays flat.

Two sources, each reported null when it could not be read and never as zero:

* **Upgrades** - ``ItemCraftedAction`` rows, through the crafting atlas's
  ``by_crafted`` board (cached, shared with ``get_crafting_atlas``). That board
  is a confirmed floor, not a total: the server folds old crafts into hourly
  per-citizen rollups that keep only one item label, so an upgrade crafted only
  inside a rollup is invisible here (eco-app#131). A ``highestStage`` can
  therefore read low, never high.
* **Specialties** - ``GainSpecialty`` / ``LoseSpecialty`` rows, folded over every
  event by ``progression.build_history``.

The stage ladder is ``norms.STAGES`` (Basic 1 to Modern 4, Scholars folded in).
"""

from __future__ import annotations

import re
from typing import Any

from .crafting import CraftingAtlas
from .norms import STAGES, upgrade_stage
from .progression import ProgressionHistory

CRAFT_ACTION = "ItemCraftedAction"
SPECIALTY_ACTION = "GainSpecialty"

# `BasicUpgradeLvl1Item`, `ScholarsAdvancedUpgradeLvl3Item`. Level 5 specialist
# modules are not on the ladder and are left out on purpose.
_UPGRADE_ITEM = re.compile(r"^(Scholars)?(Basic|Advanced|Modern)UpgradeLvl([1-4])Item$")

UPGRADES_NOTE = (
    "Crafts are confirmed iterations, a floor. Older crafts are merged into hourly "
    "per-citizen rollups that keep one item label, so a tier crafted only inside a "
    "rollup does not show. highestStage can read low and never high."
)


def upgrades_crafted(by_crafted: list[tuple[str, float]]) -> list[dict[str, Any]]:
    """Ladder upgrade items on the crafted board, highest stage first."""
    rows: list[dict[str, Any]] = []
    for item, crafts in by_crafted:
        m = _UPGRADE_ITEM.match(item)
        if not m:
            continue
        scholars, tier, level = bool(m[1]), m[2], int(m[3])
        name = f"{'Scholars ' if scholars else ''}{tier} Upgrade {level}"
        rows.append(
            {
                "item": item,
                "name": name,
                "tier": tier,
                "level": level,
                "stage": upgrade_stage(name),
                "scholars": scholars,
                "crafts": int(crafts),
            }
        )
    rows.sort(key=lambda r: (-r["stage"], r["scholars"], r["item"]))
    return rows


def _upgrades_section(
    atlas: CraftingAtlas | None, atlas_error: str | None, warnings: list[str]
) -> dict[str, Any]:
    section: dict[str, Any] = {
        "highestStage": None,
        "highest": None,
        "crafted": None,
        "upgradesNote": UPGRADES_NOTE,
    }
    if atlas is None or CRAFT_ACTION not in atlas.per_action_counts:
        why = atlas_error or "; ".join(
            w for w in (atlas.warnings if atlas else []) if w.startswith(f"{CRAFT_ACTION}:")
        )
        warnings.append(
            f"tech progression: highest upgrade crafted is unmeasured, the {CRAFT_ACTION} "
            f"exporter could not be read{f' ({why})' if why else ''}"
        )
        return section
    rows = upgrades_crafted(atlas.by_crafted)
    section["crafted"] = rows
    # 0 is "none crafted yet", the `none` stage in norms.STAGES. Null stays "unmeasured".
    section["highestStage"] = rows[0]["stage"] if rows else 0
    if rows:
        top = rows[0]["stage"]
        at_top = [r for r in rows if r["stage"] == top]
        section["highest"] = {
            "stageName": STAGES[top],
            "tier": rows[0]["tier"],
            "level": rows[0]["level"],
            "items": [r["item"] for r in at_top],
            "crafts": sum(r["crafts"] for r in at_top),
        }
    return section


def _specialties_section(history: ProgressionHistory, warnings: list[str]) -> dict[str, Any]:
    if SPECIALTY_ACTION not in history.per_action_counts:
        warnings.append(
            f"tech progression: specialties taken are unmeasured, the {SPECIALTY_ACTION} "
            "exporter could not be read"
        )
        return {"takenCount": None, "taken": None}
    if history.unnamed_specialty_gains:
        warnings.append(
            f"tech progression: {history.unnamed_specialty_gains} {SPECIALTY_ACTION} rows "
            "carry no recognised skill column, so they are counted in no specialty"
        )
    return {
        "takenCount": len(history.specialties_taken),
        "taken": list(history.specialties_taken),
    }


def build_tech_progression(
    history: ProgressionHistory,
    atlas: CraftingAtlas | None,
    atlas_error: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """The ``techProgression`` section plus the warnings naming what it lacks."""
    warnings: list[str] = []
    section = {
        **_upgrades_section(atlas, atlas_error, warnings),
        "specialties": _specialties_section(history, warnings),
    }
    return section, warnings


def tech_progression_markdown(section: dict[str, Any]) -> str:
    """Two bullets for MCP hosts reading the text block."""
    highest = section["highest"]
    if section["highestStage"] is None:
        up = "highest upgrade crafted: not measured"
    elif highest is None:
        up = "highest upgrade crafted: none yet"
    else:
        up = f"highest upgrade crafted: **{highest['stageName']}** (at least)"
    spec = section["specialties"]
    if spec["takenCount"] is None:
        sp = "specialties taken: not measured"
    else:
        names = ", ".join(r["pretty"] for r in spec["taken"][:5])
        sp = f"specialties taken: **{spec['takenCount']}**" + (f" ({names}, ...)" if names else "")
    return f"- Tech progression, {up}\n- Tech progression, {sp}"
