from __future__ import annotations

from dataclasses import replace
from itertools import permutations

import pytest

from proof_video.proof.correspondence import (
    Correspondence,
    CorrespondenceEdge,
    EntityKind,
    EntityRef,
    ExplicitGoalEdge,
    ExplicitOccurrenceEdge,
    MatchProvenance,
    RelationKind,
    build_correspondence,
    complete_correspondence,
    validate_correspondence,
    validate_total_correspondence,
)
from proof_video.proof import correspondence as correspondence_module
from proof_video.proof.diff import (
    compose_transitions,
    diff_proof_states,
    semantically_equivalent,
)
from proof_video.proof.effects import (
    GoalDescriptor,
    GoalEffect,
    ContextEffectKind,
    GoalEffectKind,
    ProofTransition,
    TargetEffect,
    TargetEffectKind,
    apply_transition,
)
from proof_video.proof.state import (
    CharacterSpan,
    ExprOccurrence,
    Expression,
    GoalState,
    LocalDecl,
    LocalKind,
    ProofState,
    SourceRange,
    validate_state,
)


TYPE_REAL = "type:Real"


def expression(
    name: str,
    *,
    latex: str | None = None,
    lean_identity: str = "",
    occurrence_id: str | None = None,
    fingerprint: str | None = None,
    expression_id: str | None = None,
) -> Expression:
    rendered = latex if latex is not None else name
    node_fingerprint = fingerprint or f"node:{name}"
    return Expression(
        expression_id=expression_id or f"expr:{name}",
        fingerprint=fingerprint or f"expr-fp:{name}",
        lean=name,
        latex=rendered,
        type_fingerprint=TYPE_REAL,
        occurrences=(
            ExprOccurrence(
                occurrence_id=occurrence_id or f"occ:{name}",
                kind="fvar" if lean_identity.startswith("fvar:") else "const",
                path=(),
                fingerprint=node_fingerprint,
                lean_identity=lean_identity,
                type_fingerprint=TYPE_REAL,
                latex_spans=(CharacterSpan(0, len(rendered)),),
            ),
        ),
    )


def local(
    decl_id: str,
    *,
    name: str | None = None,
    type_name: str = "Real",
    value: str | None = None,
    aliases: tuple[str, ...] = (),
    dependencies: tuple[str, ...] = (),
) -> LocalDecl:
    return LocalDecl(
        decl_id=decl_id,
        user_name=name or decl_id,
        type_expr=expression(type_name, lean_identity=f"const:{type_name}"),
        value_expr=(expression(value) if value is not None else None),
        aliases=aliases,
        dependencies=dependencies,
    )


def goal(
    goal_id: str = "g",
    *,
    locals_: tuple[LocalDecl, ...] = (),
    target: Expression | None = None,
    lineage_id: str | None = None,
    parent_goal_id: str | None = None,
    branch_index: int | None = None,
) -> GoalState:
    return GoalState(
        goal_id=goal_id,
        lineage_id=lineage_id or goal_id,
        locals=locals_,
        target=target or expression("P", lean_identity="const:P"),
        parent_goal_id=parent_goal_id,
        branch_kind="case" if parent_goal_id is not None else "",
        branch_index=branch_index,
    )


def state(
    *goals: GoalState,
    focus: tuple[str, ...] | None = None,
) -> ProofState:
    return ProofState(
        goals=tuple(goals),
        focus=focus if focus is not None else tuple(g.goal_id for g in goals[:1]),
    )


def effect_kinds(before: ProofState, after: ProofState) -> set[ContextEffectKind]:
    transition = diff_proof_states(before, after)
    assert apply_transition(before, transition) == after
    return {item.kind for item in transition.context_effects}


