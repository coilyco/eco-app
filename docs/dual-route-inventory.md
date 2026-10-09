# Dual REST and MCP route inventory

Classifies the fused service's routes for the shared [`DualRouteRegistry`](../src/eco_mcp_app/dual_routes.py) (eco-app#205). Sources: `server.py`, `http_app.py`, `eco_spec_tracker/`, `eco_replay/`.

- **Dual-register** - REST and MCP share one contract.
- **After prerequisite** - needs typed models, bounds, or a read-only split first.
- **Single-surface** - REST-only, MCP-only, or browser-only on purpose.
- **Excluded** - plumbing or privileged semantics the registry must not widen.

## State

- **Registered** - 20 tools on both surfaces at `GET /preview/<tool>.json`. Wave 1 keeps short paths: `preview.json` (`get_server_status`), `world`, `stores`, `market`, `logistics`, `civics`.
- **MCP-only** (COI-2095) - `get_progression` and `get_currency` have `rest_path=None`, and `get_social` had no route. Their `/preview/*.json` planes are deleted. `get_social` names need `ECO_SOCIAL_ALLOW_NAMES` plus `reveal_names`.
- **After prerequisite** - `items`, `food`, `item`, `price-history`.
- **Single-surface REST** - `preview-map.json`, `recipes.json`, `/api/service`, Jobs FastAPI docs.
- **Excluded** - the `/preview/{tool}` adapter, `/healthz`, `/page-auth`, mounts, SPA fallbacks.

## Reply templates

A tool's `tools/list` entry carries one-line reply templates so a router answers without an LLM. Renderer: `src/eco_mcp_app/reply_templates.py`. Sirens Echo renders it in Go.

### Contract

- **Key** - `_meta["coilyco/templates"]` is an ordered list of `{"when_args": [...], "text": "..."}`. The first eligible entry wins, else the LLM path.
- **`when_args`** - every named argument was passed and is non-blank. An empty list always applies.
- **`when_unmatched`** - replaces `when_args`: a literal reply when every named argument's vocabulary matched nothing. No placeholders.
- **`text`** - `{{path}}` placeholders only: dot-separated keys into `structuredContent`, a decimal segment indexes a list, a leading `args` reads call arguments.
- **Values** - floats print at most 2 decimals. Missing, null, blank, boolean, object, or list values make it ineligible.
- **Cap** - over 280 code points is ineligible.

Shipped: `find_trade`, `get_market`, `price_recipe`, `get_currency`, `price_by_stage`, `get_server_status`. `get_recipes` has none (a list needs a loop). `tests/mcp/test_reply_templates.py` renders each.

### Argument vocabularies

`_meta["coilyco/args"]` maps an argument to `{"vocabulary": <resource uri>, "field": "id"|"name"}`. The caller matches words there and passes `field`. Source: `src/eco_mcp_app/vocab.py`.

- **Resources** - `eco://vocab/{items,currencies,priced-items,stages}`, each `{"entries": [{"id", "name", "aliases"}]}`, unannotated.
- **Fields** - `name` for `find_trade.item`, `price_by_stage.item`, `get_currency.currency`. `id` for `get_market.item` (spaces folded from the id only) and `price_recipe.product`.
- **Ignore** - an `ignore` list names domain words that are never values (shop words).
