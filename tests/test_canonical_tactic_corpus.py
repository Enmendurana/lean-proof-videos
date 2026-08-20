from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterable
from pathlib import Path

import pytest

from proof_video.cli import _restore_windows_path
from proof_video.lean_export import export_trace
from proof_video.models import Movie
from proof_video.presentation.model import VisualPrimitiveKind
from proof_video.proof.correspondence import (
    EntityKind,
    EntityRef,
    validate_correspondence,
)
from proof_video.proof.effects import GoalEffectKind, apply_transition


ROOT = Path(__file__).resolve().parents[1]
LEAN_FIXTURE = ROOT / "Input" / "CanonicalTacticCorpus.lean"
THEOREM = "CanonicalTacticCorpus.canonicalStateCorpus"
TRACE_ARTIFACT = ROOT / ".pytest_cache" / "canonical-tactic-corpus-trace.json"
HYPEREDGE_FIXTURE = ROOT / "Input" / "CanonicalHyperedgeSmoke.lean"
HYPEREDGE_THEOREM = "CanonicalHyperedgeSmoke.splitBinder"
HYPEREDGE_TRACE_ARTIFACT = (
    ROOT / ".pytest_cache" / "canonical-hyperedge-smoke-trace.json"
)


@pytest.fixture(scope="module")
def canonical_tactic_trace() -> dict:
    supplied_trace = os.environ.get("CANONICAL_TACTIC_CORPUS_TRACE")
    if supplied_trace:
        return json.loads(Path(supplied_trace).read_text(encoding="utf-8"))
    if shutil.which("lake") is None:
        pytest.skip("Lean integration fixture requires lake")
    _restore_windows_path()
    trace = export_trace(ROOT, LEAN_FIXTURE, THEOREM, "tactic")
    TRACE_ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    pending = TRACE_ARTIFACT.with_suffix(".tmp")
    pending.write_text(json.dumps(trace, ensure_ascii=False), encoding="utf-8")
    pending.replace(TRACE_ARTIFACT)
    return trace


@pytest.fixture(scope="module")
def canonical_tactic_movie(canonical_tactic_trace: dict) -> Movie:
    return Movie.from_json(canonical_tactic_trace)


@pytest.fixture(scope="module")
def canonical_hyperedge_trace() -> dict:
    supplied_trace = os.environ.get("CANONICAL_HYPEREDGE_TRACE")
    if supplied_trace:
        return json.loads(Path(supplied_trace).read_text(encoding="utf-8"))
    if shutil.which("lake") is None:
        pytest.skip("Lean integration fixture requires lake")
    _restore_windows_path()
    trace = export_trace(ROOT, HYPEREDGE_FIXTURE, HYPEREDGE_THEOREM, "tactic")
    HYPEREDGE_TRACE_ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    pending = HYPEREDGE_TRACE_ARTIFACT.with_suffix(".tmp")
    pending.write_text(json.dumps(trace, ensure_ascii=False), encoding="utf-8")
    pending.replace(HYPEREDGE_TRACE_ARTIFACT)
    return trace


def _all_goals(trace: dict) -> Iterable[dict]:
    yield trace["startGoal"]
    for action in trace["actions"]:
        yield from action.get("beforeState", ())
        yield from action.get("afterState", ())
        for goal_action in action["goalActions"]:
            for result in goal_action["results"]:
                yield result["goal"]


def _canonical_frontier(goals: Iterable[dict]) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            goal["goalId"],
            tuple(goal.get("canonicalLocals", ())),
            goal.get("canonicalTarget"),
        )
        for goal in goals
    )


def _frames_containing(movie: Movie, fragment: str):
    return tuple(frame for frame in movie.frames if fragment in frame.tactic)


def _primitive_entities(frame, kind: VisualPrimitiveKind):
    plan = frame.visual_plan
    assert plan is not None
    for primitive in plan.primitives_of_kind(kind):
        sources = tuple(
            anchor.entity
            for anchor_id in primitive.source_anchor_ids
            if (anchor := plan.anchor(anchor_id)) is not None
        )
        targets = tuple(
            anchor.entity
            for anchor_id in primitive.target_anchor_ids
            if (anchor := plan.anchor(anchor_id)) is not None
        )
        yield primitive, sources, targets