def test_local_definition_values_are_canonical_state_not_presentation() -> None:
    one = state(goal(locals_=(local("x", value="1"),)))
    two = state(goal(locals_=(local("x", value="2"),)))

    assert one != two
    assert one.fingerprint != two.fingerprint
    assert one.goals[0].locals[0].kind is LocalKind.DEFINITION
    assert validate_state(one) == ()
    assert validate_state(two) == ()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dependencies", ("parameter",)),
        ("aliases", ("old-x",)),
        ("is_proof", True),
        ("presentation_visible", False),
        ("metadata", (("binderId", "17"),)),
        ("source_range", SourceRange("Demo.lean", 3, 4, 3, 9)),
    ],
)
def test_every_canonical_local_field_participates_in_fingerprint_and_replay(
    field: str,
    value: object,
) -> None:
    """A field cannot affect equality while being invisible to diff/replay.

    These fields are Lean identity/provenance evidence, rather than visual
    decoration.  If one is intentionally made non-canonical in the future,
    its dataclass equality semantics must change at the same boundary.
    """

    old_local = local("x")
    new_local = replace(old_local, **{field: value})
    prefix = (local("parameter"),) if field == "dependencies" else ()
    before = state(goal(locals_=(*prefix, old_local)))
    after = state(goal(locals_=(*prefix, new_local)))

    assert before != after
    assert before.fingerprint != after.fingerprint
    transition = diff_proof_states(before, after)
    assert apply_transition(before, transition) == after


@pytest.mark.parametrize(
    ("before_locals", "after_locals", "expected"),
    [
        ((), (local("x"),), ContextEffectKind.ADD_LOCAL),
        ((), (local("x", value="1"),), ContextEffectKind.ADD_LOCAL_DEFINITION),
        ((local("x"),), (), ContextEffectKind.REMOVE_LOCAL),
        (
            (local("x", name="x"),),
            (local("x", name="y"),),
            ContextEffectKind.RENAME_LOCAL,
        ),
        (
            (local("x", type_name="Nat"),),
            (local("x", type_name="Int"),),
            ContextEffectKind.UPDATE_LOCAL_TYPE,
        ),
        (
            (local("x"),),
            (local("x", value="1"),),
            ContextEffectKind.ADD_LOCAL_DEFINITION,
        ),
        (
            (local("x", value="1"),),
            (local("x", value="2"),),
            ContextEffectKind.UPDATE_LOCAL_VALUE,
        ),
        (
            (local("x", value="1"),),
            (local("x"),),
            ContextEffectKind.CLEAR_LOCAL_VALUE,
        ),
        (
            (local("h-old", name="h"),),
            (local("h-new", name="h", aliases=("h-old",)),),
            ContextEffectKind.REPLACE_LOCAL,
        ),
        (
            (local("x"), local("y")),
            (local("y"), local("x")),
            ContextEffectKind.REORDER_LOCALS,
        ),
    ],
)
def test_every_context_effect_is_observable_and_replayable(
    before_locals: tuple[LocalDecl, ...],
    after_locals: tuple[LocalDecl, ...],
    expected: ContextEffectKind,
) -> None:
    before = state(goal(locals_=before_locals))
    after = state(goal(locals_=after_locals))

    assert expected in effect_kinds(before, after)


def test_unchanged_target_is_the_normal_form_for_keep() -> None:
    before = state(goal(locals_=(local("x"),)))
    transition = diff_proof_states(before, before)

    assert transition.is_identity
    assert transition.target_effects == ()
    assert apply_transition(before, transition) is before


def test_explicit_keep_target_effect_is_replayable() -> None:
    before = state(goal())
    current_goal = before.goals[0]
    transition = ProofTransition(
        before_fingerprint=before.fingerprint,
        after_fingerprint=before.fingerprint,
        correspondence=Correspondence(),
        goal_effects=(
            GoalEffect(
                GoalEffectKind.PRESERVE,
                (current_goal.goal_id,),
                (GoalDescriptor.of(current_goal),),
            ),
        ),
        target_effects=(
            TargetEffect(
                TargetEffectKind.KEEP,
                current_goal.goal_id,
                current_goal.target,
                current_goal.target,
            ),
        ),
    )

    assert apply_transition(before, transition) == before


def test_target_presentation_change_does_not_become_a_rewrite() -> None:
    old = expression("P", latex="P", expression_id="pretty:old")
    new = expression("P", latex="\\mathit{P}", expression_id="pretty:new")
    before = state(goal(target=old))
    after = state(goal(target=new))

    transition = diff_proof_states(before, after)

    assert [item.kind for item in transition.target_effects] == [
        TargetEffectKind.CHANGE_PRESENTATION
    ]
    assert apply_transition(before, transition) == after


