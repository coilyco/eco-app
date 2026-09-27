# Kit checks

The coilyco kit ships with CI that holds a page to its rules: axe, a 16px
type floor, the layering rule, transfer budgets. eco-app runs the same checks,
in two halves, because its CI job has no browser.

## In CI, on every push: `just frontend-test`

These run inside the existing `frontend` job, so a change that breaks them
fails the pull request.

* `frontend/src/test/a11y.test.tsx` - axe on every route in
  `data/spa_routes.json`, with the kit's exact tag set
  (`wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`, `best-practice`), with every
  data plane failing. The outage state is the one most likely to lose a
  heading or a label, so it is the one checked. jsdom has no layout, so
  `color-contrast` and `scrollable-region-focusable` are left to the browser
  half.
* `frontend/src/test/contrastMatrix.test.ts` - every text token against every
  surface token, resolved from the vendored kit with `eco-theme.css` on top:
  4.5:1 for text, 4.5:1 for labels on filled buttons, 3:1 for the focus ring.
  A primitive edit that breaks any pair fails here.
* `frontend/src/test/kitFloor.test.ts` - no font size in eco-app's CSS below
  16px, and nothing sizing the root. SVG chart labels are sized in viewBox
  units, so their CSS number says nothing about the screen, and the browser
  half measures them instead.

Each test asserts it found enough to check, so an empty read cannot pass. That
guard caught vitest stubbing CSS imports to `""`, which is why the CSS is read
from disk.

## In a browser, on demand: `just frontend-kit-check`

`frontend/scripts/kit-check.mjs` drives an installed Chrome
(`playwright-core`, no download) over every public route at the kit's two
viewports, 1000x660 and 320x900, and reports per route:

* axe with the full tag set, colour contrast included
* any text rendering below 16px, with SVG text scaled by its screen matrix
* layering: a bordered element sharing its parent's fill (the kit's rule 4)
* horizontal overflow
* transfer weight against a per-route budget, at 1000px only

```
just frontend-kit-check                                   # production
just frontend-kit-check --base http://localhost:5173      # a dev server
just frontend-kit-check --only /map,/trade
```

Budgets are eco-app's, not the kit's 150K. eco-app is a data app, and a route's
weight is its API payloads. They sit just above what each route moved on
2026-09-27, so a regression fails while today's pages pass.

Wiring this half into CI needs a browser in the runner image, which is
eng-platform's to add.

## Layering, the rule most pages broke

A container that paints `--k-surface` hands `--k-ground` to what it holds,
through `--k-content`. A leaf paints `background: var(--k-content)` and so
flips correctly wherever it sits. Legacy `.card` sets
`--k-content: var(--k-ground)` like a kit container does. A literal
`var(--k-ground)` on a bordered leaf is the defect this exists to stop.