def _is_local_entity(ref: EntityRef) -> bool:
    return ref.kind in {
        EntityKind.LOCAL,
        EntityKind.LOCAL_TYPE,
        EntityKind.LOCAL_VALUE,
    } or (
        ref.kind is EntityKind.OCCURRENCE
        and ref.expression_role in {"local-type", "local-value"}
    )


def _is_target_occurrence(ref: EntityRef) -> bool:
    return ref.kind is EntityKind.OCCURRENCE and ref.expression_role == "target"


# This table is only a coverage inventory for the real Lean fixture.  It is
# deliberately not used to select a diff adapter or to interpret a transition.
# State semantics below are checked exclusively through canonical diff/replay.
TACTIC_COVERAGE = {
    "intro": ("intro n",),
    "intros": ("intros n m",),
    "rintro": ("rintro ⟨n, hn⟩",),
    "revert": ("revert n",),
    "have": ("have h : a = a",),
    "let": ("let k : Nat := a + 1",),
    "set": ("set k := a + 1 with hk",),
    "replace": ("replace h := h.1",),
    "specialize": ("specialize h 3",),
    "clear": ("clear m",),
    "clear_value": ("clear_value k",),
    "subst": ("subst b",),
    "generalize": ("generalize h : a + b = n",),
    "rw": ("rw [hab]",),
    "simp": ("simp only [Nat.add_zero]",),
    "dsimp": ("dsimp [twice]",),
    "unfold": ("unfold twice",),
    "change": ("change a + a = a + a",),
    "show": ("show a + a = a + a",),
    "symm": ("symm",),
    "apply": ("apply Eq.trans hab",),
    "refine": ("refine Eq.trans hab ?_",),
    "exact": ("exact hab",),
    "assumption": ("assumption",),
    "rfl": ("rfl",),
    "constructor": ("constructor",),
    "left": ("left",),
    "right": ("right",),
    "use": ("use a",),
    "exists": ("exists a",),
    "trans": ("trans b",),
    "congr": ("congr 1",),
    "cases": ("cases h with", "cases h"),
    "rcases": ("rcases h with ⟨hp', hq'⟩",),
    "obtain": ("obtain ⟨n, hn⟩ := hExists",),
    "induction": ("induction n with",),
    "by_cases": ("by_cases h : p",),
    "case": ("case inl hp'",),
    "swap": ("swap",),
    "rotate_left": ("rotate_left",),
    "rotate_right": ("rotate_right",),
}

# These parser combinators select or map child tactics, but Lean 4.28 does not
# create a distinct `TacticInfo` node for the wrapper itself.  The trace
# therefore observes the child `trivial` actions and their real frontiers.  We
# keep their source presence and exact child sequence under regression without
# pretending that a nonexistent wrapper action was exported.
STRUCTURAL_WRAPPERS = {
    "next": "next => trivial",
    "focus": "focus trivial",
    "all_goals": "all_goals trivial",
}


def test_real_lean_trace_covers_the_supported_tactic_families(
    canonical_tactic_trace: dict,
) -> None:
    observed = tuple(
        " ".join(str(action.get("tacticText", "")).split())
        for action in canonical_tactic_trace["actions"]
    )
    missing = {
        label: alternatives
        for label, alternatives in TACTIC_COVERAGE.items()
        if not any(
            any(fragment in action for fragment in alternatives) for action in observed
        )
    }
    assert not missing, (
        "the real Lean InfoTree did not expose these fixture tactics: "
        f"{missing}; observed={observed}"
    )


def test_non_emitting_structural_wrappers_have_observed_child_actions(
    canonical_tactic_trace: dict,
) -> None:
    source = LEAN_FIXTURE.read_text(encoding="utf-8")
    observed = tuple(
        " ".join(str(action.get("tacticText", "")).split())
        for action in canonical_tactic_trace["actions"]
    )
    assert all(fragment in source for fragment in STRUCTURAL_WRAPPERS.values())
    assert not any(
        fragment in action
        for fragment in STRUCTURAL_WRAPPERS.values()
        for action in observed
    )

    for label in ("tNext", "tFocus", "tAllGoals"):
        start = next(
            index
            for index, action in enumerate(observed)
            if action.startswith(f"have {label} ")
        )
        end = next(
            index
            for index in range(start + 1, len(observed))
            if observed[index].startswith("have t")
        )
        assert observed[start + 1 : end] == ("constructor", "trivial", "trivial")