def nested_expression(name: str, child: str) -> Expression:
    return Expression(
        expression_id=f"expr:{name}",
        fingerprint=f"expr-fp:{name}",
        lean=name,
        latex=name,
        type_fingerprint=TYPE_REAL,
        occurrences=(
            ExprOccurrence(
                occurrence_id=f"{name}:root",
                kind="app",
                path=(),
                fingerprint="stable-app-shell",
                type_fingerprint=TYPE_REAL,
            ),
            ExprOccurrence(
                occurrence_id=f"{name}:child",
                kind="fvar",
                path=(0,),
                fingerprint=f"node:{child}",
                lean_identity=f"fvar:{child}",
                type_fingerprint=TYPE_REAL,
                parent_id=f"{name}:root",
            ),
        ),
    )


def test_target_subexpression_rewrite_records_the_smallest_changed_path() -> None:
    before = state(goal(target=nested_expression("f(x)", "x")))
    after = state(goal(target=nested_expression("f(y)", "y")))

    transition = diff_proof_states(before, after)

    assert len(transition.target_effects) == 1
    effect = transition.target_effects[0]
    assert effect.kind is TargetEffectKind.REWRITE_SUBEXPRESSION
    assert effect.source_path == (0,)
    assert effect.target_path == (0,)
    assert apply_transition(before, transition) == after


def test_target_substitution_names_the_removed_lean_entity() -> None:
    before = state(
        goal(
            locals_=(local("x"),),
            target=nested_expression("f(x)", "x"),
        )
    )
    after = state(goal(target=nested_expression("f(2)", "two")))

    transition = diff_proof_states(before, after)

    assert len(transition.target_effects) == 1
    effect = transition.target_effects[0]
    assert effect.kind is TargetEffectKind.SUBSTITUTE_ENTITY
    assert effect.entity_id == "x"
    assert apply_transition(before, transition) == after


def test_goal_create_and_close_are_inverse_replayable_changes() -> None:
    empty = state(focus=())
    live = state(goal("g"), focus=("g",))

    create = diff_proof_states(empty, live)
    close = diff_proof_states(live, empty)

    assert GoalEffectKind.CREATE in {item.kind for item in create.goal_effects}
    assert GoalEffectKind.CLOSE in {item.kind for item in close.goal_effects}
    assert apply_transition(empty, create) == live
    assert apply_transition(live, close) == empty


def test_goal_split_preserves_explicit_parent_and_branch_order() -> None:
    before = state(goal("parent"), focus=("parent",))
    left = goal("left", parent_goal_id="parent", branch_index=0)
    right = goal("right", parent_goal_id="parent", branch_index=1)
    after = state(left, right, focus=("left",))

    transition = diff_proof_states(before, after)

    splits = [
        item for item in transition.goal_effects if item.kind is GoalEffectKind.SPLIT
    ]
    assert len(splits) == 1
    assert splits[0].source_goal_ids == ("parent",)
    assert tuple(item.goal_id for item in splits[0].created_goals) == (
        "left",
        "right",
    )
    assert apply_transition(before, transition) == after


def test_goal_reorder_and_focus_are_independent_effects() -> None:
    first = goal("first")
    second = goal("second")
    before = state(first, second, focus=("first",))
    after = state(second, first, focus=("second",))

    transition = diff_proof_states(before, after)
    kinds = {item.kind for item in transition.goal_effects}

    assert GoalEffectKind.REORDER in kinds
    assert GoalEffectKind.FOCUS in kinds
    assert apply_transition(before, transition) == after


def repeated_x_expression(prefix: str) -> Expression:
    specs = (
        ("root", "eq", (), "Eq", "const:Eq", None),
        ("plus", "app", (0,), "Add", "const:HAdd.hAdd", "root"),
        ("left", "fvar", (0, 0), "x", "fvar:x", "plus"),
        ("middle", "fvar", (0, 1), "x", "fvar:x", "plus"),
        ("right", "fvar", (1,), "x", "fvar:x", "root"),
    )
    return Expression(
        expression_id=f"{prefix}:expr",
        fingerprint="x+x=x",
        lean="x + x = x",
        latex="x+x=x",
        type_fingerprint="type:Prop",
        occurrences=tuple(
            ExprOccurrence(
                occurrence_id=f"{prefix}:{suffix}",
                kind=kind,
                path=path,
                fingerprint=fingerprint,
                lean_identity=identity,
                type_fingerprint=("type:Real" if kind == "fvar" else ""),
                parent_id=(f"{prefix}:{parent}" if parent else None),
            )
            for suffix, kind, path, fingerprint, identity, parent in specs
        ),
    )


