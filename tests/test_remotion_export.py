from dataclasses import replace

import pytest

from proof_video.models import (
    Frame,
    Goal,
    LatexHypothesis,
    Movie,
    SemanticExpression,
    SemanticExpressionNode,
    SemanticSpan,
    SemanticTransition,
    SemanticTransitionEdge,
)
from proof_video.animation.latex import _latex_matching_token_spans
from proof_video.remotion_export import (
    _visual_token_chunks,
    build_remotion_timeline,
)
from proof_video.transition_plan import TransitionPlan


def test_visual_chunks_keep_one_oversized_atomic_token_intact() -> None:
    token = r"\operatorname{AnExtremelyLongAdministrativeLeanDeclarationName}"
    assert _visual_token_chunks([(token, 0, len(token))], maximum_units=4) == ((0, 1),)


def test_visual_chunks_never_create_empty_chunks() -> None:
    tokens = [("abcdefghij", index * 10, (index + 1) * 10) for index in range(3)]
    chunks = _visual_token_chunks(tokens, maximum_units=1)
    assert chunks == ((0, 1), (1, 2), (2, 3))


def test_remotion_timeline_has_stable_rows_and_bounded_duration() -> None:
    first = Goal(
        "g1",
        "",
        latex_target="A",
        latex_context=(LatexHypothesis("x", r"\mathbb{R}", key="x-id"),),
        lineage_id="proof",
    )
    second = Goal(
        "g2",
        "",
        latex_target="B",
        latex_context=(LatexHypothesis("x", r"\mathbb{R}", key="x-id"),),
        lineage_id="proof",
    )
    movie = Movie("demo", (Frame(0, "rfl", (first,)), Frame(1, "rw", (second,))))

    timeline = build_remotion_timeline(movie, width=1280, height=720, fps=30)

    assert timeline["schemaVersion"] == 1
    assert timeline["durationInFrames"] == (
        timeline["initialFrames"]
        + timeline["transitions"][0]["durationFrames"]
        + timeline["celebrationFrames"]
        + 90
    )
    assert timeline["pacingProfile"] == "ten-second-endpoint-plateaus-v14"
    assert timeline["transitions"][0]["pacing"] == "closing"
    assert timeline["completionHoldFrames"] == 90
    assert timeline["states"][0]["rows"][0]["key"] == "hyp-x-id"
    assert timeline["states"][0]["rows"][-1]["latex"] == r"\vdash\;A"
    assert timeline["showQed"] is True
    assert timeline["transitions"][0]["fromState"] == 0
    assert timeline["transitions"][0]["toState"] == 1
    assert timeline["edgeReasons"] == []
    assert timeline["writeSpeed"] == 48.0


def test_formula_length_does_not_change_the_global_step_clock() -> None:
    first = Goal("g1", "", latex_target="A", lineage_id="proof")
    formula = " + ".join(f"x_{{{index}}}" for index in range(30))
    short = Goal("g2", "", latex_target="B", lineage_id="proof")
    long = Goal("g2", "", latex_target=formula, lineage_id="proof")
    short_timeline = build_remotion_timeline(
        Movie("short", (Frame(0, "rfl", (first,)), Frame(1, "rw", (short,)))),
        fps=30,
    )
    long_timeline = build_remotion_timeline(
        Movie("long", (Frame(0, "rfl", (first,)), Frame(1, "rw", (long,)))),
        fps=30,
    )

    short_step = short_timeline["transitions"][0]
    long_step = long_timeline["transitions"][0]
    assert short_step["durationFrames"] == long_step["durationFrames"]
    assert short_step["moveEnd"] == long_step["moveEnd"]
    assert short_step["writeStart"] == long_step["writeStart"] == 0.0
    assert short_step["writeEnd"] == long_step["writeEnd"]
    assert short_step["moveEnd"] == short_step["writeEnd"]


