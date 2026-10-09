# Build for a splat

`/build-for-splats` is a player guide to building in Eco so the build captures well as a
Gaussian splat, written for cycle 15 builders (COI-2618). The page is
`frontend/src/pages/BuildForSplats.tsx`.

## Where the words come from

The words are dev-advocate's, the facts game-dev's, both on COI-2618. Kai's three points
are the spine, quoted. The page holds the words as constants at the top of the file, so a
sentence changes in one place. The director's merge criteria bind them:

* **"45 degrees" is Kai's rule of thumb.** No capture record measures it, so the page says
  so, and the heading drops the number.
* **Point 3 is Kai's observation.** BioDrive is the example and its cameras sat closer than
  the castle's, so the page never says realism alone made it sharper.

## Decisions worth knowing

* **Not under `/cycle-14/`.** `/cycle-14` lists every route under that prefix as a scene
  card, so a guide there would show up as a scene with no 3D view.
* **`crawl: index`,** on Kai's say (2026-10-09), like `/cycle-14` and the splat pages.
* **Each point's splat is named in the file,** its still comes from the route's `art`, and
  its link goes to the route. A point whose splat is not in the table shows no picture and
  the words stand alone.
* **The caption is text in the figure and the still has an empty `alt`,** so a screen reader
  reads the sentence once. The caption is also the sentence dev-advocate wrote as alt text.
* **The cycle 14 list reads frames and points from each route's description,** so the counts
  stay in `data/spa_routes.json`. A description in another shape shows whole.
* **The underground base is text only.** It has no capture, so nothing links to it.
* **`/cycle-14` links here** in one line under its heading.

## Checking it

`just frontend-kit-check --only /build-for-splats,/cycle-14` runs axe, the 16px floor,
overflow and weight at 1000px and 320px. It needs a built app, since the dev server is
unminified and reads heavy.
