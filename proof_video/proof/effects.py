"""The small algebra of canonical proof-state effects."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import TypeAlias

from proof_video.proof.correspondence import Correspondence, validate_correspondence
from proof_video.proof.state import (
    Expression,
    GoalState,
    LocalDecl,
    ProofState,
    validate_state,
)


class ContextEffectKind(str, Enum):
    ADD_LOCAL = "add-local"
    REMOVE_LOCAL = "remove-local"
    RENAME_LOCAL = "rename-local"
    UPDATE_LOCAL_TYPE = "update-local-type"
    ADD_LOCAL_DEFINITION = "add-local-definition"
    UPDATE_LOCAL_VALUE = "update-local-value"
    CLEAR_LOCAL_VALUE = "clear-local-value"
    UPDATE_LOCAL_METADATA = "update-local-metadata"
    REPLACE_LOCAL = "replace-local"
    REORDER_LOCALS = "reorder-locals"


class TargetEffectKind(str, Enum):
    KEEP = "keep-target"
    REWRITE = "rewrite-target"
    REWRITE_SUBEXPRESSION = "rewrite-subexpression"
    CHANGE_PRESENTATION = "change-presentation"
    SUBSTITUTE_ENTITY = "substitute-entity"


class GoalEffectKind(str, Enum):
    PRESERVE = "preserve-goal"
    CREATE = "create-goal"
    CLOSE = "close-goal"
    SPLIT = "split-goal"
    MERGE = "merge-goals"
    REORDER = "reorder-goals"
    FOCUS = "focus-goal"


@dataclass(frozen=True)
class GoalDescriptor:
    goal_id: str
    lineage_id: str
    parent_goal_id: str | None
    branch_kind: str
    branch_index: int | None
    metadata: tuple[tuple[str, str], ...] = ()

    @classmethod
    def of(cls, goal: GoalState) -> GoalDescriptor:
        return cls(
            goal_id=goal.goal_id,
            lineage_id=goal.lineage_id,
            parent_goal_id=goal.parent_goal_id,
            branch_kind=goal.branch_kind,
            branch_index=goal.branch_index,
            metadata=goal.metadata,
        )


@dataclass(frozen=True)
class ContextEffect:
    kind: ContextEffectKind
    goal_id: str
    before: LocalDecl | None = None
    after: LocalDecl | None = None
    old_index: int | None = None
    new_index: int | None = None
    order: tuple[str, ...] = ()
    # Immutable Lean declaration identities whose removal caused this local's
    # type to change (for example substitution through a dependent
    # hypothesis).  These are causal evidence, not display names.
    entity_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class TargetEffect:
    kind: TargetEffectKind
    goal_id: str
    before: Expression
    after: Expression
    source_path: tuple[str | int, ...] = ()
    target_path: tuple[str | int, ...] = ()
    entity_id: str = ""


@dataclass(frozen=True)
class GoalEffect:
    kind: GoalEffectKind
    source_goal_ids: tuple[str, ...] = ()
    target_descriptors: tuple[GoalDescriptor, ...] = ()
    created_goals: tuple[GoalState, ...] = ()
    order: tuple[str, ...] = ()
    focus: tuple[str, ...] = ()


Effect: TypeAlias = ContextEffect | TargetEffect | GoalEffect


@dataclass(frozen=True)
class TransitionMetadata:
    tactic_text: str = ""
    interpretation_hint: str = ""
    proof_kind: str = ""
    proof_fingerprint: str = ""
    source: str = "canonical-state-delta"
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProofTransition:
    before_fingerprint: str
    after_fingerprint: str
    correspondence: Correspondence
    context_effects: tuple[ContextEffect, ...] = ()
    target_effects: tuple[TargetEffect, ...] = ()
    goal_effects: tuple[GoalEffect, ...] = ()
    metadata: TransitionMetadata = TransitionMetadata()
    schema_version: str = "1.0"

    @property
    def effects(self) -> tuple[Effect, ...]:
        return (*self.goal_effects, *self.context_effects, *self.target_effects)

    @property
    def is_identity(self) -> bool:
        return (
            self.before_fingerprint == self.after_fingerprint
            and not self.context_effects
            and not self.target_effects
            and not self.goal_effects
            and not self.correspondence.edges
        )

    def normalized(self) -> ProofTransition:
        context_priority = {
            ContextEffectKind.REPLACE_LOCAL: 0,
            ContextEffectKind.RENAME_LOCAL: 1,
            ContextEffectKind.UPDATE_LOCAL_TYPE: 2,
            ContextEffectKind.ADD_LOCAL_DEFINITION: 3,
            ContextEffectKind.UPDATE_LOCAL_VALUE: 4,
            ContextEffectKind.CLEAR_LOCAL_VALUE: 5,
            ContextEffectKind.UPDATE_LOCAL_METADATA: 6,
            ContextEffectKind.REMOVE_LOCAL: 7,
            ContextEffectKind.ADD_LOCAL: 8,
            ContextEffectKind.REORDER_LOCALS: 9,
        }
        goal_priority = {
            GoalEffectKind.PRESERVE: 0,
            GoalEffectKind.CLOSE: 1,
            GoalEffectKind.SPLIT: 2,
            GoalEffectKind.MERGE: 3,
            GoalEffectKind.CREATE: 4,
            GoalEffectKind.REORDER: 5,
            GoalEffectKind.FOCUS: 6,
        }

        def context_key(effect: ContextEffect) -> tuple:
            # Turning an existing hypothesis into a local definition is an
            # in-place update and must precede later value operations.  A
            # *new* local definition is an insertion, so it belongs beside
            # ADD_LOCAL, after removals.  Treating both forms alike made the
            # result depend on transient list indices and forced a spurious
            # final reorder.
            priority = context_priority[effect.kind]
            if (
                effect.kind is ContextEffectKind.ADD_LOCAL_DEFINITION
                and effect.before is None
            ):
                priority = context_priority[ContextEffectKind.ADD_LOCAL]
            return (
                effect.goal_id,
                priority,
                effect.old_index if effect.old_index is not None else 1 << 30,
                effect.new_index if effect.new_index is not None else 1 << 30,
                effect.before.decl_id if effect.before is not None else "",
                effect.after.decl_id if effect.after is not None else "",
                effect.entity_ids,
            )

        def target_key(effect: TargetEffect) -> tuple:
            return (
                effect.goal_id,
                effect.kind.value,
                effect.source_path,
                effect.target_path,
                effect.entity_id,
            )

        def goal_key(effect: GoalEffect) -> tuple:
            return (
                goal_priority[effect.kind],
                effect.source_goal_ids,
                tuple(item.goal_id for item in effect.target_descriptors),
                tuple(item.goal_id for item in effect.created_goals),
                effect.order,
                effect.focus,
            )

        return replace(
            self,
            correspondence=self.correspondence.normalized(),
            context_effects=tuple(
                sorted(dict.fromkeys(self.context_effects), key=context_key)
            ),
            target_effects=tuple(
                sorted(dict.fromkeys(self.target_effects), key=target_key)
            ),
            goal_effects=tuple(sorted(dict.fromkeys(self.goal_effects), key=goal_key)),
        )


def _validate_context_effect_shape(effect: ContextEffect) -> None:
    before = effect.before
    after = effect.after
    kind = effect.kind
    if len(effect.entity_ids) != len(set(effect.entity_ids)):
        raise ValueError(f"{kind.value} repeats a causal entity")
    if kind is ContextEffectKind.REORDER_LOCALS:
        if before is not None or after is not None or effect.entity_ids:
            raise ValueError("reorder-locals cannot carry declarations")
        if len(effect.order) != len(set(effect.order)):
            raise ValueError("reorder-locals repeats a declaration")
        return
    if effect.order:
        raise ValueError(f"{kind.value} cannot carry a context order")
    if effect.entity_ids and kind is not ContextEffectKind.UPDATE_LOCAL_TYPE:
        raise ValueError(f"{kind.value} cannot carry substitution-cause entities")
    if kind is ContextEffectKind.ADD_LOCAL:
        if before is not None or after is None or after.value_expr is not None:
            raise ValueError("add-local must create one hypothesis")
        return
    if kind is ContextEffectKind.REMOVE_LOCAL:
        if before is None or after is not None:
            raise ValueError("remove-local must consume one declaration")
        return
    if kind is ContextEffectKind.ADD_LOCAL_DEFINITION:
        if after is None or after.value_expr is None:
            raise ValueError("add-local-definition requires a resulting value")
        if before is not None and (
            before.decl_id != after.decl_id or before.value_expr is not None
        ):
            raise ValueError(
                "add-local-definition may only define an existing hypothesis"
            )
        return
    if before is None or after is None:
        raise ValueError(f"{kind.value} must relate two declarations")
    if kind is ContextEffectKind.REPLACE_LOCAL:
        if before.decl_id == after.decl_id:
            raise ValueError("replace-local requires a fresh declaration identity")
        return
    if before.decl_id != after.decl_id:
        raise ValueError(f"{kind.value} cannot change declaration identity")

    changed = {
        field_name
        for field_name in (
            "user_name",
            "type_expr",
            "value_expr",
            "binder_info",
            "dependencies",
            "aliases",
            "source_range",
            "is_proof",
            "presentation_visible",
            "metadata",
        )
        if getattr(before, field_name) != getattr(after, field_name)
    }
    allowed_changes = {
        ContextEffectKind.RENAME_LOCAL: frozenset({"user_name"}),
        ContextEffectKind.UPDATE_LOCAL_TYPE: frozenset(
            {"type_expr", "dependencies", "is_proof"}
        ),
        ContextEffectKind.ADD_LOCAL_DEFINITION: frozenset({"value_expr"}),
        ContextEffectKind.UPDATE_LOCAL_VALUE: frozenset({"value_expr"}),
        ContextEffectKind.CLEAR_LOCAL_VALUE: frozenset({"value_expr"}),
        ContextEffectKind.UPDATE_LOCAL_METADATA: frozenset(
            {
                "binder_info",
                "dependencies",
                "aliases",
                "source_range",
                "is_proof",
                "presentation_visible",
                "metadata",
            }
        ),
    }
    allowed = allowed_changes.get(kind)
    if allowed is not None and (not changed or not changed <= allowed):
        raise ValueError(
            f"{kind.value} changes fields outside its typed effect: "
            f"{sorted(changed - allowed) or ['none']}"
        )
    if kind is ContextEffectKind.RENAME_LOCAL and "user_name" not in changed:
        raise ValueError("rename-local does not change the user-facing name")
    if kind is ContextEffectKind.UPDATE_LOCAL_TYPE and "type_expr" not in changed:
        raise ValueError("update-local-type does not change the local type")
    if kind is ContextEffectKind.UPDATE_LOCAL_VALUE and (
        before.value_expr is None or after.value_expr is None
    ):
        raise ValueError("update-local-value requires old and new values")
    if kind is ContextEffectKind.CLEAR_LOCAL_VALUE and (
        before.value_expr is None or after.value_expr is not None
    ):
        raise ValueError("clear-local-value must remove only the value")


def _validate_target_effect_shape(effect: TargetEffect) -> None:
    source_paths = {item.path for item in effect.before.occurrences}
    target_paths = {item.path for item in effect.after.occurrences}
    if effect.source_path and effect.source_path not in source_paths:
        raise ValueError(
            f"{effect.kind.value} references missing source path {effect.source_path!r}"
        )
    if effect.target_path and effect.target_path not in target_paths:
        raise ValueError(
            f"{effect.kind.value} references missing target path {effect.target_path!r}"
        )
    if effect.kind is TargetEffectKind.KEEP and effect.before != effect.after:
        raise ValueError("keep-target changes the target expression")
    if (
        effect.kind is TargetEffectKind.CHANGE_PRESENTATION
        and effect.before.canonical_key != effect.after.canonical_key
    ):
        raise ValueError("change-presentation changes canonical expression structure")
    if effect.kind is TargetEffectKind.REWRITE_SUBEXPRESSION and not (
        effect.source_path or effect.target_path
    ):
        raise ValueError("rewrite-subexpression has no proper subtree path")
    if effect.kind is TargetEffectKind.SUBSTITUTE_ENTITY and not effect.entity_id:
        raise ValueError("substitute-entity has no causal entity")
    if effect.kind is not TargetEffectKind.SUBSTITUTE_ENTITY and effect.entity_id:
        raise ValueError(f"{effect.kind.value} cannot carry a substitution entity")


def _validate_goal_effect_shape(effect: GoalEffect) -> None:
    kind = effect.kind
    sources = effect.source_goal_ids
    descriptors = effect.target_descriptors
    created = effect.created_goals
    if len(sources) != len(set(sources)):
        raise ValueError(f"{kind.value} repeats a source goal")
    created_ids = tuple(item.goal_id for item in created)
    if len(created_ids) != len(set(created_ids)):
        raise ValueError(f"{kind.value} repeats a created goal")

    if kind is GoalEffectKind.PRESERVE:
        valid = len(sources) == len(descriptors) == 1 and not created
    elif kind is GoalEffectKind.CLOSE:
        valid = bool(sources) and not descriptors and not created
    elif kind is GoalEffectKind.CREATE:
        valid = not sources and not descriptors and bool(created)
    elif kind is GoalEffectKind.SPLIT:
        valid = len(sources) == 1 and not descriptors and len(created) > 1
        if valid and any(
            item.parent_goal_id not in {None, sources[0]} for item in created
        ):
            raise ValueError("split-goal has a child with a conflicting parent")
    elif kind is GoalEffectKind.MERGE:
        valid = len(sources) > 1 and not descriptors and len(created) == 1
    elif kind is GoalEffectKind.REORDER:
        valid = not sources and not descriptors and not created and bool(effect.order)
    else:  # FOCUS; an empty tuple intentionally clears focus.
        valid = not sources and not descriptors and not created and not effect.order
    if not valid:
        raise ValueError(f"invalid {kind.value} effect shape")
    if kind is not GoalEffectKind.REORDER and effect.order:
        raise ValueError(f"{kind.value} cannot carry goal order")
    if kind is not GoalEffectKind.FOCUS and effect.focus:
        raise ValueError(f"{kind.value} cannot carry focus")


def _validate_transition_effect_shapes(transition: ProofTransition) -> None:
    for effect in transition.context_effects:
        _validate_context_effect_shape(effect)
    for effect in transition.goal_effects:
        _validate_goal_effect_shape(effect)
    for effect in transition.target_effects:
        _validate_target_effect_shape(effect)
    context_reorders: set[str] = set()
    for effect in transition.context_effects:
        if effect.kind is ContextEffectKind.REORDER_LOCALS:
            if effect.goal_id in context_reorders:
                raise ValueError(
                    f"goal {effect.goal_id} has multiple context reorder effects"
                )
            context_reorders.add(effect.goal_id)
    if (
        sum(effect.kind is GoalEffectKind.REORDER for effect in transition.goal_effects)
        > 1
    ):
        raise ValueError("transition has multiple goal reorder effects")
    if (
        sum(effect.kind is GoalEffectKind.FOCUS for effect in transition.goal_effects)
        > 1
    ):
        raise ValueError("transition has multiple focus effects")

    consumed_goals: set[str] = set()
    produced_goals: set[str] = set()
    for effect in transition.goal_effects:
        if effect.kind in {GoalEffectKind.REORDER, GoalEffectKind.FOCUS}:
            continue
        repeated_sources = consumed_goals.intersection(effect.source_goal_ids)
        if repeated_sources:
            raise ValueError(
                f"transition consumes a goal more than once: {sorted(repeated_sources)}"
            )
        consumed_goals.update(effect.source_goal_ids)
        target_ids = {
            *(item.goal_id for item in effect.target_descriptors),
            *(item.goal_id for item in effect.created_goals),
        }
        repeated_targets = produced_goals.intersection(target_ids)
        if repeated_targets:
            raise ValueError(
                f"transition produces a goal more than once: {sorted(repeated_targets)}"
            )
        produced_goals.update(target_ids)
    target_goals = [effect.goal_id for effect in transition.target_effects]
    if len(target_goals) != len(set(target_goals)):
        raise ValueError("transition has multiple target effects for one goal")

    removed_entities = {
        effect.before.decl_id
        for effect in transition.context_effects
        if effect.kind is ContextEffectKind.REMOVE_LOCAL and effect.before is not None
    }
    for effect in transition.context_effects:
        unknown = set(effect.entity_ids) - removed_entities
        if unknown:
            raise ValueError(
                f"{effect.kind.value} references non-removed causal entities "
                f"{sorted(unknown)}"
            )
        for entity_id in effect.entity_ids:
            if effect.before is None or not any(
                item.lean_identity == f"fvar:{entity_id}"
                for item in effect.before.type_expr.occurrences
            ):
                raise ValueError(
                    f"{effect.kind.value} source does not contain causal entity "
                    f"{entity_id}"
                )
    for effect in transition.target_effects:
        if effect.kind is not TargetEffectKind.SUBSTITUTE_ENTITY:
            continue
        if effect.entity_id not in removed_entities:
            raise ValueError(
                f"substitute-entity references non-removed local {effect.entity_id}"
            )
        if not any(
            item.lean_identity == f"fvar:{effect.entity_id}"
            for item in effect.before.occurrences
        ):
            raise ValueError(
                f"substitute-entity source does not contain {effect.entity_id}"
            )


def _apply_context_effects(
    goal: GoalState, effects: tuple[ContextEffect, ...]
) -> GoalState:
    locals_by_id = {local.decl_id: local for local in goal.locals}
    order = [local.decl_id for local in goal.locals]
    final_order: tuple[str, ...] | None = None
    for effect in effects:
        _validate_context_effect_shape(effect)
        if effect.kind == ContextEffectKind.REORDER_LOCALS:
            final_order = effect.order
            continue
        before_id = effect.before.decl_id if effect.before is not None else ""
        after_id = effect.after.decl_id if effect.after is not None else ""
        if effect.kind == ContextEffectKind.REMOVE_LOCAL:
            if before_id not in locals_by_id:
                raise ValueError(f"cannot remove nonexistent local {before_id}")
            if locals_by_id[before_id] != effect.before:
                raise ValueError(
                    f"{effect.kind.value} source declaration does not match {before_id}"
                )
            locals_by_id.pop(before_id)
            order.remove(before_id)
            continue
        if (
            effect.kind
            in {
                ContextEffectKind.ADD_LOCAL,
                ContextEffectKind.ADD_LOCAL_DEFINITION,
            }
            and effect.before is None
        ):
            if effect.after is None:
                raise ValueError(f"{effect.kind.value} has no target declaration")
            if after_id in locals_by_id:
                raise ValueError(f"cannot add existing local {after_id}")
            locals_by_id[after_id] = effect.after
            insertion = effect.new_index if effect.new_index is not None else len(order)
            if not 0 <= insertion <= len(order):
                raise ValueError(
                    f"cannot insert local {after_id} at invalid index {insertion}"
                )
            order.insert(insertion, after_id)
            continue
        if effect.kind == ContextEffectKind.REPLACE_LOCAL:
            if effect.after is None or before_id not in locals_by_id:
                raise ValueError(f"cannot replace nonexistent local {before_id}")
            if locals_by_id[before_id] != effect.before:
                raise ValueError(
                    f"{effect.kind.value} source declaration does not match {before_id}"
                )
            if after_id != before_id and after_id in locals_by_id:
                raise ValueError(f"cannot replace with existing local {after_id}")
            position = order.index(before_id)
            locals_by_id.pop(before_id)
            locals_by_id[after_id] = effect.after
            order[position] = after_id
            continue
        if before_id not in locals_by_id or effect.after is None:
            raise ValueError(f"invalid {effect.kind.value} reference {before_id}")
        if locals_by_id[before_id] != effect.before:
            raise ValueError(
                f"{effect.kind.value} source declaration does not match {before_id}"
            )
        # Rename/type/value operations carry the exact resulting immutable
        # declaration.  The operation kind remains meaningful and auditable;
        # replay does not reconstruct semantic data from strings.
        locals_by_id.pop(before_id)
        locals_by_id[after_id] = effect.after
        order[order.index(before_id)] = after_id
    if final_order is not None:
        if (
            len(final_order) != len(locals_by_id)
            or len(final_order) != len(set(final_order))
            or set(final_order) != set(locals_by_id)
        ):
            raise ValueError("local reorder is not a permutation of live declarations")
        order = list(final_order)
    return replace(goal, locals=tuple(locals_by_id[item] for item in order))


def apply_transition(before: ProofState, transition: ProofTransition) -> ProofState:
    """Replay a normalized transition and reconstruct its canonical target."""

    transition = transition.normalized()
    _validate_transition_effect_shapes(transition)
    if before.fingerprint != transition.before_fingerprint:
        raise ValueError("transition source fingerprint does not match proof state")
    if transition.is_identity:
        return before

    goals = {goal.goal_id: goal for goal in before.goals}
    order = list(before.goal_order)
    focus = before.focus
    for effect in transition.goal_effects:
        _validate_goal_effect_shape(effect)
        if effect.kind == GoalEffectKind.PRESERVE:
            if len(effect.source_goal_ids) != 1 or len(effect.target_descriptors) != 1:
                raise ValueError("preserve-goal must be 1→1")
            source_id = effect.source_goal_ids[0]
            if source_id not in goals:
                raise ValueError(f"cannot preserve nonexistent goal {source_id}")
            descriptor = effect.target_descriptors[0]
            source = goals.pop(source_id)
            if descriptor.goal_id != source_id and descriptor.goal_id in goals:
                raise ValueError(
                    f"cannot preserve goal {source_id} as existing goal "
                    f"{descriptor.goal_id}"
                )
            target = replace(
                source,
                goal_id=descriptor.goal_id,
                lineage_id=descriptor.lineage_id,
                parent_goal_id=descriptor.parent_goal_id,
                branch_kind=descriptor.branch_kind,
                branch_index=descriptor.branch_index,
                metadata=descriptor.metadata,
            )
            goals[target.goal_id] = target
            order[order.index(source_id)] = target.goal_id
            focus = tuple(
                target.goal_id if item == source_id else item for item in focus
            )
        elif effect.kind == GoalEffectKind.CLOSE:
            for source_id in effect.source_goal_ids:
                if source_id not in goals:
                    raise ValueError(f"cannot close nonexistent goal {source_id}")
                goals.pop(source_id)
                order.remove(source_id)
                focus = tuple(item for item in focus if item != source_id)
        elif effect.kind in {
            GoalEffectKind.CREATE,
            GoalEffectKind.SPLIT,
            GoalEffectKind.MERGE,
        }:
            insertion = len(order)
            for source_id in effect.source_goal_ids:
                if source_id not in goals:
                    raise ValueError(f"cannot consume nonexistent goal {source_id}")
                insertion = min(insertion, order.index(source_id))
                goals.pop(source_id)
                order.remove(source_id)
                focus = tuple(item for item in focus if item != source_id)
            for offset, target in enumerate(effect.created_goals):
                if target.goal_id in goals:
                    raise ValueError(f"cannot create existing goal {target.goal_id}")
                goals[target.goal_id] = target
                order.insert(insertion + offset, target.goal_id)
        elif effect.kind == GoalEffectKind.REORDER:
            if (
                len(effect.order) != len(goals)
                or len(effect.order) != len(set(effect.order))
                or set(effect.order) != set(goals)
            ):
                raise ValueError("goal reorder is not a permutation of live goals")
            order = list(effect.order)
        elif effect.kind == GoalEffectKind.FOCUS:
            if any(item not in goals for item in effect.focus):
                raise ValueError("focus effect references a nonexistent goal")
            focus = effect.focus

    context_by_goal: dict[str, list[ContextEffect]] = {}
    for effect in transition.context_effects:
        context_by_goal.setdefault(effect.goal_id, []).append(effect)
    target_by_goal: dict[str, list[TargetEffect]] = {}
    for effect in transition.target_effects:
        target_by_goal.setdefault(effect.goal_id, []).append(effect)

    for goal_id, goal_effects in context_by_goal.items():
        if goal_id not in goals:
            raise ValueError(f"context effect references nonexistent goal {goal_id}")
        goals[goal_id] = _apply_context_effects(goals[goal_id], tuple(goal_effects))
    for goal_id, target_effects in target_by_goal.items():
        if goal_id not in goals:
            raise ValueError(f"target effect references nonexistent goal {goal_id}")
        if len(target_effects) != 1:
            raise ValueError(f"goal {goal_id} has multiple normalized target effects")
        effect = target_effects[0]
        if goals[goal_id].target != effect.before:
            raise ValueError(f"target effect source does not match goal {goal_id}")
        goals[goal_id] = replace(goals[goal_id], target=effect.after)

    result = ProofState(
        goals=tuple(goals[item] for item in order),
        focus=focus,
        schema_version=before.schema_version,
        metadata=before.metadata,
    )
    state_errors = validate_state(result)
    if state_errors:
        raise ValueError(
            "transition produced invalid state: " + "; ".join(state_errors)
        )
    correspondence_errors = validate_correspondence(
        before, result, transition.correspondence
    )
    if correspondence_errors:
        raise ValueError(
            "transition has invalid correspondence: " + "; ".join(correspondence_errors)
        )
    if result.fingerprint != transition.after_fingerprint:
        raise ValueError("replayed transition does not reconstruct the target state")
    return result
