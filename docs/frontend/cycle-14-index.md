# The cycle 14 index

`/cycle-14` lists every cycle 14 splat page as a card: the link-card still, the
title and the description (COI-2401). The page is
`frontend/src/pages/Cycle14Index.tsx`.

## There is no list to keep

The cards come from `data/spa_routes.json`: every route whose path starts with
`/cycle-14/`, in table order, with its `title`, `description` and `art`. A new splat
page appears here when its route lands, with no edit to the index. The stills are
the `frontend/src/assets/og-art/<art>` files the link cards already use.

Each splat page links back with "All cycle 14 scenes".

## Decisions worth knowing

* **The words are placeholders.** The heading, the one line under it and the empty
  state are not Kai's or dev-advocate's yet. The card text is each route's own
  description.
* **`crawl: noindex`,** unlike the pages it lists, until Kai says otherwise. The
  pages are indexed on her say, and an index page was not part of that.
* **The empty state** reads "No 3D scenes are published yet." It cannot show while the
  manifest holds a splat route, and the test renders it with an empty list.
* **No budget entry** in `frontend/scripts/kit-check.mjs`: three stills and the shell
  stay under the 900K default. Run `just frontend-kit-check --only /cycle-14` once it
  is live to confirm.
