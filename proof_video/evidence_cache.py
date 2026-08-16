"""Durable, architecture-independent storage for checked Lean traces."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from proof_video.cache import read_json, write_json


EVIDENCE_ENVELOPE_VERSION = 1


def evidence_trace_path(cache_root: Path, evidence_key: str) -> Path:
    return cache_root / "lean-evidence" / f"{evidence_key}.json"


def _metadata_path(trace_path: Path) -> Path:
    return trace_path.with_suffix(".meta.json")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_trace_evidence(
    trace_path: Path,
    trace: dict[str, Any],
    identity: dict[str, Any],
) -> None:
    """Commit trace first and its validating envelope last.

    A crash between the two writes leaves an uncommitted trace that is ignored
    on the next run. Both individual JSON writes are atomic.
    """

    write_json(trace_path, trace)
    write_json(
        _metadata_path(trace_path),
        {
            "schemaVersion": EVIDENCE_ENVELOPE_VERSION,
            "kind": "kernel-checked-lean-trace",
            "identity": identity,
            "traceSha256": _file_sha256(trace_path),
            "traceSchemaVersion": str(trace.get("schemaVersion", "legacy")),
        },
    )


def read_trace_evidence(
    trace_path: Path,
    expected_identity: dict[str, Any],
) -> dict[str, Any] | None:
    """Return compatible evidence, rejecting stale or corrupt artifacts."""

    metadata_path = _metadata_path(trace_path)
    if not trace_path.exists() or not metadata_path.exists():
        return None
    metadata = read_json(metadata_path)
    if metadata.get("schemaVersion") != EVIDENCE_ENVELOPE_VERSION:
        return None
    if metadata.get("kind") != "kernel-checked-lean-trace":
        return None
    if metadata.get("identity") != expected_identity:
        return None
    expected_hash = str(metadata.get("traceSha256", ""))
    if not expected_hash or _file_sha256(trace_path) != expected_hash:
        raise ValueError(f"corrupt persistent Lean evidence: {trace_path}")
    trace = read_json(trace_path)
    if str(trace.get("theoremName", "")) != str(expected_identity["theorem"]):
        raise ValueError(f"Lean evidence theorem mismatch: {trace_path}")
    return trace
