"""Renderer-independent presentation plans for canonical proof states."""

from proof_video.presentation.model import (
    AnchorSide,
    LayoutAnchor,
    LayoutRowKind,
    PlanDiagnostic,
    SemanticVisualPlan,
    VisualPrimitive,
    VisualPrimitiveKind,
    validate_visual_plan,
)
from proof_video.presentation.debug import build_canonical_transition_debug
from proof_video.presentation.goal_forest import (
    GoalCard,
    GoalForestLayout,
    build_goal_forest_layout,
    build_goal_forest_timeline,
    validate_goal_forest_layout,
)
from proof_video.presentation.semantic_plan import plan_visual_transition
from proof_video.presentation.rows import (
    ContextPresentationRow,
    context_presentation_rows,
    presentation_local_declarations,
)

__all__ = [
    "AnchorSide",
    "ContextPresentationRow",
    "GoalCard",
    "GoalForestLayout",
    "LayoutAnchor",
    "LayoutRowKind",
    "PlanDiagnostic",
    "SemanticVisualPlan",
    "VisualPrimitive",
    "VisualPrimitiveKind",
    "build_canonical_transition_debug",
    "build_goal_forest_layout",
    "build_goal_forest_timeline",
    "context_presentation_rows",
    "presentation_local_declarations",
    "plan_visual_transition",
    "validate_goal_forest_layout",
    "validate_visual_plan",
]
