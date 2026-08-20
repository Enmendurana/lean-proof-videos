from __future__ import annotations

from hypothesis import given, strategies as st

from proof_video.proof.diff import diff_proof_states
from proof_video.proof.effects import apply_transition
from proof_video.proof.state import (
    ExprOccurrence,
    Expression,
    GoalState,
    LocalDecl,
    ProofState,
    validate_state,
)


def expression(name: str) -> Expression:
    return Expression(
        expression_id=f"expr:{name}",
        fingerprint=f"fingerprint:{name}",
        lean=name,
        latex=name,
        type_fingerprint="type:Real",
        occurrences=(
            ExprOccurrence(
                occurrence_id=f"occ:{name}",
                kind="const",
                path=(),
                fingerprint=f"node:{name}",
                lean_identity=f"const:{name}",
                type_fingerprint="type:Real",
            ),
        ),
    )


@st.composite
def local_declarations(draw: st.DrawFn) -> tuple[LocalDecl, ...]:
    ids = draw(
        st.lists(
            st.sampled_from(("a", "b", "c", "d")),
            unique=True,
            max_size=4,
        )
    )
    order = draw(st.permutations(ids)) if ids else ()
    result: list[LocalDecl] = []
    for decl_id in order:
        user_name = draw(st.sampled_from((decl_id, f"{decl_id}'")))
        type_name = draw(st.sampled_from(("Real", "Nat", "Int")))
        value_name = draw(st.one_of(st.none(), st.sampled_from(("0", "1", "2"))))
        result.append(
            LocalDecl(
                decl_id=decl_id,
                user_name=user_name,
                type_expr=expression(type_name),
                value_expr=expression(value_name) if value_name is not None else None,
            )
        )
    return tuple(result)


@st.composite
def canonical_states(draw: st.DrawFn) -> ProofState:
    locals_ = draw(local_declarations())
    focused = draw(st.booleans())
    return ProofState(
        goals=(
            GoalState(
                goal_id="g",
                lineage_id="lineage:g",
                locals=locals_,
                target=expression("P"),
            ),
        ),
        focus=("g",) if focused else (),
    )


@given(canonical_states())
def test_generated_states_are_valid_and_identity_diff_replays(
    generated: ProofState,
) -> None:
    assert validate_state(generated) == ()
    identity = diff_proof_states(generated, generated)
    assert identity.is_identity
    assert apply_transition(generated, identity) == generated


@given(canonical_states(), canonical_states())
def test_diff_replay_reconstructs_every_small_valid_target(
    before: ProofState,
    after: ProofState,
) -> None:
    transition = diff_proof_states(before, after)
    assert apply_transition(before, transition) == after


@given(canonical_states(), canonical_states())
def test_diff_and_normalization_are_deterministic(
    before: ProofState,
    after: ProofState,
) -> None:
    first = diff_proof_states(before, after)
    second = diff_proof_states(before, after)

    assert first == second
    assert first.normalized() == first
    assert first.normalized().normalized() == first.normalized()
