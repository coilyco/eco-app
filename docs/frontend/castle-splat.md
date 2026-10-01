# The castle splat page

`/cycle-14/castle` shows the cycle 14 castle of La Croisée des Bois: a still, a
live view of a Gaussian splat, and two flythroughs of it. It started as the first
test capture for epic 49 (`teable:coilyco/eco-app#8572`) and is now v2
(`teable:coilyco/eco-app#8601`). The page is `frontend/src/pages/CastleSplat.tsx`.
The engine-facing code is `frontend/src/lib/splatViewer.ts`.

## Where the files live

Not in this repo, which caps files at 2 MB. They are on the files host, under
`https://files.coilysiren.me/eco/cycle-14/`:

* `castle-v2.sog`, the splat (8.2 MB). The host admits GET and HEAD from the
  eco-app origin only (infrastructure#1237), so the page fails to load it from
  localhost, by design, and shows its failure state with a retry. To see the
  render locally, serve the file with CORS in its place (a request intercept
  works).
* `eco-cycle-14-castle-flythrough-1.mp4` (55 s, 27 MB) and `-2.mp4` (30 s, 25 MB),
  each with a `-poster.jpg` beside it (`teable:coilyco/infrastructure#8605`).

The still on the stage, `frontend/src/assets/castle-v2-hero.jpg` (1920x1080, 177 KB),
is in the repo. So is the link-preview card art, `og-art/castle.jpg`.

## What a person sees

* **Loading** - the still, with a status line naming the size over it.
* **Failed** - the still, with an alert and a Try again button over it. The canvas
  is hidden.
* **Unsupported** - no WebGL 2. The still stays, and the engine is never fetched.
* **Ready** - an orbiting view that starts where the still was taken, and clears to
  the still's sky colour so the handoff does not flash. Orbit starts paused for
  someone who prefers reduced motion. Drag orbits, scroll and pinch zoom, and with
  the view focused the arrow keys turn it and plus and minus zoom.
* **Flythroughs** - each is its poster on a labelled Play button. The video is not
  in the page until it is pressed, so nothing downloads before then. A file that
  will not load says so with a Try again. A poster that will not load leaves the
  button on black.

## Decisions worth knowing

* **The engine loads on this route only.** `playcanvas` is pinned exact and
  imported on demand, a separate 630 KB gzipped chunk. The rest of the site pays
  nothing for it.
* **The splat is turned 180 degrees about Z.** 3DGS is y-down, so a capture arrives
  upside down without it. v2 is y-down too, and was checked in a browser.
* **The camera is in blocks.** v2 is about 150 Eco blocks across (v1 was about 10
  arbitrary units), so the start camera, zoom range and clip planes are in blocks:
  start at `[90, 70, -90]` looking at `[0, 10, 0]`, fov 50, the reference viewer's.
* **`RESOLUTION_AUTO`, no resize observer.** The engine resizes its pixel buffer
  only in that mode, and then follows the canvas's CSS size every frame.
* **Start of the engine is deferred a tick.** React's dev double-mount otherwise
  boots two engines on one canvas, which broke Play after a Pause.
* **`crawl: index`.** Public, in the sitemap, and answered with a canonical link
  header, on Kai's say. The title and description are dev-advocate's, in the
  route manifest.
* **Budget 10000K** in `frontend/scripts/kit-check.mjs`: 827K shell, 177K still,
  8376K splat and two posters. The flythroughs are not in it, since each loads only
  when played. `kit-check` cannot load the splat from localhost, so there it
  measures the failure state.
* **Posters on a button, not a `<video>` with `preload="none"`.** axe waits for
  metadata on any `<video>` that has a `src`, and with `preload="none"` that never
  arrives, so the accessibility check stalled. The `<video>` is mounted on press.

## Checking it

`just frontend-test` covers every state with the viewer mocked. The render
itself needs a browser: serve the splat with CORS in place of the files host
(a request intercept works) and look.
