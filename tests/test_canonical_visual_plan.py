from __future__ import annotations

from dataclasses import replace

from proof_video.presentation import (
    VisualPrimitive,
    VisualPrimitiveKind,
    plan_visual_transition,
    validate_visual_plan,
)
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
    ProofTransition,
    TargetEffect,
    TargetEffectKind,
    TransitionMetadata,
)
from proof_video.proof.state import (
    CharacterSpan,
    ExprOccurrence,
    Expression,
    GoalState,
    LocalDecl,
    ProofState,
)


def expr(
    expression_id: str,
    *nodes: tuple[str, str, tuple[str | int, ...], str, str, str | None],
    latex: str | None = None,
    fingerprint: str | None = None,
) -> Expression:
    return Expression(
        expression_id=expression_id,
        fingerprint=fingerprint or f"fp:{expression_id}",
        lean=expression_id,
        latex=latex or expression_id,
        type_fingerprint="type:Prop",
        occurrences=tuple(
            ExprOccurrence(
                occurrence_id=occurrence_id,
                kind=kind,
                path=path,
                fingerprint=node_fingerprint,
                lean_identity=identity,
                type_fingerprint="type:Real" if kind in {"fvar", "bvar"} else "",
                parent_id=parent_id,
            )
            for occurrence_id, kind, path, node_fingerprint, identity, parent_id in nodes
        ),
    )


def atom(name: str, *, occurrence_id: str | None = None) -> Expression:
    return expr(
        f"expr:{name}",
        (
            occurrence_id or f"occ:{name}",
            "const",
            (),
            f"node:{name}",
            f"const:{name}",
            None,
        ),
        latex=name,
    )


def local(
    decl_id: str,
    *,
    name: str | None = None,
    aliases: tuple[str, ...] = (),
    value: Expression | None = None,
) -> LocalDecl:
    return LocalDecl(
        decl_id=decl_id,
        user_name=name or decl_id,
        type_expr=atom("Real", occurrence_id=f"type:{decl_id}"),
        value_expr=value,
        aliases=aliases,
    )


def goal(
    goal_id: str = "g",
    *,
    locals_: tuple[LocalDecl, ...] = (),
    target: Expression | None = None,
    parent_goal_id: str | None = None,
    branch_index: int | None = None,
) -> GoalState:
    return GoalState(
        goal_id=goal_id,
        lineage_id=f"lineage:{goal_id}",
        locals=locals_,
        target=target or atom("P"),
        parent_goal_id=parent_goal_id,
        branch_kind="case" if parent_goal_id else "",
        branch_index=branch_index,
    )


def state(
    *goals: GoalState,
    focus: tuple[str, ...] | None = None,
) -> ProofState:
    return ProofState(
        tuple(goals),
        focus if focus is not None else tuple(item.goal_id for item in goals[:1]),
    )


def primitive_entities(
    plan, primitive: VisualPrimitive
) -> tuple[tuple[EntityRef, ...], tuple[EntityRef, ...]]:
    return (
        tuple(plan.anchor(item).entity for item in primitive.source_anchor_ids),
        tuple(plan.anchor(item).entity for item in primitive.target_anchor_ids),
    )


def with_cross_role_edge(
    transition: ProofTransition,
    source: EntityRef,
    target: EntityRef,
) -> ProofTransition:
    edges = tuple(
        edge
        for edge in transition.correspondence.edges
        if not (edge.relation is RelationKind.REMOVE and source in edge.sources)
        and not (edge.relation is RelationKind.CREATE and target in edge.targets)
    )
    return replace(
        transition,
        correspondence=Correspondence(
            (
                *edges,
                CorrespondenceEdge(
                    (source,),
                    (target,),
                    RelationKind.PRESERVE,
                    MatchProvenance.EXPLICIT,
                    ("Lean binder identity",),
                ),
            )
        ).normalized(),
    )


