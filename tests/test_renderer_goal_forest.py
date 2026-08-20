from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

from manim import VGroup

from proof_video.animation.semantic import (
    RendererTransitionSource,
    compile_renderer_transition_plan,
    compile_renderer_transition_plan_from_sources,
)
from proof_video.models import Frame, Goal, Movie
from proof_video.presentation.model import (
    AnchorSide,
    LayoutAnchor,
    LayoutRowKind,
    SemanticVisualPlan,
    VisualPrimitive,
    VisualPrimitiveKind,
)
from proof_video.proof.correspondence import (
    CorrespondenceEdge,
    EntityKind,
    EntityRef,
    ExplicitGoalEdge,
    MatchProvenance,
    RelationKind,
)
from proof_video.proof.schema import (
    SemanticExpression,
    SemanticExpressionNode,
    SemanticSpan,
    SemanticTransition,
)
from proof_video.proof.state import CharacterSpan, ExprOccurrence, Expression
from proof_video.remotion_export import build_remotion_timeline
from proof_video.scene import ProofScene


def _goal(
    goal_id: str,
    lineage: str,
    target: str,
    *,
    parent: str | None = None,
) -> Goal:
    return Goal(
        goal_id,
        "",
        latex_target=target,
        lineage_id=lineage,
        parent_goal_id=parent,
        canonical_target=Expression(
            expression_id=f"target:{goal_id}",
            fingerprint=f"fingerprint:{target}",
            lean=target,
            latex=target,
        ),
    )


def _split_movie() -> Movie:
    parent = _goal("parent", "root", "P")
    left = _goal("left", "root/left", "L", parent="parent")
    right = _goal("right", "root/right", "R", parent="parent")
    split = ExplicitGoalEdge(
        ("parent",),
        ("left", "right"),
        "kernel-goal-split",
        RelationKind.SPLIT,
    )
    return Movie(
        "multi-goal",
        (
            Frame(0, "", (parent,), (parent,), canonical_abi=5),
            Frame(
                1,
                "",
                (left, right),
                (left,),
                goal_lineage=(split,),
                canonical_abi=5,
            ),
            # Reordering/focusing does not change either card identity.
            Frame(2, "", (right, left), (right,), canonical_abi=5),
        ),
    )


def _close_movie() -> Movie:
    movie = _split_movie()
    parent, split, _reordered = movie.frames
    right = split.goals[1]
    close = ExplicitGoalEdge(
        (split.goals[0].goal_id,),
        (),
        "kernel-goal-close",
        RelationKind.REMOVE,
    )
    return Movie(
        "close-branch",
        (
            parent,
            split,
            Frame(
                2,
                "",
                (right,),
                (right,),
                goal_lineage=(close,),
                canonical_abi=5,
            ),
        ),
    )


def _merge_movie() -> Movie:
    left = _goal("left", "left-lineage", "L")
    right = _goal("right", "right-lineage", "R")
    joined = _goal("joined", "joined-lineage", "J")
    merge = ExplicitGoalEdge(
        (left.goal_id, right.goal_id),
        (joined.goal_id,),
        "kernel-goal-merge",
        RelationKind.MERGE,
    )
    return Movie(
        "merge-branches",
        (
            Frame(0, "", (left, right), (left, right), canonical_abi=5),
            Frame(
                1,
                "",
                (joined,),
                (joined,),
                goal_lineage=(merge,),
                canonical_abi=5,
            ),
        ),
    )


