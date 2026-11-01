from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from scripts import modio_publish


def _manifest(package_name: str, version: str, archive: bytes) -> dict[str, Any]:
    checksum = hashlib.sha256(archive).hexdigest()
    archive_name = "EcoReplay-1.2.3.zip"
    return {
        "schema": 1,
        "package": {
            "archive": archive_name,
            "checksum": checksum,
            "checksum_file": f"{archive_name}.sha256",
            "mod_version": "1.2.3",
            "package_name": package_name,
            "registry_version": version,
            "source_revision": "abc123",
        },
    }


def test_download_artifact_requires_the_published_checksum_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_name = "eco-replay"
    version = "1.2.3+abc123"
    archive = b"zip bytes"
    manifest = _manifest(package_name, version, archive)
    archive_name = manifest["package"]["archive"]
    checksum = manifest["package"]["checksum"]

    def fetch(url: str) -> bytes:
        if url.endswith(".json"):
            return json.dumps(manifest).encode()
        if url.endswith(".sha256"):
            return f"{checksum}  {archive_name}\n".encode()
        assert url.endswith(".zip")
        return archive

    monkeypatch.setattr(modio_publish, "_fetch_bytes", fetch)
    artifact = modio_publish.download_artifact(
        "https://github.com/coilyco/eco-app/releases/download/mods-abc123",
        package_name,
        version,
        "EcoReplay-1.2.3.zip",
    )

    assert artifact.sha256 == checksum
    assert artifact.source_version == "1.2.3"
    assert artifact.zip_bytes == archive


def test_download_artifact_rejects_tampered_zip(monkeypatch: pytest.MonkeyPatch) -> None:
    package_name = "eco-replay"
    version = "1.2.3+abc123"
    archive = b"expected zip"
    manifest = _manifest(package_name, version, archive)
    checksum = manifest["package"]["checksum"]

    def fetch(url: str) -> bytes:
        if url.endswith(".json"):
            return json.dumps(manifest).encode()
        if url.endswith(".sha256"):
            return f"{checksum}  EcoReplay-1.2.3.zip\n".encode()
        return b"tampered zip"

    monkeypatch.setattr(modio_publish, "_fetch_bytes", fetch)

    with pytest.raises(ValueError, match="SHA-256"):
        modio_publish.download_artifact(
            "https://github.com/coilyco/eco-app/releases/download/mods-abc123",
            package_name,
            version,
            "EcoReplay-1.2.3.zip",
        )


def test_upload_artifact_is_idempotent_for_matching_existing_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = b"zip bytes"
    artifact = modio_publish.PackageArtifact(
        archive_name="EcoReplay-1.2.3.zip",
        package_name="eco-replay",
        registry_version="1.2.3+abc123",
        source_revision="abc123",
        source_version="1.2.3",
        sha256=hashlib.sha256(archive).hexdigest(),
        zip_bytes=archive,
    )
    monkeypatch.setattr(
        modio_publish,
        "_modio_json",
        lambda *_args: {
            "data": [{"id": 42, "version": "1.2.3", "filehash": {"md5": artifact.md5}}]
        },
    )

    assert modio_publish.upload_artifact(artifact, "6", "7", "token", "notes") == (
        "already uploaded as mod.io file 42"
    )


def test_upload_artifact_rejects_same_version_with_other_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = modio_publish.PackageArtifact(
        archive_name="EcoReplay-1.2.3.zip",
        package_name="eco-replay",
        registry_version="1.2.3+abc123",
        source_revision="abc123",
        source_version="1.2.3",
        sha256="a" * 64,
        zip_bytes=b"zip bytes",
    )
    monkeypatch.setattr(
        modio_publish,
        "_modio_json",
        lambda *_args: {"data": [{"id": 42, "version": "1.2.3", "filehash": {"md5": "other"}}]},
    )

    with pytest.raises(RuntimeError, match="different archive checksum"):
        modio_publish.upload_artifact(artifact, "6", "7", "token", "notes")
