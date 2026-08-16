"""Small atomic integrity envelopes for reusable binary artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from proof_video.cache import read_json, write_json


ARTIFACT_ENVELOPE_VERSION = 1


def artifact_metadata_path(artifact_path: Path) -> Path:
    return artifact_path.with_suffix(artifact_path.suffix + ".proof-video.json")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact_is_current(
    artifact_path: Path,
    *,
    kind: str,
    expected_identity: dict[str, Any],
) -> bool:
    metadata_path = artifact_metadata_path(artifact_path)
    if not artifact_path.is_file() or not metadata_path.is_file():
        return False
    try:
        metadata = read_json(metadata_path)
    except (OSError, UnicodeError, ValueError):
        return False
    if metadata.get("schemaVersion") != ARTIFACT_ENVELOPE_VERSION:
        return False
    if metadata.get("kind") != kind:
        return False
    if metadata.get("identity") != expected_identity:
        return False
    expected_hash = str(metadata.get("artifactSha256", ""))
    return bool(expected_hash) and file_sha256(artifact_path) == expected_hash


def record_artifact(
    artifact_path: Path,
    *,
    kind: str,
    identity: dict[str, Any],
) -> Path:
    if not artifact_path.is_file():
        raise FileNotFoundError(artifact_path)
    metadata_path = artifact_metadata_path(artifact_path)
    write_json(
        metadata_path,
        {
            "schemaVersion": ARTIFACT_ENVELOPE_VERSION,
            "kind": kind,
            "identity": identity,
            "artifactSha256": file_sha256(artifact_path),
        },
    )
    return metadata_path
