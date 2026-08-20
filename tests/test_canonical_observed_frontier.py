from __future__ import annotations

from proof_video.models import Movie
from proof_video.presentation import VisualPrimitiveKind
from proof_video.proof.correspondence import MatchProvenance, RelationKind
from proof_video.proof.effects import GoalEffectKind, apply_transition


def _goal(goal_id: str, target: str) -> dict[str, object]:
    return {
        "goalId": goal_id,
        "state": f"\u22a2 {target}",
        "latexTarget": target,
    }


def _canonical_target(name: str) -> dict[str, object]:
    return {
        "id": f"expr:{name}",
        "fingerprint": f"fp:{name}",
        "lean": name,
        "latex": name,
        "occurrences": [
            {
                "id": f"occ:{name}",
                "kind": "const",
                "path": [],
                "fingerprint": f"node:{name}",
                "identity": f"const:{name}",
            }
        ],
    }


def test_abi5_frontiers_are_consumed_without_reconstructing_duplicate_goals() -> None:
    start = _goal("g1", "A \\land B")
    left = _goal("g2", "A")
    right = _goal("g3", "B")
    raw = {
        "theoremName": "Demo.frontier",
        "startGoal": start,
        "actions": [
            {
                "tacticText": "constructor",
                "beforeState": [start],
                "afterState": [left, right],
                "focusBefore": ["g1"],
                "focusAfter": ["g2"],
                "goalLineage": [
                    {
                        "sourceGoalIds": ["g1"],
                        "targetGoalIds": ["g2", "g3"],
                        "relation": "split",
                    }
                ],
                "goalActions": [
                    {
                        "startGoalId": "g1",
                        "results": [{"goal": left}, {"goal": right}],
                    }
                ],
            },
            {
                "tacticText": "assumption",
                "beforeState": [left, right],
                "afterState": [right],
                "focusBefore": ["g2"],
                "focusAfter": ["g3"],
                "goalLineage": [
                    {
                        "sourceGoalIds": ["g2"],
                        "targetGoalIds": [],
                        "relation": "close",
                    }
                ],
                "goalActions": [{"startGoalId": "g2", "results": []}],
            },
        ],
    }

    movie = Movie.from_json(raw)

    assert [tuple(goal.goal_id for goal in frame.goals) for frame in movie.frames] == [
        ("g1",),
        ("g2", "g3"),
        ("g3",),
    ]
    split = movie.frames[1].proof_transition
    close = movie.frames[2].proof_transition
    assert split is not None and close is not None
    assert (
        apply_transition(movie.frames[0].proof_state, split)
        == movie.frames[1].proof_state
    )
    assert (
        apply_transition(movie.frames[1].proof_state, close)
        == movie.frames[2].proof_state
    )
    assert any(effect.kind is GoalEffectKind.SPLIT for effect in split.goal_effects)
    assert any(effect.kind is GoalEffectKind.CLOSE for effect in close.goal_effects)
    assert movie.frames[1].visual_plan is not None
    assert movie.frames[1].visual_plan.primitives_of_kind(VisualPrimitiveKind.SPLIT)


def test_abi5_native_entity_hyperedge_is_authoritative() -> None:
    source = {
        **_goal("g1", "x"),
        "semanticNodes": [
            {
                "id": "old-x",
                "kind": "fvar",
                "identity": "fvar:old",
                "fingerprint": "old-fingerprint",
                "path": [],
                "latexSpans": [{"start": 0, "end": 1}],
            }
        ],
    }
    target = {
        **_goal("g2", "y"),
        "semanticNodes": [
            {
                "id": "new-y",
                "kind": "fvar",
                "identity": "fvar:new",
                "fingerprint": "new-fingerprint",
                "path": [],
                "latexSpans": [{"start": 0, "end": 1}],
            }
        ],
    }
    raw = {
        "canonicalAbi": 5,
        "capabilities": ["canonical-entity-hyperedges"],
        "theoremName": "Demo.native_edge",
        "startGoal": source,
        "actions": [
            {
                "tacticText": "custom_tactic",
                "beforeState": [source],
                "afterState": [target],
                "focusBefore": ["g1"],
                "focusAfter": ["g2"],
                "goalLineage": [
                    {
                        "sourceGoalIds": ["g1"],
                        "targetGoalIds": ["g2"],
                        "relation": "evolve",
                    }
                ],
                "canonicalCorrespondence": [
                    {
                        "sources": [
                            {
                                "kind": "occurrence",
                                "goalId": "g1",
                                "expressionRole": "target",
                                "occurrenceId": "old-x",
                            }
                        ],
                        "targets": [
                            {
                                "kind": "occurrence",
                                "goalId": "g2",
                                "expressionRole": "target",
                                "occurrenceId": "new-y",
                            }
                        ],
                        "relation": "rewrite",
                        "provenance": "lean-defeq",
                        "evidence": ["kernel-checked-example"],
                    }
                ],
                "goalActions": [],
            }
        ],
    }

    movie = Movie.from_json(raw)
    frame = movie.frames[1]
    transition = frame.proof_transition

    assert frame.canonical_abi == 5
    assert transition is not None
    native = next(
        edge
        for edge in transition.correspondence.edges
        if edge.sources and edge.targets and edge.sources[0].occurrence_id == "old-x"
    )
    assert native.targets[0].occurrence_id == "new-y"
    assert native.relation is RelationKind.REWRITE
    assert native.provenance is MatchProvenance.LEAN_DEFEQ
    assert "kernel-checked-example" in native.evidence


def test_abi5_continuity_uses_canonical_state_not_pretty_printer_spelling() -> None:
    start = {
        **_goal("g1", "P"),
        "canonicalLocals": [],
        "canonicalTarget": _canonical_target("P"),
    }
    same_canonical_before = {
        **start,
        "state": "pretty printer chose a second spelling",
        "latexTarget": r"\mathit{P}",
    }
    target = {
        **_goal("g2", "Q"),
        "canonicalLocals": [],
        "canonicalTarget": _canonical_target("Q"),
    }
    raw = {
        "canonicalAbi": 5,
        "capabilities": ["canonical-proof-state"],
        "theoremName": "Demo.pretty_view",
        "startGoal": start,
        "actions": [
            {
                "tacticText": "custom",
                "beforeState": [same_canonical_before],
                "afterState": [target],
                "focusBefore": ["g1"],
                "focusAfter": ["g2"],
                "goalLineage": [
                    {
                        "sourceGoalIds": ["g1"],
                        "targetGoalIds": ["g2"],
                        "relation": "rewrite",
                    }
                ],
                "goalActions": [],
            }
        ],
    }

    movie = Movie.from_json(raw)

    assert len(movie.frames) == 2
    assert movie.frames[0].proof_state is not None
    assert movie.frames[1].proof_state is not None