def test_semantic_moves_change_the_plan_but_not_the_global_step_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = Goal("g1", "", latex_target="A", lineage_id="proof")
    formula = " + ".join(f"x_{{{index}}}" for index in range(30))
    marker = object()
    second = Goal(
        "g2",
        "",
        latex_target=formula,
        lineage_id="proof",
        semantic_transition=marker,  # type: ignore[arg-type]
    )
    movie = Movie("demo", (Frame(0, "rfl", (first,)), Frame(1, "rw", (second,))))

    def moved_plan(*_args, **_kwargs) -> TransitionPlan:
        target_count = len(
            _latex_matching_token_spans(r"\vdash\;" + formula)
        )
        return TransitionPlan(
            source_count=1,
            target_count=target_count,
            selected=(),
            # Everything except the final numeral is treated as a certified
            # move. Only that last token is fresh handwriting.
            created_targets=(target_count - 1,),
            deleted_sources=(),
            valid=True,
        )

    monkeypatch.setattr(
        "proof_video.remotion_export._semantic_transition_plan", moved_plan
    )
    timeline = build_remotion_timeline(movie, fps=30, chars_per_second=10.0)
    transition = timeline["transitions"][0]
    assert transition["plan"]["created"] == [
        len(_latex_matching_token_spans(r"\vdash\;" + formula)) - 1
    ]
    assert transition["moveEnd"] == transition["writeEnd"]
    assert transition["writeStart"] == 0.0


def test_premise_branch_x_is_copied_into_new_product_instead_of_written() -> None:
    """Exercise the renderer contract for the IMO ``mul_pos`` transition.

    The proof-DAG test in ``test_models`` certifies the logical origin.  This
    companion test crosses the renderer boundary and ensures that the origin
    remains a COPY pair in the serialized Remotion plan.  In particular, the
    equal-looking ``x`` inside ``f(x)`` must not steal the occurrence that
    comes from ``hx : x < 0``.
    """

    context = (
        LatexHypothesis("hx", "x < 0", key="proof-context-23"),
        LatexHypothesis("h1", "f(x) < 0", key="proof-context-24"),
    )
    source = Goal(
        "before-mul-pos",
        "",
        latex_target="Q",
        latex_context=context,
        lineage_id="proof",
    )
    source_latex = source.latex_state()
    target_latex_formula = r"0 < (x - 0) \cdot (f(x) - 0)"
    target = Goal(
        "after-mul-pos",
        "",
        latex_target=target_latex_formula,
        latex_context=context,
        lineage_id="proof",
    )
    target_latex = target.latex_state()

    source_hx_x = source_latex.index("x < 0")
    target_left_x = target_latex.index("x - 0")
    persistent_hx_span = SemanticSpan(0, source_latex.index("\n"))
    persistent_target_hx_span = SemanticSpan(0, target_latex.index("\n"))
    semantic_transition = SemanticTransition(
        source=SemanticExpression(
            (
                SemanticExpressionNode(
                    "proof-context-23",
                    kind="proof-context",
                    path=("context", 23),
                    latex_spans=(persistent_hx_span,),
                ),
                SemanticExpressionNode(
                    "proof-context-23/hx-x",
                    kind="fvar",
                    identity="fvar:x",
                    parent_id="proof-context-23",
                    path=("context", 23, "0", "0", "1"),
                    latex_spans=(SemanticSpan(source_hx_x, source_hx_x + 1),),
                ),
            )
        ),
        target=SemanticExpression(
            (
                SemanticExpressionNode(
                    "proof-context-23",
                    kind="proof-context",
                    path=("context", 23),
                    latex_spans=(persistent_target_hx_span,),
                ),
                SemanticExpressionNode(
                    "target-left-x",
                    kind="fvar",
                    identity="fvar:x",
                    path=("0", "1", "0", "1", "0", "1"),
                    latex_spans=(SemanticSpan(target_left_x, target_left_x + 1),),
                ),
            )
        ),
        edges=(
            SemanticTransitionEdge(
                "proof-context-23/hx-x",
                "target-left-x",
                "verified-premise-branch-atom",
                1.0,
            ),
        ),
        proof_kind="certified-proof-term",
        adapter="theorem-application",
    )
    target = replace(target, semantic_transition=semantic_transition)

    timeline = build_remotion_timeline(
        Movie(
            "mul-pos-regression",
            (Frame(0, "", (source,)), Frame(1, "theorem-application", (target,))),
        ),
        fps=30,
    )

    source_tokens = [
        token
        for row in timeline["states"][0]["rows"]
        for token, _start, _end in row["tokens"]
    ]
    target_tokens = [
        token
        for row in timeline["states"][1]["rows"]
        for token, _start, _end in row["tokens"]
    ]
    target_row_offset = sum(
        len(row["tokens"])
        for row in timeline["states"][1]["rows"]
        if row["kind"] == "context"
    )
    target_formula_tokens = target_tokens[target_row_offset:]
    target_x = target_row_offset + target_formula_tokens.index("x")
    pairs = timeline["transitions"][0]["plan"]["pairs"]
    source_x, _target_x, copy = next(pair for pair in pairs if pair[1] == target_x)

    assert source_tokens[source_x] == "x"
    assert copy == 1
    assert target_x not in timeline["transitions"][0]["plan"]["created"]