def forall_target(prefix: str) -> Expression:
    return expr(
        f"{prefix}:forall",
        (f"{prefix}:root", "forall", (), "forall", "const:forall", None),
        (
            f"{prefix}:binder",
            "bvar",
            (0,),
            "x",
            "bvar:x",
            f"{prefix}:root",
        ),
        (
            f"{prefix}:body",
            "const",
            (1,),
            "P",
            "const:P",
            f"{prefix}:root",
        ),
        latex="\\forall x, P(x)",
    )


def body_target(prefix: str) -> Expression:
    return expr(
        f"{prefix}:body-expr",
        (f"{prefix}:root", "app", (), "P-app", "const:P", None),
        (
            f"{prefix}:x",
            "fvar",
            (0,),
            "x",
            "fvar:x",
            f"{prefix}:root",
        ),
        latex="P(x)",
    )


def canonical_forall_target(prefix: str, local_type: Expression) -> Expression:
    """A small faithful model of the nodes emitted by ``renderSemanticExpr``."""

    return expr(
        f"{prefix}:forall-canonical",
        (f"{prefix}:forall", "forall", (), "forall-fp", "", None),
        (
            f"{prefix}:quantifier",
            "quantifier-symbol",
            ("quantifier",),
            local_type.fingerprint,
            f"quantifier:{prefix}",
            f"{prefix}:forall",
        ),
        (
            f"{prefix}:binder",
            "declaration",
            ("binder",),
            local_type.fingerprint,
            f"binder:{prefix}",
            f"{prefix}:forall",
        ),
        (
            f"{prefix}:colon",
            "declaration-punctuation",
            ("binder", "colon"),
            local_type.fingerprint,
            f"binder-colon:{prefix}",
            f"{prefix}:forall",
        ),
        (
            f"{prefix}:domain",
            "const",
            (0,),
            local_type.fingerprint,
            "const:Real",
            f"{prefix}:forall",
        ),
        (f"{prefix}:body", "app", (1,), "P-app", "", f"{prefix}:forall"),
        (
            f"{prefix}:P",
            "const",
            (1, 0),
            "P",
            "const:P",
            f"{prefix}:body",
        ),
        (
            f"{prefix}:bound-x",
            "bvar",
            (1, 1),
            "x",
            "bvar:0",
            f"{prefix}:body",
        ),
        latex="\\forall x : \\mathbb{R}, P(x)",
    )


def canonical_body_target(prefix: str, decl_id: str) -> Expression:
    return expr(
        f"{prefix}:body-canonical",
        (f"{prefix}:body", "app", (), "P-app", "", None),
        (
            f"{prefix}:P",
            "const",
            (0,),
            "P",
            "const:P",
            f"{prefix}:body",
        ),
        (
            f"{prefix}:free-x",
            "fvar",
            (1,),
            "x",
            f"fvar:{decl_id}",
            f"{prefix}:body",
        ),
        latex="P(x)",
    )


def test_public_visual_vocabulary_contains_all_canonical_primitives() -> None:
    assert {
        "keep",
        "move",
        "copy",
        "rewrite",
        "create",
        "remove",
        "split",
        "merge",
        "close",
        "focus",
        "reorder",
    } <= {item.value for item in VisualPrimitiveKind}


def test_intro_moves_binder_to_context_without_tactic_dispatch() -> None:
    before = state(goal(target=forall_target("old")))
    after = state(goal(locals_=(local("x"),), target=body_target("new")))
    source = EntityRef(
        EntityKind.OCCURRENCE,
        "g",
        expression_role="target",
        occurrence_id="old:binder",
    )
    target = EntityRef(EntityKind.LOCAL, "g", local_id="x")
    transition = with_cross_role_edge(diff_proof_states(before, after), source, target)

    intro_named = replace(
        transition,
        metadata=TransitionMetadata(tactic_text="intro x"),
    )
    unrelated_name = replace(
        transition,
        metadata=TransitionMetadata(tactic_text="totally unrelated text"),
    )
    first = plan_visual_transition(before, after, intro_named)
    second = plan_visual_transition(before, after, unrelated_name)

    assert first == second
    moves = first.primitives_of_kind(VisualPrimitiveKind.MOVE)
    assert any(
        primitive_entities(first, item) == ((source,), (target,)) for item in moves
    )
    assert validate_visual_plan(first) == ()


