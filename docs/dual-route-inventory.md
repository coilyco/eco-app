# Dual REST and MCP route inventory Classifies the routes the fused service
owns, for adoption by the shared
[`DualRouteRegistry`](../src/eco_mcp_app/dual_routes.py). Tracking:
eco-app#205, with Wave 1 in #207 and Wave 2 in #209. Owning sources:
`server.py`, `admin/server.py`, `http_app.py`, `eco_spec_tracker/main.py`,
`eco_replay/main.py`, `frontend/src/App.tsx`. The in-game C# mod and external
Eco server endpoints are upstream dependencies, not routes this service owns.

**Classification.**
- **Dual-register** - REST and MCP perform the same operation with the same
  input, output, authorization, disclosure, side effects, and error semantics.
- **Dual-register after prerequisite** - shared use is real, but the contract
  first needs typed models, result bounds, transport-safe input projection, or
  a read-only split.
- **Single-surface** - intentionally REST-only, MCP-only, or browser-only.
- **Excluded** - transport plumbing, privileged infrastructure, or disclosure
  and mutation semantics the public registry must not widen.

## State

The public server defines 25 tools, two of them disabled (`get_economy`,
`fair_price`). **Twenty-four are registered** and serve both
surfaces, each at `GET /preview/<tool>.json` except the Wave 1 set, which keeps
its shorter paths: `preview.json` (`get_server_status`), and `world`, `stores`,
`progression`, `market`, `logistics`, `currency`, `civics`, and
`list_public_eco_servers`.

**One awaits a prerequisite.** `get_social`, because the REST path always
suppresses `reveal_names` while MCP may accept it behind `ECO_SOCIAL_ALLOW_NAMES`.

Also awaiting prerequisites: `items`, `food`, `item`, and `price-history`, each
needing a bounded typed operation. Single-surface REST keeps `preview-map.json`
(browser-only biome rasters), `user.json` (an identity-bearing dossier),
`recipes.json` (a 1,453-recipe browser data plane), `/api/service`, and the
generated FastAPI docs under the Jobs mount. Excluded: `/preview/{tool}` as a
compatibility adapter, `/healthz`, both `/page-auth` verbs, the `/mcp`,
`/admin`, `/assets`, and livereload mounts, and the SPA fallbacks.

## Reply templates

A tool can carry one-line reply templates in its `tools/list` entry, so a
router that has already picked the tool answers from the call result without
an LLM. Descriptions are untouched, because they are routing criteria
(teable:coilyco/sirens-echo#8229). The shipped set and the reference renderer
live in `src/eco_mcp_app/reply_templates.py`. Sirens Echo renders them in Go
against this same contract.

### Contract

- **Key** - `_meta["coilyco/templates"]` holds an ordered list of
  `{"when_args": [...], "text": "..."}`. The first eligible entry wins, and
  when none is eligible the caller falls back to its LLM path.
- **`when_args`** - every named call argument was passed and is not empty or
  whitespace. An empty list always applies.
- **`text`** - plain text with `{{path}}` placeholders and nothing else, no
  sections or loops. A path is dot-separated keys into the call result's
  `structuredContent`. A decimal segment indexes a list. A leading `args`
  segment reads the call arguments instead, so a payload key named `args`
  is unreachable.
- **Values** - strings render as-is, integers as digits, and floats with at
  most 2 decimals, trailing zeros and point trimmed (`-0` becomes `0`).
  Missing, null, empty or whitespace strings, booleans, objects and lists
  make the entry ineligible.
- **Cap** - a rendered reply over 280 characters (Unicode code points) is
  ineligible.

### Shipped

- `find_trade`, `get_market`, `price_recipe`, `get_currency`,
  `price_by_stage`, and `get_server_status`.
- `get_recipes` has no template, because an ingredient list needs a loop.
- `get_milestones` has no template, because the next unfinished
  achievement needs a filter, since rows sort completed ones first.

Every shipped template is rendered in `tests/mcp/test_reply_templates.py`
against a payload from the builder that produces the tool's result.

### Argument vocabularies

A template gated on `when_args` needs the argument filled without a model
(teable:coilyco/sirens-echo#8249). `_meta["coilyco/args"]` maps an argument
to `{"vocabulary": "<resource uri>", "field": "id" | "name"}`, and the caller
matches the member's words against that resource and passes the matched
entry's `field`. Source: `src/eco_mcp_app/vocab.py`.

- **Resources** - `eco://vocab/items` (every product and ingredient in the
  recipe graph `get_recipes` serves, tags excluded) and
  `eco://vocab/currencies` (the live named-currency roster, an empty list
  when admin data is unreachable), plus `eco://vocab/priced-items` for
  `price_by_stage` alone (items with trade history, `iron` as an alias of Iron
  Bar). All are `application/json`
  `{"entries": [{"id", "name", "aliases"}]}` with no annotations, so no
  client reads them as grounding.
- **Fields** - `find_trade.item`, `price_by_stage.item` and `get_currency.currency` take `name`.
  `get_market.item` and `price_recipe.product` take `id`: `get_market`
  folds spaces out of the id side only, so a multi-word display name never
  matches there.
- **Ignore** - an optional `ignore` list names forms that are domain words
  for that tool, never values. The trade tools ignore shop words, since
  Store is an item and "my store" means the shop.