def test_writing_density_is_unbounded_but_step_duration_has_a_frame_floor() -> None:
    movie = Movie("demo", (Frame(0, "rfl", (Goal("g", "", latex_target="A"),)),))

    timeline = build_remotion_timeline(movie, fps=15, chars_per_second=1000.0)

    assert timeline["writeSpeed"] == 1000.0
    assert timeline["transitionFrames"] == 2

    timeline_30fps = build_remotion_timeline(
        movie, fps=30, chars_per_second=1000.0
    )
    assert timeline_30fps["transitionFrames"] == 3


def test_remotion_preview_is_the_first_twenty_seconds() -> None:
    frames = tuple(
        Frame(index, "rfl", (Goal(f"g{index}", "", latex_target=str(index)),))
        for index in range(100)
    )
    timeline = build_remotion_timeline(
        Movie("demo", frames), fps=30, preview_seconds=20.0
    )

    assert len(timeline["states"]) > 15
    assert timeline["states"][0]["proofFrameIndex"] == 0
    assert timeline["states"][-1]["proofFrameIndex"] < 99
    assert timeline["durationInFrames"] == 20 * 30
    assert timeline["completionHoldFrames"] == 0
    assert timeline["showQed"] is False


def test_short_opening_preview_does_not_exceed_requested_window() -> None:
    frames = (
        Frame(0, "rfl", (Goal("g0", "", latex_target="A"),)),
        Frame(1, "rw", (Goal("g1", "", latex_target="B"),)),
    )
    timeline = build_remotion_timeline(
        Movie("demo", frames), fps=30, preview_seconds=10.0
    )

    assert len(timeline["states"]) == 2
    assert len(timeline["transitions"]) == 1
    assert timeline["durationInFrames"] <= 10 * 30


def test_remotion_tail_preview_is_the_final_twenty_seconds_with_qed() -> None:
    frames = tuple(
        Frame(index, "rfl", (Goal(f"g{index}", "", latex_target=str(index)),))
        for index in range(100)
    )
    timeline = build_remotion_timeline(
        Movie("demo", frames), fps=30, preview_tail_seconds=20.0
    )

    assert len(timeline["states"]) > 15
    assert timeline["states"][0]["proofFrameIndex"] > 0
    assert timeline["states"][-1]["proofFrameIndex"] == 99
    assert timeline["durationInFrames"] <= 20 * 30
    assert timeline["completionHoldFrames"] == 90
    assert timeline["showQed"] is True


