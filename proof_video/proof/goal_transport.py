"""Lift goal-forest hyperedges to their shared semantic contents.

A split goal is not an opaque deletion followed by several unrelated cards.
Free locals and maximal typed subtrees that Lean preserved are copied into the
children; a merge is the inverse relation.  The construction is purely about
canonical states and goal lineage, never tactic spelling or rendered text.
"""

from __future__ import annotations

from dataclasses import dataclass

from proof_video.proof.correspondence import (
    Correspondence,
    CorrespondenceEdge,
    EntityKind,
    EntityRef,
    MatchProvenance,
    RelationKind,
    expression_ref,
    local_ref,
    occurrence_ref,
)
from proof_video.proof.state import (
    ExprOccurrence,
    Expression,
    GoalState,
    LocalDecl,
    ProofState,
    expression_path_sort_key,
)


@dataclass(frozen=True)
class _Owner:
    goal: GoalState
    expression: Expression
    role: EntityKind
    expression_role: str
    local_id: str = ""

    @property
    def root_ref(self) -> EntityRef:
        return expression_ref(
            self.goal,
            self.role,
            self.expression_role,
            local_id=self.local_id,
        )

    def occurrence_ref(self, node: ExprOccurrence) -> EntityRef:
        return occurrence_ref(
            self.goal,
            node,
            self.expression_role,
            local_id=self.local_id,
        )


def _local_owner(
    goal: GoalState, local: LocalDecl, *, value: bool = False
) -> _Owner | None:
    expression = local.value_expr if value else local.type_expr
    if expression is None:
        return None
    return _Owner(
        goal,
        expression,
        EntityKind.LOCAL_VALUE if value else EntityKind.LOCAL_TYPE,
        "local-value" if value else "local-type",
        local.decl_id,
    )


def _target_owner(goal: GoalState) -> _Owner:
    return _Owner(goal, goal.target, EntityKind.TARGET, "target")


def _node_key(node: ExprOccurrence) -> tuple[str, str, str, str]:
    return (
        node.kind,
        node.fingerprint,
        node.type_fingerprint,
        node.lean_identity,
    )


def _maximal_unique_subtrees(
    sources: tuple[_Owner, ...],
    targets: tuple[_Owner, ...],
) -> tuple[CorrespondenceEdge, ...]:
    source_groups: dict[
        tuple[str, str, str, str], list[tuple[_Owner, ExprOccurrence]]
    ] = {}
    target_groups: dict[
        tuple[str, str, str, str], list[tuple[_Owner, ExprOccurrence]]
    ] = {}
    for owner in sources:
        for node in owner.expression.occurrences:
            source_groups.setdefault(_node_key(node), []).append((owner, node))
    for owner in targets:
        for node in owner.expression.occurrences:
            target_groups.setdefault(_node_key(node), []).append((owner, node))

    candidates: list[
        tuple[_Owner, ExprOccurrence, tuple[tuple[_Owner, ExprOccurrence], ...]]
    ] = []
    for key in sorted(source_groups, key=repr):
        old = source_groups[key]
        new = target_groups.get(key, ())
        # Repeated equal-looking subtrees are ambiguous.  A single certified
        # source may, however, occur once in several distinct branch goals.
        if len(old) != 1 or not new:
            continue
        by_goal: dict[str, list[tuple[_Owner, ExprOccurrence]]] = {}
        for item in new:
            by_goal.setdefault(item[0].goal.goal_id, []).append(item)
        if any(len(items) != 1 for items in by_goal.values()):
            continue
        candidates.append(
            (old[0][0], old[0][1], tuple(items[0] for items in by_goal.values()))
        )

    # An ancestor subtree already contains all of its visible descendants.
    # Keeping only maximal matches prevents double animation of the same ink.
    selected: list[
        tuple[_Owner, ExprOccurrence, tuple[tuple[_Owner, ExprOccurrence], ...]]
    ] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (
            len(item[1].path),
            expression_path_sort_key(item[1].path),
        ),
    ):
        owner, node, target_nodes = candidate
        if any(
            existing_owner == owner
            and node.path[: len(existing_node.path)] == existing_node.path
            for existing_owner, existing_node, _targets in selected
        ):
            continue
        selected.append((owner, node, target_nodes))

    result: list[CorrespondenceEdge] = []
    for owner, node, target_nodes in selected:
        source_ref = owner.occurrence_ref(node)
        target_refs = tuple(
            target_owner.occurrence_ref(target_node)
            for target_owner, target_node in target_nodes
        )
        result.append(
            CorrespondenceEdge(
                (source_ref,),
                target_refs,
                RelationKind.COPY if len(target_refs) > 1 else RelationKind.PRESERVE,
                MatchProvenance.LEAN_IDENTITY
                if node.lean_identity
                else MatchProvenance.TYPED_STRUCTURE,
                ("goal-forest-shared-typed-subtree",),
            )
        )
    return tuple(result)