def test_native_canonical_correspondence_exports_intro_and_revert_binders(
    canonical_tactic_trace: dict,
) -> None:
    edges = tuple(
        edge
        for action in canonical_tactic_trace["actions"]
        for edge in action.get("canonicalCorrespondence", ())
    )
    assert edges

    assert any(
        edge.get("provenance") == "lean-defeq"
        and "forall-binder-introduced-as-fvar" in edge.get("evidence", ())
        and len(edge.get("sources", ())) == len(edge.get("targets", ())) == 1
        and edge["sources"][0].get("kind") == "occurrence"
        and edge["sources"][0].get("expressionRole") == "target"
        and edge["targets"][0].get("kind") == "local"
        for edge in edges
    )
    assert any(
        edge.get("provenance") == "lean-defeq"
        and "fvar-reverted-as-forall-binder" in edge.get("evidence", ())
        and len(edge.get("sources", ())) == len(edge.get("targets", ())) == 1
        and edge["sources"][0].get("kind") == "local"
        and edge["targets"][0].get("kind") == "occurrence"
        and edge["targets"][0].get("expressionRole") == "target"
        for edge in edges
    )


def test_native_canonical_correspondence_copies_one_binder_to_all_split_goals(
    canonical_hyperedge_trace: dict,
) -> None:
    action = next(
        action
        for action in canonical_hyperedge_trace["actions"]
        if "refine fun n" in " ".join(str(action.get("tacticText", "")).split())
    )
    before_ids = tuple(goal["goalId"] for goal in action["beforeState"])
    after_ids = tuple(goal["goalId"] for goal in action["afterState"])
    assert len(before_ids) == 1
    assert len(after_ids) == 2
    assert any(
        tuple(lineage["sourceGoalIds"]) == before_ids
        and tuple(lineage["targetGoalIds"]) == after_ids
        and lineage["relation"] == "split"
        for lineage in action["goalLineage"]
    )

    edges = tuple(action.get("canonicalCorrespondence", ()))
    binder_edges = tuple(
        edge
        for edge in edges
        if "forall-binder-introduced-as-fvar" in edge.get("evidence", ())
    )
    assert len(binder_edges) == 1
    binder_edge = binder_edges[0]
    assert binder_edge["relation"] == "copy"
    assert binder_edge["provenance"] == "lean-defeq"
    assert len(binder_edge["sources"]) == 1
    assert binder_edge["sources"][0] == {
        "kind": "occurrence",
        "goalId": before_ids[0],
        "localId": "",
        "expressionRole": "target",
        "occurrenceId": "target/0/binder",
    }
    assert {target["goalId"] for target in binder_edge["targets"]} == set(after_ids)
    assert {target["kind"] for target in binder_edge["targets"]} == {"local"}
    copied_local_ids = {target["localId"] for target in binder_edge["targets"]}
    assert len(copied_local_ids) == 1
    assert "" not in copied_local_ids

    domain_edges = tuple(
        edge
        for edge in edges
        if "forall-domain-defeq-local-type" in edge.get("evidence", ())
    )
    assert len(domain_edges) == 1
    domain_edge = domain_edges[0]
    assert domain_edge["relation"] == "copy"
    assert domain_edge["provenance"] == "lean-defeq"
    assert len(domain_edge["sources"]) == 1
    assert len(domain_edge["targets"]) == 2
    assert {target["goalId"] for target in domain_edge["targets"]} == set(after_ids)
    assert {target["expressionRole"] for target in domain_edge["targets"]} == {
        "local-type"
    }


def test_every_observed_goal_exports_canonical_structure(
    canonical_tactic_trace: dict,
) -> None:
    goals = tuple(_all_goals(canonical_tactic_trace))
    assert goals
    for goal in goals:
        assert goal.get("canonicalTarget") is not None
        assert isinstance(goal.get("canonicalLocals"), list)

        target = goal["canonicalTarget"]
        assert target["id"]
        assert target["lean"]
        assert isinstance(target["occurrences"], list)

        local_ids = [local["id"] for local in goal["canonicalLocals"]]
        assert len(local_ids) == len(set(local_ids))
        for local in goal["canonicalLocals"]:
            assert local["type"]["id"]
            assert local["userName"]
            assert isinstance(local.get("presentationVisible"), bool)

        visible_names = [
            local["userName"]
            for local in goal["canonicalLocals"]
            if local["presentationVisible"]
        ]
        assert visible_names == [item["name"] for item in goal.get("latexContext", ())]

    # A definition value is part of the kernel-visible state.  Observing at
    # least one here protects `let`/`set` and `clear_value` from degenerating
    # into presentation-only text heuristics.
    assert any(
        local.get("value") is not None
        for goal in goals
        for local in goal["canonicalLocals"]
    )
    assert any(
        not local["presentationVisible"]
        for goal in goals
        for local in goal["canonicalLocals"]
    )


