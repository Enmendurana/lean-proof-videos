"""Deterministic observation and replay of canonical proof-state changes."""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from proof_video.proof.binder_transport import augment_binder_transport
from proof_video.proof.correspondence import (
    Correspondence,
    CorrespondenceEdge,
    EntityKind,
    ExplicitGoalEdge,
    ExplicitOccurrenceEdge,
    RelationKind,
    build_correspondence,
    complete_correspondence,
    validate_total_correspondence,
)
from proof_video.proof.effects import (
    ContextEffect,
    ContextEffectKind,
    GoalDescriptor,
    GoalEffect,
    GoalEffectKind,
    ProofTransition,
    TargetEffect,
    TargetEffectKind,
    TransitionMetadata,
    apply_transition,
)
from proof_video.proof.goal_transport import augment_goal_transport
from proof_video.proof.state import Expression, GoalState, ProofState, validate_state


def _goal_correspondence(
    correspondence: Correspondence,
    before: ProofState,
    after: ProofState,
) -> tuple[
    tuple[tuple[str, str], ...],
    tuple[tuple[str, tuple[str, ...]], ...],
    tuple[tuple[tuple[str, ...], str], ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    preserved: list[tuple[str, str]] = []
    splits: list[tuple[str, tuple[str, ...]]] = []
    merges: list[tuple[tuple[str, ...], str]] = []
    removed: list[str] = []
    created: list[str] = []
    before_order = {goal_id: index for index, goal_id in enumerate(before.goal_order)}
    after_order = {goal_id: index for index, goal_id in enumerate(after.goal_order)}
    for edge in correspondence.edges:
        sources = tuple(
            sorted(
                [item.goal_id for item in edge.sources if item.kind == EntityKind.GOAL],
                key=before_order.__getitem__,
            )
        )
        targets = tuple(
            sorted(
                [item.goal_id for item in edge.targets if item.kind == EntityKind.GOAL],
                key=after_order.__getitem__,
            )
        )
        if not sources and not targets:
            continue
        if len(sources) == len(targets) == 1 and edge.relation in {
            RelationKind.PRESERVE,
            RelationKind.REWRITE,
        }:
            preserved.append((sources[0], targets[0]))
        elif len(sources) == 1 and len(targets) > 1:
            splits.append((sources[0], targets))
        elif len(sources) > 1 and len(targets) == 1:
            merges.append((sources, targets[0]))
        elif sources and not targets:
            removed.extend(sources)
        elif targets and not sources:
            created.extend(targets)
    return (
        tuple(sorted(preserved, key=lambda item: before_order[item[0]])),
        tuple(sorted(splits, key=lambda item: before_order[item[0]])),
        tuple(sorted(merges, key=lambda item: after_order[item[1]])),
        tuple(sorted(dict.fromkeys(removed), key=before_order.__getitem__)),
        tuple(sorted(dict.fromkeys(created), key=after_order.__getitem__)),
    )


def _local_correspondence(
    correspondence: Correspondence,
    source_goal_id: str,
    target_goal_id: str,
) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for edge in correspondence.edges:
        sources = [
            item
            for item in edge.sources
            if item.kind == EntityKind.LOCAL and item.goal_id == source_goal_id
        ]
        targets = [
            item
            for item in edge.targets
            if item.kind == EntityKind.LOCAL and item.goal_id == target_goal_id
        ]
        if len(sources) == len(targets) == 1:
            result.append((sources[0].local_id, targets[0].local_id))
    return tuple(result)


def _context_effects(
    source_goal: GoalState,
    target_goal: GoalState,
    correspondence: Correspondence,
) -> tuple[ContextEffect, ...]:
    source_by_id = {item.decl_id: item for item in source_goal.locals}
    target_by_id = {item.decl_id: item for item in target_goal.locals}
    source_order = {
        item.decl_id: index for index, item in enumerate(source_goal.locals)
    }
    target_order = {
        item.decl_id: index for index, item in enumerate(target_goal.locals)
    }
    pairs = _local_correspondence(
        correspondence, source_goal.goal_id, target_goal.goal_id
    )
    mapped_source = {source for source, _target in pairs}
    mapped_target = {target for _source, target in pairs}
    removed_entity_ids = frozenset(source_by_id) - mapped_source
    result: list[ContextEffect] = []

    def mentions(expression: Expression, decl_id: str) -> bool:
        return any(
            occurrence.lean_identity == f"fvar:{decl_id}"
            for occurrence in expression.occurrences
        )

    for source_id, target_id in pairs:
        source = source_by_id[source_id]
        target = target_by_id[target_id]
        if source_id != target_id:
            result.append(
                ContextEffect(
                    ContextEffectKind.REPLACE_LOCAL,
                    target_goal.goal_id,
                    source,
                    target,
                    source_order[source_id],
                    target_order[target_id],
                )
            )
            continue
        current = source
        if current.user_name != target.user_name:
            renamed = replace(
                current,
                user_name=target.user_name,
            )
            result.append(
                ContextEffect(
                    ContextEffectKind.RENAME_LOCAL,
                    target_goal.goal_id,
                    current,
                    renamed,
                    source_order[source_id],
                    target_order[target_id],
                )
            )
            current = renamed
        if current.type_expr != target.type_expr:
            substituted_entities = tuple(
                sorted(
                    decl_id
                    for decl_id in removed_entity_ids
                    if mentions(current.type_expr, decl_id)
                    and not mentions(target.type_expr, decl_id)
                )
            )
            updated = replace(
                current,
                type_expr=target.type_expr,
                dependencies=target.dependencies,
                is_proof=target.is_proof,
            )
            result.append(
                ContextEffect(
                    ContextEffectKind.UPDATE_LOCAL_TYPE,
                    target_goal.goal_id,
                    current,
                    updated,
                    source_order[source_id],
                    target_order[target_id],
                    entity_ids=substituted_entities,
                )
            )
            current = updated
        if current.value_expr is None and target.value_expr is not None:
            updated = replace(current, value_expr=target.value_expr)
            result.append(
                ContextEffect(
                    ContextEffectKind.ADD_LOCAL_DEFINITION,
                    target_goal.goal_id,
                    current,
                    updated,
                    source_order[source_id],
                    target_order[target_id],
                )
            )
            current = updated
        elif current.value_expr is not None and target.value_expr is None:
            updated = replace(current, value_expr=None)
            result.append(
                ContextEffect(
                    ContextEffectKind.CLEAR_LOCAL_VALUE,
                    target_goal.goal_id,
                    current,
                    updated,
                    source_order[source_id],
                    target_order[target_id],
                )
            )
            current = updated
        elif current.value_expr != target.value_expr:
            updated = replace(current, value_expr=target.value_expr)
            result.append(
                ContextEffect(
                    ContextEffectKind.UPDATE_LOCAL_VALUE,
                    target_goal.goal_id,
                    current,
                    updated,
                    source_order[source_id],
                    target_order[target_id],
                )
            )
            current = updated

        if current != target:
            # Extraction metadata, dependency evidence and aliases are part
            # of the canonical declaration even when its displayed
            # name/type/value is unchanged.  Carry the exact immutable target
            # rather than silently losing that information during replay.
            result.append(
                ContextEffect(
                    ContextEffectKind.UPDATE_LOCAL_METADATA,
                    target_goal.goal_id,
                    current,
                    target,
                    source_order[source_id],
                    target_order[target_id],
                )
            )

    for source in source_goal.locals:
        if source.decl_id not in mapped_source:
            result.append(
                ContextEffect(
                    ContextEffectKind.REMOVE_LOCAL,
                    target_goal.goal_id,
                    source,
                    None,
                    source_order[source.decl_id],
                    None,
                )
            )
    for target in target_goal.locals:
        if target.decl_id not in mapped_target:
            result.append(
                ContextEffect(
                    (
                        ContextEffectKind.ADD_LOCAL_DEFINITION
                        if target.value_expr is not None
                        else ContextEffectKind.ADD_LOCAL
                    ),
                    target_goal.goal_id,
                    None,
                    target,
                    None,
                    target_order[target.decl_id],
                )
            )

    target_ids = tuple(item.decl_id for item in target_goal.locals)
    source_to_target = dict(pairs)
    # Creation, deletion and replacement already carry exact positions.  A
    # reorder is a separate control effect only when the *relative order of
    # persistent declarations* changes.  Comparing raw identifier tuples
    # mislabeled every intro, clear, set and replace as a reorder.
    surviving_source_order = tuple(
        source_to_target[item.decl_id]
        for item in source_goal.locals
        if item.decl_id in source_to_target
    )
    surviving_target_order = tuple(
        item.decl_id for item in target_goal.locals if item.decl_id in mapped_target
    )
    if target_ids and surviving_source_order != surviving_target_order:
        result.append(
            ContextEffect(
                ContextEffectKind.REORDER_LOCALS,
                target_goal.goal_id,
                order=target_ids,
            )
        )
    return tuple(result)


def _smallest_changed_paths(
    source: Expression, target: Expression
) -> tuple[tuple[str | int, ...], tuple[str | int, ...]]:
    old_by_path = {item.path: item for item in source.occurrences}
    new_by_path = {item.path: item for item in target.occurrences}
    changed_common = {
        path
        for path in old_by_path.keys() & new_by_path.keys()
        if (
            old_by_path[path].kind,
            old_by_path[path].fingerprint,
            old_by_path[path].lean_identity,
            old_by_path[path].type_fingerprint,
        )
        != (
            new_by_path[path].kind,
            new_by_path[path].fingerprint,
            new_by_path[path].lean_identity,
            new_by_path[path].type_fingerprint,
        )
    }
    old_only = set(old_by_path) - set(new_by_path)
    new_only = set(new_by_path) - set(old_by_path)
    if not changed_common and not old_only and not new_only:
        return (), ()

    def boundary(paths: set[tuple[str | int, ...]]) -> set[tuple[str | int, ...]]:
        return {
            path
            for path in paths
            if not any(
                len(parent) < len(path) and path[: len(parent)] == parent
                for parent in paths
            )
        }

    candidates = changed_common | boundary(old_only) | boundary(new_only)
    # A parent fingerprint normally changes when any descendant changes.  It
    # is not itself an edit if a deeper changed/boundary node explains it.
    frontier = {
        path
        for path in candidates
        if not any(
            len(other) > len(path) and other[: len(path)] == path
            for other in candidates
        )
    }

    def nearest_existing(
        path: tuple[str | int, ...],
        available: set[tuple[str | int, ...]],
    ) -> tuple[str | int, ...]:
        candidates = [
            item
            for item in available
            if len(item) <= len(path) and path[: len(item)] == item
        ]
        return max(candidates, key=len, default=())

    def common_prefix(
        paths: list[tuple[str | int, ...]],
    ) -> tuple[str | int, ...]:
        prefix = list(paths[0])
        for path in paths[1:]:
            common = 0
            for left, right in zip(prefix, path, strict=False):
                if left != right:
                    break
                common += 1
            prefix = prefix[:common]
        return tuple(prefix)

    ordered_frontier = sorted(frontier, key=lambda path: (len(path), repr(path)))
    source_paths = [
        path if path in old_by_path else nearest_existing(path, set(old_by_path))
        for path in ordered_frontier
    ]
    target_paths = [
        path if path in new_by_path else nearest_existing(path, set(new_by_path))
        for path in ordered_frontier
    ]
    return common_prefix(source_paths), common_prefix(target_paths)


def _target_effect(
    source_goal: GoalState,
    target_goal: GoalState,
    context_effects: tuple[ContextEffect, ...],
    correspondence: Correspondence,
) -> TargetEffect | None:
    source = source_goal.target
    target = target_goal.target
    if source == target:
        return None
    if source.canonical_key == target.canonical_key:
        return TargetEffect(
            TargetEffectKind.CHANGE_PRESENTATION,
            target_goal.goal_id,
            source,
            target,
        )
    source_path, target_path = _smallest_changed_paths(source, target)
    removed_ids = {
        effect.before.decl_id
        for effect in context_effects
        if effect.kind == ContextEffectKind.REMOVE_LOCAL and effect.before is not None
    }
    # Reversion also removes a free local from the context, but its certified
    # local→binder edge proves transport into a quantifier rather than
    # substitution.  Identity of the causal operation comes from the
    # correspondence, not the tactic spelling.
    reverted_ids = {
        source.local_id
        for edge in correspondence.edges
        if any("binder" in item.lower() for item in edge.evidence)
        for source in edge.sources
        if source.kind is EntityKind.LOCAL
        for target_ref in edge.targets
        if target_ref.kind is EntityKind.OCCURRENCE
    }
    substituted = next(
        (
            decl_id
            for decl_id in sorted(removed_ids - reverted_ids)
            if any(
                item.lean_identity == f"fvar:{decl_id}" for item in source.occurrences
            )
        ),
        "",
    )
    if substituted:
        kind = TargetEffectKind.SUBSTITUTE_ENTITY
    elif source_path or target_path:
        kind = TargetEffectKind.REWRITE_SUBEXPRESSION
    else:
        kind = TargetEffectKind.REWRITE
    return TargetEffect(
        kind,
        target_goal.goal_id,
        source,
        target,
        source_path,
        target_path,
        substituted,
    )


def _structural_goal_control_state(
    before: ProofState,
    effects: list[GoalEffect],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Replay only goal topology to decide whether control effects are real.

    This is deliberately smaller than full transition replay: it computes the
    canonical order and focus induced by preserve/create/close/split/merge.
    REORDER and FOCUS are emitted only for residual control changes.
    """

    shell = ProofTransition(
        before.fingerprint,
        before.fingerprint,
        Correspondence(),
        goal_effects=tuple(effects),
    ).normalized()
    order = list(before.goal_order)
    focus = list(before.focus)
    for effect in shell.goal_effects:
        if effect.kind is GoalEffectKind.PRESERVE:
            source_id = effect.source_goal_ids[0]
            target_id = effect.target_descriptors[0].goal_id
            order[order.index(source_id)] = target_id
            focus = [target_id if item == source_id else item for item in focus]
        elif effect.kind is GoalEffectKind.CLOSE:
            for source_id in effect.source_goal_ids:
                order.remove(source_id)
                focus = [item for item in focus if item != source_id]
        elif effect.kind in {
            GoalEffectKind.CREATE,
            GoalEffectKind.SPLIT,
            GoalEffectKind.MERGE,
        }:
            insertion = len(order)
            for source_id in effect.source_goal_ids:
                insertion = min(insertion, order.index(source_id))
                order.remove(source_id)
                focus = [item for item in focus if item != source_id]
            for offset, created_goal in enumerate(effect.created_goals):
                order.insert(insertion + offset, created_goal.goal_id)
    return tuple(order), tuple(focus)


def diff_proof_states(
    before: ProofState,
    after: ProofState,
    *,
    explicit_occurrence_edges: Iterable[ExplicitOccurrenceEdge] = (),
    explicit_goal_edges: Iterable[ExplicitGoalEdge] = (),
    explicit_entity_edges: Iterable[CorrespondenceEdge] = (),
    metadata: TransitionMetadata | None = None,
) -> ProofTransition:
    """Observe two states and return their deterministic normal-form delta."""

    before_errors = validate_state(before)
    after_errors = validate_state(after)
    if before_errors or after_errors:
        details = tuple(f"before: {item}" for item in before_errors) + tuple(
            f"after: {item}" for item in after_errors
        )
        raise ValueError("invalid canonical proof state: " + "; ".join(details))

    if before == after:
        return ProofTransition(
            before.fingerprint,
            after.fingerprint,
            Correspondence(),
            metadata=metadata or TransitionMetadata(),
        )
    correspondence = build_correspondence(
        before,
        after,
        explicit_occurrence_edges=explicit_occurrence_edges,
        explicit_goal_edges=explicit_goal_edges,
        explicit_entity_edges=explicit_entity_edges,
    )
    correspondence = augment_goal_transport(before, after, correspondence)
    before_by_id = {goal.goal_id: goal for goal in before.goals}
    after_by_id = {goal.goal_id: goal for goal in after.goals}
    for edge in correspondence.edges:
        sources = tuple(
            ref.goal_id for ref in edge.sources if ref.kind is EntityKind.GOAL
        )
        targets = tuple(
            ref.goal_id for ref in edge.targets if ref.kind is EntityKind.GOAL
        )
        if len(sources) == len(targets) == 1:
            correspondence = augment_binder_transport(
                before_by_id[sources[0]],
                after_by_id[targets[0]],
                correspondence,
            )
    correspondence = complete_correspondence(before, after, correspondence)
    errors = validate_total_correspondence(before, after, correspondence)
    if errors:
        raise ValueError("invalid proof-state correspondence: " + "; ".join(errors))
    preserved, splits, merges, removed, created = _goal_correspondence(
        correspondence, before, after
    )
    goal_effects: list[GoalEffect] = []
    context_effects: list[ContextEffect] = []
    target_effects: list[TargetEffect] = []
    consumed_before: set[str] = set()
    consumed_after: set[str] = set()
    for source_id, target_id in preserved:
        source_goal = before_by_id[source_id]
        target_goal = after_by_id[target_id]
        consumed_before.add(source_id)
        consumed_after.add(target_id)
        goal_effects.append(
            GoalEffect(
                GoalEffectKind.PRESERVE,
                (source_id,),
                (GoalDescriptor.of(target_goal),),
            )
        )
        local_effects = _context_effects(source_goal, target_goal, correspondence)
        context_effects.extend(local_effects)
        target_effect = _target_effect(
            source_goal, target_goal, local_effects, correspondence
        )
        if target_effect is not None:
            target_effects.append(target_effect)

    for source_id, target_ids in splits:
        consumed_before.add(source_id)
        consumed_after.update(target_ids)
        children = tuple(after_by_id[item] for item in target_ids)
        conflicting_parent = next(
            (
                child
                for child in children
                if child.parent_goal_id not in {None, source_id}
            ),
            None,
        )
        if conflicting_parent is not None:
            raise ValueError(
                f"split child {conflicting_parent.goal_id} has parent "
                f"{conflicting_parent.parent_goal_id}, expected {source_id}"
            )
        goal_effects.append(
            GoalEffect(
                GoalEffectKind.SPLIT,
                (source_id,),
                created_goals=children,
            )
        )
    for source_ids, target_id in merges:
        consumed_before.update(source_ids)
        consumed_after.add(target_id)
        goal_effects.append(
            GoalEffect(
                GoalEffectKind.MERGE,
                source_ids,
                created_goals=(after_by_id[target_id],),
            )
        )
    for source_id in removed:
        if source_id in consumed_before:
            continue
        consumed_before.add(source_id)
        goal_effects.append(GoalEffect(GoalEffectKind.CLOSE, (source_id,)))
    created_goals = tuple(
        after_by_id[target_id]
        for target_id in created
        if target_id not in consumed_after
    )
    if created_goals:
        consumed_after.update(item.goal_id for item in created_goals)
        goal_effects.append(
            GoalEffect(
                GoalEffectKind.CREATE,
                created_goals=created_goals,
            )
        )

    structural_order, structural_focus = _structural_goal_control_state(
        before, goal_effects
    )
    if structural_order != after.goal_order:
        goal_effects.append(GoalEffect(GoalEffectKind.REORDER, order=after.goal_order))
    if structural_focus != after.focus:
        goal_effects.append(GoalEffect(GoalEffectKind.FOCUS, focus=after.focus))

    return ProofTransition(
        before.fingerprint,
        after.fingerprint,
        correspondence,
        tuple(context_effects),
        tuple(target_effects),
        tuple(goal_effects),
        metadata or TransitionMetadata(),
    ).normalized()


def compose_transitions(
    before: ProofState,
    first: ProofTransition,
    second: ProofTransition,
) -> ProofTransition:
    """Compose by replaying once and re-normalizing the observable endpoint.

    This definition is intentionally extensional.  It avoids a second,
    competing algebra for pairwise cancellation while guaranteeing that the
    composite has the same semantics as ``diff(before, final)``.
    """

    middle = apply_transition(before, first)
    final = apply_transition(middle, second)
    return diff_proof_states(
        before,
        final,
        metadata=TransitionMetadata(
            source="composed-canonical-state-delta",
            notes=(
                first.metadata.proof_fingerprint,
                second.metadata.proof_fingerprint,
            ),
        ),
    )


def semantically_equivalent(
    before: ProofState,
    left: ProofTransition,
    right: ProofTransition,
) -> bool:
    return apply_transition(before, left) == apply_transition(before, right)