def _same_local_edges(
    source_goal: GoalState,
    target_goals: tuple[GoalState, ...],
) -> tuple[CorrespondenceEdge, ...]:
    result: list[CorrespondenceEdge] = []
    target_by_goal = [
        {local.decl_id: local for local in goal.locals} for goal in target_goals
    ]
    for source_local in source_goal.locals:
        targets = tuple(
            (goal, locals_[source_local.decl_id])
            for goal, locals_ in zip(target_goals, target_by_goal, strict=True)
            if source_local.decl_id in locals_
        )
        if not targets:
            continue
        target_refs = tuple(local_ref(goal, local) for goal, local in targets)
        result.append(
            CorrespondenceEdge(
                (local_ref(source_goal, source_local),),
                target_refs,
                RelationKind.COPY if len(target_refs) > 1 else RelationKind.PRESERVE,
                MatchProvenance.LEAN_IDENTITY,
                ("same-fvar-across-goal-forest",),
            )
        )
        for value in (False, True):
            source_owner = _local_owner(source_goal, source_local, value=value)
            if source_owner is None:
                continue
            target_owners = tuple(
                owner
                for goal, local in targets
                if (owner := _local_owner(goal, local, value=value)) is not None
            )
            if len(target_owners) != len(targets):
                continue
            if all(
                source_owner.expression.canonical_key == owner.expression.canonical_key
                for owner in target_owners
            ):
                roots = tuple(owner.root_ref for owner in target_owners)
                result.append(
                    CorrespondenceEdge(
                        (source_owner.root_ref,),
                        roots,
                        RelationKind.COPY if len(roots) > 1 else RelationKind.PRESERVE,
                        MatchProvenance.LEAN_IDENTITY,
                        ("same-local-expression-across-goal-forest",),
                    )
                )
            result.extend(_maximal_unique_subtrees((source_owner,), target_owners))
    return tuple(result)


def _reverse(edge: CorrespondenceEdge) -> CorrespondenceEdge:
    relation = (
        RelationKind.MERGE
        if edge.relation in {RelationKind.COPY, RelationKind.SPLIT}
        else edge.relation
    )
    return CorrespondenceEdge(
        edge.targets,
        edge.sources,
        relation,
        edge.provenance,
        tuple(f"merge:{item}" for item in edge.evidence),
        edge.confidence,
    )


def _append_nonconflicting(
    correspondence: Correspondence,
    additions: tuple[CorrespondenceEdge, ...],
) -> Correspondence:
    edges = list(correspondence.edges)
    occupied_sources = {ref for edge in edges for ref in edge.sources}
    occupied_targets = {ref for edge in edges for ref in edge.targets}
    for edge in additions:
        if occupied_sources.intersection(edge.sources) or occupied_targets.intersection(
            edge.targets
        ):
            continue
        edges.append(edge)
        occupied_sources.update(edge.sources)
        occupied_targets.update(edge.targets)
    return Correspondence(tuple(edges)).normalized()


def augment_goal_transport(
    before: ProofState,
    after: ProofState,
    correspondence: Correspondence,
) -> Correspondence:
    """Propagate content through every certified split or merge hyperedge."""

    before_by_id = {goal.goal_id: goal for goal in before.goals}
    after_by_id = {goal.goal_id: goal for goal in after.goals}
    result = correspondence
    goal_edges = tuple(
        edge
        for edge in correspondence.edges
        if edge.sources
        and edge.targets
        and all(ref.kind is EntityKind.GOAL for ref in (*edge.sources, *edge.targets))
    )
    for edge in goal_edges:
        if len(edge.sources) == 1 and len(edge.targets) > 1:
            source = before_by_id[edge.sources[0].goal_id]
            targets = tuple(after_by_id[ref.goal_id] for ref in edge.targets)
            additions = (
                *_same_local_edges(source, targets),
                *_maximal_unique_subtrees(
                    (_target_owner(source),),
                    tuple(_target_owner(goal) for goal in targets),
                ),
            )
            result = _append_nonconflicting(result, additions)
        elif len(edge.sources) > 1 and len(edge.targets) == 1:
            sources = tuple(before_by_id[ref.goal_id] for ref in edge.sources)
            target = after_by_id[edge.targets[0].goal_id]
            forward = (
                *_same_local_edges(target, sources),
                *_maximal_unique_subtrees(
                    (_target_owner(target),),
                    tuple(_target_owner(goal) for goal in sources),
                ),
            )
            result = _append_nonconflicting(
                result, tuple(_reverse(item) for item in forward)
            )
    return result