def test_lean_frontiers_form_one_contiguous_timeline(
    canonical_tactic_trace: dict,
) -> None:
    """An MVar may survive many tactics; time is not keyed by the MVar ID."""

    frontier = (canonical_tactic_trace["startGoal"],)
    for action_index, action in enumerate(canonical_tactic_trace["actions"]):
        before = tuple(action["beforeState"])
        after = tuple(action["afterState"])
        assert _canonical_frontier(before) == _canonical_frontier(frontier), (
            f"discontinuous beforeState at action {action_index}"
        )

        before_ids = tuple(goal["goalId"] for goal in before)
        after_ids = tuple(goal["goalId"] for goal in after)
        assert len(before_ids) == len(set(before_ids))
        assert len(after_ids) == len(set(after_ids))
        assert set(action.get("focusBefore", ())) <= set(before_ids)
        assert set(action.get("focusAfter", ())) <= set(after_ids)

        for goal_action in action["goalActions"]:
            assert goal_action["startGoalId"] in before_ids
            assert {
                result["goal"]["goalId"] for result in goal_action["results"]
            } <= set(after_ids)
        for lineage in action.get("goalLineage", ()):
            assert set(lineage["sourceGoalIds"]) <= set(before_ids)
            assert set(lineage["targetGoalIds"]) <= set(after_ids)
        frontier = after

    assert not frontier, "the proved theorem still has a live goal"


def test_late_structural_controls_preserve_and_reorder_the_real_frontier(
    canonical_tactic_trace: dict,
) -> None:
    actions = canonical_tactic_trace["actions"]
    observed = tuple(
        " ".join(str(action.get("tacticText", "")).split()) for action in actions
    )

    all_goals_start = next(
        index
        for index, tactic in enumerate(observed)
        if tactic.startswith("have tAllGoals ")
    )
    all_goals_end = next(
        index
        for index in range(all_goals_start + 1, len(observed))
        if observed[index].startswith("have tNativeHyperedge ")
    )
    all_goals_actions = actions[all_goals_start + 1 : all_goals_end]
    assert tuple(
        " ".join(str(action.get("tacticText", "")).split())
        for action in all_goals_actions
    ) == ("constructor", "trivial", "trivial")
    constructor_after = tuple(
        goal["goalId"] for goal in all_goals_actions[0]["afterState"]
    )
    assert len(constructor_after) == 2
    assert (
        tuple(goal["goalId"] for goal in all_goals_actions[1]["beforeState"])
        == constructor_after
    )
    assert (
        tuple(goal["goalId"] for goal in all_goals_actions[1]["afterState"])
        == constructor_after[1:]
    )
    assert (
        tuple(goal["goalId"] for goal in all_goals_actions[2]["beforeState"])
        == constructor_after[1:]
    )

    for tactic_name in ("swap", "rotate_left", "rotate_right"):
        index = observed.index(tactic_name)
        before_ids = tuple(goal["goalId"] for goal in actions[index]["beforeState"])
        after_ids = tuple(goal["goalId"] for goal in actions[index]["afterState"])
        assert len(before_ids) == 2
        assert after_ids == tuple(reversed(before_ids))

        # The newly focused branch is the one closed by the following tactic;
        # the other branch survives exactly once and is then closed as well.
        first_close = actions[index + 1]
        second_close = actions[index + 2]
        assert tuple(first_close.get("focusBefore", ())) == after_ids[:1]
        assert (
            tuple(goal["goalId"] for goal in first_close["afterState"])[:1]
            == after_ids[1:]
        )
        assert tuple(second_close.get("focusBefore", ())) == after_ids[1:]


