"""Regression tests for the canonical proof-morphism invariants.

These cases deliberately use no tactic names.  They test the state delta as
an algebra: structural creation/removal is not a reorder, continuity of goal
identity transports focus, branch order comes from the canonical frontier,
and malformed hand-authored morphisms cannot replay successfully.
"""

from __future__ import annotations

from dataclasses import replace
from itertools import combinations, permutations

import pytest

from proof_video.proof.correspondence import (
    Correspondence,
    CorrespondenceEdge,
    EntityKind,
    EntityRef,
    ExplicitGoalEdge,
    MatchProvenance,
    RelationKind,
)
from proof_video.proof.diff import diff_proof_states
from proof_video.proof.effects import (
    ContextEffect,
    ContextEffectKind,
    GoalDescriptor,
    GoalEffect,
    GoalEffectKind,
    ProofTransition,
    apply_transition,
)
from proof_video.proof.interpretation import SemanticEvent, interpret_transition
from proof_video.presentation import VisualPrimitiveKind, plan_visual_transition
from proof_video.proof.state import (
    ExprOccurrence,
    Expression,
    GoalState,
    LocalDecl,
    ProofState,
    SourceRange,
    validate_state,
)


def atom(name: str, *, identity: str = "") -> Expression:
    return Expression(
        expression_id=f"expr:{name}",
        fingerprint=f"expr-fp:{name}",
        lean=name,
        latex=name,
        type_fingerprint="type:Prop",
        occurrences=(
            ExprOccurrence(
                occurrence_id=f"occ:{name}",
                kind="fvar" if identity else "const",
                path=(),
                fingerprint=f"node:{name}",
                lean_identity=identity or f"const:{name}",
                type_fingerprint="type:Prop",
            ),
        ),
    )


def local(decl_id: str, *, value: str | None = None) -> LocalDecl:
    return LocalDecl(
        decl_id=decl_id,
        user_name=decl_id,
        type_expr=atom("Nat"),
        value_expr=atom(value) if value is not None else None,
    )


def goal(
    goal_id: str,
    *,
    lineage: str | None = None,
    locals_: tuple[LocalDecl, ...] = (),
    target: Expression | None = None,
    parent: str | None = None,
    branch_index: int | None = None,
) -> GoalState:
    return GoalState(
        goal_id=goal_id,
        lineage_id=lineage or f"lineage:{goal_id}",
        locals=locals_,
        target=target or atom("P"),
        parent_goal_id=parent,
        branch_kind="case" if parent else "",
        branch_index=branch_index,
    )


def state(*goals: GoalState, focus: tuple[str, ...] = ()) -> ProofState:
    return ProofState(tuple(goals), focus=focus)


@pytest.mark.parametrize(
    ("before_locals", "after_locals"),
    [
        ((local("a"),), (local("a"), local("b"))),
        ((local("a"), local("b")), (local("b"),)),
        ((local("a"),), (local("replacement"),)),
        ((local("a"),), (local("definition", value="1"),)),
    ],
)
def test_structural_context_changes_are_not_mislabeled_as_reordering(
    before_locals: tuple[LocalDecl, ...],
    after_locals: tuple[LocalDecl, ...],
) -> None:
    before = state(goal("g", locals_=before_locals))
    after = state(goal("g", locals_=after_locals))

    transition = diff_proof_states(before, after)

    assert ContextEffectKind.REORDER_LOCALS not in {
        effect.kind for effect in transition.context_effects
    }
    assert apply_transition(before, transition) == after


