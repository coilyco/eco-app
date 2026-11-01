from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from scripts import mod_packages
from scripts.mod_packages import ModProject, package_mods


def test_package_mods_creates_deterministic_install_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "mods" / "sample" / "SampleMod.csproj"
    project.parent.mkdir(parents=True)
    project.write_text(
        """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
    <AssemblyName>SampleMod</AssemblyName>
    <Version>1.2.3</Version>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Eco.ReferenceAssemblies" Version="1.0.0" />
  </ItemGroup>
</Project>
""",
        encoding="utf-8",
    )
    build_output = project.parent / "bin" / "Release" / "net10.0"
    native_output = build_output / "runtimes" / "linux-x64" / "native"
    native_output.mkdir(parents=True)
    (build_output / "SampleMod.dll").write_bytes(b"assembly")
    (build_output / "SampleMod.deps.json").write_text("{}\n", encoding="utf-8")
    (native_output / "libsample.so").write_bytes(b"native")

    original_rglob = Path.rglob
    reverse_build_enumeration = False

    def varied_rglob(path: Path, pattern: str):
        entries = list(original_rglob(path, pattern))
        if path == build_output and reverse_build_enumeration:
            entries.reverse()
        return iter(entries)

    monkeypatch.setattr(Path, "rglob", varied_rglob)

    output = tmp_path / "packages"
    package_mods(tmp_path, output, "abc123")
    archive = output / "SampleMod-1.2.3.zip"
    first_digest = hashlib.sha256(archive.read_bytes()).hexdigest()

    reverse_build_enumeration = True
    package_mods(tmp_path, output, "abc123")

    assert hashlib.sha256(archive.read_bytes()).hexdigest() == first_digest
    with zipfile.ZipFile(archive) as package:
        assert package.namelist() == [
            "Mods/SampleMod/SampleMod.deps.json",
            "Mods/SampleMod/SampleMod.dll",
            "Mods/SampleMod/runtimes/linux-x64/native/libsample.so",
        ]

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_revision"] == "abc123"
    assert manifest["packages"][0]["package_name"] == "sample-mod"
    assert manifest["packages"][0]["registry_version"] == "1.2.3+abc123"
    assert manifest["packages"][0]["checksum"] == first_digest


def test_package_name_splits_assembly_words() -> None:
    project = ModProject(Path("Sample.csproj"), "EcoJobsTracker", "1.0.0", "net10.0")

    assert project.package_name == "eco-jobs-tracker"


def test_publish_mods_selects_one_manifest_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = []
    for package_name in ("eco-replay", "eco-telemetry"):
        archive = f"{package_name}.zip"
        metadata = f"{package_name}.json"
        checksum = f"{archive}.sha256"
        for file_name in (archive, metadata, checksum):
            (tmp_path / file_name).write_bytes(file_name.encode())
        records.append(
            {
                "archive": archive,
                "checksum_file": checksum,
                "metadata": metadata,
                "package_name": package_name,
                "registry_version": "0.1.0+abc123",
            }
        )
    (tmp_path / "manifest.json").write_text(
        json.dumps({"schema": 1, "packages": records}), encoding="utf-8"
    )
    uploads: list[str] = []
    monkeypatch.setattr(mod_packages, "_ensure_release", lambda *_args: None)
    monkeypatch.setattr(
        mod_packages,
        "_upload_idempotently",
        lambda path, _repo, _tag: uploads.append(path.name),
    )

    mod_packages.publish_mods(tmp_path, "coilyco/eco-app", "mods-abc123", "a" * 40, "eco-replay")

    assert len(uploads) == 3
    assert all(name.startswith("eco-replay") for name in uploads)


def test_release_tag_is_the_short_revision() -> None:
    assert (
        mod_packages.release_tag("0123456789abcdef0123456789abcdef01234567") == "mods-0123456789ab"
    )


def test_upload_skips_a_matching_asset_and_rejects_different_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "EcoReplay-1.0.0.zip"
    archive.write_bytes(b"zip")
    calls: list[tuple[str, ...]] = []

    def fake_gh_checked(*args: str) -> bytes:
        calls.append(args)
        if args[:2] == ("release", "view"):
            return json.dumps({"assets": [{"name": archive.name}]}).encode()
        return remote_bytes

    monkeypatch.setattr(mod_packages, "_gh_checked", fake_gh_checked)

    remote_bytes = b"zip"
    mod_packages._upload_idempotently(archive, "coilyco/eco-app", "mods-abc123")
    assert not any(call[:2] == ("release", "upload") for call in calls)

    remote_bytes = b"other"
    with pytest.raises(RuntimeError, match="different bytes"):
        mod_packages._upload_idempotently(archive, "coilyco/eco-app", "mods-abc123")


def test_upload_sends_a_new_asset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "EcoReplay-1.0.0.zip"
    archive.write_bytes(b"zip")
    calls: list[tuple[str, ...]] = []

    def fake_gh_checked(*args: str) -> bytes:
        calls.append(args)
        return json.dumps({"assets": []}).encode()

    monkeypatch.setattr(mod_packages, "_gh_checked", fake_gh_checked)

    mod_packages._upload_idempotently(archive, "coilyco/eco-app", "mods-abc123")

    assert calls[-1] == (
        "release",
        "upload",
        "mods-abc123",
        str(archive),
        "--repo",
        "coilyco/eco-app",
    )
