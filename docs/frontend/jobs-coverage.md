# The coverage gaps section on /jobs

A "Coverage gaps" section on `/jobs` lists the specialties someone has taken that
fewer than two Active or Long Term players cover (COI-752). It sits above Professions.
The code is `coverageGaps` and `CoverageGaps` in `frontend/src/pages/Jobs.tsx`.

## What counts as a gap

* **Taken by at least one player ever** (`total` above zero), so a specialty nobody
  has picked is not a gap. Kai asked for unlocked jobs only. The jobs API carries no
  unlock state, so "someone has taken it" stands in for it.
* **Zero or one Active or Long Term holder** (`covered` at most one). Active and Long
  Term are the player roles the page already counts as "N of M Active or Long Term".
  One holder reads as a gap because the point is resilience, a missing smelter stalls
  the server.
* **Order:** nobody covering first, then one, and within each the specialty more
  players have taken. Ties sort by name.
* The universal starter skills never reach it, since they are filtered out of the
  specialty list for the whole page.

## What a person sees

Each gap is a card with the specialty, its profession, and one line: "Nobody Active
or Long Term. 3 players have it." or "Only delta is Active or Long Term." With no gaps
the section says so in a sentence. It shows only once the jobs data has loaded.