def _canonical_merge_movie() -> Movie:
    def expression(
        expression_id: str,
        latex: str,
        occurrences: tuple[tuple[str, str, int, int], ...],
    ) -> Expression:
        return Expression(
            expression_id,
            f"fingerprint:{expression_id}",
            lean=latex,
            latex=latex,
            occurrences=tuple(
                ExprOccurrence(
                    occurrence_id,
                    "fvar",
                    (index,),
                    f"fingerprint:{identity}",
                    lean_identity=f"fvar:{identity}",
                    latex_spans=(CharacterSpan(start, end),),
                )
                for index, (occurrence_id, identity, start, end) in enumerate(
                    occurrences
                )
            ),
        )

    left_expression = expression("left", "a", (("left-a", "a", 0, 1),))
    right_expression = expression("right", "b", (("right-b", "b", 0, 1),))
    joined_expression = expression(
        "joined",
        "a+b",
        (("joined-a", "a", 0, 1), ("joined-b", "b", 2, 3)),
    )
    left = replace(_goal("left", "left-lineage", "a"), canonical_target=left_expression)
    right = replace(
        _goal("right", "right-lineage", "b"), canonical_target=right_expression
    )
    joined = replace(
        _goal("joined", "joined-lineage", "a+b"),
        canonical_target=joined_expression,
    )
    goal_merge = ExplicitGoalEdge(
        ("left", "right"),
        ("joined",),
        "kernel-goal-merge",
        RelationKind.MERGE,
    )
    correspondence = (
        CorrespondenceEdge(
            (
                EntityRef(
                    EntityKind.OCCURRENCE,
                    "left",
                    expression_role="target",
                    occurrence_id="left-a",
                ),
            ),
            (
                EntityRef(
                    EntityKind.OCCURRENCE,
                    "joined",
                    expression_role="target",
                    occurrence_id="joined-a",
                ),
            ),
            RelationKind.PRESERVE,
            MatchProvenance.EXPLICIT,
            ("verified-left-parent",),
        ),
        CorrespondenceEdge(
            (
                EntityRef(
                    EntityKind.OCCURRENCE,
                    "right",
                    expression_role="target",
                    occurrence_id="right-b",
                ),
            ),
            (
                EntityRef(
                    EntityKind.OCCURRENCE,
                    "joined",
                    expression_role="target",
                    occurrence_id="joined-b",
                ),
            ),
            RelationKind.PRESERVE,
            MatchProvenance.EXPLICIT,
            ("verified-right-parent",),
        ),
    )
    return Movie(
        "canonical-merge",
        (
            Frame(0, "", (left, right), (left, right), canonical_abi=5),
            Frame(
                1,
                "",
                (joined,),
                (joined,),
                goal_lineage=(goal_merge,),
                canonical_correspondence=correspondence,
                canonical_abi=5,
            ),
        ),
    ).with_canonical_timeline()


def test_remotion_exports_every_live_goal_as_a_stable_scoped_card() -> None:
    timeline = build_remotion_timeline(_split_movie(), fps=30)

    split_state = timeline["states"][1]
    reordered_state = timeline["states"][2]
    split_cards = split_state["goalForest"]["cards"]
    reordered_cards = reordered_state["goalForest"]["cards"]

    assert [card["goalId"] for card in split_cards] == ["left", "right"]
    assert [card["goalId"] for card in reordered_cards] == ["right", "left"]
    assert {card["id"] for card in split_cards} == {
        card["id"] for card in reordered_cards
    }
    assert all(card["parentCardIds"] for card in split_cards)
    assert {row["goalId"] for row in split_state["rows"]} == {"left", "right"}
    assert all(
        row["id"].startswith(f"{row['goalCardId']}/") for row in split_state["rows"]
    )
    assert timeline["rendererContract"] == "strict-proof-transition-v16-goal-forest"


def test_manim_projects_the_same_multi_goal_cards_without_fuzzy_pairing() -> None:
    frames = _split_movie().semantic_frames()
    scene = object.__new__(ProofScene)
    scene._goal_forest_layouts = {}
    scene._prepare_goal_forest(frames)

    def fake_rows(source: str, **_kwargs):
        row = VGroup()
        row.proof_latex_source = source
        return [(row, 0, len(source))]

    with (
        patch("proof_video.scene._initial_context_lines", return_value=[]),
        patch(
            "proof_video.scene._goal_latex", side_effect=lambda goal: goal.latex_target
        ),
        patch("proof_video.scene._wrapped_math_rows_with_spans", side_effect=fake_rows),
    ):
        split_block = scene._step_block(frames[1])
        reordered_block = scene._step_block(frames[2])

    assert len(split_block) == 2
    assert [block.proof_goal_id for block in split_block] == ["left", "right"]
    assert [block.proof_goal_id for block in reordered_block] == ["right", "left"]
    assert {block.proof_block_key for block in split_block} == {
        block.proof_block_key for block in reordered_block
    }
    assert all(block.proof_parent_block_keys for block in split_block)
    assert all(
        row.proof_row_id.startswith(f"{block.proof_block_key}/")
        for block in split_block
        for row in block
    )
    assert split_block.proof_canonical_state is True


