#!/usr/bin/env python3
"""Promote a checksum-verified GitHub Release mod asset to mod.io without rebuilding it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PackageArtifact:
    archive_name: str
    package_name: str
    registry_version: str
    source_revision: str
    source_version: str
    sha256: str
    zip_bytes: bytes

    @property
    def md5(self) -> str:
        return hashlib.md5(self.zip_bytes, usedforsecurity=False).hexdigest()


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _fetch_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def _release_base_url(repo: str, tag: str) -> str:
    """Public release assets download without a credential."""
    return f"https://github.com/{repo}/releases/download/{urllib.parse.quote(tag, safe='')}"


def _record_string(record: dict[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"package metadata is missing {key!r}")
    return value


def download_artifact(
    release_base: str,
    package_name: str,
    registry_version: str,
    archive_name: str,
) -> PackageArtifact:
    """Download one release package and require metadata + sidecar checksum agreement."""
    valid_archive_name = re.fullmatch(r"[A-Za-z0-9._+-]+\.zip", archive_name)
    if Path(archive_name).name != archive_name or not valid_archive_name:
        raise ValueError("MOD_PACKAGE_ARCHIVE must be a ZIP filename without a path")
    package_base = release_base
    # Every release publishes this manifest record next to its ZIP, so promotion
    # never needs a build or a republished artifact.
    metadata_name = f"{archive_name[:-4]}.json"
    metadata_url = f"{package_base}/{urllib.parse.quote(metadata_name, safe='')}"
    manifest = json.loads(_fetch_bytes(metadata_url).decode("utf-8"))
    if manifest.get("schema") != SCHEMA_VERSION:
        raise ValueError(f"unsupported package manifest schema: {manifest.get('schema')!r}")
    record = manifest.get("package")
    if not isinstance(record, dict):
        raise ValueError("package manifest has no package record")

    if _record_string(record, "package_name") != package_name:
        raise ValueError("package manifest package_name does not match the requested package")
    if _record_string(record, "registry_version") != registry_version:
        raise ValueError("package manifest registry_version does not match the requested version")
    if _record_string(record, "archive") != archive_name:
        raise ValueError("package manifest archive does not match the requested filename")
    checksum_name = _record_string(record, "checksum_file")
    declared_sha256 = _record_string(record, "checksum")
    valid_sha256 = len(declared_sha256) == 64 and all(
        char in "0123456789abcdef" for char in declared_sha256
    )
    if not valid_sha256:
        raise ValueError("package manifest has an invalid SHA-256 checksum")

    checksum_text = _fetch_bytes(
        f"{package_base}/{urllib.parse.quote(checksum_name, safe='')}"
    ).decode("utf-8")
    checksum_parts = checksum_text.strip().split(maxsplit=1)
    if len(checksum_parts) != 2 or checksum_parts[0] != declared_sha256:
        raise ValueError("package checksum sidecar does not match package metadata")
    if checksum_parts[1].lstrip("*") != archive_name:
        raise ValueError("package checksum sidecar names a different archive")

    zip_bytes = _fetch_bytes(f"{package_base}/{urllib.parse.quote(archive_name, safe='')}")
    observed_sha256 = hashlib.sha256(zip_bytes).hexdigest()
    if observed_sha256 != declared_sha256:
        raise ValueError("downloaded archive SHA-256 does not match its package manifest")

    return PackageArtifact(
        archive_name=archive_name,
        package_name=package_name,
        registry_version=registry_version,
        source_revision=_record_string(record, "source_revision"),
        source_version=_record_string(record, "mod_version"),
        sha256=declared_sha256,
        zip_bytes=zip_bytes,
    )


def _modio_url(game_id: str, mod_id: str, suffix: str = "") -> str:
    return (
        f"https://g-{urllib.parse.quote(game_id, safe='')}.modapi.io/v1/games/"
        f"{urllib.parse.quote(game_id, safe='')}/mods/"
        f"{urllib.parse.quote(mod_id, safe='')}/files{suffix}"
    )


def _modio_json(url: str, token: str) -> Any:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _matching_file(file: dict[str, Any], artifact: PackageArtifact) -> bool:
    return (
        file.get("version") == artifact.source_version
        and isinstance(file.get("filehash"), dict)
        and file["filehash"].get("md5") == artifact.md5
    )


def _existing_file(
    game_id: str,
    mod_id: str,
    token: str,
    artifact: PackageArtifact,
    expected_file_id: str | None,
) -> dict[str, Any] | None:
    if expected_file_id:
        candidate = _modio_json(
            _modio_url(game_id, mod_id, f"/{urllib.parse.quote(expected_file_id, safe='')}"), token
        )
        if not isinstance(candidate, dict) or not _matching_file(candidate, artifact):
            raise ValueError("MODIO_FILE_ID does not identify this version and checksum")
        return candidate

    query = urllib.parse.urlencode({"version": artifact.source_version, "limit": "100"})
    response = _modio_json(_modio_url(game_id, mod_id, f"?{query}"), token)
    files = response.get("data", []) if isinstance(response, dict) else []
    if not isinstance(files, list):
        raise RuntimeError("mod.io returned an invalid modfile list")
    same_version = [
        file
        for file in files
        if isinstance(file, dict) and file.get("version") == artifact.source_version
    ]
    if not same_version:
        return None
    matched = [file for file in same_version if _matching_file(file, artifact)]
    if matched:
        return matched[0]
    raise RuntimeError("mod.io already has this version with a different archive checksum")


def _multipart_body(artifact: PackageArtifact, changelog: str) -> tuple[str, bytes]:
    boundary = f"----eco-modio-{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    def field(name: str, value: str) -> None:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )

    field("version", artifact.source_version)
    field("changelog", changelog)
    field("active", "true")
    field("filehash", artifact.md5)
    field(
        "metadata_blob",
        f"release_package={artifact.package_name}@{artifact.registry_version};"
        f"source_revision={artifact.source_revision};sha256={artifact.sha256}",
    )
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            (
                'Content-Disposition: form-data; name="filedata"; '
                f'filename="{artifact.archive_name}"\r\n'
                "Content-Type: application/zip\r\n\r\n"
            ).encode(),
            artifact.zip_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return boundary, b"".join(chunks)


def upload_artifact(
    artifact: PackageArtifact,
    game_id: str,
    mod_id: str,
    token: str,
    changelog: str,
    expected_file_id: str | None = None,
) -> str:
    """Upload once, treating a matching prior version/checksum as a successful retry."""
    existing = _existing_file(game_id, mod_id, token, artifact, expected_file_id)
    if existing is not None:
        return f"already uploaded as mod.io file {existing.get('id', '<unknown>')}"

    boundary, body = _multipart_body(artifact, changelog)
    request = urllib.request.Request(
        _modio_url(game_id, mod_id),
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        if response.status != 201:
            raise RuntimeError(f"mod.io upload returned HTTP {response.status}")
        uploaded = json.loads(response.read().decode("utf-8"))
    if not isinstance(uploaded, dict):
        raise RuntimeError("mod.io upload returned an invalid response")
    return f"uploaded as mod.io file {uploaded.get('id', '<unknown>')}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-name", default=os.environ.get("MOD_PACKAGE_NAME"))
    parser.add_argument("--registry-version", default=os.environ.get("MOD_PACKAGE_VERSION"))
    parser.add_argument("--changelog", default=os.environ.get("MODIO_CHANGELOG", ""))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=os.environ.get("MODIO_DRY_RUN") == "true",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        package_name = args.package_name or _required_env("MOD_PACKAGE_NAME")
        registry_version = args.registry_version or _required_env("MOD_PACKAGE_VERSION")
        artifact = download_artifact(
            _release_base_url(_required_env("GITHUB_REPOSITORY"), _required_env("MOD_RELEASE_TAG")),
            package_name,
            registry_version,
            _required_env("MOD_PACKAGE_ARCHIVE"),
        )
        print(
            f"verified {artifact.archive_name}: sha256={artifact.sha256} "
            f"version={artifact.source_version} revision={artifact.source_revision}"
        )
        if args.dry_run:
            print("dry run: mod.io upload skipped")
            return 0
        result = upload_artifact(
            artifact,
            _required_env("MODIO_GAME_ID"),
            _required_env("MODIO_MOD_ID"),
            _required_env("MODIO_ACCESS_TOKEN"),
            args.changelog,
            os.environ.get("MODIO_FILE_ID") or None,
        )
        print(result)
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
        print(f"mod.io promotion: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
