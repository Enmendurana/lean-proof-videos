from pathlib import Path

import pytest

from proof_video.studio.sources import SourceConflictError, SourceManager
from proof_video.studio.store import StudioStore


LEAN = """import Mathlib\n\ntheorem demo : True := by\n  trivial\n"""


def test_source_revisions_are_content_addressed_and_restorable(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    entry = root / "Demo.lean"
    entry.write_text(LEAN, encoding="utf-8")
    store = StudioStore(root / ".lean-proof-video-web")
    sources = SourceManager(root, store)
    project = sources.create_project("Demo.lean")
    original = sources.read_project_source(project["id"])

    updated = sources.save_source(
        project["id"], LEAN.replace("trivial", "simp"), original["sha256"]
    )
    assert updated["sha256"] != original["sha256"]
    assert len(store.revisions(project["id"])) == 2

    restored = sources.restore_revision(
        project["id"], store.revisions(project["id"])[-1]["id"], updated["sha256"]
    )
    assert restored["sha256"] == original["sha256"]
    assert entry.read_text(encoding="utf-8") == LEAN


def test_source_save_rejects_external_change_and_path_escape(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    entry = root / "Demo.lean"
    entry.write_text(LEAN, encoding="utf-8")
    store = StudioStore(root / ".lean-proof-video-web")
    sources = SourceManager(root, store)
    project = sources.create_project("Demo.lean")
    opened = sources.read_project_source(project["id"])
    entry.write_text(LEAN + "\n-- external\n", encoding="utf-8")
    with pytest.raises(SourceConflictError):
        sources.save_source(project["id"], LEAN, opened["sha256"])
    with pytest.raises(ValueError):
        sources.resolve_entry("../Outside.lean")


def test_artifact_registry_never_exposes_job_snapshots(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "Demo.lean").write_text(LEAN, encoding="utf-8")
    store = StudioStore(root / ".lean-proof-video-web")
    sources = SourceManager(root, store)
    project = sources.create_project("Demo.lean")
    revision = store.revisions(project["id"])[0]
    job = store.create_job(project["id"], revision["id"], "validate", {}, root / "x")
    job_root = store.jobs_root / job["id"]
    (job_root / "result.mp4").write_bytes(b"video")
    snapshot = job_root / "snapshot" / "Demo.lean"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(LEAN, encoding="utf-8")
    rows = store.sync_artifacts(job["id"])
    assert [row["name"] for row in rows] == ["result.mp4"]


def test_jobs_share_content_addressed_execution_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    entry = root / "Input" / "Demo.lean"
    entry.parent.mkdir(parents=True)
    entry.write_text(LEAN, encoding="utf-8")
    store = StudioStore(root / ".lean-proof-video-web")
    sources = SourceManager(root, store)
    project = sources.create_project("Input/Demo.lean")
    revision = store.revisions(project["id"])[0]

    first = sources.snapshot_for_job("job-one", revision["id"])
    second = sources.snapshot_for_job("job-two", revision["id"])

    assert first == second
    assert revision["sha256"] in first.parts
    assert first.read_text(encoding="utf-8") == LEAN
    assert (store.jobs_root / "job-one" / "snapshot" / "Input" / "Demo.lean").is_file()
    assert (store.jobs_root / "job-two" / "snapshot" / "Input" / "Demo.lean").is_file()
