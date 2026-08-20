from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from proof_video.cli import _restore_windows_path
from proof_video.lean_export import export_trace
from proof_video.models import Movie
from proof_video.prooftrace import validate_trace
from proof_video.strict_audit import build_strict_audit


ROOT = Path(__file__).resolve().parents[1]
LEAN_FIXTURE = ROOT / "Input" / "ProofTraceHierarchyFixtures.lean"
THEOREM = "ProofTraceHierarchyFixtures.main"


@pytest.fixture(scope="module")
def hierarchy_export(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict, Path]:
    if shutil.which("lake") is None:
        pytest.skip("Lean integration fixture requires lake")
    _restore_windows_path()
    checkpoint_dir = tmp_path_factory.mktemp("prooftrace-chapters")
    trace = export_trace(
        ROOT,
        LEAN_FIXTURE,
        THEOREM,
        "proof-term",
        checkpoint_dir=checkpoint_dir,
    )
    return trace, checkpoint_dir


@pytest.fixture(scope="module")
def hierarchy_trace(hierarchy_export: tuple[dict, Path]) -> dict:
    return hierarchy_export[0]


def test_local_proofs_are_topological_chapters(hierarchy_trace: dict) -> None:
    chapters = hierarchy_trace["chapters"]
    names = [chapter["theoremName"].rsplit(".", 1)[-1] for chapter in chapters]
    assert set(names) == {"seed", "pair", "swap", "main"}
    assert names.index("seed") < names.index("pair") < names.index("main")
    assert names.index("swap") < names.index("main")
    assert [chapter["isMain"] for chapter in chapters].count(True) == 1
    assert chapters[-1]["isMain"] is True
    assert chapters[-1]["finalStepId"] == hierarchy_trace["finalStepId"]
    assert hierarchy_trace["validation"]["valid"] is True


def test_each_local_proof_is_emitted_once_and_linked(hierarchy_trace: dict) -> None:
    chapters = hierarchy_trace["chapters"]
    steps = hierarchy_trace["steps"]
    assert [step["id"] for step in steps] == list(range(len(steps)))

    final_by_name = {
        chapter["theoremName"]: chapter["finalStepId"] for chapter in chapters
    }
    for theorem_name in final_by_name:
        uses = [
            step
            for step in steps
            if step.get("theoremName") == theorem_name
            and step["id"] != final_by_name[theorem_name]
        ]
        for use in uses:
            assert final_by_name[theorem_name] in use["premises"]

    semantic_ids = [
        node["id"] for step in steps for node in step.get("semanticNodes", ())
    ]
    assert len(semantic_ids) == len(set(semantic_ids))
    semantic_paths = [
        node["path"] for step in steps for node in step.get("semanticNodes", ())
    ]
    assert semantic_paths
    assert all(not path.startswith("chapter-") for path in semantic_paths)


def test_hierarchy_builds_a_strict_renderer_timeline(hierarchy_trace: dict) -> None:
    movie = Movie.from_json(hierarchy_trace)
    assert movie.proof_trace is not None
    assert validate_trace(movie.proof_trace).valid
    audit = build_strict_audit(movie)
    assert audit["valid"], audit["errors"][:8]
    assert len(movie.proof_trace.chapters) == 4


def test_each_chapter_is_persisted_atomically(
    hierarchy_export: tuple[dict, Path],
) -> None:
    trace, checkpoint_dir = hierarchy_export
    checkpoints = sorted(checkpoint_dir.glob("chapter-*.json"))
    assert len(checkpoints) == len(trace["chapters"])
    assert not list(checkpoint_dir.glob("*.tmp"))
