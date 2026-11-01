# Eco mod packages

How the in-game C# plugins under `mods/` become install-ready packages.

## Build contract

The application Docker build uses the .NET 10 SDK image to discover and compile
every real `Eco.ReferenceAssemblies` project under `mods/`. It emits deterministic install-ready ZIPs rooted at
`Mods/<AssemblyName>/` and carries them at `/mod-packages` in the final image.

Deterministic means the same source produces byte-identical archives, so
republishing an unchanged revision is a no-op rather than a new artifact.

## CI policy

Main CI builds the packages and publishes them as release assets. The push workflow does not treat the
mod compile as a required green signal, because a game-server SDK breakage
would otherwise make ordinary application CI red. The manual
`mods-diagnostic` workflow runs the compile and test path on demand instead
(#60).

## Versions and release coordinates

A package version combines the repository version with the source revision, so
a coordinate names exactly one build. Forgejo's generic package registry is gone,
so each main push publishes a GitHub release tagged `mods-<first 12 of the source
sha>` (COI-3122). Its assets are each package's ZIP, metadata JSON and `.sha256`
sidecar, taken from the `/mod-packages` directory of the published image so the
release bytes are the bytes the image shipped. `publish-mods` in `build-publish`
runs `scripts/publish-mod-from-image.sh`, which is idempotent: an asset already
present with the same bytes is skipped, and one with different bytes fails.

**Commands.** The `build-mod-*` ward verbs build one project each, and
`build-mods` restores and builds all four with per-project timings.

## mod.io promotion

Promotion to mod.io is an operator step, not part of main CI. Dispatch
`modio-publish` with the release tag, the package version and the archive name.
It downloads the release asset, requires the metadata and the `.sha256` sidecar
to agree with the ZIP, and uploads those bytes without rebuilding. `dry_run`
defaults to true and stops after the verification. It needs the
`MODIO_ACCESS_TOKEN` secret for a real upload. The public listing copy lives with
the mod in `coilyco-gaming/eco-mods` rather than here.

See also: [FEATURES.md](FEATURES.md), and `coilyco-gaming/eco-mods` for the
public mod source.
