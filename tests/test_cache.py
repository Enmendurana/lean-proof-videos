from pathlib import Path

import pytest

from proof_video.cache import (
    lean_checkpoint_key,
    lean_evidence_identity,
    lean_trace_key,
    stable_hash,
)
from proof_video.evidence_cache import read_trace_evidence, write_trace_evidence
from proof_video.models import (
    Frame,
    Goal,
    SemanticExpression,
    SemanticExpressionNode,
    SemanticSpan,
    SemanticTransition,
    SemanticTransitionEdge,
)
from proof_video.render import (
    _full_key,
    _opengl_safe_for_frame,
    _preview_indices,
    effective_write_speed,
)
from proof_video.rendering.planning import _chunk_key, _segment_key


def test_stable_hash_is_order_independent_for_mapping_keys() -> None:
    assert stable_hash({"a": 1, "b": 2}) == stable_hash({"b": 2, "a": 1})


def test_trace_key_changes_when_local_lean_source_changes(tmp_path: Path) -> None:
    (tmp_path / "lean-toolchain").write_text("leanprover/lean4:v4.26.0")
    proof = tmp_path / "Proof.lean"
    proof.write_text("theorem demo : True := by trivial")
    first = lean_trace_key(tmp_path, proof, "demo")
    proof.write_text("theorem demo : True := by exact True.intro")
    second = lean_trace_key(tmp_path, proof, "demo")
    assert first != second


def test_trace_key_ignores_unrelated_local_lean_source(tmp_path: Path) -> None:
    (tmp_path / "lean-toolchain").write_text("leanprover/lean4:v4.28.0")
    proof = tmp_path / "Proof.lean"
    proof.write_text("theorem demo : True := by trivial")
    unrelated = tmp_path / "Unrelated.lean"
    unrelated.write_text("theorem other : True := by trivial")
    first = lean_trace_key(tmp_path, proof, "demo")
    unrelated.write_text("theorem other : False := by sorry")
    assert lean_trace_key(tmp_path, proof, "demo") == first


def test_checkpoint_namespace_survives_input_edits(tmp_path: Path) -> None:
    (tmp_path / "lean-toolchain").write_text("leanprover/lean4:v4.28.0")
    proof = tmp_path / "Proof.lean"
    proof.write_text("theorem demo : True := by trivial")
    first = lean_checkpoint_key(tmp_path, proof, "demo")
    proof.write_text("theorem demo : True := by exact True.intro")
    assert lean_checkpoint_key(tmp_path, proof, "demo") == first


def test_evidence_key_survives_extractor_and_renderer_changes(tmp_path: Path) -> None:
    (tmp_path / "lean-toolchain").write_text("leanprover/lean4:v4.28.0")
    proof = tmp_path / "Proof.lean"
    proof.write_text("theorem demo : True := by trivial")
    extractor = tmp_path / "Animate.lean"
    extractor.write_text("def implementationVersion := 1")

    first = lean_evidence_identity(tmp_path, proof, "demo", "hybrid")
    legacy_first = lean_trace_key(tmp_path, proof, "demo:hybrid")
    extractor.write_text("def implementationVersion := 2")

    assert lean_evidence_identity(tmp_path, proof, "demo", "hybrid") == first
    assert lean_trace_key(tmp_path, proof, "demo:hybrid") != legacy_first


def test_evidence_key_changes_with_source_toolchain_or_mode(tmp_path: Path) -> None:
    toolchain = tmp_path / "lean-toolchain"
    toolchain.write_text("leanprover/lean4:v4.28.0")
    proof = tmp_path / "Proof.lean"
    proof.write_text("theorem demo : True := by trivial")
    first = lean_evidence_identity(tmp_path, proof, "demo", "hybrid")

    proof.write_text("theorem demo : True := by exact True.intro")
    assert lean_evidence_identity(tmp_path, proof, "demo", "hybrid") != first
    proof.write_text("theorem demo : True := by trivial")
    assert lean_evidence_identity(tmp_path, proof, "demo", "proof-term") != first
    toolchain.write_text("leanprover/lean4:v4.29.0")
    assert lean_evidence_identity(tmp_path, proof, "demo", "hybrid") != first


def test_only_proof_term_evidence_declares_the_certified_trace_contract(
    tmp_path: Path,
) -> None:
    (tmp_path / "lean-toolchain").write_text("leanprover/lean4:v4.28.0")
    proof = tmp_path / "Proof.lean"
    proof.write_text("theorem demo : True := by trivial")

    proof_term = lean_evidence_identity(tmp_path, proof, "demo", "proof-term")
    hybrid = lean_evidence_identity(tmp_path, proof, "demo", "hybrid")

    assert proof_term["proofTraceContract"].startswith("proof-trace-2.3-")
    assert "proofTraceContract" not in hybrid


def test_evidence_key_ignores_comment_only_edits(tmp_path: Path) -> None:
    (tmp_path / "lean-toolchain").write_text("leanprover/lean4:v4.28.0")
    proof = tmp_path / "Proof.lean"
    proof.write_text(
        "-- first comment\ntheorem demo : True := by\n  trivial -- tail\n",
        encoding="utf-8",
    )
    first = lean_evidence_identity(tmp_path, proof, "demo", "hybrid")
    proof.write_text(
        "/- a different and longer comment -/\n"
        "theorem demo : True := by\n  trivial -- changed tail\n",
        encoding="utf-8",
    )
    assert lean_evidence_identity(tmp_path, proof, "demo", "hybrid") == first