def test_real_tactic_timeline_is_exactly_replayable(
    canonical_tactic_movie: Movie,
) -> None:
    movie = canonical_tactic_movie
    assert len(movie.frames) > 2
    for before, after in zip(movie.frames, movie.frames[1:], strict=False):
        assert before.proof_state is not None
        assert after.proof_state is not None
        assert after.proof_transition is not None
        assert (
            apply_transition(before.proof_state, after.proof_transition)
            == after.proof_state
        )


def test_real_tactic_correspondence_is_referentially_valid(
    canonical_tactic_movie: Movie,
) -> None:
    movie = canonical_tactic_movie
    for before, frame in zip(movie.frames, movie.frames[1:], strict=False):
        assert before.proof_state is not None
        assert frame.proof_state is not None
        transition = frame.proof_transition
        assert transition is not None
        assert not validate_correspondence(
            before.proof_state, frame.proof_state, transition.correspondence
        )


def test_real_tactic_frames_never_duplicate_live_goals_or_locals(
    canonical_tactic_movie: Movie,
) -> None:
    movie = canonical_tactic_movie
    for frame in movie.frames:
        state = frame.proof_state
        assert state is not None
        assert len(state.goal_order) == len(set(state.goal_order))
        for goal in state.goals:
            fvar_ids = tuple(local.decl_id for local in goal.locals)
            assert len(fvar_ids) == len(set(fvar_ids))


def test_real_intro_and_revert_are_cross_row_moves_without_injected_edges(
    canonical_tactic_movie: Movie,
) -> None:
    movie = canonical_tactic_movie

    intro_frames = _frames_containing(movie, "intro n")
    assert intro_frames
    assert any(
        any(_is_target_occurrence(source) for source in sources)
        and any(_is_local_entity(target) for target in targets)
        and "text-fallback" not in primitive.provenance
        for frame in intro_frames
        for primitive, sources, targets in _primitive_entities(
            frame, VisualPrimitiveKind.MOVE
        )
    )

    revert_frames = _frames_containing(movie, "revert n")
    assert revert_frames
    assert any(
        any(_is_local_entity(source) for source in sources)
        and any(_is_target_occurrence(target) for target in targets)
        and "text-fallback" not in primitive.provenance
        for frame in revert_frames
        for primitive, sources, targets in _primitive_entities(
            frame, VisualPrimitiveKind.MOVE
        )
    )


@pytest.mark.parametrize(
    "tactic_fragment",
    ("constructor", "cases h", "induction n with"),
)
def test_real_branching_tactics_have_split_goal_lifecycle(
    canonical_tactic_movie: Movie,
    tactic_fragment: str,
) -> None:
    movie = canonical_tactic_movie
    candidates = _frames_containing(movie, tactic_fragment)
    split_frames = tuple(
        frame
        for frame in candidates
        if frame.proof_transition is not None
        and any(
            effect.kind is GoalEffectKind.SPLIT
            for effect in frame.proof_transition.goal_effects
        )
    )
    assert split_frames, f"{tactic_fragment!r} did not expose a goal split"

    for frame in split_frames:
        splits = tuple(_primitive_entities(frame, VisualPrimitiveKind.SPLIT))
        assert any(
            len(sources) == 1 and len(targets) > 1 for _, sources, targets in splits
        )
        assert all(
            "text-fallback" not in primitive.provenance
            for primitive, _sources, _targets in splits
        )

    # Every branch created by the selected split disappears before the theorem
    # closes; no consumed parent goal is resurrected later in the timeline.
    frame_positions = {id(frame): index for index, frame in enumerate(movie.frames)}
    for frame in split_frames:
        position = frame_positions[id(frame)]
        split_effects = tuple(
            effect
            for effect in frame.proof_transition.goal_effects
            if effect.kind is GoalEffectKind.SPLIT
        )
        for effect in split_effects:
            parent_ids = set(effect.source_goal_ids)
            child_ids = {goal.goal_id for goal in effect.created_goals}
            later_states = (later.proof_state for later in movie.frames[position + 1 :])
            later_goal_sets = [
                {goal.goal_id for goal in state.goals}
                for state in later_states
                if state is not None
            ]
            assert all(not (parent_ids & live) for live in later_goal_sets)
            assert later_goal_sets and not (child_ids & later_goal_sets[-1])