def test_both_renderers_share_close_and_merge_card_provenance() -> None:
    closed_timeline = build_remotion_timeline(_close_movie(), fps=30)
    split_cards = closed_timeline["states"][1]["goalForest"]["cards"]
    closed_forest = closed_timeline["states"][2]["goalForest"]
    left_card_id = next(card["id"] for card in split_cards if card["goalId"] == "left")
    assert [card["goalId"] for card in closed_forest["cards"]] == ["right"]
    assert closed_forest["closedCardIds"] == [left_card_id]
    assert left_card_id in closed_forest["retiredCardIds"]

    merge_movie = _merge_movie()
    merged_timeline = build_remotion_timeline(merge_movie, fps=30)
    source_ids = tuple(
        card["id"] for card in merged_timeline["states"][0]["goalForest"]["cards"]
    )
    merged_card = merged_timeline["states"][1]["goalForest"]["cards"][0]
    assert tuple(merged_card["parentCardIds"]) == source_ids
    assert merged_card["incomingRelation"] == "merge"

    frames = merge_movie.semantic_frames()
    scene = object.__new__(ProofScene)
    scene._goal_forest_layouts = {}
    scene._prepare_goal_forest(frames)

    def fake_rows(source: str, **_kwargs):
        row = VGroup()
        row.proof_latex_source = source
        return [(row, 0, len(source))]

    with (
        patch("proof_video.scene._initial_context_lines", return_value=[]),
        patch(
            "proof_video.scene._goal_latex", side_effect=lambda goal: goal.latex_target
        ),
        patch("proof_video.scene._wrapped_math_rows_with_spans", side_effect=fake_rows),
    ):
        source_block = scene._step_block(frames[0])
        merged_block = scene._step_block(frames[1])

    assert tuple(merged_block[0].proof_parent_block_keys) == tuple(
        block.proof_block_key for block in source_block
    )
    assert merged_block[0].proof_goal_relation == "merge"


def test_canonical_merge_routes_all_parent_cards_through_both_renderers() -> None:
    movie = _canonical_merge_movie()
    timeline = build_remotion_timeline(movie, fps=30)
    plan = timeline["transitions"][0]["plan"]
    assert plan is not None

    source_goal_by_token: list[str] = []
    for row in timeline["states"][0]["rows"]:
        source_goal_by_token.extend([row["goalId"]] * len(row["tokens"]))
    selected_source_goals = {
        source_goal_by_token[source_index]
        for source_index, _target_index, _copy in plan["pairs"]
    }
    assert selected_source_goals == {"left", "right"}

    frames = movie.semantic_frames()
    scene = object.__new__(ProofScene)
    scene._goal_forest_layouts = {}
    scene._prepare_goal_forest(frames)

    def fake_rows(source: str, **_kwargs):
        row = VGroup()
        row.proof_latex_source = source
        return [(row, 0, len(source))]

    with (
        patch("proof_video.scene._initial_context_lines", return_value=[]),
        patch(
            "proof_video.scene._goal_latex", side_effect=lambda goal: goal.latex_target
        ),
        patch("proof_video.scene._wrapped_math_rows_with_spans", side_effect=fake_rows),
        patch(
            "proof_video.scene._mapped_source_groups_animations", return_value=[]
        ) as multi_source_mapper,
    ):
        source_block = scene._step_block(frames[0])
        target_block = scene._step_block(frames[1])
        scene._row_transition_parts(source_block, target_block)

    source_groups = multi_source_mapper.call_args.args[0]
    assert tuple(goal_id for goal_id, _rows, _expression in source_groups) == (
        "left",
        "right",
    )


