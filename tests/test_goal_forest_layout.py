from __future__ import annotations

import pytest

from proof_video.presentation.goal_forest import (
    build_goal_forest_layout,
    validate_goal_forest_layout,
)
from proof_video.proof.correspondence import ExplicitGoalEdge, RelationKind
from proof_video.proof.schema import Frame
from proof_video.proof.state import Expression, GoalState, ProofState


def expression(name: str = "P") -> Expression:
    return Expression(
        expression_id=f"expr:{name}",
        fingerprint=f"fingerprint:{name}",
        lean=name,
        latex=name,
        type_fingerprint="Prop",
    )


def goal(
    goal_id: str,
    *,
    lineage: str | None = None,
    parent: str | None = None,
    branch_kind: str = "",
    branch_index: int | None = None,
) -> GoalState:
    return GoalState(
        goal_id=goal_id,
        lineage_id=lineage or f"lineage:{goal_id}",
        locals=(),
        target=expression(goal_id),
        parent_goal_id=parent,
        branch_kind=branch_kind,
        branch_index=branch_index,
    )


def state(*goals: GoalState, focus: tuple[str, ...] = ()) -> ProofState:
    return ProofState(tuple(goals), focus)


def frame(
    proof_state: ProofState,
    *lineage: ExplicitGoalEdge,
    tactic: str = "arbitrary_custom_tactic",
) -> Frame:
    return Frame(
        index=1,
        tactic=tactic,
        goals=(),
        proof_state=proof_state,
        goal_lineage=tuple(lineage),
    )


def test_split_children_retain_consumed_parent_and_deterministic_branch_order() -> None:
    parent = goal("g-parent", lineage="root-lineage")
    previous = build_goal_forest_layout(state(parent, focus=(parent.goal_id,)))
    parent_card = previous.card_for_goal(parent.goal_id)
    assert parent_card is not None

    # Deliberately put branch 1 first in the live-goal order. Global order and
    # logical sibling order are separate renderer-independent facts.
    right = goal("g-right", parent=parent.goal_id, branch_index=1)
    left = goal("g-left", parent=parent.goal_id, branch_index=0)
    split = ExplicitGoalEdge(
        (parent.goal_id,),
        (left.goal_id, right.goal_id),
        "observed-metavariable-split",
        RelationKind.SPLIT,
    )
    layout = build_goal_forest_layout(
        frame(state(right, left, focus=(right.goal_id,)), split),
        previous=previous,
    )

    assert validate_goal_forest_layout(layout) == ()
    right_card = layout.card_for_goal(right.goal_id)
    left_card = layout.card_for_goal(left.goal_id)
    assert right_card is not None and left_card is not None
    assert right_card.order == 0 and left_card.order == 1
    assert right_card.sibling_order == 1 and left_card.sibling_order == 0
    assert (
        right_card.parent_card_ids
        == left_card.parent_card_ids
        == (parent_card.stable_id,)
    )
    assert (
        right_card.root_card_ids == left_card.root_card_ids == (parent_card.stable_id,)
    )
    assert right_card.depth == left_card.depth == 1
    assert right_card.incoming_relation == left_card.incoming_relation == "split"
    assert layout.active_card_id == right_card.stable_id
    assert layout.retired_card_ids == (parent_card.stable_id,)


@pytest.mark.parametrize("branch_kind", ["cases", "induction"])
def test_cases_and_induction_like_branches_preserve_kind_depth_and_stable_ids(
    branch_kind: str,
) -> None:
    root = goal("root", lineage="shared-root")
    previous = build_goal_forest_layout(state(root, focus=(root.goal_id,)))
    first = goal(
        "branch-a",
        parent=root.goal_id,
        branch_kind=branch_kind,
        branch_index=0,
    )
    second = goal(
        "branch-b",
        parent=root.goal_id,
        branch_kind=branch_kind,
        branch_index=1,
    )
    edge = ExplicitGoalEdge(
        (root.goal_id,),
        (first.goal_id, second.goal_id),
        "observed-branch",
        RelationKind.SPLIT,
    )
    layout = build_goal_forest_layout(
        frame(state(first, second, focus=(first.goal_id, second.goal_id)), edge),
        previous=previous,
    )
    repeated = build_goal_forest_layout(
        frame(
            state(first, second, focus=(first.goal_id, second.goal_id)),
            edge,
            tactic="a_completely_different_name",
        ),
        previous=previous,
    )

    assert layout == repeated
    assert tuple(card.branch_kind for card in layout.cards) == (
        branch_kind,
        branch_kind,
    )
    assert tuple(card.depth for card in layout.cards) == (1, 1)
    assert tuple(card.focus_rank for card in layout.cards) == (0, 1)
    assert layout.cards[0].is_active
    assert not layout.cards[1].is_active


