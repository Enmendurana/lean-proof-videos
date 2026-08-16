import json
from pathlib import Path

from proof_video.incremental_snapshot import (
    commit_snapshot,
    snapshot_deps_path,
    validate_snapshot,
)


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    source = tmp_path / "Proof.lean"
    source.write_text("import Demo\n\ntheorem proof : True := by trivial\n", encoding="utf-8")
    manifest = tmp_path / "lake-manifest.json"
    manifest.write_text('{"version":"1"}', encoding="utf-8")
    dependency = tmp_path / "Demo.olean"
    dependency.write_bytes(b"olean")
    snapshot = tmp_path / "snapshots" / "full.incr"
    snapshot.parent.mkdir()
    snapshot.write_bytes(b"snapshot")
    snapshot_deps_path(snapshot).write_text(
        json.dumps([{"olean": str(dependency)}]), encoding="utf-8"
    )
    return source, manifest, dependency, snapshot


def _validate(snapshot: Path, source: Path, manifest: Path):
    return validate_snapshot(
        snapshot,
        source=source,
        lean_toolchain="leanprover/lean4:v4.32.1",
        mathlib_version="v4.32.1",
        lake_manifest=manifest,
        extractor_abi=4,
    )


def _commit(snapshot: Path, source: Path, manifest: Path) -> None:
    commit_snapshot(
        snapshot,
        source=source,
        lean_toolchain="leanprover/lean4:v4.32.1",
        mathlib_version="v4.32.1",
        lake_manifest=manifest,
        extractor_abi=4,
    )


def test_unchanged_snapshot_is_a_hit_and_late_source_edit_is_partial_reuse(
    tmp_path: Path,
) -> None:
    source, manifest, _dependency, snapshot = _fixture(tmp_path)
    _commit(snapshot, source, manifest)
    assert _validate(snapshot, source, manifest).status == "snapshot-hit"

    source.write_text(
        "import Demo\n\n-- late edit\ntheorem proof : True := by trivial\n",
        encoding="utf-8",
    )
    result = _validate(snapshot, source, manifest)
    assert result.valid is True
    assert result.status == "partial-reuse"


def test_import_or_olean_change_rejects_snapshot(tmp_path: Path) -> None:
    source, manifest, dependency, snapshot = _fixture(tmp_path)
    _commit(snapshot, source, manifest)

    source.write_text("import Other\n\ntheorem proof : True := by trivial\n", encoding="utf-8")
    assert _validate(snapshot, source, manifest).status == "environment-mismatch"

    source.write_text("import Demo\n\ntheorem proof : True := by trivial\n", encoding="utf-8")
    dependency.write_bytes(b"changed")
    assert _validate(snapshot, source, manifest).status == "environment-mismatch"


def test_corrupt_or_interrupted_snapshot_is_never_valid(tmp_path: Path) -> None:
    source, manifest, _dependency, snapshot = _fixture(tmp_path)
    assert _validate(snapshot, source, manifest).status == "missing"
    _commit(snapshot, source, manifest)
    snapshot.write_bytes(b"corrupt")
    assert _validate(snapshot, source, manifest).status == "snapshot-hash-mismatch"


def test_manifest_or_dependency_list_corruption_rejects_snapshot(tmp_path: Path) -> None:
    source, manifest, _dependency, snapshot = _fixture(tmp_path)
    _commit(snapshot, source, manifest)
    manifest.write_text('{"version":"2"}', encoding="utf-8")
    assert _validate(snapshot, source, manifest).status == "environment-mismatch"

    manifest.write_text('{"version":"1"}', encoding="utf-8")
    snapshot_deps_path(snapshot).write_text("not-json-and-not-a-path\n", encoding="utf-8")
    assert _validate(snapshot, source, manifest).status in {
        "environment-mismatch",
        "deps-hash-mismatch",
    }
