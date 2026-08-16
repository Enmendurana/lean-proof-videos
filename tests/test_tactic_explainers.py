from __future__ import annotations

from proof_video.proof.explainers import explain_tactic
from proof_video.proof.schema import SemanticTransition


def _transition(adapter: str) -> SemanticTransition:
    transition = SemanticTransition.from_json(
        {
            "sourceNodes": [],
            "targetNodes": [],
            "edges": [],
            "adapter": adapter,
            "proofKind": "equality-transport",
            "proofFingerprint": "kernel-proof-42",
            "proofPremises": ["h", "h"],
            "proofConstants": ["Eq.mp", "Eq.mp", "congrArg"],
        }
    )
    assert transition is not None
    return transition


def test_rewrite_explanation_uses_only_certified_assignment_evidence() -> None:
    explanation = explain_tactic(_transition("rewrite"))
    assert explanation.certified
    assert explanation.strategy == "equality-transport"
    assert explanation.premise_ids == ("h",)
    assert explanation.supporting_constants == ("Eq.mp", "congrArg")


def test_unknown_adapter_is_not_marked_expandable() -> None:
    explanation = explain_tactic(_transition("aesop"))
    assert explanation.strategy == "kernel-assignment"
    assert not explanation.expandable