def test_revert_moves_context_entity_back_into_quantified_target() -> None:
    before = state(goal(locals_=(local("x"),), target=body_target("old")))
    after = state(goal(target=forall_target("new")))
    source = EntityRef(EntityKind.LOCAL, "g", local_id="x")
    target = EntityRef(
        EntityKind.OCCURRENCE,
        "g",
        expression_role="target",
        occurrence_id="new:binder",
    )
    transition = with_cross_role_edge(diff_proof_states(before, after), source, target)

    plan = plan_visual_transition(before, after, transition)

    assert any(
        primitive_entities(plan, item) == ((source,), (target,))
        for item in plan.primitives_of_kind(VisualPrimitiveKind.MOVE)
    )


def test_native_intro_is_derived_from_alpha_equivalent_state_shape() -> None:
    introduced = local("x")
    before = state(
        goal(target=canonical_forall_target("old-native", introduced.type_expr))
    )
    after = state(
        goal(
            locals_=(introduced,),
            target=canonical_body_target("new-native", introduced.decl_id),
        )
    )

    plan = plan_visual_transition(before, after, diff_proof_states(before, after))
    source = EntityRef(
        EntityKind.OCCURRENCE,
        "g",
        expression_role="target",
        occurrence_id="old-native:binder",
    )
    target = EntityRef(EntityKind.LOCAL, "g", local_id="x")

    assert any(
        primitive_entities(plan, item) == ((source,), (target,))
        for item in plan.primitives_of_kind(VisualPrimitiveKind.MOVE)
    )
    assert not any(
        target in primitive_entities(plan, item)[1]
        for item in plan.primitives_of_kind(VisualPrimitiveKind.CREATE)
    )


def test_native_revert_is_the_exact_inverse_binder_transport() -> None:
    reverted = local("x")
    before = state(
        goal(
            locals_=(reverted,),
            target=canonical_body_target("old-revert", reverted.decl_id),
        )
    )
    after = state(
        goal(target=canonical_forall_target("new-revert", reverted.type_expr))
    )

    plan = plan_visual_transition(before, after, diff_proof_states(before, after))
    source = EntityRef(EntityKind.LOCAL, "g", local_id="x")
    target = EntityRef(
        EntityKind.OCCURRENCE,
        "g",
        expression_role="target",
        occurrence_id="new-revert:binder",
    )

    assert any(
        primitive_entities(plan, item) == ((source,), (target,))
        for item in plan.primitives_of_kind(VisualPrimitiveKind.MOVE)
    )


def test_replace_is_one_rewrite_with_a_persistent_local_anchor() -> None:
    old = local("h-old", name="h")
    new = local("h-new", name="h", aliases=("h-old",))
    before = state(goal(locals_=(old,)))
    after = state(goal(locals_=(new,)))

    plan = plan_visual_transition(before, after, diff_proof_states(before, after))
    local_rewrites = []
    for primitive in plan.primitives_of_kind(VisualPrimitiveKind.REWRITE):
        sources, targets = primitive_entities(plan, primitive)
        if (
            sources
            and targets
            and sources[0].kind is targets[0].kind is EntityKind.LOCAL
        ):
            local_rewrites.append(primitive)

    assert len(local_rewrites) == 1
    primitive = local_rewrites[0]
    source_anchor = plan.anchor(primitive.source_anchor_ids[0])
    target_anchor = plan.anchor(primitive.target_anchor_ids[0])
    assert source_anchor.persistent_id == target_anchor.persistent_id
    assert "replace-local" in primitive.evidence


