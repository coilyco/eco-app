# The BioDrive splat page

`/cycle-14/biodrive` shows Scuba Steve's BioDrive station in Phantom Springs: a still, a live view of a Gaussian splat, and the facts of the capture (COI-2368). It is the castle page without the flythroughs, so the markup, states and styles are [the castle page's](castle-splat.md). The page is `frontend/src/pages/BioDriveSplat.tsx`, a config for the shared `frontend/src/pages/SplatPage.tsx`.

## Add another build

Copy `BioDriveSplat.tsx` (`TerraceFarmsSplat.tsx`, COI-2541, is the first copy): a new splat URL, still, words, facts and `view`. Then add a route in `data/spa_routes.json` and a name in `frontend/src/routes.tsx`. The viewer takes its camera from `view`, in blocks (`frontend/src/lib/splatViewer.ts`).

## Where the files live

* `biodrive-v2.sog` on the files host under `https://files.coilysiren.me/eco/cycle-14/` (15,551,763 bytes, sha256 `cb89f17050272a0ce0d2701f99469913160e8ea5984852b5129896b98c4424f7`). Like the castle's, it admits the eco-app origin only, so a local render needs a request intercept that adds CORS.
* The still is `frontend/src/assets/biodrive-v2-hero.jpg` (1920x1080) and the link card art is `frontend/src/assets/og-art/biodrive.jpg`, both in the repo.

## Decisions worth knowing

* **Start camera `[-34, 24, -34]` looking at `[0, 3, 0]`, fov 50.** Where the still was taken, so the page's start render matches the still.
* **The station is about 36 by 54 blocks,** so zoom is 6 to 120 and far clip 600, against the castle's 15 to 400 and 2000.
* **The sky is the still's `#8B9EBF`,** same as the castle's.
* **Title and caption are Kai's.** Facts come from the capture's `run.json` in eco-ops. The PSNR is the run's 30,000-step figure, measured before the trim box removed a haze sheet.
* **`crawl: index`,** matching the castle, on Kai's say.
* **Budget 15700K** in `frontend/scripts/kit-check.mjs`, set just above the 15374K that `just frontend-kit-check --only /cycle-14/biodrive` reported live on 2026-10-09. The pre-deploy estimate was 18700K, from the castle's measured parts.
