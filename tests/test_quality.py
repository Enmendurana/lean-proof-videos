from __future__ import annotations

from proof_video.models import (
    Frame,
    Goal,
    Movie,
    SemanticExpression,
    SemanticExpressionNode,
    SemanticTransition,
    SemanticTransitionEdge,
)
from proof_video.quality import build_movie_quality_report, build_quality_report


def _trace(with_edge: bool = True) -> dict:
    source = {"id": "s", "kind": "app", "identity": "expr:f(x)", "fingerprint": "fp"}
    target = {"id": "t", "kind": "app", "identity": "expr:f(x)", "fingerprint": "fp"}
    fingerprint = "proof"
    return {
        "schemaVersion": "3.0",
        "chapters": [
            {
                "theoremName": "Demo.main",
                "movie": {
                    "startGoal": {"latexTarget": "f(x)=f(x)", "latexContext": []},
                    "actions": [
                        {
                            "goalActions": [
                                {
                                    "proofFingerprint": fingerprint,
                                    "explanation": {
                                        "adapter": "rewrite",
                                        "certificateFingerprint": fingerprint,
                                    },
                                    "results": [
                                        {
                                            "goal": {"latexTarget": "f(x)=f(x)", "latexContext": []},
                                            "semanticTransition": {
                                                "sourceNodes": [source],
                                                "targetNodes": [target],
                                                "edges": (
                                                    [{"sourceNodeId": "s", "targetNodeId": "t"}]
                                                    if with_edge
                                                    else []
                                                ),
                                            },
                                        }
                                    ],
                                }
                            ]
                        }
                    ],
                },
            }
        ],
    }


def test_quality_accepts_certified_persistent_application() -> None:
    report = build_quality_report(_trace())
    assert report["valid"], report["errors"]
    assert report["summary"]["persistentObjects"] == 1


def test_quality_rejects_blinking_unique_persistent_application() -> None:
    report = build_quality_report(_trace(with_edge=False))
    assert not report["valid"]
    assert "has no certified visual edge" in " ".join(report["errors"])


def test_quality_rejects_unhandled_lean_latex() -> None:
    raw = _trace()
    raw["chapters"][0]["movie"]["startGoal"]["latexTarget"] = (
        r"\operatorname{Lean}\left[\text{HMul.hMul}\right]"
    )
    report = build_quality_report(raw)
    assert not report["valid"]
    assert "unhandled Lean expression" in " ".join(report["errors"])


def test_movie_quality_audits_renderer_facing_proof_trace_transitions() -> None:
    source = SemanticExpressionNode("s", "app", "expr:f(x)", "fp")
    target = SemanticExpressionNode("t", "app", "expr:f(x)", "fp")
    transition = SemanticTransition(
        SemanticExpression((source,)),
        SemanticExpression((target,)),
        (SemanticTransitionEdge("s", "t", "verified-same-expression"),),
        adapter="proof-term",
    )
    movie = Movie(
        "demo",
        (
            Frame(0, "start", (Goal("g0", "", latex_target="f(x)"),)),
            Frame(
                1,
                "rw",
                (Goal("g1", "", latex_target="f(x)+0", semantic_transition=transition),),
            ),
        ),
    )

    report = build_movie_quality_report(movie)

    assert report["valid"], report["errors"]
    assert report["summary"]["checkedTransitions"] == 1
    assert report["summary"]["persistentObjects"] == 1


def test_movie_quality_treats_certified_hybrid_chapter_start_as_boundary() -> None:
    movie = Movie(
        "demo",
        (
            Frame(
                0,
                "",
                (Goal("chapter-0/g0", "", latex_target="A", lineage_id="chapter-0/goal-0"),),
            ),
            Frame(
                1,
                "",
                (Goal("chapter-1/g0", "", latex_target="B", lineage_id="chapter-1/goal-0"),),
            ),
        ),
        hybrid_trace={"schemaVersion": "3.1"},
    )

    report = build_movie_quality_report(movie)

    assert report["valid"], report["errors"]
    assert report["summary"]["checkedTransitions"] == 0
