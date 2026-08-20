from __future__ import annotations

from proof_video.models import Movie
from proof_video.strict_audit import build_hybrid_audit


def _goal(goal_id: str, proposition: str) -> dict:
    return {
        "goalId": goal_id,
        "state": f"⊢ {proposition}",
        "latexTarget": proposition,
        "semanticNodes": [],
    }


def _chapter(chapter_id: int, theorem: str, dependencies: list[str]) -> dict:
    fingerprint = f"proof-{theorem}"
    return {
        "id": chapter_id,
        "theoremName": theorem,
        "dependencies": dependencies,
        "proofFingerprint": fingerprint,
        "axioms": [],
        "isMain": theorem == "Demo.main",
        "validation": {
            "valid": True,
            "kernelChecked": True,
            "noSorry": True,
            "errors": [],
        },
        "movie": {
            "theoremName": theorem,
            "startGoal": _goal("g0", theorem),
            "actions": [
                {
                    "tacticText": "exact proof",
                    "goalActions": [
                        {
                            "startGoalId": "g0",
                            "startState": f"⊢ {theorem}",
                            "results": [],
                            "proofKind": "goal-reduction",
                            "proofFingerprint": fingerprint,
                            "proofTerm": "proof",
                            "proofDescendants": [],
                        }
                    ],
                }
            ],
            "highlighting": [],
        },
    }


def _trace() -> dict:
    return {
        "schemaVersion": "3.0",
        "theoremName": "Demo.main",
        "chapters": [
            _chapter(0, "Demo.seed", []),
            _chapter(1, "Demo.main", ["Demo.seed"]),
        ],
        "validation": {
            "valid": True,
            "dependencyOrderValid": True,
            "allChaptersKernelChecked": True,
            "noSorry": True,
            "errors": [],
        },
    }


def test_hybrid_chapters_are_flattened_without_empty_closing_frames() -> None:
    movie = Movie.from_json(_trace())
    assert movie.hybrid_trace is not None
    assert movie.proof_trace is None
    assert [frame.index for frame in movie.frames] == [0, 1]
    assert movie.frames[0].goals[0].goal_id == "chapter-0/g0"
    assert movie.frames[1].goals[0].goal_id == "chapter-1/g0"
    assert movie.frames[0].goals[0].lineage_id != movie.frames[1].goals[0].lineage_id


def test_hybrid_audit_requires_kernel_and_tactic_certificates() -> None:
    raw = _trace()
    audit = build_hybrid_audit(raw)
    assert audit["valid"], audit["errors"]
    assert audit["summary"]["chapters"] == 2
    assert audit["summary"]["certifiedGoalActions"] == 2

    raw["chapters"][1]["movie"]["actions"][0]["goalActions"][0]["proofFingerprint"] = ""
    broken = build_hybrid_audit(raw)
    assert not broken["valid"]
    assert "no certified assignment" in " ".join(broken["errors"])
