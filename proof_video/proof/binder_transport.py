"""Quantifier/context correspondence derived from canonical expression trees.

Introducing or reverting a binder changes its representation from a bound
occurrence in a ``forall`` target to a free local declaration (or conversely).
Lean therefore cannot give both sides the same raw identifier.  This module
recognizes that one general alpha-equivalent state change without consulting a
tactic name.  It augments, but never overrides, stronger identity/alias edges.
"""

from __future__ import annotations

from dataclasses import dataclass

from proof_video.proof.correspondence import (
    Correspondence,
    CorrespondenceEdge,
    EntityKind,
    MatchProvenance,
    RelationKind,
    local_ref,
    occurrence_ref,
)
from proof_video.proof.state import (
    ExprOccurrence,
    Expression,
    GoalState,
    LocalDecl,
    expression_path_sort_key,
)


_DECORATION_KINDS = frozenset(
    {"quantifier-symbol", "declaration", "declaration-punctuation"}
)
_FORALL_KINDS = frozenset({"forall", "forallE"})


@dataclass(frozen=True)
class _BinderLayer:
    root: ExprOccurrence
    binder: ExprOccurrence
    domain: ExprOccurrence


def _actual_nodes(expression: Expression) -> tuple[ExprOccurrence, ...]:
    return tuple(
        node for node in expression.occurrences if node.kind not in _DECORATION_KINDS
    )


def _root(expression: Expression) -> ExprOccurrence | None:
    nodes = _actual_nodes(expression)
    roots = tuple(node for node in nodes if node.parent_id is None)
    return min(
        roots or nodes,
        key=lambda node: (len(node.path), node.occurrence_id),
        default=None,
    )


def _at_path(
    expression: Expression, path: tuple[str | int, ...]
) -> ExprOccurrence | None:
    return next(
        (node for node in _actual_nodes(expression) if node.path == path),
        None,
    )


def _decorative_child(
    expression: Expression,
    parent: ExprOccurrence,
    kind: str,
) -> ExprOccurrence | None:
    candidates = tuple(
        node
        for node in expression.occurrences
        if node.parent_id == parent.occurrence_id and node.kind == kind
    )
    return min(
        candidates,
        key=lambda node: (expression_path_sort_key(node.path), node.occurrence_id),
        default=None,
    )


def _peel_foralls(
    expression: Expression,
    count: int,
) -> tuple[tuple[_BinderLayer, ...], ExprOccurrence] | None:
    current = _root(expression)
    if current is None:
        return None
    layers: list[_BinderLayer] = []
    for _index in range(count):
        if current.kind not in _FORALL_KINDS:
            return None
        domain = _at_path(expression, (*current.path, 0))
        body = _at_path(expression, (*current.path, 1))
        binder = _decorative_child(expression, current, "declaration")
        if domain is None or body is None or binder is None:
            return None
        layers.append(_BinderLayer(current, binder, domain))
        current = body
    return tuple(layers), current


def _relative_path(
    node: ExprOccurrence,
    root: ExprOccurrence,
) -> tuple[str | int, ...] | None:
    prefix = root.path
    if node.path[: len(prefix)] != prefix:
        return None
    return node.path[len(prefix) :]


def _binder_depth(
    expression: Expression,
    node: ExprOccurrence,
    residual_root: ExprOccurrence,
) -> int:
    by_id = {item.occurrence_id: item for item in _actual_nodes(expression)}
    depth = 0
    current = node
    while current.occurrence_id != residual_root.occurrence_id:
        if current.parent_id is None or current.parent_id not in by_id:
            return -1
        current = by_id[current.parent_id]
        if current.kind in _FORALL_KINDS:
            depth += 1
    return depth


def _compatible_atom(
    source: ExprOccurrence,
    target: ExprOccurrence,
    *,
    source_expression: Expression,
    residual_root: ExprOccurrence,
    introduced: tuple[LocalDecl, ...],
) -> bool:
    if source.kind == "bvar" and target.kind == "fvar":
        try:
            bound_index = int(source.lean_identity.removeprefix("bvar:"))
        except ValueError:
            return False
        local_depth = _binder_depth(source_expression, source, residual_root)
        peeled_index = bound_index - local_depth
        local_index = len(introduced) - peeled_index - 1
        return (
            local_depth >= 0
            and 0 <= local_index < len(introduced)
            and target.lean_identity == f"fvar:{introduced[local_index].decl_id}"
            and (
                not source.type_fingerprint
                or not target.type_fingerprint
                or source.type_fingerprint == target.type_fingerprint
            )
        )
    if source.kind != target.kind:
        return False
    if source.lean_identity or target.lean_identity:
        return source.lean_identity == target.lean_identity
    return source.fingerprint == target.fingerprint and (
        not source.type_fingerprint
        or not target.type_fingerprint
        or source.type_fingerprint == target.type_fingerprint
    )