def test_only_relative_order_of_persistent_locals_is_a_reorder() -> None:
    a, b, new = local("a"), local("b"), local("new")
    before = state(goal("g", locals_=(a, b)))
    after = state(goal("g", locals_=(new, b, a)))

    transition = diff_proof_states(before, after)

    reorders = [
        effect
        for effect in transition.context_effects
        if effect.kind is ContextEffectKind.REORDER_LOCALS
    ]
    assert len(reorders) == 1
    assert reorders[0].order == ("new", "b", "a")
    assert apply_transition(before, transition) == after


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (state(goal("a"), goal("b")), state(goal("b"))),
        (state(goal("a")), state(goal("a"), goal("b"))),
    ],
)
def test_goal_creation_and_closure_do_not_emit_spurious_reorder(
    before: ProofState, after: ProofState
) -> None:
    transition = diff_proof_states(before, after)

    assert GoalEffectKind.REORDER not in {
        effect.kind for effect in transition.goal_effects
    }
    assert apply_transition(before, transition) == after


def test_goal_identity_transport_preserves_focus_without_control_noise() -> None:
    before = state(goal("old", lineage="same"), focus=("old",))
    after = state(goal("new", lineage="same"), focus=("new",))

    transition = diff_proof_states(before, after)
    kinds = {effect.kind for effect in transition.goal_effects}

    assert GoalEffectKind.PRESERVE in kinds
    assert GoalEffectKind.REORDER not in kinds
    assert GoalEffectKind.FOCUS not in kinds
    assert apply_transition(before, transition) == after


def test_all_small_multigoal_frontiers_replay_with_exact_order_and_focus() -> None:
    frontiers: list[ProofState] = []
    identities = ("a", "b")
    for size in range(len(identities) + 1):
        for subset in combinations(identities, size):
            for order in permutations(subset):
                goals = tuple(goal(goal_id) for goal_id in order)
                for focus in dict.fromkeys(((), order[:1], order)):
                    frontiers.append(state(*goals, focus=focus))

    for before in frontiers:
        for after in frontiers:
            transition = diff_proof_states(before, after)
            assert apply_transition(before, transition) == after


def test_split_uses_canonical_frontier_order_not_sorted_goal_ids() -> None:
    before = state(goal("parent"), focus=("parent",))
    # Lexicographic order is deliberately the inverse of branch order.
    z_branch = goal("z-branch", parent="parent", branch_index=0)
    a_branch = goal("a-branch", parent="parent", branch_index=1)
    after = state(z_branch, a_branch, focus=("z-branch",))

    transition = diff_proof_states(before, after)
    split = next(
        effect
        for effect in transition.goal_effects
        if effect.kind is GoalEffectKind.SPLIT
    )

    assert tuple(item.goal_id for item in split.created_goals) == (
        "z-branch",
        "a-branch",
    )
    assert GoalEffectKind.REORDER not in {
        effect.kind for effect in transition.goal_effects
    }
    assert apply_transition(before, transition) == after


def expression_with_optional_right(name: str, *, include_right: bool) -> Expression:
    occurrences = [
        ExprOccurrence(
            occurrence_id=f"{name}:root",
            kind="app",
            path=(),
            fingerprint=f"tree:{name}",
            type_fingerprint="type:Prop",
        ),
        ExprOccurrence(
            occurrence_id=f"{name}:left",
            kind="const",
            path=(0,),
            fingerprint="node:left",
            lean_identity="const:left",
            type_fingerprint="type:Prop",
            parent_id=f"{name}:root",
        ),
    ]
    if include_right:
        occurrences.append(
            ExprOccurrence(
                occurrence_id=f"{name}:right",
                kind="const",
                path=(1,),
                fingerprint="node:right",
                lean_identity="const:right",
                type_fingerprint="type:Prop",
                parent_id=f"{name}:root",
            )
        )
    return Expression(
        expression_id=f"expr:{name}",
        fingerprint=f"expr-fp:{name}",
        lean=name,
        latex=name,
        type_fingerprint="type:Prop",
        occurrences=tuple(occurrences),
    )


