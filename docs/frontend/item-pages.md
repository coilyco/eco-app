# One page per item

Each item has one page, `/item?id=<id>`, holding its market, every recipe that makes it, and what it goes into (teable:coilyco/eco-app#8383).

## The id

The key is the item id with its `Item` suffix dropped (`AcornPowderItem` becomes `AcornPowder`). `lib/itemPage.ts` owns the rule (`itemPageId`, `itemPageHref`) and `components/ItemLink.tsx` builds every item link through it.

The rule also folds the recipe export's split spellings onto one page. Byproducts arrive as `GoldScrap` and ingredients as `GoldScrapItem`, so `/item?id=GoldScrap` covers both. Six items split this way (gold, copper, iron and wood scrap, tailings, wet tailings).

The market pivot is keyed by the game's class name. The page takes it from the item's own recipes when there are any, which finds skill books (no suffix), and otherwise uses the suffixed spelling (`pivotItemFor`).

## Loading

The whole recipe graph is 1.2 MB and sent uncompressed, too heavy for the page every price links to. The page loads only the item's own recipes up front, through the service's `?product=` filter (about 2 KB). "What it's used in" and "Also comes out of" need the whole graph, so they load when the reader taps "Show what uses ...".

## Recipes as cards

A recipe is a card (`components/RecipeCard.tsx`) on its primary product's page, anchored `#recipe-<name>`. The main recipe comes first: the one named like the page, then any the export marks as its family's default. A recipe with byproducts lives under its first product, and each byproduct's page lists it under "Also comes out of", linking to that card.

## Old links

* `/item?item=<full id>` redirects to `/item?id=<id>` and keeps its filters.
* `/recipe?id=<name>` loads the graph and redirects to `/item?id=<product>#recipe-<name>`, scrolling to and focusing the card. An unknown name shows a not-found note, never a 404.

`/recipe` stays in `data/spa_routes.json` so the server keeps serving the shell for it. Both routes are `noindex`, so the redirects run in the client.

Stations display without their class suffix (`MillObject` reads "Mill") through `prettifyEcoName` in `lib/format.ts`. The frontend no longer reads the service's `stationDisplayName`.

`src/test/itemPageGraph.test.ts` runs the resolver over the whole shipped graph (`data/eco_autogen_data.json.gz`): every recipe lands on a page showing its card, every byproduct's page links back, every item id maps to a covering page.
