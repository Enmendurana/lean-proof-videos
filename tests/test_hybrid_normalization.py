from __future__ import annotations

from copy import deepcopy

from proof_video.models import Movie
from proof_video.proof.hybrid_normalization import (
    normalize_fallback_latex,
    normalize_source_tactic_movie,
)
from proof_video.quality import build_movie_quality_report


_LEAN_X_TIMES_Y = r"\operatorname{Lean}\left[\text{x * y}\right]"


def test_fallback_normalization_preserves_boundaries_after_replacement() -> None:
    source = rf"A + {_LEAN_X_TIMES_Y} = B"

    normalized, boundary_map = normalize_fallback_latex(source)

    assert r"\operatorname{Lean}" not in normalized
    assert r"x \cdot  y" in normalized
    old_suffix = source.index(" = B")
    new_suffix = normalized.index(" = B")
    assert boundary_map.span(old_suffix, len(source)) == (
        new_suffix,
        len(normalized),
    )


def test_old_trace_is_normalized_without_mutating_kernel_evidence() -> None:
    source_state = rf"\vdash\;{_LEAN_X_TIMES_Y}"
    node = {
        "id": "source-expression",
        "kind": "app",
        "identity": "expr:x-times-y",
        "fingerprint": "same-expression",
        "latexSpans": [{"start": 0, "end": len(source_state)}],
    }
    raw = {
        "theoremName": "Demo.legacy",
        "startGoal": {
            "goalId": "g0",
            "state": "⊢ x * y",
            "latexTarget": _LEAN_X_TIMES_Y,
            "latexContext": [],
        },
        "actions": [
            {
                "tacticText": "simpa",
                "goalActions": [
                    {
                        "startGoalId": "g0",
                        "results": [
                            {
                                "goal": {
                                    "goalId": "g1",
                                    "state": "⊢ x * y + 0",
                                    "latexTarget": rf"{_LEAN_X_TIMES_Y} + 0",
                                    "latexContext": [],
                                },
                                "semanticTransition": {
                                    "proofKind": "goal-reduction",
                                    "adapter": "legacy",
                                    "sourceNodes": [node],
                                    "targetNodes": [
                                        {
                                            **deepcopy(node),
                                            "id": "target-expression",
                                        }
                                    ],
                                    "edges": [],
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }
    certified_evidence = deepcopy(raw)

    normalized = normalize_source_tactic_movie(raw)

    assert raw == certified_evidence
    assert r"\operatorname{Lean}" not in normalized["startGoal"]["latexTarget"]
    transition = normalized["actions"][0]["goalActions"][0]["results"][0][
        "semanticTransition"
    ]
    assert transition["edges"] == [
        {
            "sourceNodeId": "source-expression",
            "targetNodeId": "target-expression",
            "reason": "verified-unique-expression-identity",
            "confidence": 1.0,
        }
    ]
    normalized_state = rf"\vdash\;{normalized['startGoal']['latexTarget']}"
    assert transition["sourceNodes"][0]["latexSpans"] == [
        {"start": 0, "end": len(normalized_state)}
    ]

    movie = Movie.from_json(raw)
    report = build_movie_quality_report(movie)
    assert report["valid"], report["errors"]
    assert report["summary"]["checkedTransitions"] == 1
    assert report["summary"]["persistentObjects"] == 1


def test_normalizer_preserves_missing_latex_fallback_to_lean_state() -> None:
    raw = {
        "theoremName": "Demo.state-only",
        "startGoal": {"goalId": "g0", "state": "goal A"},
        "actions": [
            {
                "goalActions": [
                    {
                        "startGoalId": "g0",
                        "results": [{"goal": {"goalId": "g1", "state": "goal B"}}],
                    }
                ]
            }
        ],
    }

    normalized = normalize_source_tactic_movie(raw)

    assert "latexTarget" not in normalized["startGoal"]
    assert (
        "latexTarget"
        not in normalized["actions"][0]["goalActions"][0]["results"][0]["goal"]
    )
    assert len(Movie.from_json(raw).semantic_frames()) == 2


def test_abi5_frontiers_normalize_canonical_local_expression_fallbacks() -> None:
    real_fallback = r"\operatorname{Lean}\left[\text{ℝ}\right]"
    inequality_lean = "f (-1) ≤ -1 * f (-1) - -1 * f (-1) + f (f (-1))"
    inequality_fallback = (
        r"\operatorname{Lean}\left[\text{" + inequality_lean + r"}\right]"
    )

    def expression(expression_id: str, lean: str, latex: str) -> dict:
        return {
            "id": expression_id,
            "fingerprint": f"fp:{expression_id}",
            "lean": lean,
            "latex": latex,
            "occurrences": [
                {
                    "id": f"{expression_id}/0",
                    "kind": "mvar",
                    "path": "0",
                    "fingerprint": f"fp:{expression_id}",
                    "identity": f"mvar:{expression_id}",
                    "latexSpans": [{"start": 0, "end": len(latex)}],
                }
            ],
        }

    def goal(
        goal_id: str,
        name: str,
        lean: str,
        latex: str,
    ) -> dict:
        return {
            "goalId": goal_id,
            "state": f"{name} : {lean}\n⊢ False",
            "latexTarget": r"\text{False}",
            "latexContext": [{"name": name, "latex": latex}],
            "canonicalLocals": [
                {
                    "id": f"local:{name}",
                    "userName": name,
                    "type": expression(f"type:{name}", lean, latex),
                    "value": None,
                    "isProof": name == "hp",
                }
            ],
            "canonicalTarget": expression(
                f"target:{goal_id}", "False", r"\text{False}"
            ),
        }

    before = goal("g0", "s", "ℝ", real_fallback)
    after = goal("g1", "hp", inequality_lean, inequality_fallback)
    raw = {
        "canonicalAbi": 5,
        "theoremName": "Demo.abi5-fallback",
        "startGoal": before,
        "actions": [
            {
                "tacticText": "custom",
                "beforeState": [before],
                "afterState": [after],
                "focusBefore": ["g0"],
                "focusAfter": ["g1"],
                "goalActions": [],
            }
        ],
    }
    certified_evidence = deepcopy(raw)

    normalized = normalize_source_tactic_movie(raw)

    assert raw == certified_evidence
    normalized_before = normalized["actions"][0]["beforeState"][0]
    normalized_after = normalized["actions"][0]["afterState"][0]
    assert normalized_before["latexContext"][0]["latex"] == r"\mathbb{R}"
    assert normalized_before["canonicalLocals"][0]["type"]["latex"] == r"\mathbb{R}"
    assert r"\operatorname{Lean}" not in normalized_after["latexContext"][0]["latex"]
    assert r"\le" in normalized_after["latexContext"][0]["latex"]
    assert r"\cdot" in normalized_after["latexContext"][0]["latex"]
    normalized_type = normalized_after["canonicalLocals"][0]["type"]
    assert r"\operatorname{Lean}" not in normalized_type["latex"]
    assert normalized_type["occurrences"][0]["latexSpans"] == [
        {"start": 0, "end": len(normalized_type["latex"])}
    ]
