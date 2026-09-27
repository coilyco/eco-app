# The homepage

`/` is the one link a player hands anyone to learn about the Sirens server. It
replaced a hand-kept Discord info block that went stale (it still said Cycle 13
and 72x72 after the world moved on). Built in `teable:coilyco/eco-app#8306`.

## Two sources, split by how fast they change

* **Live status** (`/preview.json` via `useEcoStatus`) - cycle number, world
  size, meteor length, meteor state, collaboration, game speed, Eco version,
  and players online. Parsed in `frontend/src/lib/serverBrief.ts`, because the
  cycle and world size only exist inside the server's free-text description.
* **The reviewed brief** (`data/server_brief.json`) - join steps, the next
  cycle notice, settings, mods, skill trees, and gameplay notes. game-dev owns
  the wording and eco-ops is its source of truth. `reviewedOn` is shown on the
  page.

Nothing per-cycle lives in the brief, and a test fails if it creeps in. A
second copy of a live fact is how the Discord block went stale.

## States, worst first

* **Live status fails** - the hero keeps the server name, the join steps, and
  the Discord invite from the brief. Every live fact reads Unknown rather than
  falling back to an old value.
* **Meteor** - destroyed (parsed from the world achievement, "Destroyed the
  meteor on Day 57, 23:13") wins over any countdown. Then a countdown in meteor
  amber, then no meteor, then "not reporting the meteor date". `/info`'s
  meteor banner reads the same parser.
* **Loading** - "Checking the server" in the live pill, and the brief renders
  at once because it ships in the bundle.

## Layout

Hero with the join panel beside it, the live facts strip, then settings, mods
(linking to `/mods` for the attributed catalog), skill trees grouped by their
first skill, gameplay notes, and the directory of every other surface. All of it
uses kit components. See [kit-theme.md](kit-theme.md).
