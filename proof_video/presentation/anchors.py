"""Stable semantic layout anchors for canonical proof states."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from proof_video.presentation.model import AnchorSide, LayoutAnchor, LayoutRowKind
from proof_video.proof.correspondence import (
    CorrespondenceEdge,
    EntityKind,
    EntityRef,
    MatchProvenance,
    expression_ref,
    goal_ref,
    local_ref,
    occurrence_ref,
)
from proof_video.proof.state import ExprOccurrence, GoalState, LocalDecl, ProofState


@dataclass(frozen=True)
class _LocatedEntity:
    ref: EntityRef
    persistent_id: str
    goal_index: int
    row_kind: LayoutRowKind
    row_index: int
    expression_path: tuple[str | int, ...] = ()


def _goal_persistent_id(goal: GoalState) -> str:
    return f"goal:{goal.lineage_id or goal.goal_id}"


def _local_persistent_id(goal: GoalState, local: LocalDecl) -> str:
    # Extractors place the previous fvar id first when a replacement creates
    # an alias.  That keeps the local anchor stable across ``replace``.
    identity = local.aliases[0] if local.aliases else local.decl_id
    return f"{_goal_persistent_id(goal)}/local:{identity}"


def _occurrence_identity(node: ExprOccurrence) -> str:
    semantic = (
        node.aliases[0]
        if node.aliases
        else node.lean_identity or node.fingerprint or node.occurrence_id
    )
    path = json.dumps(node.path, separators=(",", ":"), ensure_ascii=False)
    return f"{node.kind}:{semantic}:path={path}"


def _locate_state(state: ProofState) -> dict[EntityRef, _LocatedEntity]:
    result: dict[EntityRef, _LocatedEntity] = {}
    for goal_index, goal in enumerate(state.goals):
        goal_identity = _goal_persistent_id(goal)
        goal_entity = goal_ref(goal)
        result[goal_entity] = _LocatedEntity(
            goal_entity,
            goal_identity,
            goal_index,
            LayoutRowKind.GOAL,
            0,
        )
        for local_index, local in enumerate(goal.locals):
            local_identity = _local_persistent_id(goal, local)
            local_entity = local_ref(goal, local)
            result[local_entity] = _LocatedEntity(
                local_entity,
                local_identity,
                goal_index,
                LayoutRowKind.CONTEXT,
                local_index,
            )
            type_entity = expression_ref(
                goal,
                EntityKind.LOCAL_TYPE,
                "local-type",
                local_id=local.decl_id,
            )
            result[type_entity] = _LocatedEntity(
                type_entity,
                f"{local_identity}/type",
                goal_index,
                LayoutRowKind.CONTEXT,
                local_index,
            )
            for node in local.type_expr.occurrences:
                entity = occurrence_ref(
                    goal,
                    node,
                    "local-type",
                    local_id=local.decl_id,
                )
                result[entity] = _LocatedEntity(
                    entity,
                    f"{local_identity}/type/{_occurrence_identity(node)}",
                    goal_index,
                    LayoutRowKind.CONTEXT,
                    local_index,
                    node.path,
                )
            if local.value_expr is not None:
                value_entity = expression_ref(
                    goal,
                    EntityKind.LOCAL_VALUE,
                    "local-value",
                    local_id=local.decl_id,
                )
                result[value_entity] = _LocatedEntity(
                    value_entity,
                    f"{local_identity}/value",
                    goal_index,
                    LayoutRowKind.CONTEXT,
                    local_index,
                )
                for node in local.value_expr.occurrences:
                    entity = occurrence_ref(
                        goal,
                        node,
                        "local-value",
                        local_id=local.decl_id,
                    )
                    result[entity] = _LocatedEntity(
                        entity,
                        f"{local_identity}/value/{_occurrence_identity(node)}",
                        goal_index,
                        LayoutRowKind.CONTEXT,
                        local_index,
                        node.path,
                    )
        target_entity = expression_ref(goal, EntityKind.TARGET, "target")
        target_row = len(goal.locals)
        result[target_entity] = _LocatedEntity(
            target_entity,
            f"{goal_identity}/target",
            goal_index,
            LayoutRowKind.TARGET,
            target_row,
        )
        for node in goal.target.occurrences:
            entity = occurrence_ref(goal, node, "target")
            result[entity] = _LocatedEntity(
                entity,
                f"{goal_identity}/target/{_occurrence_identity(node)}",
                goal_index,
                LayoutRowKind.TARGET,
                target_row,
                node.path,
            )
    return result


def _anchor_id(side: AnchorSide, entity: EntityRef) -> str:
    digest = hashlib.sha256(f"{side.value}:{entity.key}".encode()).hexdigest()[:20]
    return f"anchor:{side.value}:{digest}"


def build_layout_anchors(
    before: ProofState,
    after: ProofState,
    edges: tuple[CorrespondenceEdge, ...],
) -> tuple[
    tuple[LayoutAnchor, ...],
    dict[EntityRef, LayoutAnchor],
    dict[EntityRef, LayoutAnchor],
]:
    before_locations = _locate_state(before)
    after_locations = _locate_state(after)
    before_persistent = {
        ref: item.persistent_id for ref, item in before_locations.items()
    }
    after_persistent = {
        ref: item.persistent_id for ref, item in after_locations.items()
    }

    # A 1→1 semantic edge owns one persistent visual identity.  Selecting the
    # target identity means the intermediate state uses the same id as the
    # next transition.  Merge sources similarly converge on the target id.
    for edge in edges:
        if (
            edge.provenance is MatchProvenance.TEXT_FALLBACK
            or len(edge.targets) != 1
            or not edge.sources
        ):
            continue
        target = edge.targets[0]
        persistent = after_persistent[target]
        for source in edge.sources:
            before_persistent[source] = persistent

    def make(
        side: AnchorSide,
        locations: dict[EntityRef, _LocatedEntity],
        persistent: dict[EntityRef, str],
    ) -> tuple[LayoutAnchor, ...]:
        return tuple(
            LayoutAnchor(
                anchor_id=_anchor_id(side, ref),
                persistent_id=persistent[ref],
                side=side,
                entity=ref,
                goal_index=item.goal_index,
                row_kind=item.row_kind,
                row_index=item.row_index,
                expression_path=item.expression_path,
            )
            for ref, item in sorted(locations.items(), key=lambda pair: pair[0].key)
        )

    before_anchors = make(AnchorSide.BEFORE, before_locations, before_persistent)
    after_anchors = make(AnchorSide.AFTER, after_locations, after_persistent)
    before_by_ref = {item.entity: item for item in before_anchors}
    after_by_ref = {item.entity: item for item in after_anchors}
    return (
        (*before_anchors, *after_anchors),
        before_by_ref,
        after_by_ref,
    )
