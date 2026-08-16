import json
import os
from pathlib import Path

from proof_video.checkpoint_migration import seed_compatible_command_captures


def _write_candidate(root: Path, source: Path, *, fingerprint: str = "proof") -> Path:
    checkpoint = root / "trace-chapters" / "old"
    checkpoint.mkdir(parents=True)
    (checkpoint / "command-profile.json").write_text(
        json.dumps({"schemaVersion": 1, "sourceFile": str(source)}),
        encoding="utf-8",
    )
    captures = checkpoint / "command-captures"
    captures.mkdir()
    capture = captures / "123.json"
    capture.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "theoremName": "Demo.helper",
                "proofFingerprint": fingerprint,
                "movie": {"theoremName": "Demo.helper", "actions": []},
            }
        ),
        encoding="utf-8",
    )
    return capture


def test_identical_source_seeds_atomic_fingerprint_candidates(tmp_path: Path) -> None:
    source = tmp_path / "Proof.lean"
    source.write_text("theorem demo : True := by trivial\n", encoding="utf-8")
    cache = tmp_path / "cache"
    original = _write_candidate(cache, source)
    target = cache / "trace-chapters" / "new"

    count = seed_compatible_command_captures(
        source_file=source,
        target_checkpoint_dir=target,
        search_cache_roots=[cache],
    )

    migrated = target / "command-captures" / original.name
    assert count == 1
    assert migrated.read_bytes() == original.read_bytes()
    assert (target / "capture-migration.json").is_file()
    if os.name == "nt":
        assert migrated.stat().st_ino == original.stat().st_ino


def test_changed_source_does_not_seed_old_capture(tmp_path: Path) -> None:
    old_source = tmp_path / "Old.lean"
    old_source.write_text("theorem demo : True := by trivial\n", encoding="utf-8")
    new_source = tmp_path / "New.lean"
    new_source.write_text("theorem demo : 1 = 1 := by rfl\n", encoding="utf-8")
    cache = tmp_path / "cache"
    _write_candidate(cache, old_source)

    count = seed_compatible_command_captures(
        source_file=new_source,
        target_checkpoint_dir=cache / "trace-chapters" / "new",
        search_cache_roots=[cache],
    )

    assert count == 0


def test_lean_432_transport_header_does_not_discard_candidates(tmp_path: Path) -> None:
    old_source = tmp_path / "Old.lean"
    old_source.write_text("theorem demo : True := by trivial\n", encoding="utf-8")
    mirrored = tmp_path / "workspace" / "Old.lean"
    mirrored.parent.mkdir()
    mirrored.write_text(
        "import SnapshotCertificate432\n"
        "import ProofLatex\n"
        "theorem demo : True := by trivial\n",
        encoding="utf-8",
    )
    cache = tmp_path / "cache"
    _write_candidate(cache, old_source)

    count = seed_compatible_command_captures(
        source_file=mirrored,
        target_checkpoint_dir=cache / "trace-chapters" / "new",
        search_cache_roots=[cache],
    )

    assert count == 1
