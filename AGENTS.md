---
ward:
  workflow: pull-request-and-merge
---
# Agent instructions

Workspace conventions load globally via `~/.claude/CLAUDE.md`. This file covers only what is specific to this repo.

## Scope

The Eco application monorepo: one fused Python service (MCP + SPA + jobs API), a local replay browser, and the in-game C# plugins that feed them. Consolidated per [coilysiren/inbox#101](https://forgejo.coilysiren.me/coilyco-bridge/inbox/issues/101).

## Project shape

- `src/eco_mcp_app/` - the core service. `server.py` is the MCP server, `__main__.py` the stdio entry for Claude Desktop, `http_app.py` the Starlette ASGI app.
- `src/eco_spec_tracker/` - jobs JSON API (FastAPI), mounted at `/jobs/api`. The jobs UI is the SPA's `/jobs` route.
- `src/eco_replay/` - FastAPI browser for the replay mod's SQLite event log. Local-only.
- `frontend/` - Vite + React + TypeScript SPA, served at `/` by the fused service. Built in the Dockerfile's node stage; local dev via `just frontend-dev` against `just http`.
- `mods/jobs/`, `mods/replay/`, `mods/telemetry/` - C# Eco server plugins. jobs and replay share DTO contracts with their Python consumers, so they live here, not in eco-mods.
- `data/ecoregions.json` - bundled WWF ecoregion definitions. Species/ecopedia lookups go to live web fetches.
- `data/eco_gnome_data.json` - the vendored, en-US-trimmed vanilla Eco recipe graph. `data/eco_gnome_data.LICENSE.txt` carries its attribution.
- `tests/mcp/`, `tests/jobs/` - per-component pytest suites under one `tests/` root.
- `investigation/` - preserved post-mortem from eco-mcp-app. Read before questioning weird-looking decisions.
- `Dockerfile` - the single fused image, entrypoint `eco_mcp_app.http_app:app` on port 4000.

## Repo boundaries

This repo is the application layer (`infra -> eco-app -> deploy`). Its deploy surface (k8s manifests, rollout) lives in `coilyco-bridge/deploy/services/eco-app`, never here. Gameplay mods belong in `coilyco-gaming/eco-mods`.

## Commands

Route every dev command through the gate as `just <verb>`. The canonical manifest is the [`justfile`](justfile). Each verb invokes one tool directly or delegates shell behavior to the focused [`scripts/ward-command.sh`](scripts/ward-command.sh) dispatcher.

## Validation

- `just test` - pytest across tests/mcp and tests/jobs.
- `just lint` - ruff check + format check + mypy.
- `just smoke` - stdio MCP round-trip.
- `just precommit` - the managed agentic-os hook suite. Opt-outs live under `[tool.agentic-os.*]` in `pyproject.toml`.

## Safety

Keep every artifact public-safe. Opaque ids, tokens, and sensitive hosts go in AWS SSM, never tracked files. Telemetry endpoints stay in the deploy repo, never hardcoded in application source.

## Cross-repo contracts

- `coilyco-bridge/deploy` - owns this service's manifests and rollout. A change to ports, env vars, or secrets here needs a matching change there.
- `coilyco-gaming/eco-mods` - gameplay mods and their Unity assets.
- Update [docs/FEATURES.md](docs/FEATURES.md) whenever a feature is added or reshaped.

## Release

Canonical history lives on Forgejo (`coilyco-gaming/eco-app`). CI tests,
builds, and publishes the private single-architecture image as
`forgejo.coilysiren.me/coilyco-gaming/eco-app:<full-source-sha>` on every push
to `main`. The trusted publisher verifies the remote manifest. A commit to
`main` is not a deploy. Rollout is driven from `coilyco-bridge/deploy` through
a separate read-only package credential.

## Agent rules

<!-- BEGIN managed by agentic-os/scripts/apply-git-workflow.py -->
### Git workflow