@pytest.mark.parametrize("adding", [False, True])
def test_one_sided_tree_edit_is_a_proper_subexpression_rewrite(adding: bool) -> None:
    small = expression_with_optional_right("small", include_right=False)
    large = expression_with_optional_right("large", include_right=True)
    before = state(goal("g", target=small if adding else large))
    after = state(goal("g", target=large if adding else small))

    transition = diff_proof_states(before, after)
    effect = transition.target_effects[0]

    assert effect.kind.value == "rewrite-subexpression"
    assert effect.source_path or effect.target_path
    assert apply_transition(before, transition) == after


def test_apply_rejects_correspondence_with_nonexistent_entity() -> None:
    before = state(goal("g"))
    invalid = Correspondence(
        (
            CorrespondenceEdge(
                (EntityRef(EntityKind.GOAL, "absent"),),
                (EntityRef(EntityKind.GOAL, "g"),),
                RelationKind.PRESERVE,
                MatchProvenance.EXPLICIT,
            ),
        )
    )
    transition = ProofTransition(
        before.fingerprint,
        before.fingerprint,
        invalid,
        goal_effects=(
            GoalEffect(
                GoalEffectKind.PRESERVE,
                ("g",),
                target_descriptors=(GoalDescriptor.of(before.goals[0]),),
            ),
        ),
    )

    with pytest.raises(ValueError, match="invalid correspondence"):
        apply_transition(before, transition)


def test_apply_rejects_effect_whose_declared_source_is_not_current() -> None:
    actual = local("x")
    forged = replace(actual, user_name="z")
    after_local = replace(actual, user_name="y")
    before = state(goal("g", locals_=(actual,)))
    after = state(goal("g", locals_=(after_local,)))
    clean = diff_proof_states(before, after)
    bad_effect = ContextEffect(
        ContextEffectKind.RENAME_LOCAL,
        "g",
        before=forged,
        after=after_local,
        old_index=0,
        new_index=0,
    )
    transition = replace(clean, context_effects=(bad_effect,))

    with pytest.raises(ValueError, match="source declaration"):
        apply_transition(before, transition)


def test_apply_rejects_create_effect_that_consumes_a_goal() -> None:
    before = state(goal("source"))
    target = goal("target")
    after = state(target)
    transition = ProofTransition(
        before.fingerprint,
        after.fingerprint,
        Correspondence(),
        goal_effects=(
            GoalEffect(
                GoalEffectKind.CREATE,
                source_goal_ids=("source",),
                created_goals=(target,),
            ),
        ),
    )

    with pytest.raises(ValueError, match="create-goal"):
        apply_transition(before, transition)


def test_typed_context_effect_cannot_hide_a_different_foundational_change() -> None:
    hypothesis = local("x")
    definition = replace(hypothesis, value_expr=atom("1"))
    before = state(goal("g", locals_=(hypothesis,)))
    after = state(goal("g", locals_=(definition,)))
    clean = diff_proof_states(before, after)
    mislabeled = replace(
        clean.context_effects[0], kind=ContextEffectKind.UPDATE_LOCAL_TYPE
    )

    with pytest.raises(ValueError, match="typed effect"):
        apply_transition(before, replace(clean, context_effects=(mislabeled,)))


def test_transition_cannot_consume_the_same_goal_twice() -> None:
    before = state(goal("g"))
    after = state()
    clean = diff_proof_states(before, after)
    duplicate_consumption = (
        GoalEffect(
            GoalEffectKind.PRESERVE,
            ("g",),
            (GoalDescriptor.of(before.goals[0]),),
        ),
        GoalEffect(GoalEffectKind.CLOSE, ("g",)),
    )

    with pytest.raises(ValueError, match="consumes a goal more than once"):
        apply_transition(before, replace(clean, goal_effects=duplicate_consumption))


