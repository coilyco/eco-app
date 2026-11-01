# Eco mod packages

How the in-game C# plugins under `mods/` become install-ready packages.

## Build contract

The application Docker build uses the moving AOS `:release` full image to
discover and compile every real `Eco.ReferenceAssemblies` project under
`mods/`. It emits deterministic install-ready ZIPs rooted at
`Mods/<AssemblyName>/` and carries them at `/mod-packages` in the final image.

Deterministic means the same source produces byte-identical archives, so
republishing an unchanged revision is a no-op rather than a new artifact.

## CI policy

Main CI builds and publishes the packages. The push workflow does not treat the
mod compile as a required green signal, because a game-server SDK breakage
would otherwise make ordinary application CI red. The manual
`mods-diagnostic` workflow runs the compile and test path on demand instead
(#60).

## Versions and registry coordinates

A package version combines the repository version with the source revision, so
a coordinate names exactly one build. Publishing packages to the private Forgejo
registry retired with Forgejo on 2026-11-01 (COI-2931). The `publish-mod` job and the
`modio-publish` workflow, which read those packages, were not ported (COI-3102), because
GitHub has no generic package registry.

**Commands.** The `build-mod-*` ward verbs build one project each, and
`build-mods` restores and builds all four with per-project timings.

## mod.io promotion

Promotion to mod.io is an operator step, not CI. The operator sets up the
mod.io credentials locally, runs the promotion against a built package, and the
public listing copy lives with the mod in `coilyco-gaming/eco-mods` rather than
here.

See also: [FEATURES.md](FEATURES.md), and `coilyco-gaming/eco-mods` for the
public mod source.