def test_repeated_x_occurrences_match_by_logical_position_not_glyph() -> None:
    old_expr = repeated_x_expression("old")
    new_expr = repeated_x_expression("new")
    before = state(goal(target=old_expr))
    after = state(goal(target=new_expr))

    correspondence = build_correspondence(before, after)
    occurrence_pairs = {
        (edge.sources[0].occurrence_id, edge.targets[0].occurrence_id)
        for edge in correspondence.edges
        if len(edge.sources) == len(edge.targets) == 1
        and edge.sources[0].kind is EntityKind.OCCURRENCE
        and edge.targets[0].kind is EntityKind.OCCURRENCE
    }

    assert occurrence_pairs >= {
        ("old:left", "new:left"),
        ("old:middle", "new:middle"),
        ("old:right", "new:right"),
    }
    assert ("old:left", "new:right") not in occurrence_pairs
    assert ("old:right", "new:left") not in occurrence_pairs


def test_explicit_occurrence_evidence_is_deterministic_under_input_order() -> None:
    before = state(goal(target=repeated_x_expression("old")))
    after = state(goal(target=repeated_x_expression("new")))
    explicit = (
        ExplicitOccurrenceEdge("old:left", "new:left", "lean-node"),
        ExplicitOccurrenceEdge("old:middle", "new:middle", "lean-node"),
        ExplicitOccurrenceEdge("old:right", "new:right", "lean-node"),
    )

    transitions = {
        diff_proof_states(
            before,
            after,
            explicit_occurrence_edges=order,
        )
        for order in permutations(explicit)
    }

    assert len(transitions) == 1


def test_untyped_legacy_evidence_cannot_override_conflicting_lean_identity() -> None:
    before = state(
        goal(
            target=expression(
                "P",
                latex="x",
                lean_identity="fvar:P",
                occurrence_id="old:x",
                fingerprint="node:P",
            )
        )
    )
    after = state(
        goal(
            target=expression(
                "Q",
                latex="x",
                lean_identity="fvar:Q",
                occurrence_id="new:x",
                fingerprint="node:Q",
            )
        )
    )

    correspondence = build_correspondence(
        before,
        after,
        explicit_occurrence_edges=(
            ExplicitOccurrenceEdge("old:x", "new:x", "legacy-glyph-candidate"),
        ),
    )

    assert not any(
        edge.sources
        and edge.targets
        and edge.sources[0].occurrence_id == "old:x"
        and edge.targets[0].occurrence_id == "new:x"
        for edge in correspondence.edges
    )


def test_competing_legacy_candidates_preserve_unique_owner_continuity() -> None:
    prop_p = expression(
        "Prop",
        occurrence_id="p:Prop",
        fingerprint="sort:Prop",
        expression_id="p:type",
    )
    prop_q = expression(
        "Prop",
        occurrence_id="q:Prop",
        fingerprint="sort:Prop",
        expression_id="q:type",
    )
    prop_binder = expression(
        "Prop",
        occurrence_id="binder:Prop",
        fingerprint="sort:Prop",
        expression_id="binder:type",
    )
    old_p = replace(local("P"), type_expr=prop_p)
    old_q = replace(local("Q"), type_expr=prop_q)
    new_p = replace(local("P"), type_expr=prop_p)
    before = state(goal(locals_=(old_p, old_q), target=expression("body-old")))
    after = state(goal(locals_=(new_p,), target=prop_binder))

    correspondence = build_correspondence(
        before,
        after,
        explicit_occurrence_edges=(
            ExplicitOccurrenceEdge("p:Prop", "p:Prop", "same-context"),
            ExplicitOccurrenceEdge("q:Prop", "binder:Prop", "binder-introduction"),
            ExplicitOccurrenceEdge("p:Prop", "binder:Prop", "legacy-copy-candidate"),
        ),
    )
    pairs = {
        (edge.sources[0].occurrence_id, edge.targets[0].occurrence_id)
        for edge in correspondence.edges
        if len(edge.sources) == len(edge.targets) == 1
        and edge.sources[0].kind is EntityKind.OCCURRENCE
        and edge.targets[0].kind is EntityKind.OCCURRENCE
    }

    assert ("p:Prop", "p:Prop") in pairs
    assert ("q:Prop", "binder:Prop") in pairs
    assert ("p:Prop", "binder:Prop") not in pairs


