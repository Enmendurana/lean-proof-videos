"""JSON diagnostics for the canonical proof-to-presentation pipeline.

This module is intentionally renderer independent.  It serializes the exact
proof states, their normalized transition, the interpretation derived from
typed effects, and the finite visual plan.  A browser, Manim and Remotion can
therefore inspect the same evidence without any of them becoming the owner of
semantic identity.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from proof_video.presentation.model import SemanticVisualPlan
from proof_video.presentation.semantic_plan import plan_visual_transition
from proof_video.proof.correspondence import (
    CorrespondenceEdge,
    EntityRef,
    validate_correspondence,
)
from proof_video.proof.effects import (
    ContextEffect,
    GoalEffect,
    ProofTransition,
    TargetEffect,
    apply_transition,
)
from proof_video.proof.interpretation import interpret_transition
from proof_video.proof.state import (
    ExprOccurrence,
    Expression,
    GoalState,
    LocalDecl,
    ProofState,
    SourceRange,
    validate_state,
)


DEBUG_SCHEMA_VERSION = "1.0"


def _source_range(value: SourceRange | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "file": value.file,
        "startLine": value.start_line,
        "startColumn": value.start_column,
        "endLine": value.end_line,
        "endColumn": value.end_column,
    }


def _occurrence(value: ExprOccurrence) -> dict[str, Any]:
    return {
        "occurrenceId": value.occurrence_id,
        "kind": value.kind,
        "path": list(value.path),
        "fingerprint": value.fingerprint,
        "leanIdentity": value.lean_identity or None,
        "typeFingerprint": value.type_fingerprint or None,
        "parentOccurrenceId": value.parent_id,
        "aliases": list(value.aliases),
        "latexSpans": [
            {"start": span.start, "end": span.end} for span in value.latex_spans
        ],
        "sourceRange": _source_range(value.source_range),
    }


def _expression(value: Expression) -> dict[str, Any]:
    return {
        "expressionId": value.expression_id,
        "fingerprint": value.fingerprint,
        "typeFingerprint": value.type_fingerprint or None,
        "lean": value.lean or None,
        "latex": value.latex or None,
        "sourceRange": _source_range(value.source_range),
        "occurrences": [_occurrence(item) for item in value.occurrences],
    }


def _local(value: LocalDecl) -> dict[str, Any]:
    return {
        "declarationId": value.decl_id,
        "userName": value.user_name,
        "kind": value.kind.value,
        "binderInfo": value.binder_info,
        "dependencies": list(value.dependencies),
        "aliases": list(value.aliases),
        "isProof": value.is_proof,
        "presentationVisible": value.presentation_visible,
        "metadata": dict(value.metadata),
        "sourceRange": _source_range(value.source_range),
        "type": _expression(value.type_expr),
        "value": _expression(value.value_expr) if value.value_expr else None,
    }


def _goal(value: GoalState) -> dict[str, Any]:
    return {
        "goalId": value.goal_id,
        "lineageId": value.lineage_id,
        "parentGoalId": value.parent_goal_id,
        "branchKind": value.branch_kind or None,
        "branchIndex": value.branch_index,
        "metadata": dict(value.metadata),
        "localOrder": [item.decl_id for item in value.locals],
        "locals": [_local(item) for item in value.locals],
        "target": _expression(value.target),
    }


def _state(value: ProofState) -> dict[str, Any]:
    return {
        "fingerprint": value.fingerprint,
        "schemaVersion": value.schema_version,
        "goalOrder": list(value.goal_order),
        "focus": list(value.focus),
        "metadata": dict(value.metadata),
        "goals": [_goal(item) for item in value.goals],
    }


def _entity(value: EntityRef) -> dict[str, Any]:
    return {
        "key": value.key,
        "kind": value.kind.value,
        "goalId": value.goal_id,
        "localId": value.local_id or None,
        "expressionRole": value.expression_role or None,
        "occurrenceId": value.occurrence_id or None,
    }


def _hyperedge(value: CorrespondenceEdge) -> dict[str, Any]:
    return {
        "sources": [_entity(item) for item in value.sources],
        "targets": [_entity(item) for item in value.targets],
        "arity": f"{len(value.sources)}->{len(value.targets)}",
        "relation": value.relation.value,
        "provenance": value.provenance.value,
        "evidence": list(value.evidence),
        "confidence": value.confidence,
    }


def _context_effect(value: ContextEffect) -> dict[str, Any]:
    return {
        "domain": "context",
        "kind": value.kind.value,
        "goalId": value.goal_id,
        "beforeDeclarationId": value.before.decl_id if value.before else None,
        "afterDeclarationId": value.after.decl_id if value.after else None,
        "oldIndex": value.old_index,
        "newIndex": value.new_index,
        "order": list(value.order),
        "entityIds": list(value.entity_ids),
    }


def _target_effect(value: TargetEffect) -> dict[str, Any]:
    return {
        "domain": "target",
        "kind": value.kind.value,
        "goalId": value.goal_id,
        "beforeExpressionId": value.before.expression_id,
        "afterExpressionId": value.after.expression_id,
        "sourcePath": list(value.source_path),
        "targetPath": list(value.target_path),
        "entityId": value.entity_id or None,
    }


def _goal_effect(value: GoalEffect) -> dict[str, Any]:
    return {
        "domain": "goal",
        "kind": value.kind.value,
        "sourceGoalIds": list(value.source_goal_ids),
        "targetGoalIds": [item.goal_id for item in value.target_descriptors],
        "createdGoalIds": [item.goal_id for item in value.created_goals],
        "order": list(value.order),
        "focus": list(value.focus),
    }


def _visual_plan(value: SemanticVisualPlan) -> dict[str, Any]:
    anchors = [
        {
            "anchorId": item.anchor_id,
            "persistentId": item.persistent_id,
            "side": item.side.value,
            "entity": _entity(item.entity),
            "goalIndex": item.goal_index,
            "rowKind": item.row_kind.value,
            "rowIndex": item.row_index,
            "expressionPath": list(item.expression_path),
        }
        for item in value.anchors
    ]
    primitives = [
        {
            "primitiveId": item.primitive_id,
            "kind": item.kind.value,
            "sourceAnchorIds": list(item.source_anchor_ids),
            "targetAnchorIds": list(item.target_anchor_ids),
            "persistentIds": list(item.persistent_ids),
            "scope": item.scope,
            "provenance": list(item.provenance),
            "evidence": list(item.evidence),
            "confidence": item.confidence,
            "usedFallback": item.used_fallback,
            "fallbackReason": item.fallback_reason or None,
        }
        for item in value.primitives
    ]
    diagnostics = [
        {
            "code": item.code,
            "message": item.message,
            "primitiveId": item.primitive_id or None,
            "entityKeys": list(item.entity_keys),
        }
        for item in value.diagnostics
    ]
    fallback_primitives = [
        item["primitiveId"] for item in primitives if item["usedFallback"]
    ]
    return {
        "schemaVersion": value.schema_version,
        "beforeFingerprint": value.before_fingerprint,
        "afterFingerprint": value.after_fingerprint,
        "anchors": anchors,
        "primitives": primitives,
        "diagnostics": diagnostics,
        "fallback": {
            "used": bool(fallback_primitives),
            "primitiveIds": fallback_primitives,
            "count": len(fallback_primitives),
        },
    }


def build_canonical_transition_debug(
    before: ProofState,
    after: ProofState,
    transition: ProofTransition,
) -> dict[str, Any]:
    """Return a complete, deterministic debug record for one transition.

    Diagnostics fail visibly instead of hiding an invalid state or visual
    plan.  The returned errors are explanatory only; strict QA remains the
    authority that decides whether rendering may continue.
    """

    normalized = transition.normalized()
    errors = [
        *(f"before: {item}" for item in validate_state(before)),
        *(f"after: {item}" for item in validate_state(after)),
        *(
            f"correspondence: {item}"
            for item in validate_correspondence(
                before, after, normalized.correspondence
            )
        ),
    ]
    replay_matches = False
    try:
        replay_matches = apply_transition(before, normalized) == after
        if not replay_matches:
            errors.append("replay result differs from the supplied after state")
    except ValueError as error:
        errors.append(f"replay: {error}")

    plan_payload: dict[str, Any]
    try:
        plan_payload = _visual_plan(plan_visual_transition(before, after, normalized))
    except ValueError as error:
        errors.append(f"presentation: {error}")
        plan_payload = {
            "schemaVersion": None,
            "beforeFingerprint": before.fingerprint,
            "afterFingerprint": after.fingerprint,
            "anchors": [],
            "primitives": [],
            "diagnostics": [
                {
                    "code": "visual-plan-rejected",
                    "message": str(error),
                    "primitiveId": None,
                    "entityKeys": [],
                }
            ],
            "fallback": {
                "used": True,
                "primitiveIds": [],
                "count": 0,
                "reason": "visual plan could not be certified",
            },
        }

    interpretation = interpret_transition(normalized)
    effects = [
        *(_goal_effect(item) for item in normalized.goal_effects),
        *(_context_effect(item) for item in normalized.context_effects),
        *(_target_effect(item) for item in normalized.target_effects),
    ]
    return {
        "schemaVersion": DEBUG_SCHEMA_VERSION,
        "before": _state(before),
        "after": _state(after),
        "transition": {
            "schemaVersion": normalized.schema_version,
            "beforeFingerprint": normalized.before_fingerprint,
            "afterFingerprint": normalized.after_fingerprint,
            "metadata": asdict(normalized.metadata),
            "hyperedges": [
                _hyperedge(item) for item in normalized.correspondence.edges
            ],
            "effects": effects,
        },
        "interpretation": {
            "primary": interpretation.primary.value,
            "secondary": [item.value for item in interpretation.secondary],
            "automationPolicy": interpretation.automation_policy.value,
            "tacticHint": interpretation.tactic_hint or None,
        },
        "presentation": plan_payload,
        "validation": {
            "valid": replay_matches and not errors,
            "replayMatches": replay_matches,
            "errors": errors,
        },
    }


__all__ = ["DEBUG_SCHEMA_VERSION", "build_canonical_transition_debug"]