def test_canonical_token_compiler_is_scoped_to_one_goal_card() -> None:
    def anchor(
        anchor_id: str,
        side: AnchorSide,
        goal_id: str,
        occurrence_id: str,
    ) -> LayoutAnchor:
        entity = EntityRef(
            EntityKind.OCCURRENCE,
            goal_id,
            expression_role="target",
            occurrence_id=occurrence_id,
        )
        return LayoutAnchor(
            anchor_id=anchor_id,
            persistent_id=f"persistent:{anchor_id}",
            side=side,
            entity=entity,
            goal_index=0,
            row_kind=LayoutRowKind.TARGET,
            row_index=0,
        )

    left_source = anchor("left-source-anchor", AnchorSide.BEFORE, "left", "left-source")
    left_target = anchor("left-target-anchor", AnchorSide.AFTER, "left", "left-target")
    right_source = anchor(
        "right-source-anchor", AnchorSide.BEFORE, "right", "right-source"
    )
    right_target = anchor(
        "right-target-anchor", AnchorSide.AFTER, "right", "right-target"
    )
    primitives = tuple(
        VisualPrimitive(
            primitive_id=f"move:{label}",
            kind=VisualPrimitiveKind.MOVE,
            source_anchor_ids=(source.anchor_id,),
            target_anchor_ids=(target.anchor_id,),
            persistent_ids=(f"persistent:{label}",),
            scope="target",
            evidence=("verified-lean-identity",),
        )
        for label, source, target in (
            ("left", left_source, left_target),
            ("right", right_source, right_target),
        )
    )
    visual_plan = SemanticVisualPlan(
        "before",
        "after",
        (left_source, left_target, right_source, right_target),
        primitives,
    )
    transition = SemanticTransition(
        source=SemanticExpression(
            (
                SemanticExpressionNode(
                    "left-source", latex_spans=(SemanticSpan(0, 1),)
                ),
                SemanticExpressionNode(
                    "right-source", latex_spans=(SemanticSpan(2, 3),)
                ),
            )
        ),
        target=SemanticExpression(
            (
                SemanticExpressionNode(
                    "left-target", latex_spans=(SemanticSpan(0, 1),)
                ),
                SemanticExpressionNode(
                    "right-target", latex_spans=(SemanticSpan(2, 3),)
                ),
            )
        ),
        edges=(),
    )

    compiled = compile_renderer_transition_plan(
        ((0, 1), (2, 3)),
        ("x", "x"),
        ((0, 1), (2, 3)),
        ("x", "x"),
        transition,
        visual_plan,
        source_goal_id="left",
        target_goal_id="left",
    )

    assert compiled.pairs == ((0, 0),)


def test_canonical_many_source_merge_compiles_every_parent_card_globally() -> None:
    def anchor(
        anchor_id: str,
        side: AnchorSide,
        goal_id: str,
        occurrence_id: str,
    ) -> LayoutAnchor:
        return LayoutAnchor(
            anchor_id=anchor_id,
            persistent_id=f"persistent:{anchor_id}",
            side=side,
            entity=EntityRef(
                EntityKind.OCCURRENCE,
                goal_id,
                expression_role="target",
                occurrence_id=occurrence_id,
            ),
            goal_index=0,
            row_kind=LayoutRowKind.TARGET,
            row_index=0,
        )

    left_source = anchor("left-source-anchor", AnchorSide.BEFORE, "left", "left-a")
    right_source = anchor("right-source-anchor", AnchorSide.BEFORE, "right", "right-b")
    left_target = anchor("left-target-anchor", AnchorSide.AFTER, "joined", "joined-a")
    right_target = anchor("right-target-anchor", AnchorSide.AFTER, "joined", "joined-b")
    visual_plan = SemanticVisualPlan(
        "before",
        "after",
        (left_source, right_source, left_target, right_target),
        (
            VisualPrimitive(
                "move:left",
                VisualPrimitiveKind.MOVE,
                (left_source.anchor_id,),
                (left_target.anchor_id,),
                ("persistent:left",),
                evidence=("verified-left-parent",),
            ),
            VisualPrimitive(
                "move:right",
                VisualPrimitiveKind.MOVE,
                (right_source.anchor_id,),
                (right_target.anchor_id,),
                ("persistent:right",),
                evidence=("verified-right-parent",),
            ),
        ),
    )
    left_expression = SemanticExpression(
        (SemanticExpressionNode("left-a", latex_spans=(SemanticSpan(0, 1),)),)
    )
    right_expression = SemanticExpression(
        (SemanticExpressionNode("right-b", latex_spans=(SemanticSpan(0, 1),)),)
    )
    target_expression = SemanticExpression(
        (
            SemanticExpressionNode("joined-a", latex_spans=(SemanticSpan(0, 1),)),
            SemanticExpressionNode("joined-b", latex_spans=(SemanticSpan(2, 3),)),
        )
    )
    transition = SemanticTransition(left_expression, target_expression, ())

    compiled = compile_renderer_transition_plan_from_sources(
        (
            RendererTransitionSource("left", ((0, 1),), ("a",), left_expression),
            RendererTransitionSource("right", ((0, 1),), ("b",), right_expression),
        ),
        ((0, 1), (2, 3)),
        ("a", "b"),
        transition,
        visual_plan,
        target_goal_id="joined",
    )

    assert compiled is not None and compiled.valid
    assert compiled.pairs == ((0, 0), (1, 1))
    assert compiled.created_targets == ()
    assert compiled.deleted_sources == ()
