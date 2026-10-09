# Features

Headline inventory for `eco-jobs-tracker`.

## Shape

A C# Eco mod exposing a read-only HTTP endpoint of every player's learned specialties, a FastAPI JSON API doing the row-shaping, and the SPA `/jobs` page rendering the "who can make what" board.

## JSON API (FastAPI)

- **Mount** - `/jobs/api` of the fused service: `/v1/professions`, `/v1/players`, `/v1/specialties`, plus `/v1/meta` reporting the mock-data flag.
- **Browser UI** - `/jobs` (`frontend/src/pages/Jobs.tsx`): server-wide progression, Professions and Specialties, the recipe-driven "Most valuable to craft" board, and skill trees from the bundled recipe graph ([eco-app#195](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/195)). Rows default to holders in the Eco demographics **Active** or **Long Term**, show covered/total counts, and warn when nobody in that union covers a populated role. A page control reveals other holders. Opportunity rows link into `/uses/price`.
- **Iframe embedding** - CSP `frame-ancestors` allows `coilysiren.me`, shipped site-wide by `FrameAncestorsCSP` in `eco_mcp_app.http_app`.
- **Mock fallback** - `UPSTREAM_URL` unset serves `mock_data.py`, flagged via `/v1/meta`.
- **Upstream fetch** - `UPSTREAM_URL` set makes `upstream.py` call `/api/v1/skills` with `UPSTREAM_API_KEY` as `X-API-Key`, 5s timeout, no fallback on a dead endpoint.

## C# Eco mod (`EcoJobsTracker.dll`)

- **`GET /api/v1/skills`** - ModKit UserCode `[ApiController]`. Iterates `UserManager.Users` with `Level > 0 && IsSpecialty`, returning name, level, max-level, online state, and membership in `DemographicManager.Active` and `LongTerm`. `Active` and `Long Term` are literal Eco demographic names, not last-seen windows.
- **`GET /api/v1/citizens`** - `{id, name}` per user: the numeric id the action exporter keys `Citizen` by, which admin `/api/v1/users` omits ([eco-app#5](https://forgejo.coilysiren.me/coilyco-gaming/eco-app/issues/5)).
- **Auth** - Eco's admin-token `X-API-Key` gate, nothing bespoke.
- **DTOs** - carry `System.Text.Json` and `Newtonsoft.Json` camelCase attributes.
- **Distribution** - mod.io listing copy and zip shape in `mod/modio.md`.
- **Shell harness** - `mod/shell/`, an ASP.NET mock on `:5100` with the same routes, DTOs, and canned data (`just run-shell-jobs`).

## Deploy and dev

- **Deploy** - manifests and rollout live in `coilyco-bridge/deploy/services/eco-app`. This repo owns the application image.
- **Mod packages** - `just package-mods`, `just publish-mod-packages`.
- **Dev loop** - `just sync`, `just http` (`:4000`), `just build-mod-jobs`.
- **Naming debt** - public name is `eco-jobs-tracker`, internals still say `eco-spec-tracker`.

See also: [README.md](../../README.md), [AGENTS.md](../../AGENTS.md), [justfile](../../justfile).