def test_focus_and_reorder_change_layout_order_without_changing_card_identity() -> None:
    first = goal("g1", lineage="lineage-one")
    second = goal("g2", lineage="lineage-two")
    before = build_goal_forest_layout(state(first, second, focus=(first.goal_id,)))
    after = build_goal_forest_layout(
        state(second, first, focus=(second.goal_id, first.goal_id)),
        previous=before,
    )

    assert after.card_for_lineage("lineage-one").stable_id == (
        before.card_for_lineage("lineage-one").stable_id
    )
    assert after.card_for_lineage("lineage-two").stable_id == (
        before.card_for_lineage("lineage-two").stable_id
    )
    assert tuple(card.lineage_id for card in after.cards) == (
        "lineage-two",
        "lineage-one",
    )
    assert tuple(card.order for card in after.cards) == (0, 1)
    assert after.active_card_id == after.cards[0].stable_id
    assert after.focus_card_ids == tuple(card.stable_id for card in after.cards)
    assert after.introduced_card_ids == ()
    assert after.retired_card_ids == ()


def test_one_to_one_goal_id_change_keeps_lineage_card_id() -> None:
    before_goal = goal("old-mvar", lineage="persistent-lineage")
    before = build_goal_forest_layout(state(before_goal, focus=(before_goal.goal_id,)))
    after_goal = goal("new-mvar", lineage="persistent-lineage")
    after = build_goal_forest_layout(
        state(after_goal, focus=(after_goal.goal_id,)), previous=before
    )

    assert after.cards[0].stable_id == before.cards[0].stable_id
    assert after.introduced_card_ids == ()
    assert after.retired_card_ids == ()


def test_close_retires_and_marks_the_observed_goal_card() -> None:
    source = goal("closing-goal")
    previous = build_goal_forest_layout(state(source, focus=(source.goal_id,)))
    close = ExplicitGoalEdge(
        (source.goal_id,), (), "observed-close", RelationKind.REMOVE
    )
    layout = build_goal_forest_layout(frame(state(), close), previous=previous)

    assert layout.cards == ()
    assert layout.focus_card_ids == ()
    assert layout.active_card_id is None
    assert layout.retired_card_ids == (previous.cards[0].stable_id,)
    assert layout.closed_card_ids == (previous.cards[0].stable_id,)


def test_merge_has_all_source_parents_and_combines_their_roots() -> None:
    left = goal("left", lineage="left-lineage")
    right = goal("right", lineage="right-lineage")
    previous = build_goal_forest_layout(
        state(left, right, focus=(left.goal_id, right.goal_id))
    )
    target = goal("joined", lineage="joined-lineage")
    merge = ExplicitGoalEdge(
        (left.goal_id, right.goal_id),
        (target.goal_id,),
        "observed-join",
        RelationKind.MERGE,
    )
    layout = build_goal_forest_layout(
        frame(state(target, focus=(target.goal_id,)), merge), previous=previous
    )

    target_card = layout.cards[0]
    source_ids = tuple(item.stable_id for item in previous.cards)
    assert target_card.parent_card_ids == source_ids
    assert target_card.root_card_ids == source_ids
    assert target_card.depth == 1
    assert target_card.incoming_relation == "merge"
    assert layout.root_card_ids == source_ids
    assert layout.introduced_card_ids == (target_card.stable_id,)
    assert layout.retired_card_ids == source_ids
    assert layout.closed_card_ids == ()