def test_correspondence_is_a_true_one_to_many_and_many_to_one_relation() -> None:
    source = EntityRef(EntityKind.OCCURRENCE, "g0", occurrence_id="source")
    left = EntityRef(EntityKind.OCCURRENCE, "g1", occurrence_id="left")
    right = EntityRef(EntityKind.OCCURRENCE, "g1", occurrence_id="right")
    merged = EntityRef(EntityKind.OCCURRENCE, "g2", occurrence_id="merged")
    relation = Correspondence(
        (
            CorrespondenceEdge(
                (source,),
                (left, right),
                RelationKind.COPY,
                MatchProvenance.EXPLICIT,
            ),
            CorrespondenceEdge(
                (left, right),
                (merged,),
                RelationKind.MERGE,
                MatchProvenance.EXPLICIT,
            ),
        )
    ).normalized()

    assert relation.targets_for(source) == (left, right)
    assert relation.sources_for(merged) == (left, right)
    assert {item.relation for item in relation.edges} == {
        RelationKind.COPY,
        RelationKind.MERGE,
    }


def test_nary_candidate_labels_are_normalized_after_component_arity_is_known() -> None:
    source = EntityRef(EntityKind.OCCURRENCE, "g0", occurrence_id="source")
    target = EntityRef(EntityKind.OCCURRENCE, "g1", occurrence_id="target")

    assert (
        correspondence_module._relation_for_arity(
            (source,), (target,), RelationKind.COPY
        )
        is RelationKind.PRESERVE
    )
    assert (
        correspondence_module._relation_for_arity(
            (source,), (target,), RelationKind.SPLIT
        )
        is RelationKind.REWRITE
    )


def test_native_copy_hyperedge_survives_state_diff_as_one_relation() -> None:
    source_target = expression(
        "x", lean_identity="fvar:x", occurrence_id="old:x", fingerprint="x"
    )
    left_target = expression(
        "x", lean_identity="fvar:x", occurrence_id="left:x", fingerprint="x"
    )
    right_target = expression(
        "x", lean_identity="fvar:x", occurrence_id="right:x", fingerprint="x"
    )
    before = state(goal("g0", target=source_target))
    after = state(
        goal("g1", target=left_target, parent_goal_id="g0", branch_index=0),
        goal("g2", target=right_target, parent_goal_id="g0", branch_index=1),
    )
    source_ref = EntityRef(
        EntityKind.OCCURRENCE,
        "g0",
        expression_role="target",
        occurrence_id="old:x",
    )
    left_ref = EntityRef(
        EntityKind.OCCURRENCE,
        "g1",
        expression_role="target",
        occurrence_id="left:x",
    )
    right_ref = EntityRef(
        EntityKind.OCCURRENCE,
        "g2",
        expression_role="target",
        occurrence_id="right:x",
    )

    transition = diff_proof_states(
        before,
        after,
        explicit_goal_edges=(
            ExplicitGoalEdge(("g0",), ("g1", "g2"), "lean-lineage", RelationKind.SPLIT),
        ),
        explicit_entity_edges=(
            CorrespondenceEdge(
                (source_ref,),
                (left_ref, right_ref),
                RelationKind.COPY,
                MatchProvenance.LEAN_DEFEQ,
                ("kernel-certified-copy",),
            ),
        ),
    )

    copy = next(
        edge
        for edge in transition.correspondence.edges
        if source_ref in edge.sources and edge.relation is RelationKind.COPY
    )
    assert copy.sources == (source_ref,)
    assert copy.targets == (left_ref, right_ref)
    assert apply_transition(before, transition) == after


def test_identity_replay_and_fingerprint_guard() -> None:
    before = state(goal(locals_=(local("x"),)))
    identity = diff_proof_states(before, before)

    assert identity.is_identity
    assert apply_transition(before, identity) == before
    with pytest.raises(ValueError, match="source fingerprint"):
        apply_transition(state(goal("other")), identity)