def _alpha_body_pairs(
    quantified: Expression,
    residual_root: ExprOccurrence,
    body: Expression,
    introduced: tuple[LocalDecl, ...],
) -> tuple[tuple[ExprOccurrence, ExprOccurrence], ...] | None:
    body_root = _root(body)
    if body_root is None:
        return None
    targets_by_relative_path = {
        relative: node
        for node in _actual_nodes(body)
        if (relative := _relative_path(node, body_root)) is not None
    }
    pairs: list[tuple[ExprOccurrence, ExprOccurrence]] = []
    for source in _actual_nodes(quantified):
        relative = _relative_path(source, residual_root)
        if relative is None:
            continue
        target = targets_by_relative_path.get(relative)
        if target is None or not _compatible_atom(
            source,
            target,
            source_expression=quantified,
            residual_root=residual_root,
            introduced=introduced,
        ):
            return None
        pairs.append((source, target))
    if len(pairs) != len(targets_by_relative_path):
        return None
    return tuple(pairs)


def _transport_edges(
    quantified_goal: GoalState,
    body_goal: GoalState,
    locals_: tuple[LocalDecl, ...],
) -> tuple[CorrespondenceEdge, ...]:
    peeled = _peel_foralls(quantified_goal.target, len(locals_))
    if peeled is None:
        return ()
    layers, residual_root = peeled
    if any(
        layer.binder.fingerprint != local.type_expr.fingerprint
        and layer.domain.fingerprint != local.type_expr.fingerprint
        for layer, local in zip(layers, locals_, strict=True)
    ):
        return ()
    body_pairs = _alpha_body_pairs(
        quantified_goal.target,
        residual_root,
        body_goal.target,
        locals_,
    )
    if body_pairs is None:
        return ()

    edges = [
        CorrespondenceEdge(
            (
                occurrence_ref(
                    quantified_goal,
                    layer.binder,
                    "target",
                ),
            ),
            (local_ref(body_goal, local),),
            RelationKind.PRESERVE,
            MatchProvenance.TYPED_STRUCTURE,
            ("forall-binder-to-local",),
        )
        for layer, local in zip(layers, locals_, strict=True)
    ]
    for source, target in body_pairs:
        source_ref = occurrence_ref(quantified_goal, source, "target")
        target_ref = occurrence_ref(body_goal, target, "target")
        provenance = (
            MatchProvenance.LEAN_IDENTITY
            if source.lean_identity == target.lean_identity and source.lean_identity
            else MatchProvenance.TYPED_STRUCTURE
        )
        edges.append(
            CorrespondenceEdge(
                (source_ref,),
                (target_ref,),
                RelationKind.PRESERVE,
                provenance,
                ("alpha-equivalent-forall-body",),
            )
        )
    return tuple(edges)


def _unmatched_locals(
    correspondence: Correspondence,
    goal_id: str,
    relation: RelationKind,
    *,
    source: bool,
) -> frozenset[str]:
    result: set[str] = set()
    for edge in correspondence.edges:
        if edge.relation is not relation:
            continue
        refs = edge.sources if source else edge.targets
        result.update(
            ref.local_id
            for ref in refs
            if ref.kind is EntityKind.LOCAL and ref.goal_id == goal_id
        )
    return frozenset(result)


def _augment(
    base: Correspondence,
    additions: tuple[CorrespondenceEdge, ...],
) -> Correspondence:
    edges = list(base.edges)
    for addition in additions:
        sources = set(addition.sources)
        targets = set(addition.targets)
        conflicts = [
            edge
            for edge in edges
            if (
                sources.intersection(edge.sources) or targets.intersection(edge.targets)
            )
            and edge.relation not in {RelationKind.CREATE, RelationKind.REMOVE}
        ]
        if conflicts:
            # Stronger identity/alias/certified relations remain authoritative.
            continue
        edges = [
            edge
            for edge in edges
            if not (
                edge.relation in {RelationKind.CREATE, RelationKind.REMOVE}
                and (
                    sources.intersection(edge.sources)
                    or targets.intersection(edge.targets)
                )
            )
        ]
        edges.append(addition)
    return Correspondence(tuple(edges)).normalized()


def augment_binder_transport(
    before_goal: GoalState,
    after_goal: GoalState,
    correspondence: Correspondence,
) -> Correspondence:
    """Add intro/revert continuity when state shape proves alpha-equivalence."""

    added_ids = _unmatched_locals(
        correspondence,
        after_goal.goal_id,
        RelationKind.CREATE,
        source=False,
    )
    added = tuple(local for local in after_goal.locals if local.decl_id in added_ids)
    if added:
        return _augment(
            correspondence,
            _transport_edges(before_goal, after_goal, added),
        )

    removed_ids = _unmatched_locals(
        correspondence,
        before_goal.goal_id,
        RelationKind.REMOVE,
        source=True,
    )
    removed = tuple(
        local for local in before_goal.locals if local.decl_id in removed_ids
    )
    if not removed:
        return correspondence
    forward = _transport_edges(after_goal, before_goal, removed)
    reversed_edges = tuple(
        CorrespondenceEdge(
            edge.targets,
            edge.sources,
            edge.relation,
            edge.provenance,
            tuple(f"revert:{item}" for item in edge.evidence),
            edge.confidence,
        )
        for edge in forward
    )
    return _augment(correspondence, reversed_edges)
