"""Single source of truth for Lean extractor modules copied and hashed by Python.

Keeping this inventory separate prevents the 4.28 evidence cache and the
isolated 4.32 workspace from silently drifting when the Lean implementation is
split into additional modules.
"""

from __future__ import annotations


EXTRACTOR_SOURCE_PATHS = (
    "Animate.lean",
    "Animate/Config.lean",
    "Animate/Schema.lean",
    "Animate/TacticTrace.lean",
    "Animate/Hybrid.lean",
    "Animate/Frontend.lean",
    "Annotations.lean",
    "HighlightSyntax.lean",
    "MathlibLatex.lean",
    "ProofLatex.lean",
    "ProofTrace.lean",
    "ProofTrace/Compat.lean",
    "ProofTrace/Schema.lean",
    "ProofTrace/Dependencies.lean",
    "ProofTrace/Extraction.lean",
    "SemanticTransitions.lean",
    "StringMatching.lean",
)


WORKSPACE_SOURCE_PATHS = (
    *EXTRACTOR_SOURCE_PATHS,
    "AnimateMain.lean",
    "SnapshotAnimate432.lean",
    "SnapshotCertificate432.lean",
)
