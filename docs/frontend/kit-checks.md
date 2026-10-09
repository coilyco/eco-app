# Kit checks

The coilyco kit ships CI that holds a page to its rules: axe, a 16px type floor, the layering rule, transfer budgets. eco-app runs them in two halves, jsdom in the `frontend` job and a browser in the `kit-check` job.

## Every push: `just frontend-test`

* `frontend/src/test/a11y.test.tsx` - axe on every route in `data/spa_routes.json` with the kit's tag set (`wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`, `best-practice`), with every data plane failing, the outage state being likeliest to lose a heading or label. jsdom has no layout, so `color-contrast` and `scrollable-region-focusable` are left to the browser.
* `frontend/src/test/contrastMatrix.test.ts` - every text token against every surface token, from the vendored kit with `eco-theme.css` on top: 4.5:1 for text and for labels on filled buttons, 3:1 for the focus ring.
* `frontend/src/test/kitFloor.test.ts` - no font size in eco-app's CSS below 16px, nothing sizing the root. SVG chart labels size in viewBox units, so the browser half measures them.

Each test asserts it found enough to check, so an empty read cannot pass. The CSS is read from disk because vitest stubs CSS imports to `""`.

## In a browser: `just frontend-kit-check`

`frontend/scripts/kit-check.mjs` drives an installed Chrome over every public route at 1000x660 and 320x900 and reports per route: axe with the full tag set, text under 16px (SVG text scaled by its screen matrix), layering (a bordered element sharing its parent's fill, kit rule 4), horizontal overflow, and wire transfer weight (cross-origin included) against a per-route budget, at 1000px only.

```
just frontend-kit-check                                   # production
just frontend-kit-check --base http://localhost:5173      # a dev server
just frontend-kit-check --only /map,/trade
```

A route is ready when the document loaded and the app drew into `#root`. The check then waits up to 15s for the network to go quiet, reporting `note: still loading`. A page that does not load or draw still fails (eco-app#8680). Budgets are eco-app's, not the kit's 150K, set just above what each route moved on 2026-09-27.

## The `kit-check` CI job

`just frontend-kit-check-ci` (`scripts/frontend-kit-check-ci.sh`) builds the SPA, installs Playwright's Chromium in the job, serves `frontend/dist` from the fused service and runs `kit-check` on every public route at both widths. Any failure above fails the job.

The service is the real one because `/info` is a server route vite's dev proxy does not stand in for. Every upstream points at a dead port, so pages draw the outage state and budgets are met by shell and outage payloads. Live data states and the castle splat are checked by hand.

## Layering

A container that paints `--k-surface` hands `--k-ground` to its contents through `--k-content`. A leaf paints `background: var(--k-content)`. A literal `var(--k-ground)` on a bordered leaf is the defect. Legacy `.card` sets `--k-content: var(--k-ground)`.
