# The castle splat page

`/cycle-14/castle` shows the cycle 14 castle of La Croisée des Bois: a still, a live Gaussian splat view, and two flythroughs. Page: `frontend/src/pages/CastleSplat.tsx`, engine code `frontend/src/lib/splatViewer.ts`. The BioDrive page reuses this markup and styles: [biodrive-splat.md](biodrive-splat.md).

## Where the files live

Not in this repo (2 MB file cap). On the files host under `https://files.coilysiren.me/eco/cycle-14/`:

* `castle-v2.sog`, the splat (8.2 MB). The host admits GET and HEAD from the eco-app origin only so localhost shows the failure state, by design. For a local render, serve the file with CORS (a request intercept works).
* `eco-cycle-14-castle-flythrough-1.mp4` (55 s, 27 MB) and `-2.mp4` (30 s, 25 MB), each with a `-poster.jpg`.

In the repo: the still `frontend/src/assets/castle-v2-hero.jpg`, rendered from the page's own start view, and `og-art/castle.jpg`.

## What a person sees

* **Loading** - the still and a status line naming the size.
* **Failed** - the still, an alert and Try again. The canvas is hidden.
* **Unsupported** - no WebGL 2. The still stays, the engine is never fetched.
* **Ready** - an orbiting view from the still's camera, cleared to the still's sky colour. Orbit starts paused under reduced motion. Drag orbits, scroll and pinch zoom, focused arrow keys turn, plus and minus zoom.
* **Flythroughs** - a poster on a labelled Play button. The `<video>` mounts on press, so nothing downloads before. A file that will not load offers Try again.

## Decisions worth knowing

* **The engine loads on this route only.** `playcanvas` is pinned exact and imported on demand, a separate 630 KB gzipped chunk.
* **The splat is turned 180 degrees about Z.** 3DGS is y-down (v2 checked in a browser).
* **The camera is in blocks** (v2 is about 150 across): start camera, zoom range, clip planes.
* **It opens on the clock tower, upright** (COI-2248). The tower is about 36 blocks tall. The view looks at `[-21.9, 27, -32.5]` from 62 blocks at a 3 degree rise, fov 50. Orbit, zoom and Reset view pivot on it.
* **The capture is levelled.** The tower leaned 4.8 degrees, which no camera undoes. `level` in the view spec (`[0.7, 0, -0.71]`) turns the splat that much, measured by triangulating two views. A new capture needs its own.
* **`RESOLUTION_AUTO`, no resize observer.** Only that mode resizes the buffer.
* **Engine start is deferred a tick.** React's dev double-mount otherwise boots two engines on one canvas.
* **`crawl: index`,** on Kai's say.
* **Budget 12300K** in `frontend/scripts/kit-check.mjs` over the 11974K the route moves (8386K splat, 2388K engine, 437K shell, 435K posters, 177K still). `kit-check` sums wire bytes since the splat host sends no `Timing-Allow-Origin`.
* **Posters on a button, not `<video preload="none">`.** axe waits for metadata on any `<video>` with a `src`, which never arrives with `preload="none"`.

`just frontend-test` covers every state, viewer mocked.
