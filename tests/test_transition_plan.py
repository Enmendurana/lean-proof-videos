from hypothesis import given, strategies as st
from itertools import permutations

from proof_video.transition_plan import (
    TokenPair,
    TransitionCandidate,
    TransitionRole,
    solve_transition_plan,
)


def candidate(
    candidate_id: str,
    pairs: tuple[tuple[int, int], ...],
    *,
    role: TransitionRole = TransitionRole.PRESERVE,
    certified: bool = True,
    exact: bool = True,
    kind: str = "app",
) -> TransitionCandidate:
    return TransitionCandidate(
        candidate_id=candidate_id,
        source_node_id=f"source-{candidate_id}",
        target_node_id=f"target-{candidate_id}",
        role=role,
        reason="verified-structural-expression",
        pairs=tuple(TokenPair(source, target) for source, target in pairs),
        certified=certified,
        exact_composite=exact,
        source_kind=kind,
        target_kind=kind,
    )


def test_uncertified_text_match_is_written_as_new() -> None:
    plan = solve_transition_plan(
        1,
        1,
        (candidate("same-letter", ((0, 0),), certified=False, kind="fvar"),),
    )

    assert plan.valid
    assert plan.pairs == ()
    assert plan.deleted_sources == (0,)
    assert plan.created_targets == (0,)
    assert "not Lean-certified" in plan.rejected_candidates[0]


def test_complete_previous_expression_beats_smaller_context_copy() -> None:
    # context f(f(x)) occupies source 0..3; the previous conclusion occupies
    # source 4..9 and contains the same suffix.  The complete old conclusion
    # must own the target, not the coincidentally equal older context phrase.
    context_copy = candidate(
        "context-copy",
        ((0, 2), (1, 3), (2, 4), (3, 5)),
        role=TransitionRole.COPY,
    )
    previous_body = candidate(
        "previous-body",
        tuple(
            (source, target)
            for source, target in zip(range(4, 10), range(6), strict=True)
        ),
    )

    plan = solve_transition_plan(10, 6, (context_copy, previous_body))

    assert plan.valid
    assert {item.candidate_id for item in plan.selected} == {"previous-body"}
    assert plan.pairs == tuple(zip(range(4, 10), range(6), strict=True))


def test_partial_application_is_rejected_even_when_certified_flag_is_set() -> None:
    plan = solve_transition_plan(
        1,
        1,
        (candidate("bare-f", ((0, 0),), exact=False),),
    )

    assert plan.valid
    assert plan.pairs == ()
    assert any("partial function application" in item for item in plan.rejected_candidates)


def test_candidate_order_cannot_change_the_physical_mapping() -> None:
    candidates = (
        candidate("whole-left", ((0, 0), (1, 1)), kind="app"),
        candidate("whole-right", ((2, 2), (3, 3)), kind="app"),
        candidate("distracting-letter", ((0, 2),), kind="fvar"),
    )

    mappings = {
        solve_transition_plan(4, 4, tuple(order)).pairs
        for order in permutations(candidates)
    }

    assert mappings == {((0, 0), (1, 1), (2, 2), (3, 3))}


def test_equally_certified_occurrences_choose_the_nearest_logical_source() -> None:
    near = candidate("near", ((4, 5),), kind="fvar")
    far = candidate("far", ((12, 5),), kind="fvar")

    mappings = {
        solve_transition_plan(13, 6, tuple(order)).pairs
        for order in permutations((near, far))
    }

    assert mappings == {((4, 5),)}


@given(
    source_count=st.integers(min_value=0, max_value=12),
    target_count=st.integers(min_value=0, max_value=12),
    raw_pairs=st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=11),
            st.integers(min_value=0, max_value=11),
        ),
        max_size=24,
    ),
)
def test_every_valid_plan_has_unique_targets_and_total_create_delete_coverage(
    source_count: int,
    target_count: int,
    raw_pairs: list[tuple[int, int]],
) -> None:
    candidates = tuple(
        candidate(
            f"edge-{index}",
            ((source, target),),
            kind="fvar",
        )
        for index, (source, target) in enumerate(raw_pairs)
        if source < source_count and target < target_count
    )
    plan = solve_transition_plan(source_count, target_count, candidates)

    assert plan.valid
    mapped_sources = [source for source, _target in plan.pairs]
    mapped_targets = [target for _source, target in plan.pairs]
    assert len(mapped_sources) == len(set(mapped_sources))
    assert len(mapped_targets) == len(set(mapped_targets))
    assert set(mapped_sources) | set(plan.deleted_sources) == set(range(source_count))
    assert set(mapped_targets) | set(plan.created_targets) == set(range(target_count))