def test_explicit_many_to_one_goal_merge_replays_without_reorder_noise() -> None:
    before = state(goal("left"), goal("right"), focus=("left", "right"))
    after = state(goal("merged"), focus=("merged",))

    transition = diff_proof_states(
        before,
        after,
        explicit_goal_edges=(
            ExplicitGoalEdge(
                ("left", "right"),
                ("merged",),
                "kernel-certified-merge",
                RelationKind.MERGE,
            ),
        ),
    )
    merge = next(
        effect
        for effect in transition.goal_effects
        if effect.kind is GoalEffectKind.MERGE
    )

    assert merge.source_goal_ids == ("left", "right")
    assert GoalEffectKind.REORDER not in {
        effect.kind for effect in transition.goal_effects
    }
    assert apply_transition(before, transition) == after


def test_branch_indices_are_nonnegative_and_unique_per_parent() -> None:
    left = goal("left", parent="parent", branch_index=0)
    duplicate = goal("duplicate", parent="parent", branch_index=0)

    errors = validate_state(state(left, duplicate))

    assert any("duplicate branch index" in error for error in errors)


def test_focus_is_an_ordered_set_and_source_ranges_are_well_formed() -> None:
    with pytest.raises(ValueError, match="repeats a focused goal"):
        state(goal("g"), focus=("g", "g"))
    with pytest.raises(ValueError, match="source range"):
        SourceRange("Demo.lean", 4, 0, 3, 0)


def test_substitution_through_only_a_dependent_local_has_a_causal_entity() -> None:
    x = local("x")
    dependent = replace(
        local("h"),
        type_expr=atom("x", identity="fvar:x"),
        dependencies=("x",),
    )
    substituted = replace(dependent, type_expr=atom("0"), dependencies=())
    before = state(goal("g", locals_=(x, dependent), target=atom("P")))
    after = state(goal("g", locals_=(substituted,), target=atom("P")))

    transition = diff_proof_states(before, after)
    update = next(
        effect
        for effect in transition.context_effects
        if effect.kind is ContextEffectKind.UPDATE_LOCAL_TYPE
    )

    assert update.entity_ids == ("x",)
    assert interpret_transition(transition).primary is SemanticEvent.SUBSTITUTION
    assert apply_transition(before, transition) == after
    plan = plan_visual_transition(before, after, transition)
    local_rewrites = plan.primitives_of_kind(VisualPrimitiveKind.REWRITE)
    assert any("entity:x" in primitive.evidence for primitive in local_rewrites)


def quantified_x(local_type: Expression) -> Expression:
    return Expression(
        expression_id="expr:forall-x",
        fingerprint="expr-fp:forall-x",
        lean="∀ x, x",
        latex=r"\forall x, x",
        type_fingerprint="type:Prop",
        occurrences=(
            ExprOccurrence(
                "forall:root",
                "forall",
                (),
                "forall-tree",
                type_fingerprint="type:Prop",
            ),
            ExprOccurrence(
                "forall:domain",
                "const",
                (0,),
                local_type.fingerprint,
                lean_identity="const:Nat",
                type_fingerprint="type:Sort",
                parent_id="forall:root",
            ),
            ExprOccurrence(
                "forall:body",
                "bvar",
                (1,),
                "body-x",
                lean_identity="bvar:0",
                type_fingerprint="type:Prop",
                parent_id="forall:root",
            ),
            ExprOccurrence(
                "forall:binder",
                "declaration",
                ("binder",),
                local_type.fingerprint,
                type_fingerprint="type:Nat",
                parent_id="forall:root",
            ),
        ),
    )


def test_certified_revert_is_not_mislabeled_as_substitution() -> None:
    x = local("x")
    body = atom("x", identity="fvar:x")
    before = state(goal("g", locals_=(x,), target=body))
    after = state(goal("g", target=quantified_x(x.type_expr)))

    transition = diff_proof_states(before, after)
    interpretation = interpret_transition(transition)

    assert all(
        effect.kind.value != "substitute-entity" for effect in transition.target_effects
    )
    assert interpretation.primary is SemanticEvent.REVERSION
    assert apply_transition(before, transition) == after