def applied_target(prefix: str, argument: str) -> Expression:
    return expr(
        f"{prefix}:f({argument})",
        (f"{prefix}:root", "app", (), "f-app", "const:f", None),
        (
            f"{prefix}:arg",
            "fvar" if argument == "x" else "const",
            (0,),
            argument,
            f"fvar:{argument}" if argument == "x" else f"const:{argument}",
            f"{prefix}:root",
        ),
        latex=f"f({argument})",
    )


def test_subst_removes_local_and_rewrites_only_the_target_semantics() -> None:
    before = state(goal(locals_=(local("x"),), target=applied_target("old", "x")))
    after = state(goal(target=applied_target("new", "2")))

    plan = plan_visual_transition(before, after, diff_proof_states(before, after))
    removals = plan.primitives_of_kind(VisualPrimitiveKind.REMOVE)
    rewrites = plan.primitives_of_kind(VisualPrimitiveKind.REWRITE)

    assert any(
        sources and sources[0].kind is EntityKind.LOCAL
        for sources, _targets in (primitive_entities(plan, item) for item in removals)
    )
    assert any(
        item.scope == "target"
        and "substitute-entity" in item.evidence
        and "entity:x" in item.evidence
        for item in rewrites
    )


def test_goal_split_close_and_focus_have_dedicated_primitives() -> None:
    initial = state(goal("parent"), focus=("parent",))
    left = goal("left", parent_goal_id="parent", branch_index=0)
    right = goal("right", parent_goal_id="parent", branch_index=1)
    branched = state(left, right, focus=("left",))
    split_plan = plan_visual_transition(
        initial,
        branched,
        diff_proof_states(initial, branched),
    )

    split = split_plan.primitives_of_kind(VisualPrimitiveKind.SPLIT)
    assert len(split) == 1
    assert len(split[0].source_anchor_ids) == 1
    assert len(split[0].target_anchor_ids) == 2
    assert split_plan.primitives_of_kind(VisualPrimitiveKind.FOCUS)

    empty = state(focus=())
    close_plan = plan_visual_transition(
        branched,
        empty,
        diff_proof_states(branched, empty),
    )
    assert len(close_plan.primitives_of_kind(VisualPrimitiveKind.CLOSE)) == 2


def test_goal_split_copies_shared_context_and_moves_maximal_target_subtrees() -> None:
    shared = local("x")
    conjunction = expr(
        "old:A-and-B",
        ("old:and", "and", (), "node:And", "const:And", None),
        ("old:A", "const", (0,), "node:A", "const:A", "old:and"),
        ("old:B", "const", (1,), "node:B", "const:B", "old:and"),
        latex=r"A \land B",
    )
    initial = state(goal("parent", locals_=(shared,), target=conjunction))
    left = goal(
        "left",
        locals_=(shared,),
        target=atom("A", occurrence_id="left:A"),
        parent_goal_id="parent",
        branch_index=0,
    )
    right = goal(
        "right",
        locals_=(shared,),
        target=atom("B", occurrence_id="right:B"),
        parent_goal_id="parent",
        branch_index=1,
    )
    branched = state(left, right)

    transition = diff_proof_states(initial, branched)
    plan = plan_visual_transition(initial, branched, transition)
    local_copies = [
        primitive_entities(plan, primitive)
        for primitive in plan.primitives_of_kind(VisualPrimitiveKind.COPY)
    ]

    assert (
        (EntityRef(EntityKind.LOCAL, "parent", local_id="x"),),
        (
            EntityRef(EntityKind.LOCAL, "left", local_id="x"),
            EntityRef(EntityKind.LOCAL, "right", local_id="x"),
        ),
    ) in local_copies
    moved = {
        (sources[0].occurrence_id, targets[0].occurrence_id)
        for primitive in plan.primitives_of_kind(VisualPrimitiveKind.MOVE)
        for sources, targets in (primitive_entities(plan, primitive),)
        if len(sources) == len(targets) == 1
        and sources[0].kind is targets[0].kind is EntityKind.OCCURRENCE
    }
    assert {("old:A", "left:A"), ("old:B", "right:B")} <= moved