def test_long_proof_continuously_accelerates_then_decelerates() -> None:
    frames = tuple(
        Frame(
            index,
            "rfl",
            (
                Goal(
                    f"g{index}",
                    "",
                    latex_target=rf"A_{{{index}}}+B_{{{index}}}+C_{{{index}}}",
                ),
            ),
        )
        for index in range(121)
    )
    timeline = build_remotion_timeline(Movie("demo", frames), fps=30)
    transitions = timeline["transitions"]
    opening = [
        transition["durationFrames"]
        for transition in transitions
        if transition["pacing"] == "opening"
    ]
    cruise = [
        transition["durationFrames"]
        for transition in transitions
        if transition["pacing"] == "cruise"
    ]
    closing = [
        transition["durationFrames"]
        for transition in transitions
        if transition["pacing"] == "closing"
    ]

    assert opening and cruise and closing
    assert all(
        left >= right
        for left, right in zip(opening, opening[1:], strict=False)
    )
    assert len(set(cruise)) == 1
    assert all(
        left <= right
        for left, right in zip(closing, closing[1:], strict=False)
    )
    assert max(
        left / right
        for left, right in zip(opening, opening[1:], strict=False)
    ) <= 1.15
    assert max(
        right / left
        for left, right in zip(closing, closing[1:], strict=False)
    ) <= 1.15
    assert opening[0] == 2 * timeline["transitionFrames"]
    assert opening[-1] == timeline["transitionFrames"]
    assert closing[0] == timeline["transitionFrames"]
    assert closing[-1] == 2 * timeline["transitionFrames"]
    assert min(
        transition["durationFrames"] for transition in transitions
    ) >= timeline["transitionFrames"] == 10
    assert all(
        transition["moveEnd"] == transition["writeEnd"]
        for transition in transitions
    )
    assert all(
        transition["moveEnd"] == transition["writeEnd"] == 1.0
        for transition in transitions
    )
    assert "activeFrames" not in timeline
    assert all(transition["writeStart"] == 0.0 for transition in transitions)

    endpoint_frames = round((2.0 / 3.0) * 30)
    opening_elapsed = 0
    for transition in transitions:
        if opening_elapsed >= 10 * 30:
            break
        assert transition["durationFrames"] == endpoint_frames
        opening_elapsed += transition["durationFrames"]
    assert opening_elapsed >= 10 * 30

    closing_elapsed = 0
    for transition in reversed(transitions):
        if closing_elapsed >= 10 * 30:
            break
        assert transition["durationFrames"] == endpoint_frames
        closing_elapsed += transition["durationFrames"]
    assert closing_elapsed >= 10 * 30


def test_seventy_one_state_demo_has_no_fixed_duration_calibration() -> None:
    frames = tuple(
        Frame(index, "rfl", (Goal(f"g{index}", "", latex_target=str(index)),))
        for index in range(71)
    )

    timeline = build_remotion_timeline(Movie("imo-demo", frames), fps=30)

    assert timeline["durationInFrames"] != 38 * 30
    assert all(
        transition["moveEnd"] == transition["writeEnd"] == 1.0
        for transition in timeline["transitions"]
    )


def test_endpoint_speed_is_independent_of_middle_speed() -> None:
    frames = tuple(
        Frame(index, "rfl", (Goal(f"g{index}", "", latex_target=str(index)),))
        for index in range(71)
    )
    movie = Movie("speed-demo", frames)

    slow_middle = build_remotion_timeline(
        movie, fps=30, chars_per_second=24.0
    )
    fast_middle = build_remotion_timeline(
        movie, fps=30, chars_per_second=60.0
    )

    assert slow_middle["transitionFrames"] == 20
    assert fast_middle["transitionFrames"] == 8
    assert slow_middle["transitions"][0]["durationFrames"] == 20
    assert fast_middle["transitions"][0]["durationFrames"] == 20
    assert slow_middle["transitions"][-1]["durationFrames"] == 20
    assert fast_middle["transitions"][-1]["durationFrames"] == 20


def test_full_timeline_has_no_ten_minute_ceiling_by_default() -> None:
    frames = tuple(
        Frame(index, "rfl", (Goal(f"g{index}", "", latex_target=str(index)),))
        for index in range(2601)
    )

    timeline = build_remotion_timeline(Movie("long-demo", frames), fps=30)

    assert timeline["durationInFrames"] > 10 * 60 * 30


def test_long_formula_is_balanced_into_visual_rows_without_losing_tokens() -> None:
    formula = r"\forall x : \mathbb{R},\ " + " + ".join(f"f(x_{i})" for i in range(30))
    movie = Movie("demo", (Frame(0, "rfl", (Goal("g", "", latex_target=formula),)),))

    timeline = build_remotion_timeline(movie, fps=30)
    rows = timeline["states"][0]["rows"]

    assert len(rows) >= 2
    assert all(row["key"].startswith("target-wrap-") for row in rows)
    rendered_tokens = [token for row in rows for token, _start, _end in row["tokens"]]
    expected_tokens = [
        token for token, _start, _end in _latex_matching_token_spans(r"\vdash\;" + formula)
    ]
    assert rendered_tokens == expected_tokens
