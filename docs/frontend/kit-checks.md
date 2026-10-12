# Kit checks

The coilyco kit holds a page to axe, a 16px type floor, the layering rule and transfer budgets. eco-app runs them in two halves, jsdom in the `frontend` job and a browser in the `kit-check` job.

## Every push: `just frontend-test`

* `frontend/src/test/a11y.test.tsx` - axe on every `data/spa_routes.json` route with the kit's tags, every data plane failing (the outage state is likeliest to lose a heading or label). jsdom has no layout, so `color-contrast` and `scrollable-region-focusable` are left to the browser.
* `frontend/src/test/contrastMatrix.test.ts` - every text token against every surface token, vendored kit plus `eco-theme.css`: 4.5:1 for text and filled-button labels, 3:1 for the focus ring.
* `frontend/src/test/kitFloor.test.ts` - no font size in eco-app's CSS below 16px, nothing sizing the root. SVG labels size in viewBox units, so the browser half measures them.

Each test asserts it found enough to check, so an empty read cannot pass.

## In a browser: `just frontend-kit-check`

`frontend/scripts/kit-check.mjs` drives an installed Chrome over every public route at 1000x660 and 320x900 and reports per route: axe, text under 16px (SVG text scaled), layering (a bordered element sharing its parent's fill, kit rule 4), horizontal overflow, and wire weight (cross-origin included) against a per-route budget at 1000px.
`--base URL` targets a dev server (production by default), `--only /map,/trade` picks routes. A route is ready once the app drew into `#root`, then the check waits up to 15s for the network to go quiet, and a page that does not draw fails (eco-app#8680). Budgets are set just above what each route moved on 2026-09-27.

## The `kit-check` CI job

`just frontend-kit-check-ci` (`scripts/frontend-kit-check-ci.sh`) builds the SPA, installs Chromium, serves `frontend/dist` from the fused service and runs three steps. Any failure fails it.

* **Outage states** - every public route at both widths, every upstream on a dead port, so pages draw the outage state. The service is the real one because vite's proxy cannot stand in for `/info`.
* **Data states** - `scripts/kit-check-stub.mjs` fronts that service and answers `/preview/*.json` from the trimmed payloads in `src/test/fixtures/norms`, mapped by `serve.json` as in `priceNormsRender.test.tsx`. `--pages` checks those pages against `FIXTURE_BUDGETS` and fails one drawing no price, so a stub miss cannot pass.
* **Negative controls** - a payload padded by 1.5 MiB must fail `/trade` on its budget, and `main table{min-width:900px}` must scroll it sideways at 320px. An uncaught control fails.

The castle, biodrive and terrace-farms splats are not loaded in CI and are checked by hand (COI-2770).

## Layering

A container painting `--k-surface` hands `--k-ground` to its contents through `--k-content`. A leaf paints `background: var(--k-content)`, and a literal `var(--k-ground)` on a bordered leaf is the defect. Legacy `.card` sets `--k-content` to `--k-ground`.
