# The castle splat page

`/cycle-14/castle` shows a Gaussian splat of the cycle 14 castle draft, the
first test capture for epic 49 (`teable:coilyco/eco-app#8572`). The page is
`frontend/src/pages/CastleSplat.tsx`. The engine-facing code is
`frontend/src/lib/splatViewer.ts`.

## Where the splat lives

Not in this repo, which caps files at 2 MB. The page loads
`https://files.coilysiren.me/eco/cycle-14/castle-draft.sog` (4.36 MB), and that
host admits GET and HEAD from the eco-app origin only (infrastructure#1237). So
the page fails to load the file from localhost, by design, and shows its failure
state with a retry.

## What a person sees

* **Loading** - a status line naming the size.
* **Failed** - an alert and a Try again button. The canvas is hidden.
* **Unsupported** - no WebGL 2. The engine is never fetched.
* **Ready** - an orbiting view. Orbit starts paused for someone who prefers
  reduced motion. Drag orbits, scroll and pinch zoom, and with the view focused
  the arrow keys turn it and plus and minus zoom.

## Decisions worth knowing

* **The engine loads on this route only.** `playcanvas` is pinned exact and
  imported on demand, a separate 630 KB gzipped chunk. The rest of the site pays
  nothing for it.
* **The splat is turned 180 degrees about Z.** COLMAP is y-down, so a capture
  arrives upside down without it.
* **`RESOLUTION_AUTO`, no resize observer.** The engine resizes its pixel buffer
  only in that mode, and then follows the canvas's CSS size every frame.
* **Start of the engine is deferred a tick.** React's dev double-mount otherwise
  boots two engines on one canvas, which broke Play after a Pause.
* **`crawl: index`.** Public, in the sitemap, and answered with a canonical link
  header, on Kai's say. It started `noindex` while it was a draft. Like every
  SPA page it shares the shell's `<title>`, since no page sets its own.
* **Budget 5400K** in `frontend/scripts/kit-check.mjs`: 827K measured without the
  splat plus its 4460K. `kit-check` cannot load the splat from localhost, so it
  measures the failure state.

## Checking it

`just frontend-test` covers every state with the viewer mocked. The render
itself needs a browser: serve the splat with CORS in place of the files host
(a request intercept works) and look.
