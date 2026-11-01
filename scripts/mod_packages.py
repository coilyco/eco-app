#!/usr/bin/env python3
"""Create and publish install-ready Eco mod archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

SCHEMA_VERSION = 1
REVISION_PATTERN = re.compile(r"^[A-Za-z0-9._+-]+$")
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class ModProject:
    project: Path
    assembly: str
    version: str
    target_framework: str

    @property
    def package_name(self) -> str:
        first_pass = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1-\2", self.assembly)
        return re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", first_pass).lower()

    @property
    def build_output(self) -> Path:
        return self.project.parent / "bin" / "Release" / self.target_framework


def _property(root: ElementTree.Element, name: str, project: Path) -> str:
    value = root.findtext(f".//{name}")
    if value is None or not value.strip():
        raise ValueError(f"{project}: missing required <{name}> property")
    return value.strip()


def discover_projects(repo_root: Path) -> list[ModProject]:
    projects: list[ModProject] = []
    for project in sorted((repo_root / "mods").rglob("*.csproj")):
        root = ElementTree.parse(project).getroot()
        references = root.findall(".//PackageReference")
        if not any(ref.get("Include") == "Eco.ReferenceAssemblies" for ref in references):
            continue
        projects.append(
            ModProject(
                project=project,
                assembly=_property(root, "AssemblyName", project),
                version=_property(root, "Version", project),
                target_framework=_property(root, "TargetFramework", project),
            )
        )
    if not projects:
        raise ValueError("no real Eco mod projects found under mods/")
    return projects


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prepare_output(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for path in output.iterdir():
        if not path.is_file():
            raise ValueError(f"refusing to replace unexpected directory: {path}")
        path.unlink()


def _write_archive(project: ModProject, archive: Path) -> None:
    build_output = project.build_output
    primary_dll = build_output / f"{project.assembly}.dll"
    if not primary_dll.is_file():
        raise ValueError(f"{project.project}: build output is missing {primary_dll}")

    files = sorted(
        (path for path in build_output.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(build_output).as_posix(),
    )
    with zipfile.ZipFile(
        archive,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as package:
        for source in files:
            relative = source.relative_to(build_output)
            destination = Path("Mods") / project.assembly / relative
            info = zipfile.ZipInfo(destination.as_posix(), ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            package.writestr(info, source.read_bytes())


def package_mods(repo_root: Path, output: Path, revision: str) -> None:
    if not REVISION_PATTERN.fullmatch(revision):
        raise ValueError(f"invalid source revision for a package version: {revision!r}")

    _prepare_output(output)
    records: list[dict[str, object]] = []
    for project in discover_projects(repo_root):
        archive_name = f"{project.assembly}-{project.version}.zip"
        archive = output / archive_name
        _write_archive(project, archive)
        checksum = _sha256(archive)
        registry_version = f"{project.version}+{revision}"
        metadata_name = f"{project.assembly}-{project.version}.json"
        checksum_name = f"{archive_name}.sha256"
        record: dict[str, object] = {
            "assembly": project.assembly,
            "archive": archive_name,
            "checksum": checksum,
            "checksum_file": checksum_name,
            "metadata": metadata_name,
            "mod_version": project.version,
            "package_name": project.package_name,
            "registry_version": registry_version,
            "size": archive.stat().st_size,
            "source_project": project.project.relative_to(repo_root).as_posix(),
            "source_revision": revision,
            "target_framework": project.target_framework,
        }
        (output / metadata_name).write_text(
            json.dumps({"schema": SCHEMA_VERSION, "package": record}, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        (output / checksum_name).write_text(
            f"{checksum}  {archive_name}\n",
            encoding="utf-8",
        )
        records.append(record)
        print(f"packaged {project.assembly} {registry_version}: {archive}")

    manifest = {
        "schema": SCHEMA_VERSION,
        "source_revision": revision,
        "packages": records,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


GH_TIMEOUT_SECONDS = 300


def release_tag(revision: str) -> str:
    """One release per source revision holds every package built from it."""
    return f"mods-{revision[:12]}"


def _gh(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["gh", *args], check=False, capture_output=True, timeout=GH_TIMEOUT_SECONDS
    )


def _gh_checked(*args: str) -> bytes:
    result = _gh(*args)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"gh {' '.join(args[:2])} failed: {detail}")
    return result.stdout


def _ensure_release(repo: str, tag: str, target: str) -> None:
    """Create the release at the source commit, tolerating a parallel job that won the race."""
    if _gh("release", "view", tag, "--repo", repo).returncode == 0:
        return
    created = _gh(
        "release",
        "create",
        tag,
        "--repo",
        repo,
        "--target",
        target,
        "--title",
        tag,
        "--notes",
        f"Install-ready Eco mod archives built from {target}.",
        "--latest=false",
    )
    if created.returncode != 0 and _gh("release", "view", tag, "--repo", repo).returncode != 0:
        detail = created.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"could not create release {tag}: {detail}")


def _upload_idempotently(path: Path, repo: str, tag: str) -> None:
    data = path.read_bytes()
    listing = _gh_checked("release", "view", tag, "--repo", repo, "--json", "assets")
    names = {asset["name"] for asset in json.loads(listing)["assets"]}
    if path.name in names:
        remote = _gh_checked(
            "release", "download", tag, "--repo", repo, "--pattern", path.name, "--output", "-"
        )
        if hashlib.sha256(remote).digest() != hashlib.sha256(data).digest():
            raise RuntimeError(f"release {tag} already has {path.name} with different bytes")
        print(f"already published with matching checksum: {path.name}")
        return
    _gh_checked("release", "upload", tag, str(path), "--repo", repo)
    print(f"published {path.name}")


def publish_mods(
    package_dir: Path,
    repo: str,
    tag: str,
    target: str,
    package_name: str | None = None,
) -> None:
    manifest_path = package_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA_VERSION:
        raise ValueError(f"unsupported package manifest schema: {manifest.get('schema')!r}")

    records = manifest["packages"]
    if package_name is not None:
        records = [record for record in records if record["package_name"] == package_name]
        if not records:
            raise ValueError(f"package manifest has no package named {package_name!r}")

    _ensure_release(repo, tag, target)
    for record in records:
        for field in ("archive", "metadata", "checksum_file"):
            _upload_idempotently(package_dir / record[field], repo, tag)


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    package_parser = subparsers.add_parser("package", help="create deterministic mod ZIPs")
    package_parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    package_parser.add_argument("--output", type=Path, required=True)
    package_parser.add_argument("--revision", required=True)

    publish_parser = subparsers.add_parser("publish", help="upload ZIPs as GitHub Release assets")
    publish_parser.add_argument(
        "--input",
        type=Path,
        default=Path(os.environ.get("MOD_PACKAGE_DIR", ".build/mod-packages")),
    )
    publish_parser.add_argument(
        "--tag", default=os.environ.get("MOD_RELEASE_TAG") or None, help="release tag"
    )
    publish_parser.add_argument(
        "--package-name",
        default=os.environ.get("MOD_PACKAGE_NAME") or None,
        help="publish only this manifest package (defaults to all packages)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "package":
            package_mods(args.repo_root.resolve(), args.output.resolve(), args.revision)
        else:
            target = _required_env("GITHUB_SHA")
            publish_mods(
                args.input.resolve(),
                _required_env("GITHUB_REPOSITORY"),
                args.tag or release_tag(target),
                target,
                args.package_name,
            )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"mod packages: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