def test_new_local_definition_is_created_not_rewritten_from_nothing() -> None:
    before = state(goal())
    after = state(goal(locals_=(local("y", value=atom("t")),)))
    plan = plan_visual_transition(before, after, diff_proof_states(before, after))

    local_primitives = [
        primitive
        for primitive in plan.primitives
        if primitive.scope == "local"
        and any("add-local-definition" in item for item in primitive.evidence)
    ]
    assert len(local_primitives) == 1
    assert local_primitives[0].kind is VisualPrimitiveKind.CREATE
    assert not local_primitives[0].source_anchor_ids
    assert local_primitives[0].target_anchor_ids


def test_certified_goal_merge_merges_shared_local_content() -> None:
    shared = local("x")
    left = goal("left", locals_=(shared,), target=atom("A"))
    right = goal("right", locals_=(shared,), target=atom("B"))
    before = state(left, right)
    merged_goal = goal("merged", locals_=(shared,), target=atom("C"))
    after = state(merged_goal)

    transition = diff_proof_states(
        before,
        after,
        explicit_goal_edges=(
            ExplicitGoalEdge(
                ("left", "right"),
                ("merged",),
                "certified-action-frontier-join",
                RelationKind.MERGE,
            ),
        ),
    )
    plan = plan_visual_transition(before, after, transition)
    merges = [
        primitive_entities(plan, primitive)
        for primitive in plan.primitives_of_kind(VisualPrimitiveKind.MERGE)
    ]

    assert (
        (
            EntityRef(EntityKind.LOCAL, "left", local_id="x"),
            EntityRef(EntityKind.LOCAL, "right", local_id="x"),
        ),
        (EntityRef(EntityKind.LOCAL, "merged", local_id="x"),),
    ) in merges


def test_local_reorder_is_a_control_primitive_and_individual_moves() -> None:
    before = state(goal(locals_=(local("x"), local("y"))))
    after = state(goal(locals_=(local("y"), local("x"))))

    plan = plan_visual_transition(before, after, diff_proof_states(before, after))

    reorder = plan.primitives_of_kind(VisualPrimitiveKind.REORDER)
    assert any(item.scope == "context-order" for item in reorder)
    moved_local_ids = {
        sources[0].local_id
        for primitive in plan.primitives_of_kind(VisualPrimitiveKind.MOVE)
        for sources, targets in (primitive_entities(plan, primitive),)
        if len(sources) == len(targets) == 1
        and sources[0].kind is targets[0].kind is EntityKind.LOCAL
    }
    assert moved_local_ids == {"x", "y"}


def test_intermediate_state_keeps_persistent_ids_across_consecutive_plans() -> None:
    first = state(goal(locals_=(local("x"), local("y"))))
    middle = state(goal(locals_=(local("y"), local("x"))))
    final = state(goal(locals_=(local("y"), local("x-new", aliases=("x",)))))
    first_plan = plan_visual_transition(
        first,
        middle,
        diff_proof_states(first, middle),
    )
    second_plan = plan_visual_transition(
        middle,
        final,
        diff_proof_states(middle, final),
    )
    middle_x = EntityRef(EntityKind.LOCAL, "g", local_id="x")
    first_target = next(
        item
        for item in first_plan.anchors
        if item.side.value == "after" and item.entity == middle_x
    )
    second_source = next(
        item
        for item in second_plan.anchors
        if item.side.value == "before" and item.entity == middle_x
    )

    assert first_target.persistent_id == second_source.persistent_id


def repeated_expression(prefix: str) -> Expression:
    return expr(
        f"{prefix}:x+x=x",
        (f"{prefix}:root", "eq", (), "Eq", "const:Eq", None),
        (f"{prefix}:plus", "app", (0,), "Add", "const:Add", f"{prefix}:root"),
        (f"{prefix}:left", "fvar", (0, 0), "x", "fvar:x", f"{prefix}:plus"),
        (f"{prefix}:middle", "fvar", (0, 1), "x", "fvar:x", f"{prefix}:plus"),
        (f"{prefix}:right", "fvar", (1,), "x", "fvar:x", f"{prefix}:root"),
        latex="x+x=x",
        fingerprint="x+x=x",
    )


