"""Safely seed a new extractor namespace from older command captures.

Command captures are presentation caches, never proof certificates.  The Lean
extractor accepts a seeded capture only after the freshly elaborated theorem
has the same name and proof-term fingerprint.  This module additionally
requires byte-identical source files before exposing old captures to that
check, so upgrading renderer/extractor code does not throw away hours of work.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Iterable

from proof_video.cache import write_json


MIGRATION_SCHEMA_VERSION = 1


def _source_sha256(path: Path) -> str:
    """Hash proof source while ignoring the 4.32 transport-only header."""

    source = path.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    injected = {"import SnapshotCertificate432", "import ProofLatex"}
    while lines and lines[0].strip() in injected:
        lines.pop(0)
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def _valid_capture(path: Path) -> bool:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return False
    return bool(
        value.get("schemaVersion") == 1
        and isinstance(value.get("theoremName"), str)
        and value.get("theoremName")
        and isinstance(value.get("proofFingerprint"), str)
        and value.get("proofFingerprint")
        and isinstance(value.get("movie"), dict)
    )


def _atomic_link_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(
        destination.suffix + f".{os.getpid()}.migrating"
    )
    temporary.unlink(missing_ok=True)
    try:
        os.link(source, temporary)
    except OSError:
        shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def seed_compatible_command_captures(
    *,
    source_file: Path,
    target_checkpoint_dir: Path,
    search_cache_roots: Iterable[Path],
) -> int:
    """Expose old atomic captures to the current fingerprint validator.

    The source equality check is an optimization guard. Correctness still
    comes from ``readHybridCommandCapture?`` in Lean, which rejects every
    capture whose freshly elaborated theorem name or proof fingerprint differs.
    """

    source_file = source_file.resolve()
    if not source_file.is_file():
        return 0
    source_hash = _source_sha256(source_file)
    target_checkpoint_dir = target_checkpoint_dir.resolve()
    destination_dir = target_checkpoint_dir / "command-captures"
    imported: list[dict[str, object]] = []
    seen_directories: set[Path] = set()

    for cache_root in search_cache_roots:
        chapter_root = cache_root.resolve() / "trace-chapters"
        if not chapter_root.is_dir():
            continue
        for candidate_dir in chapter_root.iterdir():
            candidate_dir = candidate_dir.resolve()
            if (
                candidate_dir == target_checkpoint_dir
                or candidate_dir in seen_directories
            ):
                continue
            seen_directories.add(candidate_dir)
            profile_path = candidate_dir / "command-profile.json"
            try:
                profile = json.loads(profile_path.read_text(encoding="utf-8"))
                old_source = Path(str(profile["sourceFile"])).resolve()
            except (OSError, UnicodeError, ValueError, KeyError, TypeError):
                continue
            if not old_source.is_file() or _source_sha256(old_source) != source_hash:
                continue
            captures = candidate_dir / "command-captures"
            if not captures.is_dir():
                continue
            for capture in captures.glob("*.json"):
                destination = destination_dir / capture.name
                if destination.exists() or not _valid_capture(capture):
                    continue
                _atomic_link_or_copy(capture, destination)
                imported.append(
                    {
                        "file": capture.name,
                        "bytes": capture.stat().st_size,
                        "sourceDirectory": str(candidate_dir),
                    }
                )

    if imported:
        write_json(
            target_checkpoint_dir / "capture-migration.json",
            {
                "schemaVersion": MIGRATION_SCHEMA_VERSION,
                "sourceFile": str(source_file),
                "sourceSha256": source_hash,
                "targetCheckpointDirectory": str(target_checkpoint_dir),
                "imported": imported,
                "validation": (
                    "candidate only; Lean must re-elaborate and match theoremName "
                    "plus proofFingerprint before reuse"
                ),
            },
        )
    return len(imported)
