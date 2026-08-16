"""Certified tactic-explanation registry.

The registry deliberately describes only evidence already present in Lean's
kernel-checked assignment.  It never reverse-engineers a plausible sequence
from matching glyphs.  A future tactic-specific extractor can replace one
adapter without changing the proof or renderer schemas.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from proof_video.proof.schema import SemanticTransition


@dataclass(frozen=True)
class CertifiedTacticExplanation:
    adapter: str
    certificate_kind: str
    certificate_fingerprint: str
    premise_ids: tuple[str, ...]
    supporting_constants: tuple[str, ...]
    strategy: str
    expandable: bool

    @property
    def certified(self) -> bool:
        return bool(self.certificate_fingerprint and self.certificate_kind)


class TacticExplainer(Protocol):
    adapters: frozenset[str]
    strategy: str
    expandable: bool

    def explain(self, transition: SemanticTransition) -> CertifiedTacticExplanation: ...


class AssignmentEvidenceExplainer:
    def __init__(
        self,
        *adapters: str,
        strategy: str,
        expandable: bool = True,
    ) -> None:
        self.adapters = frozenset(adapters)
        self.strategy = strategy
        self.expandable = expandable

    def explain(self, transition: SemanticTransition) -> CertifiedTacticExplanation:
        return CertifiedTacticExplanation(
            adapter=transition.adapter or "generic",
            certificate_kind=transition.proof_kind,
            certificate_fingerprint=transition.proof_fingerprint,
            premise_ids=tuple(dict.fromkeys(transition.proof_premises)),
            supporting_constants=tuple(dict.fromkeys(transition.proof_constants)),
            strategy=self.strategy,
            expandable=self.expandable,
        )


_EXPLAINERS: list[TacticExplainer] = [
    AssignmentEvidenceExplainer(
        "rewrite",
        "subst",
        "change",
        strategy="equality-transport",
    ),
    AssignmentEvidenceExplainer("simp", strategy="simplifier-certificate"),
    AssignmentEvidenceExplainer("ring", strategy="normal-form-certificate"),
    AssignmentEvidenceExplainer(
        "linear-arithmetic",
        strategy="arithmetic-contradiction-certificate",
    ),
]
_FALLBACK = AssignmentEvidenceExplainer(
    "generic", strategy="kernel-assignment", expandable=False
)


def register_tactic_explainer(explainer: TacticExplainer) -> None:
    """Register a higher-priority adapter, primarily for project extensions."""

    _EXPLAINERS.insert(0, explainer)


def explain_tactic(transition: SemanticTransition) -> CertifiedTacticExplanation:
    for explainer in _EXPLAINERS:
        if transition.adapter in explainer.adapters:
            return explainer.explain(transition)
    return _FALLBACK.explain(transition)