**This repo runs the `pull-request-and-merge` lane**, declared as `ward.workflow` in this file's frontmatter. The agent commits to a task branch, pushes it, opens a Forgejo pull request, and **merges that pull request itself** once it is green. The author of the code is the one who merges it. Opening the pull request is a step, never the stopping point.

The fleet runs one lane, and it authorizes the agent end to end. Pushing straight to `main` is over: `merge-remote-main` is retired, so no repo can declare its way back to one.

* `pull-request-and-merge` - the agent commits to a task branch, pushes it, opens a pull request, and merges that pull request itself once it is green.

**Every lane slug names what the AGENT does, never what someone else does.** `pull-request-and-merge` carries the merge because the agent that authored the code merges its own pull request. `pull-request` drops `-and-merge` because the author stops at the pull request and the director merge lane takes over. Reading `pull-request-and-merge` as "someone else merges it later" inverts the two and leaves finished work sitting unmerged.

**These actions are pre-authorized on every lane, and the agent MUST take them without asking first.** Committing, creating a branch, pushing a branch, pushing the lane's own destination, and opening a pull request are ordinary reversible work, not the destructive wall that earns a question. Stopping to ask is how a turn ends with the work stranded in a dirty worktree.

* **ALWAYS commit** in-scope work and **ALWAYS push** it to the canonical remote before pausing, reporting a checkpoint, handing off, or ending a turn. A local-only commit is not a checkpoint.
* **ALWAYS open the pull request** in the same turn as the branch's first push, on every lane except `remote-branch-only`. A pushed branch with no pull request is litter nobody reviews.
* **NEVER `--no-verify`** and **NEVER force-push**. Those two are the real walls, and they stay closed.
* **ALWAYS merge your own pull request on `pull-request-and-merge`**, in the same turn, as soon as it is green. Reporting it as open and awaiting someone is the failure this lane exists to prevent.
* **A merge refused with 405 "the head branch is behind the base branch" is yours to fix, never a reason to ask.** Update the branch yourself, with `aosguard ops forgejo pr update <owner> <repo> <index>`, the forgejo `update_pull-request` verb, or by merging `origin/main` into it and pushing, never by force. Wait for green, then merge.
* **NEVER merge on `pull-request` or `remote-branch-only`.** Those two stop where they stop, and the director merge lane carries a `pull-request` from there.
<!-- END managed by agentic-os/scripts/apply-git-workflow.py -->

Name the actor in action sentences. Route every command through the gate, never bare tooling. Pull every server dataset that could be remotely interesting - code is cheap, the game server is colocated, CPU is fine. Interesting-but-messy data gets a Forgejo cleanup issue, never a silent skip.

**Product UX is the SPA (`frontend/`).** Server HTML is only the MCP `_meta.ui` card - never build browser UI as an iframe/Jinja card.

## Checkout residency

This repo belongs on disk, whether or not Agent Compose's `repository-plan.yaml`
lists it. On a native Windows host it is worked in the canonical checkout under
the projects root, never in a session shadow, a linked worktree, or a temporary
clone. The governing rule is `Serialized checkouts on native Windows` in
agentic-os AGENTS.md, which covers eco-app, eco-mods, and eco-ops together.

These three take one writer at a time, because the Unity assets and the Eco
server state they drive corrupt on a second checkout rather than isolating.
Before the first mutation, confirm that no other agent and no open Unity Editor
holds the checkout, and stop and report when one does rather than branching
around it.

Commit and push before pausing, switching tasks, or ending a session. That still
holds, though now because the remote is the shared record and not because a
temporary root could be purged.

## See also

- [README.md](README.md) - human-facing intro.
- [docs/FEATURES.md](docs/FEATURES.md) - inventory of what ships today.
- [docs/datasets/README.md](docs/datasets/README.md) - dataset survey + probe how-to.
- [justfile](justfile) - dev verbs.

Cross-reference convention from [coilysiren/agentic-os#59](https://github.com/coilyco-flight-deck/agentic-os/issues/59).