def test_repeated_symbols_keep_distinct_stable_anchors() -> None:
    before = state(goal(target=repeated_expression("old")))
    after = state(goal(target=repeated_expression("new")))
    plan = plan_visual_transition(before, after, diff_proof_states(before, after))
    pairs = set()
    persistent = {}
    for primitive in plan.primitives:
        sources, targets = primitive_entities(plan, primitive)
        if len(sources) == len(targets) == 1:
            source = sources[0]
            target = targets[0]
            if source.kind is target.kind is EntityKind.OCCURRENCE:
                pairs.add((source.occurrence_id, target.occurrence_id))
                persistent[(source.occurrence_id, target.occurrence_id)] = (
                    plan.anchor(primitive.source_anchor_ids[0]).persistent_id,
                    plan.anchor(primitive.target_anchor_ids[0]).persistent_id,
                )

    expected = {
        ("old:left", "new:left"),
        ("old:middle", "new:middle"),
        ("old:right", "new:right"),
    }
    assert expected <= pairs
    assert ("old:left", "new:right") not in pairs
    assert all(persistent[item][0] == persistent[item][1] for item in expected)


def occurrence_ref(goal_id: str, occurrence_id: str) -> EntityRef:
    return EntityRef(
        EntityKind.OCCURRENCE,
        goal_id,
        expression_role="target",
        occurrence_id=occurrence_id,
    )


def n_ary_expression(prefix: str, names: tuple[str, ...]) -> Expression:
    return expr(
        f"{prefix}:nary",
        *(
            (
                f"{prefix}:{name}",
                "fvar",
                (index,),
                name,
                f"fvar:{name}",
                None,
            )
            for index, name in enumerate(names)
        ),
        fingerprint=f"nary:{prefix}",
    )


def replace_occurrence_edges(
    transition: ProofTransition,
    involved: frozenset[EntityRef],
    replacement: CorrespondenceEdge,
) -> ProofTransition:
    retained = tuple(
        edge
        for edge in transition.correspondence.edges
        if not involved.intersection((*edge.sources, *edge.targets))
    )
    return replace(
        transition,
        correspondence=Correspondence((*retained, replacement)).normalized(),
    )


def test_explicit_copy_and_merge_hyperedges_survive_visual_planning() -> None:
    one = state(goal(target=n_ary_expression("one", ("x",))))
    two = state(goal(target=n_ary_expression("two", ("a", "b"))))
    source = occurrence_ref("g", "one:x")
    left = occurrence_ref("g", "two:a")
    right = occurrence_ref("g", "two:b")
    copy_transition = replace_occurrence_edges(
        diff_proof_states(one, two),
        frozenset((source, left, right)),
        CorrespondenceEdge(
            (source,),
            (left, right),
            RelationKind.COPY,
            MatchProvenance.EXPLICIT,
            ("certified-copy",),
        ),
    )
    copy_plan = plan_visual_transition(one, two, copy_transition)

    copies = copy_plan.primitives_of_kind(VisualPrimitiveKind.COPY)
    assert len(copies) == 1
    assert primitive_entities(copy_plan, copies[0]) == ((source,), (left, right))

    merged = occurrence_ref("g", "one:x")
    merge_transition = replace_occurrence_edges(
        diff_proof_states(two, one),
        frozenset((left, right, merged)),
        CorrespondenceEdge(
            (left, right),
            (merged,),
            RelationKind.MERGE,
            MatchProvenance.EXPLICIT,
            ("certified-merge",),
        ),
    )
    merge_plan = plan_visual_transition(two, one, merge_transition)

    merges = merge_plan.primitives_of_kind(VisualPrimitiveKind.MERGE)
    assert len(merges) == 1
    assert primitive_entities(merge_plan, merges[0]) == ((left, right), (merged,))


