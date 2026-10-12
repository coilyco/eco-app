# mod.io publish notes

Source of truth for the mod.io listing fields. Keep in sync when the mod's behavior or
distribution shape changes. Long listing copy is condensed: `git show b012073:mods/jobs/modio.md`.

## Listing fields

- **Game:** ECO (id `6`). **Visibility:** Public. **Maturity:** None.
- **Name:** `Eco Jobs Tracker` (assembly `EcoJobsTracker`, matches the dashboard subdomain).
- **Type tag:** `Script`, used by every server-side mod with code or HTTP endpoints.
- **Logo:** `docs/logo-modio.png` (1600x900, cropped from the dashboard's Professions grid).
- **Homepage:** <https://github.com/coilyco-flight-deck/eco-jobs-tracker>
- **Live demo:** <https://eco-jobs-tracker.coilysiren.me/>, the FastAPI dashboard in this repo.

## Summary (250 chars max)

> Read-only admin endpoint that lists every player's learned professions and specialties. Authenticated with your Eco admin token. Pair with the live dashboard at eco-jobs-tracker.coilysiren.me or point any client at GET /api/v1/skills.

## Description outline

- One read-only endpoint, `GET /api/v1/skills`: every player and learned specialty with
  levels, as stable JSON for dashboards, bots and imports. Pairs with the live dashboard.
- Install: extract `EcoJobsTracker.zip` so the DLL lands at `Mods/EcoJobsTracker/EcoJobsTracker.dll`,
  restart, verify with `curl -H "X-API-Key: <admin-token>" http://<server>:3001/api/v1/skills`.
- Auth: under `/api/v1/*`, so Eco's admin-token middleware guards it, no mod-side config.
- Eco 0.14.2+ (built against `Eco.ReferenceAssemblies 0.14.2-beta-release-1099`), Linux and
  Windows. MIT. Unofficial, not affiliated with Strange Loop Games.

## Distribution zip and versions

`just package-mods` writes a deterministic `EcoJobsTracker-X.Y.Z.zip` under
`.build/mod-packages/` with a `Mods/EcoJobsTracker/` prefix (`.dll`, `.deps.json`, `.pdb`),
which mod.io extracts into the server root. `<Version>` in `mod/src/EcoJobsTracker.csproj`
is the source of truth and the uploaded filename must match. Release notes go under "Add
files" on the upload form, not here.
