"""Pure semantic-to-visual planning for canonical proof transitions.

The planner is deliberately renderer independent.  It gives Manim, Remotion,
or a future renderer the same stable anchors and the same finite animation
vocabulary without consulting tactic names, LaTeX glyph equality, geometry,
or frame timing.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from typing import Iterable

from proof_video.presentation.anchors import build_layout_anchors
from proof_video.presentation.model import (
    LayoutAnchor,
    PlanDiagnostic,
    SemanticVisualPlan,
    VisualPrimitive,
    VisualPrimitiveKind,
    validate_visual_plan,
)
from proof_video.proof.correspondence import (
    CorrespondenceEdge,
    EntityKind,
    EntityRef,
    MatchProvenance,
    RelationKind,
    expression_ref,
    goal_ref,
    local_ref,
    validate_correspondence,
)
from proof_video.proof.effects import (
    ContextEffect,
    ContextEffectKind,
    GoalEffect,
    GoalEffectKind,
    ProofTransition,
    TargetEffect,
    TargetEffectKind,
    apply_transition,
)
from proof_video.proof.state import ProofState


def _primitive_id(
    kind: VisualPrimitiveKind,
    source_anchor_ids: tuple[str, ...],
    target_anchor_ids: tuple[str, ...],
    scope: str,
) -> str:
    payload = json.dumps(
        (kind.value, source_anchor_ids, target_anchor_ids, scope),
        separators=(",", ":"),
    )
    return f"visual:{hashlib.sha256(payload.encode()).hexdigest()[:24]}"


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _same_layout(
    sources: tuple[LayoutAnchor, ...], targets: tuple[LayoutAnchor, ...]
) -> bool:
    return len(sources) == len(targets) == 1 and sources[0].slot == targets[0].slot


def _edge_kind(
    edge: CorrespondenceEdge,
    sources: tuple[LayoutAnchor, ...],
    targets: tuple[LayoutAnchor, ...],
    *,
    closed_goal_ids: frozenset[str],
    target_rewrites: frozenset[str],
) -> VisualPrimitiveKind:
    if (
        edge.relation is RelationKind.REMOVE
        and all(item.kind is EntityKind.GOAL for item in edge.sources)
        and any(item.goal_id in closed_goal_ids for item in edge.sources)
    ):
        return VisualPrimitiveKind.CLOSE
    if edge.relation is RelationKind.PRESERVE:
        if any(
            item.kind is EntityKind.TARGET and item.goal_id in target_rewrites
            for item in edge.targets
        ):
            return VisualPrimitiveKind.REWRITE
        return (
            VisualPrimitiveKind.KEEP
            if _same_layout(sources, targets)
            else VisualPrimitiveKind.MOVE
        )
    return {
        RelationKind.REWRITE: VisualPrimitiveKind.REWRITE,
        RelationKind.COPY: VisualPrimitiveKind.COPY,
        RelationKind.SPLIT: VisualPrimitiveKind.SPLIT,
        RelationKind.MERGE: VisualPrimitiveKind.MERGE,
        RelationKind.CREATE: VisualPrimitiveKind.CREATE,
        RelationKind.REMOVE: VisualPrimitiveKind.REMOVE,
    }[edge.relation]


def _scope(refs: tuple[EntityRef, ...]) -> str:
    kinds = _ordered_unique(item.kind.value for item in refs)
    return "+".join(kinds)


class _PlanBuilder:
    def __init__(self) -> None:
        self._by_signature: dict[tuple, VisualPrimitive] = {}
        self.diagnostics: list[PlanDiagnostic] = []

    def add(
        self,
        kind: VisualPrimitiveKind,
        sources: tuple[LayoutAnchor, ...],
        targets: tuple[LayoutAnchor, ...],
        *,
        scope: str,
        provenance: tuple[str, ...] = (),
        evidence: tuple[str, ...] = (),
        confidence: float = 1.0,
        fallback_reason: str = "",
    ) -> tuple[VisualPrimitive, bool]:
        source_ids = tuple(item.anchor_id for item in sources)
        target_ids = tuple(item.anchor_id for item in targets)
        signature = kind, source_ids, target_ids, scope
        current = self._by_signature.get(signature)
        if current is not None:
            added_fallback = bool(fallback_reason and not current.fallback_reason)
            merged = replace(
                current,
                persistent_ids=_ordered_unique(
                    (
                        *current.persistent_ids,
                        *(item.persistent_id for item in targets),
                        *(item.persistent_id for item in sources),
                    )
                ),
                provenance=_ordered_unique((*current.provenance, *provenance)),
                evidence=_ordered_unique((*current.evidence, *evidence)),
                confidence=min(current.confidence, confidence),
                fallback_reason=current.fallback_reason or fallback_reason,
            )
            self._by_signature[signature] = merged
            if added_fallback:
                self.diagnostics.append(
                    PlanDiagnostic(
                        "effect-fallback",
                        fallback_reason,
                        merged.primitive_id,
                        tuple(item.entity.key for item in (*sources, *targets)),
                    )
                )
            return merged, False
        primitive = VisualPrimitive(
            primitive_id=_primitive_id(kind, source_ids, target_ids, scope),
            kind=kind,
            source_anchor_ids=source_ids,
            target_anchor_ids=target_ids,
            persistent_ids=_ordered_unique(
                (
                    *(item.persistent_id for item in targets),
                    *(item.persistent_id for item in sources),
                )
            ),
            scope=scope,
            provenance=provenance,
            evidence=evidence,
            confidence=confidence,
            fallback_reason=fallback_reason,
        )
        self._by_signature[signature] = primitive
        if fallback_reason:
            self.diagnostics.append(
                PlanDiagnostic(
                    "effect-fallback",
                    fallback_reason,
                    primitive.primitive_id,
                    tuple(item.entity.key for item in (*sources, *targets)),
                )
            )
        return primitive, True

    @property
    def primitives(self) -> tuple[VisualPrimitive, ...]:
        return tuple(
            sorted(
                self._by_signature.values(),
                key=lambda item: (
                    item.kind.value,
                    item.source_anchor_ids,
                    item.target_anchor_ids,
                    item.scope,
                ),
            )
        )


def _goal_refs(
    ids: tuple[str, ...],
    state: ProofState,
) -> tuple[EntityRef, ...]:
    by_id = {goal.goal_id: goal for goal in state.goals}
    return tuple(goal_ref(by_id[item]) for item in ids if item in by_id)


def _local_effect_refs(
    effect: ContextEffect,
    before: ProofState,
    after: ProofState,
) -> tuple[tuple[EntityRef, ...], tuple[EntityRef, ...]]:
    before_goal = before.goal(effect.goal_id)
    if before_goal is None and effect.before is not None:
        before_goal = next(
            (
                goal
                for goal in before.goals
                if any(local.decl_id == effect.before.decl_id for local in goal.locals)
            ),
            None,
        )
    after_goal = after.goal(effect.goal_id)
    if after_goal is None and effect.after is not None:
        after_goal = next(
            (
                goal
                for goal in after.goals
                if any(local.decl_id == effect.after.decl_id for local in goal.locals)
            ),
            None,
        )
    sources = (
        (local_ref(before_goal, effect.before),)
        if before_goal is not None and effect.before is not None
        else ()
    )
    targets = (
        (local_ref(after_goal, effect.after),)
        if after_goal is not None and effect.after is not None
        else ()
    )
    return sources, targets


def _effect_kind(effect: ContextEffect) -> VisualPrimitiveKind:
    if effect.before is None and effect.after is not None:
        return VisualPrimitiveKind.CREATE
    if effect.before is not None and effect.after is None:
        return VisualPrimitiveKind.REMOVE
    if effect.kind is ContextEffectKind.REORDER_LOCALS:
        return VisualPrimitiveKind.REORDER
    return VisualPrimitiveKind.REWRITE


def _goal_effect_kind(effect: GoalEffect) -> VisualPrimitiveKind:
    return {
        GoalEffectKind.PRESERVE: VisualPrimitiveKind.KEEP,
        GoalEffectKind.CREATE: VisualPrimitiveKind.CREATE,
        GoalEffectKind.CLOSE: VisualPrimitiveKind.CLOSE,
        GoalEffectKind.SPLIT: VisualPrimitiveKind.SPLIT,
        GoalEffectKind.MERGE: VisualPrimitiveKind.MERGE,
        GoalEffectKind.REORDER: VisualPrimitiveKind.REORDER,
        GoalEffectKind.FOCUS: VisualPrimitiveKind.FOCUS,
    }[effect.kind]


def _target_effect_kind(effect: TargetEffect) -> VisualPrimitiveKind:
    return (
        VisualPrimitiveKind.KEEP
        if effect.kind is TargetEffectKind.KEEP
        else VisualPrimitiveKind.REWRITE
    )


def _effect_fallbacks(
    builder: _PlanBuilder,
    transition: ProofTransition,
    before: ProofState,
    after: ProofState,
    before_anchors: dict[EntityRef, LayoutAnchor],
    after_anchors: dict[EntityRef, LayoutAnchor],
) -> None:
    covered_sources = frozenset(
        ref for edge in transition.correspondence.edges for ref in edge.sources
    )
    covered_targets = frozenset(
        ref for edge in transition.correspondence.edges for ref in edge.targets
    )

    def covered(
        source_refs: tuple[EntityRef, ...],
        target_refs: tuple[EntityRef, ...],
    ) -> bool:
        return (
            bool(source_refs or target_refs)
            and all(ref in covered_sources for ref in source_refs)
            and all(ref in covered_targets for ref in target_refs)
        )

    def directly_corresponded(
        source_refs: tuple[EntityRef, ...],
        target_refs: tuple[EntityRef, ...],
    ) -> bool:
        return any(
            edge.sources == source_refs and edge.targets == target_refs
            for edge in transition.correspondence.edges
        )

    for effect in transition.context_effects:
        if effect.kind is ContextEffectKind.REORDER_LOCALS:
            old_goal = before.goal(effect.goal_id)
            new_goal = after.goal(effect.goal_id)
            sources = (
                tuple(
                    before_anchors[local_ref(old_goal, item)]
                    for item in old_goal.locals
                )
                if old_goal is not None
                else ()
            )
            target_order = (
                {item.decl_id: item for item in new_goal.locals}
                if new_goal is not None
                else {}
            )
            targets = tuple(
                after_anchors[local_ref(new_goal, target_order[item])]
                for item in effect.order
                if new_goal is not None and item in target_order
            )
            builder.add(
                VisualPrimitiveKind.REORDER,
                sources,
                targets,
                scope="context-order",
                evidence=(effect.kind.value,),
            )
            continue
        source_refs, target_refs = _local_effect_refs(effect, before, after)
        if covered(source_refs, target_refs) and not directly_corresponded(
            source_refs, target_refs
        ):
            continue
        sources = tuple(before_anchors[item] for item in source_refs)
        targets = tuple(after_anchors[item] for item in target_refs)
        evidence = [effect.kind.value]
        evidence.extend(f"entity:{item}" for item in effect.entity_ids)
        _primitive, created = builder.add(
            _effect_kind(effect),
            sources,
            targets,
            scope="local",
            evidence=tuple(evidence),
            fallback_reason="",
        )
        if created:
            # State effects are authoritative, but normally the same entity
            # relation already came from correspondence.  Recording this
            # fallback makes missing extractor provenance visible to QA.
            signature = _effect_kind(effect), sources, targets
            builder.add(
                signature[0],
                signature[1],
                signature[2],
                scope="local",
                evidence=tuple(evidence),
                fallback_reason=f"{effect.kind.value} had no correspondence edge",
            )

    for effect in transition.target_effects:
        old_goal = before.goal(effect.goal_id)
        if old_goal is None and effect.before is not None:
            old_goal = next(
                (goal for goal in before.goals if goal.target == effect.before),
                None,
            )
        new_goal = after.goal(effect.goal_id)
        if new_goal is None and effect.after is not None:
            new_goal = next(
                (goal for goal in after.goals if goal.target == effect.after),
                None,
            )
        source_refs = (
            (expression_ref(old_goal, EntityKind.TARGET, "target"),)
            if old_goal is not None
            else ()
        )
        target_refs = (
            (expression_ref(new_goal, EntityKind.TARGET, "target"),)
            if new_goal is not None
            else ()
        )
        if covered(source_refs, target_refs) and not directly_corresponded(
            source_refs, target_refs
        ):
            continue
        sources = tuple(before_anchors[item] for item in source_refs)
        targets = tuple(after_anchors[item] for item in target_refs)
        evidence = [effect.kind.value]
        if effect.entity_id:
            evidence.append(f"entity:{effect.entity_id}")
        if effect.source_path or effect.target_path:
            evidence.append(f"paths:{effect.source_path!r}->{effect.target_path!r}")
        _primitive, created = builder.add(
            _target_effect_kind(effect),
            sources,
            targets,
            scope="target",
            evidence=tuple(evidence),
        )
        if created:
            builder.add(
                _target_effect_kind(effect),
                sources,
                targets,
                scope="target",
                evidence=tuple(evidence),
                fallback_reason=f"{effect.kind.value} had no correspondence edge",
            )

    for effect in transition.goal_effects:
        if effect.kind is GoalEffectKind.REORDER:
            sources = tuple(
                before_anchors[item] for item in _goal_refs(before.goal_order, before)
            )
            targets = tuple(
                after_anchors[item] for item in _goal_refs(effect.order, after)
            )
            builder.add(
                VisualPrimitiveKind.REORDER,
                sources,
                targets,
                scope="goal-order",
                evidence=(effect.kind.value,),
            )
            continue
        if effect.kind is GoalEffectKind.FOCUS:
            sources = tuple(
                before_anchors[item] for item in _goal_refs(before.focus, before)
            )
            targets = tuple(
                after_anchors[item] for item in _goal_refs(effect.focus, after)
            )
            builder.add(
                VisualPrimitiveKind.FOCUS,
                sources,
                targets,
                scope="goal-focus",
                evidence=(effect.kind.value,),
            )
            continue
        source_refs = _goal_refs(effect.source_goal_ids, before)
        target_ids = tuple(item.goal_id for item in effect.target_descriptors) or tuple(
            item.goal_id for item in effect.created_goals
        )
        target_refs = _goal_refs(target_ids, after)
        if covered(source_refs, target_refs) and not directly_corresponded(
            source_refs, target_refs
        ):
            continue
        sources = tuple(before_anchors[item] for item in source_refs)
        targets = tuple(after_anchors[item] for item in target_refs)
        kind = _goal_effect_kind(effect)
        if kind is VisualPrimitiveKind.KEEP and not _same_layout(sources, targets):
            kind = VisualPrimitiveKind.MOVE
        _primitive, created = builder.add(
            kind,
            sources,
            targets,
            scope="goal",
            evidence=(effect.kind.value,),
        )
        if created:
            builder.add(
                kind,
                sources,
                targets,
                scope="goal",
                evidence=(effect.kind.value,),
                fallback_reason=f"{effect.kind.value} had no correspondence edge",
            )


def plan_visual_transition(
    before: ProofState,
    after: ProofState,
    transition: ProofTransition,
) -> SemanticVisualPlan:
    """Compile one canonical transition into deterministic visual primitives."""

    transition = transition.normalized()
    if transition.before_fingerprint != before.fingerprint:
        raise ValueError("visual plan source fingerprint does not match state")
    if transition.after_fingerprint != after.fingerprint:
        raise ValueError("visual plan target fingerprint does not match state")
    correspondence_errors = validate_correspondence(
        before, after, transition.correspondence
    )
    if correspondence_errors:
        raise ValueError(
            "invalid visual correspondence: " + "; ".join(correspondence_errors)
        )
    if apply_transition(before, transition) != after:
        raise ValueError("visual plan transition does not replay to target state")

    edges = transition.correspondence.edges
    anchors, before_anchors, after_anchors = build_layout_anchors(before, after, edges)
    builder = _PlanBuilder()
    closed_goal_ids = frozenset(
        goal_id
        for effect in transition.goal_effects
        if effect.kind is GoalEffectKind.CLOSE
        for goal_id in effect.source_goal_ids
    )
    target_rewrites = frozenset(
        effect.goal_id
        for effect in transition.target_effects
        if effect.kind is not TargetEffectKind.KEEP
    )
    for edge in edges:
        if edge.provenance is MatchProvenance.TEXT_FALLBACK:
            # Rendered equality is useful diagnostic evidence, not proof of
            # object continuity.  Reject the proposed continuity and render
            # two independent lifecycle events.  This is not a visual
            # fallback: remove/create is the complete, semantically safe plan
            # when Lean supplied no identity, defeq, alias, source, or typed
            # structural evidence connecting the occurrences.
            sources = tuple(before_anchors[item] for item in edge.sources)
            targets = tuple(after_anchors[item] for item in edge.targets)
            _removed, _ = builder.add(
                VisualPrimitiveKind.REMOVE,
                sources,
                (),
                scope=_scope(edge.sources),
                provenance=(edge.provenance.value,),
                evidence=edge.evidence,
                confidence=edge.confidence,
            )
            created, _ = builder.add(
                VisualPrimitiveKind.CREATE,
                (),
                targets,
                scope=_scope(edge.targets),
                provenance=(edge.provenance.value,),
                evidence=edge.evidence,
                confidence=edge.confidence,
            )
            builder.diagnostics.append(
                PlanDiagnostic(
                    "uncertified-text-continuity-rejected",
                    "rendered equality was rejected as identity and planned as remove/create",
                    created.primitive_id,
                    tuple(item.key for item in (*edge.sources, *edge.targets)),
                )
            )
            continue
        sources = tuple(before_anchors[item] for item in edge.sources)
        targets = tuple(after_anchors[item] for item in edge.targets)
        kind = _edge_kind(
            edge,
            sources,
            targets,
            closed_goal_ids=closed_goal_ids,
            target_rewrites=target_rewrites,
        )
        _primitive, _created = builder.add(
            kind,
            sources,
            targets,
            scope=_scope((*edge.sources, *edge.targets)),
            provenance=(edge.provenance.value,),
            evidence=edge.evidence,
            confidence=edge.confidence,
        )
    _effect_fallbacks(
        builder,
        transition,
        before,
        after,
        before_anchors,
        after_anchors,
    )
    plan = SemanticVisualPlan(
        before_fingerprint=before.fingerprint,
        after_fingerprint=after.fingerprint,
        anchors=anchors,
        primitives=builder.primitives,
        diagnostics=tuple(
            sorted(
                dict.fromkeys(builder.diagnostics),
                key=lambda item: (item.code, item.primitive_id, item.entity_keys),
            )
        ),
    )
    errors = validate_visual_plan(plan)
    if errors:
        raise ValueError("invalid semantic visual plan: " + "; ".join(errors))
    return plan
