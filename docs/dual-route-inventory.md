# Dual REST and MCP route inventory

Classifies the fused service's routes for the shared [`DualRouteRegistry`](../src/eco_mcp_app/dual_routes.py) (eco-app#205).

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

Shipped: `find_trade`, `get_market`, `price_recipe`, `get_currency`, `price_by_stage`, `get_server_status`. `find_trade` with `store` and `item` renders `{{reply}}`, built from that one store's shelf and null unless one store matched. With `store` alone it renders each matched store's true listing count, how many are in stock, then as many in-stock lines as fit 280 characters, most stocked first, and "and N more" for the rest (up to three stores share the budget), and is null when that exceeds the cap, so the model answers. A name no store contains but one is a word or a letter off resolves to that store (`storeResolvedFrom`), or lists the closest as `storeSuggestions`. A named store's shelf lists every line, price 0 and negative included, each with its real price and a `priceNote` worded from the exporter's DTO (a negative price is reported as given, since the DTO does not define it), and the price boards leave those lines out. `unlistedLines` counts only a line with no item name, and `truncated` says the shelf was cut. A `find_trade` without `store` (unfiltered, or by `item` or `currency`) keeps 5 rows per board and 3 offers or buyers per row, with a warning counting the rest, because the overview reached the model at 38 to 70 KB and a broad item filter at 8 to 38 KB (COI-3373). A `limit` above 10 is cut to 10 on that call, and 0 still means all. `get_stores` with `store` resolves the name through the live store list (trade history knows only the owner and block type) and renders `{{reply}}`, one line naming the owner, null unless exactly one store was named outright (COI-3407). `get_recipes` has none (a list needs a loop).

### Argument vocabularies

`_meta["coilyco/args"]` maps an argument to `{"vocabulary": <resource uri>, "field": "id"|"name"}`. The caller matches words there and passes `field`. Source: `src/eco_mcp_app/vocab.py`.

- **Resources** - `eco://vocab/{items,currencies,priced-items,stages,stores}`, each `{"entries": [{"id", "name", "aliases"}]}`, unannotated.
- **Fields** - `name` for `find_trade.store` (a name once however many stores carry it, `Owner's Name` as its own longer entry, no one-word names), `find_trade.item`, `price_by_stage.item`, `get_currency.currency`. `id` for `get_market.item` (spaces folded from the id only) and `price_recipe.product`.
- **Ignore** - an `ignore` list names domain words that are never values (shop words).
- **Evidence** - `"evidence": true` on an argument tells the caller's tool picker that a vocabulary hit in the message is a signal for this tool. Only `find_trade.store` sets it: a store is often named without the word "store", and a picker reads that as a general question (COI-3279).
