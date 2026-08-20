"""Proof data, certified traces, and semantic transition construction."""

from proof_video.proof.schema import (
    Frame,
    Goal,
    IndexMaps,
    LatexHypothesis,
    ProofStep,
    RuleAnnotation,
    SemanticExpression,
    SemanticExpressionNode,
    SemanticSpan,
    SemanticTransition,
    SemanticTransitionEdge,
)
from proof_video.proof.trace import ProofChapter, ProofTrace
from proof_video.proof.correspondence import Correspondence, CorrespondenceEdge
from proof_video.proof.completion import CompletionStatus, TerminalCompletion
from proof_video.proof.effects import ProofTransition
from proof_video.proof.state import GoalState, LocalDecl, ProofState

__all__ = [
    "Frame",
    "Goal",
    "IndexMaps",
    "LatexHypothesis",
    "ProofChapter",
    "ProofStep",
    "ProofTrace",
    "RuleAnnotation",
    "SemanticExpression",
    "SemanticExpressionNode",
    "SemanticSpan",
    "SemanticTransition",
    "SemanticTransitionEdge",
    "Correspondence",
    "CorrespondenceEdge",
    "CompletionStatus",
    "GoalState",
    "LocalDecl",
    "ProofState",
    "ProofTransition",
    "TerminalCompletion",
]
