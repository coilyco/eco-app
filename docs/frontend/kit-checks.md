# Kit checks

The coilyco kit ships with CI that holds a page to its rules: axe, a 16px
type floor, the layering rule, transfer budgets. eco-app runs the same checks,
in two halves: a jsdom half in the `frontend` job and a browser half in the
`kit-check` job.

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

## In a browser: `just frontend-kit-check`

`frontend/scripts/kit-check.mjs` drives an installed Chrome
(`playwright-core`, no download) over every public route at the kit's two
viewports, 1000x660 and 320x900, and reports per route:

* axe with the full tag set, colour contrast included
* any text rendering below 16px, with SVG text scaled by its screen matrix
* layering: a bordered element sharing its parent's fill (the kit's rule 4)
* horizontal overflow
* transfer weight on the wire, cross-origin hosts included, against a per-route budget, at 1000px only

```
just frontend-kit-check                                   # production
just frontend-kit-check --base http://localhost:5173      # a dev server
just frontend-kit-check --only /map,/trade
```

A route is ready when its document has loaded and the app has drawn into
`#root`. The check then waits up to 15s for the network to go quiet and goes on,
so a slow upstream preview shows as a `note: still loading` line and no longer
fails the page. A page that does not load or draw still fails (eco-app#8680).

Budgets are eco-app's, not the kit's 150K. eco-app is a data app, and a route's
weight is its API payloads. They sit just above what each route moved on
2026-09-27, so a regression fails while today's pages pass.

## In CI, on every push and pull request: the `kit-check` job

`just frontend-kit-check-ci` (`scripts/frontend-kit-check-ci.sh`) is the CI
half. The job builds the SPA, installs Playwright's Chromium inside the job
(apt for the system libraries, since the job container runs as root), serves
`frontend/dist` from the fused service and runs `kit-check` on every public
route at both widths. Text under 16px, a layering hit, an axe violation, a
phone overflow, a page that does not draw, or a route over its transfer budget
fails the job. The runner image is untouched, and the browser install is the
one network-dependent step.

The service is the real one because `/info` is a server route, which vite's dev
proxy does not stand in for. Every upstream points at a port nothing listens
on, so each page draws the outage state `a11y.test.tsx` already checks, and
the job needs no game server. Transfer budgets are therefore met by the shell
and outage payloads. A live data state, and the castle splat the CI egress may
not reach, are checked by running `just frontend-kit-check` against production
by hand.

## Layering, the rule most pages broke

A container that paints `--k-surface` hands `--k-ground` to what it holds,
through `--k-content`. A leaf paints `background: var(--k-content)` and so
flips correctly wherever it sits. Legacy `.card` sets
`--k-content: var(--k-ground)` like a kit container does. A literal
`var(--k-ground)` on a bordered leaf is the defect this exists to stop.