def test_persistent_evidence_round_trip_and_corruption_guard(tmp_path: Path) -> None:
    identity = {
        "schemaVersion": 1,
        "key": "evidence-key",
        "theorem": "demo",
        "traceMode": "hybrid",
        "sourceDigest": "source",
        "toolchainDigest": "toolchain",
    }
    path = tmp_path / "evidence" / "trace.json"
    trace = {"schemaVersion": "3.1", "theoremName": "demo", "chapterRefs": []}
    write_trace_evidence(path, trace, identity)

    assert read_trace_evidence(path, identity) == trace
    assert read_trace_evidence(path, {**identity, "sourceDigest": "changed"}) is None
    path.write_text('{"theoremName":"demo","changed":true}', encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt persistent Lean evidence"):
        read_trace_evidence(path, identity)


def test_preview_selects_representative_transitions() -> None:
    assert _preview_indices(10) == (0, 5, 9)
    assert _preview_indices(1) == (0,)


def test_auto_opengl_guard_rejects_dense_simultaneous_goals() -> None:
    simple = Frame(index=0, tactic="", goals=(Goal("g", "", latex_target="A"),))
    dense = Frame(
        index=1,
        tactic="split",
        goals=(
            Goal("g1", "", latex_target="A" * 190),
            Goal("g2", "", latex_target="B" * 190),
        ),
    )
    assert _opengl_safe_for_frame(simple)
    assert not _opengl_safe_for_frame(dense)


def test_full_render_key_covers_the_complete_movie() -> None:
    first = Frame(index=0, tactic="intro", goals=(Goal("g", "", latex_target="A"),))
    changed = Frame(index=0, tactic="intro", goals=(Goal("g", "", latex_target="B"),))

    options = (24.0, 0.65, 1920, 1080, 30, "cairo")
    assert _full_key((first,), *options) != _full_key((changed,), *options)


def test_render_keys_do_not_depend_on_offline_sympy_proposals(monkeypatch) -> None:
    captured: list[tuple[str, ...]] = []

    def record_digest(paths) -> str:
        captured.append(tuple(path.name for path in paths))
        return "renderer-digest"

    monkeypatch.setattr("proof_video.rendering.planning.file_digest", record_digest)
    frame = Frame(index=0, tactic="intro", goals=(Goal("g", "", latex_target="A"),))
    frames = (frame,)
    _segment_key(frames, 0, 24.0, 0.65, 1920, 1080, 30, "cairo")
    _chunk_key(frames, 0, 1, 24.0, 0.65, 1920, 1080, 30)
    _full_key(frames, 24.0, 0.65, 1920, 1080, 30, "cairo")

    assert len(captured) == 3
    assert all("sympy_matching.py" not in paths for paths in captured)


def test_full_render_key_changes_with_semantic_identity() -> None:
    def goal(target_id: str) -> Goal:
        transition = SemanticTransition(
            source=SemanticExpression(
                (SemanticExpressionNode("source", latex_spans=(SemanticSpan(0, 1),)),)
            ),
            target=SemanticExpression(
                (SemanticExpressionNode(target_id, latex_spans=(SemanticSpan(0, 1),)),)
            ),
            edges=(SemanticTransitionEdge("source", target_id, "same-fvar", 1.0),),
        )
        return Goal("g", "", latex_target="f", semantic_transition=transition)

    options = (24.0, 0.65, 1920, 1080, 30, "cairo")
    first = Frame(index=0, tactic="rw", goals=(goal("target-a"),))
    changed = Frame(index=0, tactic="rw", goals=(goal("target-b"),))
    assert _full_key((first,), *options) != _full_key((changed,), *options)


def test_duration_limit_accelerates_typing_not_transitions() -> None:
    frames = tuple(
        Frame(
            index=index,
            tactic="",
            goals=(
                Goal(f"g{index}", "", latex_target="A" * 200, lineage_id=f"l{index}"),
            ),
        )
        for index in range(3)
    )
    speed = effective_write_speed(
        frames,
        requested=1.0,
        max_duration=30.0,
        transition_seconds=0.65,
        fps=30,
    )
    assert 1.0 < speed <= 30.0

    with pytest.raises(SystemExit, match="visible-density ceiling"):
        effective_write_speed(
            frames,
            requested=1.0,
            max_duration=6.0,
            transition_seconds=0.65,
            fps=30,
        )

    with pytest.raises(SystemExit, match="fixed camera/formula transitions"):
        effective_write_speed(
            frames * 20,
            requested=100.0,
            max_duration=5.0,
            transition_seconds=0.65,
            fps=30,
        )


def test_unlimited_duration_preserves_requested_typing_speed() -> None:
    frames = tuple(
        Frame(
            index=index,
            tactic="",
            goals=(Goal(f"g{index}", "", latex_target="A" * 500),),
        )
        for index in range(20)
    )

    assert (
        effective_write_speed(
            frames,
            requested=7.5,
            max_duration=None,
            transition_seconds=0.65,
            fps=30,
        )
        == 7.5
    )

    assert (
        effective_write_speed(
            frames,
            requested=1000.0,
            max_duration=None,
            transition_seconds=0.65,
            fps=30,
        )
        == 180.0
    )