def test_normalization_is_idempotent_and_removes_duplicate_effects() -> None:
    before = state(goal(locals_=(local("x", value="1"),)))
    after = state(goal(locals_=(local("x", value="2"), local("y"))))
    clean = diff_proof_states(before, after)
    noisy = replace(
        clean,
        correspondence=Correspondence(
            tuple(reversed(clean.correspondence.edges)) + clean.correspondence.edges[:1]
        ),
        context_effects=tuple(reversed(clean.context_effects))
        + clean.context_effects[:1],
        target_effects=tuple(reversed(clean.target_effects)) + clean.target_effects[:1],
        goal_effects=tuple(reversed(clean.goal_effects)) + clean.goal_effects[:1],
    )

    normalized = noisy.normalized()

    assert normalized == normalized.normalized()
    assert normalized == clean
    assert apply_transition(before, normalized) == after


def test_composition_is_extensionally_equal_to_direct_diff() -> None:
    first = state(goal(locals_=(local("x", value="1"),)))
    middle = state(goal(locals_=(local("x", name="y", value="1"),)))
    final = state(goal(locals_=(local("x", name="y", value="2"), local("h"))))
    first_delta = diff_proof_states(first, middle)
    second_delta = diff_proof_states(middle, final)
    composed = compose_transitions(first, first_delta, second_delta)
    direct = diff_proof_states(first, final)

    assert apply_transition(first, composed) == final
    assert semantically_equivalent(first, composed, direct)


def test_referential_validation_rejects_nonexistent_entities() -> None:
    before = state(goal("g"))
    after = state(goal("g"))
    bad = Correspondence(
        (
            CorrespondenceEdge(
                (EntityRef(EntityKind.GOAL, "missing"),),
                (EntityRef(EntityKind.GOAL, "g"),),
                RelationKind.PRESERVE,
                MatchProvenance.EXPLICIT,
            ),
        )
    )

    errors = validate_correspondence(before, after, bad)

    assert any("nonexistent source" in item and "missing" in item for item in errors)


def test_total_correspondence_makes_unmatched_branch_content_explicit() -> None:
    before = state(goal("parent", target=expression("A")))
    after = state(
        goal("left", target=expression("B"), parent_goal_id="parent"),
        goal("right", target=expression("C"), parent_goal_id="parent"),
    )
    partial = Correspondence(
        (
            CorrespondenceEdge(
                (EntityRef(EntityKind.GOAL, "parent"),),
                (
                    EntityRef(EntityKind.GOAL, "left"),
                    EntityRef(EntityKind.GOAL, "right"),
                ),
                RelationKind.SPLIT,
                MatchProvenance.LEAN_IDENTITY,
            ),
        )
    )

    assert any(
        "unrelated target entity" in item
        for item in validate_total_correspondence(before, after, partial)
    )

    total = complete_correspondence(before, after, partial)

    assert not validate_total_correspondence(before, after, total)
    assert any(
        edge.relation is RelationKind.CREATE
        and edge.targets[0].kind is EntityKind.OCCURRENCE
        for edge in total.edges
        if edge.targets
    )


def test_state_validation_rejects_dangling_expression_parent() -> None:
    broken_target = Expression(
        expression_id="broken",
        fingerprint="broken",
        occurrences=(
            ExprOccurrence(
                occurrence_id="child",
                kind="fvar",
                path=(0,),
                fingerprint="x",
                lean_identity="fvar:x",
                parent_id="absent",
            ),
        ),
    )

    errors = validate_state(state(goal(target=broken_target)))

    assert any("missing parent absent" in item for item in errors)


def test_normalized_correspondence_rejects_empty_and_repeated_endpoints() -> None:
    source = EntityRef(EntityKind.GOAL, "g")
    with pytest.raises(ValueError, match="empty correspondence"):
        CorrespondenceEdge((), (), RelationKind.PRESERVE, MatchProvenance.EXPLICIT)
    with pytest.raises(ValueError, match="repeats a source"):
        CorrespondenceEdge(
            (source, source),
            (EntityRef(EntityKind.GOAL, "h"),),
            RelationKind.MERGE,
            MatchProvenance.EXPLICIT,
        )
