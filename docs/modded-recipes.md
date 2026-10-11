# Modded recipe exports How eco-app serves a modded server's own recipe graph
instead of the bundled vanilla seed, and how it refuses to pretend it has one.
Built for #179. Recipe and cost surfaces start from a vendored vanilla Eco
Gnome export. On a modded server the recipe graph, output quantities, and
upgrades all differ, so pricing evidence derived from vanilla is misleading in
the worst way, because it looks authoritative.

## The three tiers

`load_recipe_index()` tries them in order. Only tier 1 is server-specific.

## The failure mode is the point

Any refusal falls back **and says why**, on the payload's `warnings`. Refusals
are an unreadable file, invalid JSON, a payload that is not DataExporter-shaped
(no `Recipes[]`), an unparseable `ExportedAt`, a parse error, fewer than half the
recipes parsing (a changed layout), or an export past the freshness bound. A
stale modded graph is refused rather than trusted, because one that no longer
matches the server is wrong *and* claims to be server-specific. A `Version`
outside `MODDED_EXPORT_CHECKED_VERSIONS` (3 today) is served with a warning. An
export with no `ExportedAt` is accepted, since the operator pointed at it
deliberately, but the source string says so. When no export is configured
nothing warns: vanilla is correct for a vanilla deploy, and crying wolf on
every default install trains readers to ignore the warning that matters.

## Operator setup

Produce a DataExporter export on the Eco server (eco-ops#71 owns this), mount
it read-only into the pod, set `ECO_MODDED_RECIPE_EXPORT` to its path, and
optionally `ECO_MODDED_RECIPE_MAX_AGE_DAYS` (default 14). Verify through
`/preview/recipes.json`, which should show `"serverSpecific": true`. Refresh on
the same cadence as the bound, or raise the bound deliberately.

## Boundaries

eco-app opens one file the operator names and writes nothing: no game state, no
player or store state, no pricing configuration. The export goes through the
same `build_recipe_index` the vanilla seed does, so there is no parser fork.
Producing and mounting the export is an operator action, and eco-app's half is
ingestion, provenance, and the refusals.
