from __future__ import annotations

import json
from pathlib import Path

import pytest

from proof_video.models import Movie
from proof_video.trace_store import (
    hydrate_hybrid_manifest,
    ingest_hybrid_manifest,
    is_embedded_hybrid_trace,
    is_hybrid_manifest,
    iter_hybrid_chapters,
    relativize_hybrid_manifest,
)


def test_trace_v4_manifest_remains_backward_compatible() -> None:
    assert is_hybrid_manifest({"schemaVersion": "4.0", "chapterRefs": []})
    assert is_embedded_hybrid_trace({"schemaVersion": "4.0", "chapters": []})


def _chapter(theorem: str, fingerprint: str) -> dict:
    return {
        "id": 0,
        "theoremName": theorem,
        "dependencies": [],
        "movie": {"theoremName": theorem, "startGoal": {"goalId": "g", "state": "⊢ True"}, "actions": [], "highlighting": []},
        "proofFingerprint": fingerprint,
        "axioms": [],
        "validation": {"valid": True, "kernelChecked": True, "noSorry": True, "errors": []},
        "isMain": True,
    }


def test_manifest_ingestion_is_content_addressed_and_hydrates(tmp_path) -> None:
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps(_chapter("Demo.main", "proof-1")), encoding="utf-8")
    manifest = {
        "schemaVersion": "3.1",
        "theoremName": "Demo.main",
        "chapterRefs": [
            {
                "id": 0,
                "theoremName": "Demo.main",
                "dependencies": [],
                "proofFingerprint": "proof-1",
                "validation": {"valid": True, "kernelChecked": True, "noSorry": True},
                "isMain": True,
                "objectPath": str(raw),
                "objectHash": "",
            }
        ],
        "validation": {"valid": True},
    }
    stored = ingest_hybrid_manifest(manifest, tmp_path / "objects")
    reference = stored["chapterRefs"][0]
    assert len(reference["objectHash"]) == 64
    assert (tmp_path / "objects" / f"{reference['objectHash']}.json").exists()
    hydrated = hydrate_hybrid_manifest(stored)
    assert hydrated["chapters"][0]["theoremName"] == "Demo.main"


def test_embedded_hybrid_trace_migrates_to_small_object_manifest(tmp_path) -> None:
    chapter = _chapter("Demo.main", "proof-1")
    embedded = {
        "schemaVersion": "3.0",
        "theoremName": "Demo.main",
        "source": "Lean.InfoTree",
        "granularity": "source-tactic/local-theorem-chapters",
        "chapters": [chapter],
        "validation": {"valid": True},
    }

    stored = ingest_hybrid_manifest(embedded, tmp_path / "objects")

    assert stored["schemaVersion"] == "3.1"
    assert "chapters" not in stored
    assert stored["chapterRefs"][0]["proofFingerprint"] == "proof-1"
    assert list(iter_hybrid_chapters(stored))[0] == chapter


def test_snapshot_trace_keeps_v4_when_content_addressed(tmp_path) -> None:
    embedded = {
        "schemaVersion": "4.0",
        "theoremName": "Demo.main",
        "chapters": [_chapter("Demo.main", "proof-4")],
        "validation": {"valid": True},
    }

    stored = ingest_hybrid_manifest(embedded, tmp_path / "objects-v4")

    assert stored["schemaVersion"] == "4.0"
    assert is_hybrid_manifest(stored)
    assert hydrate_hybrid_manifest(stored)["schemaVersion"] == "4.0"


def test_manifest_rejects_changed_object_content(tmp_path) -> None:
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps(_chapter("Demo.main", "proof-1")), encoding="utf-8")
    manifest = {
        "schemaVersion": "3.1",
        "theoremName": "Demo.main",
        "chapterRefs": [{"theoremName": "Demo.main", "proofFingerprint": "proof-1", "objectPath": str(raw)}],
        "validation": {"valid": True},
    }
    stored = ingest_hybrid_manifest(manifest, tmp_path / "objects")
    object_path = tmp_path / "objects" / f"{stored['chapterRefs'][0]['objectHash']}.json"
    object_path.write_text(json.dumps(_chapter("Demo.main", "proof-2")), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        hydrate_hybrid_manifest(stored)


def test_published_manifest_uses_portable_relative_object_paths(tmp_path) -> None:
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps(_chapter("Demo.main", "proof-1")), encoding="utf-8")
    manifest = {
        "schemaVersion": "3.1",
        "theoremName": "Demo.main",
        "chapterRefs": [
            {
                "theoremName": "Demo.main",
                "proofFingerprint": "proof-1",
                "objectPath": str(raw),
            }
        ],
        "validation": {"valid": True},
    }
    stored = ingest_hybrid_manifest(manifest, tmp_path / "video.trace" / "objects")
    portable = relativize_hybrid_manifest(stored, manifest_dir=tmp_path)
    reference = portable["chapterRefs"][0]
    assert not Path(reference["objectPath"]).is_absolute()
    hydrated = hydrate_hybrid_manifest(portable, base_dir=tmp_path)
    assert hydrated["chapters"][0]["proofFingerprint"] == "proof-1"

    movie = Movie.from_hybrid_chapters(
        portable,
        iter_hybrid_chapters(portable, base_dir=tmp_path),
    )
    assert movie.theorem_name == "Demo.main"
    assert "chapters" not in (movie.hybrid_trace or {})