def test_missing_correspondence_is_an_explicit_debuggable_fallback() -> None:
    before = state(goal())
    current = before.goals[0]
    transition = ProofTransition(
        before_fingerprint=before.fingerprint,
        after_fingerprint=before.fingerprint,
        correspondence=Correspondence(),
        target_effects=(
            TargetEffect(
                TargetEffectKind.KEEP,
                current.goal_id,
                current.target,
                current.target,
            ),
        ),
    )

    plan = plan_visual_transition(before, before, transition)

    keep = plan.primitives_of_kind(VisualPrimitiveKind.KEEP)
    assert len(keep) == 1
    assert keep[0].used_fallback
    assert "no correspondence edge" in keep[0].fallback_reason
    assert any(item.code == "effect-fallback" for item in plan.diagnostics)


def text_only_expression(prefix: str, fingerprint: str) -> Expression:
    return Expression(
        expression_id=f"{prefix}:text",
        fingerprint=f"expr:{fingerprint}",
        lean="x",
        latex="x",
        occurrences=(
            ExprOccurrence(
                occurrence_id=f"{prefix}:symbol",
                kind="atom",
                path=(),
                fingerprint=fingerprint,
                latex_spans=(CharacterSpan(0, 1),),
            ),
        ),
    )


def test_uncertified_text_fallback_is_remove_create_not_a_move() -> None:
    before = state(goal(target=text_only_expression("old", "old-fp")))
    after = state(goal(target=text_only_expression("new", "new-fp")))

    plan = plan_visual_transition(before, after, diff_proof_states(before, after))
    text_primitives = [
        item for item in plan.primitives if "text-fallback" in item.provenance
    ]

    assert {item.kind for item in text_primitives} == {
        VisualPrimitiveKind.CREATE,
        VisualPrimitiveKind.REMOVE,
    }
    assert all(not item.used_fallback for item in text_primitives)
    assert any(
        item.code == "uncertified-text-continuity-rejected" for item in plan.diagnostics
    )
    assert validate_visual_plan(plan) == ()


def test_visual_plan_rejects_physical_motion_certified_only_by_rendered_text() -> None:
    before = state(goal(target=text_only_expression("old", "old-fp")))
    after = state(goal(target=text_only_expression("new", "new-fp")))
    plan = plan_visual_transition(before, after, diff_proof_states(before, after))
    removed = next(
        item
        for item in plan.primitives
        if item.kind is VisualPrimitiveKind.REMOVE
        and "text-fallback" in item.provenance
    )
    created = next(
        item
        for item in plan.primitives
        if item.kind is VisualPrimitiveKind.CREATE
        and "text-fallback" in item.provenance
    )
    invalid = replace(
        plan,
        primitives=(
            replace(
                removed,
                kind=VisualPrimitiveKind.MOVE,
                target_anchor_ids=created.target_anchor_ids,
            ),
        ),
    )

    assert any(
        "rendered-text equality cannot certify physical continuity" in error
        for error in validate_visual_plan(invalid)
    )


def test_one_new_formula_can_draw_certified_subtrees_from_multiple_rows() -> None:
    premise = LocalDecl(
        decl_id="hA",
        user_name="hA",
        type_expr=expr(
            "old:A",
            ("old:A-root", "app", (), "subtree:A", "const:A", None),
            latex="A",
            fingerprint="expr:A",
        ),
        is_proof=True,
    )
    old_target = expr(
        "old:B",
        ("old:B-root", "app", (), "subtree:B", "const:B", None),
        latex="B",
        fingerprint="expr:B",
    )
    new_target = expr(
        "new:A-and-B",
        ("new:root", "and", (), "subtree:and", "const:And", None),
        ("new:A", "app", (0,), "subtree:A", "const:A", "new:root"),
        ("new:B", "app", (1,), "subtree:B", "const:B", "new:root"),
        latex=r"A \land B",
        fingerprint="expr:A-and-B",
    )
    before = state(goal(locals_=(premise,), target=old_target))
    after = state(goal(target=new_target))

    plan = plan_visual_transition(before, after, diff_proof_states(before, after))
    moved_pairs = {
        (sources[0].occurrence_id, targets[0].occurrence_id)
        for primitive in plan.primitives_of_kind(VisualPrimitiveKind.MOVE)
        for sources, targets in (primitive_entities(plan, primitive),)
        if len(sources) == len(targets) == 1
        and sources[0].kind is targets[0].kind is EntityKind.OCCURRENCE
    }

    assert ("old:A-root", "new:A") in moved_pairs
    assert ("old:B-root", "new:B") in moved_pairs


def test_stationary_premise_can_also_copy_its_typed_subtree_into_target() -> None:
    premise = LocalDecl(
        decl_id="hA",
        user_name="hA",
        type_expr=expr(
            "old:A",
            ("old:A-root", "app", (), "subtree:A", "const:A", None),
            latex="A",
            fingerprint="expr:A",
        ),
        is_proof=True,
    )
    old_target = expr(
        "old:B",
        ("old:B-root", "app", (), "subtree:B", "const:B", None),
        latex="B",
        fingerprint="expr:B",
    )
    new_target = expr(
        "new:A-and-B",
        ("new:root", "and", (), "subtree:and", "const:And", None),
        ("new:A", "app", (0,), "subtree:A", "const:A", "new:root"),
        ("new:B", "app", (1,), "subtree:B", "const:B", "new:root"),
        latex=r"A \land B",
        fingerprint="expr:A-and-B",
    )
    before = state(goal(locals_=(premise,), target=old_target))
    after = state(goal(locals_=(premise,), target=new_target))

    transition = diff_proof_states(before, after)
    plan = plan_visual_transition(before, after, transition)
    copied = [
        primitive_entities(plan, primitive)
        for primitive in plan.primitives_of_kind(VisualPrimitiveKind.COPY)
    ]

    source = EntityRef(
        EntityKind.OCCURRENCE,
        "g",
        local_id="hA",
        expression_role="local-type",
        occurrence_id="old:A-root",
    )
    stationary = EntityRef(
        EntityKind.OCCURRENCE,
        "g",
        local_id="hA",
        expression_role="local-type",
        occurrence_id="old:A-root",
    )
    target = EntityRef(
        EntityKind.OCCURRENCE,
        "g",
        expression_role="target",
        occurrence_id="new:A",
    )
    assert any(
        sources == (source,) and set(targets) == {stationary, target}
        for sources, targets in copied
    )


def test_structural_copy_uses_the_maximal_function_application_not_its_glyphs() -> None:
    application = expr(
        "old:f-x",
        ("old:app", "app", (), "subtree:f-x", "", None),
        ("old:f", "const", (0,), "const:f", "const:f", "old:app"),
        ("old:x", "fvar", (1,), "fvar:x", "fvar:x", "old:app"),
        latex="f(x)",
        fingerprint="expr:f-x",
    )
    premise = LocalDecl("h", "h", application, is_proof=True)
    target = expr(
        "new:pair",
        ("new:root", "pair", (), "pair:f-x", "", None),
        ("new:app", "app", (0,), "subtree:f-x", "", "new:root"),
        ("new:f", "const", (0, 0), "const:f", "const:f", "new:app"),
        ("new:x", "fvar", (0, 1), "fvar:x", "fvar:x", "new:app"),
        ("new:C", "const", (1,), "const:C", "const:C", "new:root"),
        latex=r"f(x) \land C",
        fingerprint="expr:pair",
    )
    before = state(goal(locals_=(premise,), target=atom("C")))
    after = state(goal(locals_=(premise,), target=target))

    plan = plan_visual_transition(before, after, diff_proof_states(before, after))
    copy_sources = {
        sources[0].occurrence_id
        for primitive in plan.primitives_of_kind(VisualPrimitiveKind.COPY)
        for sources, _targets in (primitive_entities(plan, primitive),)
        if len(sources) == 1
    }

    assert "old:app" in copy_sources
    assert {"old:f", "old:x"}.isdisjoint(copy_sources)
