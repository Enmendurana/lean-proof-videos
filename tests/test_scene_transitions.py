from unittest.mock import Mock, patch
from types import SimpleNamespace

from proof_video.models import (
    IndexMaps,
    SemanticExpression,
    SemanticExpressionNode,
    SemanticSpan,
    SemanticTransition,
    SemanticTransitionEdge,
)
from proof_video.scene import ProofScene
from proof_video.animation.scene_helpers import (
    _glyph_reveal,
    _continues_visual_block,
    _contiguous_pair_runs,
    _fallback_row_animation,
    _mapped_row_animations,
    _mapped_rows_animations,
    _phased_token_transition,
    _shares_proof_block,
)
from proof_video.animation.semantic import (
    _semantic_transition_plan,
    _semantic_token_pairs,
    _stable_visual_rows,
    _supplement_logically_stable_syntax_pairs,
)


class TaggedGroup(list):
    pass


class TaggedRow:
    pass


def row(key: str) -> TaggedRow:
    result = TaggedRow()
    result.proof_row_key = key
    return result


def block(key: str, *rows: TaggedRow) -> TaggedGroup:
    result = TaggedGroup(rows)
    result.proof_block_key = key
    return result


def test_stable_visual_rows_require_same_block_row_and_canonical_latex() -> None:
    unchanged = row("hyp-proof-context-1:0")
    unchanged.proof_latex_source = r"h \;:\; P"
    changed = row("target:0")
    changed.proof_latex_source = "P"
    source = block("proof-sequent", unchanged, changed)

    same = row("hyp-proof-context-1:0")
    same.proof_latex_source = r"h \;:\; P"
    new_target = row("target:0")
    new_target.proof_latex_source = "Q"
    target = block("proof-sequent", same, new_target)

    assert _stable_visual_rows(TaggedGroup([source]), TaggedGroup([target])) == [
        unchanged
    ]


def parallel(*animations):
    return ("parallel", *animations)


def sequence(*animations, **_kwargs):
    return ("sequence", *animations)


def test_new_rows_are_written_and_matching_stays_inside_a_block() -> None:
    old_target = row("target:0")
    new_target = row("target:0")
    new_hypothesis = row("hyp-h:0")
    unrelated_target = row("target:0")

    source = TaggedGroup([block("goal-0", old_target)])
    target = TaggedGroup(
        [
            block("goal-0", new_hypothesis, new_target),
            block("goal-1", unrelated_target),
        ]
    )

    with (
        patch("proof_video.scene._glyph_reveal", return_value="reveal") as reveal,
        patch("proof_video.animation.scene_helpers.FadeOut", return_value="fade-out"),
        patch("proof_video.animation.scene_helpers.FadeIn", return_value="fade-in"),
        patch(
            "proof_video.animation.scene_helpers.Succession", side_effect=sequence
        ) as succession,
    ):
        animations = ProofScene._row_transition_animations(source, target)

    assert animations[0] == ("sequence", "fade-out", "fade-in")
    succession.assert_called_once()
    assert [call.args[0] for call in reveal.call_args_list] == [
        new_hypothesis,
        unrelated_target,
    ]


def test_intro_binder_moves_into_its_new_context_row() -> None:
    old_target = row("target:0")
    old_target.proof_tokens = ("x",)
    old_target.proof_token_spans = ((2, 3),)
    old_target.proof_token_mobjects = (object(),)
    old_target.proof_char_span = (0, 4)

    new_context = row("hyp-x:0")
    new_context.proof_tokens = ("x", ":", r"\mathbb{R}")
    new_context.proof_token_spans = ((0, 1), (2, 3), (4, 14))
    new_context.proof_token_mobjects = (object(), object(), object())
    new_context.proof_char_span = (0, 14)

    new_target = row("target:0")
    new_target.proof_tokens = ("P",)
    new_target.proof_token_spans = ((0, 1),)
    new_target.proof_token_mobjects = (object(),)
    new_target.proof_char_span = (15, 16)

    transition = SemanticTransition(
        source=SemanticExpression(
            (
                SemanticExpressionNode(
                    "forall-binder",
                    kind="declaration",
                    latex_spans=(SemanticSpan(2, 3),),
                ),
            )
        ),
        target=SemanticExpression(
            (
                SemanticExpressionNode(
                    "context-x",
                    kind="declaration",
                    latex_spans=(SemanticSpan(0, 1),),
                ),
            )
        ),
        edges=(
            SemanticTransitionEdge(
                "forall-binder", "context-x", "verified-intro-binder", 1.0
            ),
        ),
    )
    old_block = block("goal", old_target)
    new_block = block("goal", new_context, new_target)
    new_block.proof_semantic_transition = transition
    new_block.proof_latex_index_maps = None

    with patch(
        "proof_video.scene._mapped_rows_animations", return_value=["mapped"]
    ) as mapped:
        animations, new_rows = ProofScene._row_transition_parts(
            TaggedGroup([old_block]), TaggedGroup([new_block])
        )

    assert animations == ["mapped"]
    assert mapped.call_args.args[1] == [new_context, new_target]
    assert new_rows == []


def test_same_lineage_continues_one_visual_block() -> None:
    source = TaggedGroup([block("goal-0", row("target:0"))])
    continuation = TaggedGroup([block("goal-0", row("hyp-new:0"), row("target:0"))])
    different_branch = TaggedGroup([block("goal-1", row("target:0"))])

    assert _shares_proof_block(source, continuation)
    assert not _shares_proof_block(source, different_branch)


def test_similar_returning_branch_reuses_the_visual_block() -> None:
    source = TaggedGroup(
        [block("active-subgoal", row("hyp-f:0"), row("hyp-hf:0"), row("target:0"))]
    )
    resumed = TaggedGroup(
        [
            block(
                "dormant-continuation",
                row("hyp-f:0"),
                row("hyp-hf:0"),
                row("hyp-f_nonpos:0"),
                row("target:0"),
            )
        ]
    )

    assert _continues_visual_block(source, resumed)


def test_latex_index_map_drives_token_transform_without_shape_guessing() -> None:
    source_tokens = (object(), object())
    target_tokens = (object(), object())
    source = SimpleNamespace(
        proof_tokens=("x", "y"),
        proof_token_spans=((0, 1), (2, 3)),
        proof_token_mobjects=source_tokens,
        proof_char_span=(0, 3),
    )
    target = SimpleNamespace(
        proof_tokens=("x", "y"),
        proof_token_spans=((0, 1), (2, 3)),
        proof_token_mobjects=target_tokens,
        proof_char_span=(0, 3),
    )
    maps = IndexMaps(
        source_to_target=(0, None, 2),
        target_to_source=(0, None, 2),
    )

    with (
        patch(
            "proof_video.animation.scene_helpers.Transform",
            side_effect=lambda source, target: ("move", source, target),
        ) as transform,
        patch("proof_video.animation.scene_helpers.FadeOut", return_value="removed"),
        patch("proof_video.animation.scene_helpers.FadeIn", return_value="introduced"),
        patch(
            "proof_video.animation.scene_helpers.AnimationGroup", side_effect=parallel
        ),
        patch("proof_video.animation.scene_helpers.Succession", side_effect=sequence),
    ):
        animations = _mapped_row_animations(source, target, maps)

    assert animations == [
        (
            "sequence",
            (
                "parallel",
                ("move", source_tokens[0], target_tokens[0]),
                ("move", source_tokens[1], target_tokens[1]),
            ),
        )
    ]
    assert transform.call_count == 2


def test_introduced_tokens_arrive_oversized_while_the_row_moves_in_parallel() -> None:
    kept_source, removed_source = object(), object()
    kept_target, introduced_target = object(), object()
    source = SimpleNamespace(
        proof_tokens=("x", "-"),
        proof_token_spans=((0, 1), (2, 3)),
        proof_token_mobjects=(kept_source, removed_source),
        proof_char_span=(0, 3),
    )
    target = SimpleNamespace(
        proof_tokens=("x", "+"),
        proof_token_spans=((0, 1), (2, 3)),
        proof_token_mobjects=(kept_target, introduced_target),
        proof_char_span=(0, 3),
    )
    maps = IndexMaps(
        source_to_target=(0, None, None),
        target_to_source=(0, None, None),
    )

    with (
        patch("proof_video.animation.scene_helpers.Transform", return_value="move"),
        patch(
            "proof_video.animation.scene_helpers.FadeOut", return_value="fade-out"
        ) as fade_out,
        patch(
            "proof_video.animation.scene_helpers.FadeIn", return_value="fade-in"
        ) as fade_in,
        patch(
            "proof_video.animation.scene_helpers.AnimationGroup", side_effect=parallel
        ),
        patch("proof_video.animation.scene_helpers.Succession", side_effect=sequence),
    ):
        animations = _mapped_row_animations(source, target, maps)

    assert animations == [
        (
            "sequence",
            ("parallel", "fade-out", "move", "fade-in"),
        )
    ]
    fade_out.assert_called_once_with(removed_source)
    fade_in.assert_called_once()
    assert fade_in.call_args.args == (introduced_target,)
    assert tuple(fade_in.call_args.kwargs["shift"]) == (0.24, 0.0, 0.0)
    assert fade_in.call_args.kwargs["scale"] == 1.65


def test_contiguous_semantic_expression_moves_as_one_rigid_phrase() -> None:
    positions = (
        ("target", 0, 1),
        ("target", 1, 2),
        ("target", 2, 3),
        ("target", 3, 4),
        ("target", 5, 9),
        ("target", 10, 11),
    )
    # The input order intentionally resembles semantic-priority resolution,
    # which does not necessarily emit pairs from left to right.
    pairs = ((0, 0), (2, 2), (5, 5), (1, 1), (3, 3), (4, 4))

    assert _contiguous_pair_runs(pairs, positions, positions) == [
        [(0, 0), (1, 1), (2, 2), (3, 3), (4, 4), (5, 5)]
    ]


def test_contiguous_pair_run_never_joins_different_rows() -> None:
    source_positions = (("target", 0, 1), ("hyp-x", 0, 1))
    target_positions = (("target", 0, 1), ("hyp-x", 0, 1))

    assert _contiguous_pair_runs(
        ((0, 0), (1, 1)), source_positions, target_positions
    ) == [[(0, 0)], [(1, 1)]]


def test_contiguous_semantic_tokens_use_one_group_transform() -> None:
    old = (object(), object(), object(), object())
    new = (object(), object(), object(), object())
    positions = tuple(("target", index, index + 1) for index in range(4))

    with (
        patch("proof_video.animation.scene_helpers.Mobject", object),
        patch(
            "proof_video.animation.scene_helpers.VGroup",
            side_effect=lambda *tokens: ("group", *tokens),
        ),
        patch(
            "proof_video.animation.scene_helpers.Transform",
            side_effect=lambda source, target: ("move", source, target),
        ),
        patch(
            "proof_video.animation.scene_helpers.AnimationGroup", side_effect=parallel
        ),
        patch("proof_video.animation.scene_helpers.Succession", side_effect=sequence),
    ):
        animation = _phased_token_transition(
            old,
            new,
            ((0, 0), (1, 1), (2, 2), (3, 3)),
            positions,
            positions,
        )

    assert animation == (
        "sequence",
        (
            "parallel",
            ("move", ("group", *old), ("group", *new)),
        ),
    )


def test_relations_operators_and_punctuation_require_a_logical_owner() -> None:
    tokens = ("f", "(", "x", ")", r"\leq", "0", ",", ".")
    spans = tuple((index, index + 1) for index in range(len(tokens)))
    positions = tuple(("target", index, index + 1) for index in range(len(tokens)))
    transition = SemanticTransition(
        source=SemanticExpression(
            (
                SemanticExpressionNode(
                    "old-expression",
                    kind="app",
                    path=(0,),
                    latex_spans=(SemanticSpan(0, len(tokens)),),
                ),
            )
        ),
        target=SemanticExpression(
            (
                SemanticExpressionNode(
                    "new-expression",
                    kind="app",
                    path=(0,),
                    latex_spans=(SemanticSpan(0, len(tokens)),),
                ),
            )
        ),
        edges=(
            SemanticTransitionEdge(
                "old-expression", "new-expression", "defeq-normal-form", 0.95
            ),
        ),
    )

    pairs = _supplement_logically_stable_syntax_pairs(
        [(0, 0), (2, 2), (5, 5)],
        spans,
        positions,
        tokens,
        spans,
        positions,
        tokens,
        transition,
    )

    assert set(pairs) == {
        (0, 0),
        (1, 1),
        (2, 2),
        (3, 3),
        (4, 4),
        (5, 5),
        (6, 6),
        (7, 7),
    }


def test_same_symbol_at_same_position_is_not_enough_without_a_lean_edge() -> None:
    tokens = (r"\leq", ",", ".")
    spans = ((0, 4), (4, 5), (5, 6))
    positions = tuple(("target", start, end) for start, end in spans)
    transition = SemanticTransition(
        source=SemanticExpression(
            (SemanticExpressionNode("old", latex_spans=(SemanticSpan(0, 6),)),)
        ),
        target=SemanticExpression(
            (SemanticExpressionNode("new", latex_spans=(SemanticSpan(0, 6),)),)
        ),
        edges=(),
    )

    assert (
        _supplement_logically_stable_syntax_pairs(
            [], spans, positions, tokens, spans, positions, tokens, transition
        )
        == []
    )


def test_index_map_never_morphs_different_latex_tokens() -> None:
    old_colon, new_minus = object(), object()
    source = SimpleNamespace(
        proof_tokens=(":",),
        proof_token_spans=((0, 1),),
        proof_token_mobjects=(old_colon,),
        proof_char_span=(0, 1),
    )
    target = SimpleNamespace(
        proof_tokens=("-",),
        proof_token_spans=((0, 1),),
        proof_token_mobjects=(new_minus,),
        proof_char_span=(0, 1),
    )
    maps = IndexMaps(source_to_target=(0,), target_to_source=(0,))

    with (
        patch(
            "proof_video.animation.scene_helpers.Transform", return_value="bad-morph"
        ) as transform,
        patch(
            "proof_video.animation.scene_helpers.FadeOut", return_value="fade-out"
        ) as fade_out,
        patch(
            "proof_video.animation.scene_helpers.FadeIn", return_value="fade-in"
        ) as fade_in,
        patch(
            "proof_video.animation.scene_helpers.AnimationGroup", side_effect=parallel
        ),
        patch("proof_video.animation.scene_helpers.Succession", side_effect=sequence),
    ):
        animations = _mapped_row_animations(source, target, maps)

    assert animations == [
        (
            "sequence",
            ("parallel", "fade-out", "fade-in"),
        )
    ]
    transform.assert_not_called()
    fade_out.assert_called_once_with(old_colon)
    fade_in.assert_called_once()
    assert fade_in.call_args.args == (new_minus,)
    assert tuple(fade_in.call_args.kwargs["shift"]) == (0.24, 0.0, 0.0)
    assert fade_in.call_args.kwargs["scale"] == 1.65


def test_identical_token_moves_when_its_path_is_clear() -> None:
    def glyph(left: float, bottom: float = 0.0):
        return SimpleNamespace(
            get_left=lambda: (left, bottom + 0.5, 0.0),
            get_right=lambda: (left + 0.5, bottom + 0.5, 0.0),
            get_top=lambda: (left + 0.25, bottom + 1.0, 0.0),
            get_bottom=lambda: (left + 0.25, bottom, 0.0),
        )

    old_x = glyph(0.0)
    new_x = glyph(2.0)
    source = SimpleNamespace(
        proof_tokens=("x",),
        proof_token_spans=((0, 1),),
        proof_token_mobjects=(old_x,),
        proof_char_span=(0, 1),
    )
    target = SimpleNamespace(
        proof_tokens=("x",),
        proof_token_spans=((0, 1),),
        proof_token_mobjects=(new_x,),
        proof_char_span=(0, 1),
    )
    maps = IndexMaps(source_to_target=(0,), target_to_source=(0,))

    with (
        patch(
            "proof_video.animation.scene_helpers.Transform", return_value="crossing"
        ) as transform,
        patch("proof_video.animation.scene_helpers.FadeOut", return_value="fade-out"),
        patch("proof_video.animation.scene_helpers.FadeIn", return_value="fade-in"),
        patch(
            "proof_video.animation.scene_helpers.AnimationGroup", side_effect=parallel
        ),
        patch("proof_video.animation.scene_helpers.Succession", side_effect=sequence),
    ):
        animations = _mapped_row_animations(source, target, maps)

    assert animations == [("sequence", ("parallel", "crossing"))]
    transform.assert_called_once_with(old_x, new_x)


def test_crossing_tokens_preserve_every_mapped_object() -> None:
    def glyph(left: float):
        return SimpleNamespace(
            get_left=lambda: (left, 0.5, 0.0),
            get_right=lambda: (left + 0.5, 0.5, 0.0),
            get_top=lambda: (left + 0.25, 1.0, 0.0),
            get_bottom=lambda: (left + 0.25, 0.0, 0.0),
        )

    old_x, old_y = glyph(0.0), glyph(2.0)
    new_x, new_y = glyph(2.0), glyph(0.0)
    source = SimpleNamespace(
        proof_tokens=("x", "y"),
        proof_token_spans=((0, 1), (2, 3)),
        proof_token_mobjects=(old_x, old_y),
        proof_char_span=(0, 3),
    )
    target = SimpleNamespace(
        proof_tokens=("y", "x"),
        proof_token_spans=((0, 1), (2, 3)),
        proof_token_mobjects=(new_y, new_x),
        proof_char_span=(0, 3),
    )
    maps = IndexMaps(
        source_to_target=(2, None, 0),
        target_to_source=(2, None, 0),
    )

    with (
        patch(
            "proof_video.animation.scene_helpers.Transform", return_value="mapped"
        ) as transform,
        patch(
            "proof_video.animation.scene_helpers.FadeOut",
            side_effect=lambda token: ("out", token),
        ),
        patch(
            "proof_video.animation.scene_helpers.FadeIn",
            side_effect=lambda token: ("in", token),
        ),
        patch(
            "proof_video.animation.scene_helpers.AnimationGroup", side_effect=parallel
        ),
        patch("proof_video.animation.scene_helpers.Succession", side_effect=sequence),
    ):
        animations = _mapped_row_animations(source, target, maps)

    assert animations == [("sequence", ("parallel", "mapped", "mapped"))]
    assert transform.call_args_list == [
        ((old_y, new_y),),
        ((old_x, new_x),),
    ]


def test_crossing_paths_do_not_override_trace_identity() -> None:
    def glyph(left: float):
        return SimpleNamespace(
            get_left=lambda: (left, 0.5, 0.0),
            get_right=lambda: (left + 0.3, 0.5, 0.0),
            get_top=lambda: (left + 0.15, 1.0, 0.0),
            get_bottom=lambda: (left + 0.15, 0.0, 0.0),
        )

    old_f, old_t = glyph(1.7), glyph(1.3)
    new_f, new_t = glyph(1.4), glyph(2.4)
    source = SimpleNamespace(
        proof_tokens=("f", "t"),
        proof_token_spans=((0, 1), (2, 3)),
        proof_token_mobjects=(old_f, old_t),
        proof_char_span=(0, 3),
    )
    target = SimpleNamespace(
        proof_tokens=("t", "f"),
        proof_token_spans=((0, 1), (2, 3)),
        proof_token_mobjects=(new_t, new_f),
        proof_char_span=(0, 3),
    )
    maps = IndexMaps(
        source_to_target=(2, None, 0),
        target_to_source=(2, None, 0),
    )

    with (
        patch(
            "proof_video.animation.scene_helpers.Transform", return_value="mapped"
        ) as transform,
        patch(
            "proof_video.animation.scene_helpers.FadeOut",
            side_effect=lambda token: ("out", token),
        ),
        patch(
            "proof_video.animation.scene_helpers.FadeIn",
            side_effect=lambda token: ("in", token),
        ),
        patch(
            "proof_video.animation.scene_helpers.AnimationGroup", side_effect=parallel
        ),
        patch("proof_video.animation.scene_helpers.Succession", side_effect=sequence),
    ):
        animations = _mapped_row_animations(source, target, maps)

    assert animations == [("sequence", ("parallel", "mapped", "mapped"))]
    assert transform.call_args_list == [
        ((old_t, new_t),),
        ((old_f, new_f),),
    ]


def test_fallback_preserves_only_same_text_with_same_full_bounds() -> None:
    stable_old = SimpleNamespace(
        get_left=lambda: (0.0, 0.5, 0.0),
        get_right=lambda: (0.5, 0.5, 0.0),
        get_top=lambda: (0.25, 1.0, 0.0),
        get_bottom=lambda: (0.25, 0.0, 0.0),
    )
    stable_new = SimpleNamespace(
        get_left=lambda: (0.0, 0.5, 0.0),
        get_right=lambda: (0.5, 0.5, 0.0),
        get_top=lambda: (0.25, 1.0, 0.0),
        get_bottom=lambda: (0.25, 0.0, 0.0),
    )
    moved_old = SimpleNamespace(
        get_left=lambda: (1.0, 0.5, 0.0),
        get_right=lambda: (1.5, 0.5, 0.0),
        get_top=lambda: (1.25, 1.0, 0.0),
        get_bottom=lambda: (1.25, 0.0, 0.0),
    )
    moved_new = SimpleNamespace(
        get_left=lambda: (1.0, 1.5, 0.0),
        get_right=lambda: (1.5, 1.5, 0.0),
        get_top=lambda: (1.25, 2.0, 0.0),
        get_bottom=lambda: (1.25, 1.0, 0.0),
    )
    source = SimpleNamespace(
        proof_latex_source="x+y",
        proof_tokens=("x", "y"),
        proof_token_mobjects=(stable_old, moved_old),
    )
    target = SimpleNamespace(
        proof_latex_source="x-y",
        proof_tokens=("x", "y"),
        proof_token_mobjects=(stable_new, moved_new),
    )

    with (
        patch(
            "proof_video.animation.scene_helpers.Transform", return_value="stable"
        ) as transform,
        patch(
            "proof_video.animation.scene_helpers.FadeOut", return_value="fade-out"
        ) as fade_out,
        patch(
            "proof_video.animation.scene_helpers.FadeIn", return_value="fade-in"
        ) as fade_in,
        patch(
            "proof_video.animation.scene_helpers.AnimationGroup", side_effect=parallel
        ),
        patch("proof_video.animation.scene_helpers.Succession", side_effect=sequence),
    ):
        animation = _fallback_row_animation(source, target)

    assert animation == (
        "sequence",
        ("parallel", "fade-out", "fade-in"),
    )
    # Identical text at identical full 2D bounds remains the same settled
    # blackboard object; even Transform(x, x) can flash SVG families.
    transform.assert_not_called()
    fade_out.assert_called_once_with(moved_old)
    fade_in.assert_called_once()
    assert fade_in.call_args.args == (moved_new,)
    assert tuple(fade_in.call_args.kwargs["shift"]) == (0.24, 0.0, 0.0)
    assert fade_in.call_args.kwargs["scale"] == 1.65


def test_upstream_mapping_moves_tokens_across_rows_inside_one_block() -> None:
    source_a, source_b = object(), object()
    target_b, target_a = object(), object()
    source_rows = [
        SimpleNamespace(
            proof_tokens=("a",),
            proof_token_spans=((0, 1),),
            proof_token_mobjects=(source_a,),
            proof_char_span=(0, 1),
        ),
        SimpleNamespace(
            proof_tokens=("b",),
            proof_token_spans=((0, 1),),
            proof_token_mobjects=(source_b,),
            proof_char_span=(2, 3),
        ),
    ]
    target_rows = [
        SimpleNamespace(
            proof_tokens=("b",),
            proof_token_spans=((0, 1),),
            proof_token_mobjects=(target_b,),
            proof_char_span=(0, 1),
        ),
        SimpleNamespace(
            proof_tokens=("a",),
            proof_token_spans=((0, 1),),
            proof_token_mobjects=(target_a,),
            proof_char_span=(2, 3),
        ),
    ]
    maps = IndexMaps(
        source_to_target=(2, None, 0),
        target_to_source=(2, None, 0),
    )

    with (
        patch(
            "proof_video.animation.scene_helpers.Transform",
            side_effect=lambda source, target: (source, target),
        ),
        patch("proof_video.animation.scene_helpers.FadeOut"),
        patch("proof_video.animation.scene_helpers.FadeIn"),
        patch(
            "proof_video.animation.scene_helpers.AnimationGroup", side_effect=parallel
        ),
        patch("proof_video.animation.scene_helpers.Succession", side_effect=sequence),
    ):
        animations = _mapped_rows_animations(source_rows, target_rows, maps)

    assert animations == [
        (
            "sequence",
            (
                "parallel",
                (source_b, target_b),
                (source_a, target_a),
            ),
        )
    ]


def test_semantic_edges_are_preferred_to_conflicting_character_maps() -> None:
    old_a, old_b, new_b, new_a = object(), object(), object(), object()
    source = SimpleNamespace(
        proof_tokens=("a", "b"),
        proof_token_spans=((0, 1), (2, 3)),
        proof_token_mobjects=(old_a, old_b),
        proof_char_span=(0, 3),
    )
    target = SimpleNamespace(
        proof_tokens=("b", "a"),
        proof_token_spans=((0, 1), (2, 3)),
        proof_token_mobjects=(new_b, new_a),
        proof_char_span=(0, 3),
    )
    transition = SemanticTransition(
        source=SemanticExpression(
            (
                SemanticExpressionNode("a", latex_spans=(SemanticSpan(0, 1),)),
                SemanticExpressionNode("b", latex_spans=(SemanticSpan(2, 3),)),
            )
        ),
        target=SemanticExpression(
            (
                SemanticExpressionNode("b2", latex_spans=(SemanticSpan(0, 1),)),
                SemanticExpressionNode("a2", latex_spans=(SemanticSpan(2, 3),)),
            )
        ),
        edges=(
            SemanticTransitionEdge("a", "a2", "verified-rewrite-position", 1.0),
            SemanticTransitionEdge("b", "b2", "verified-rewrite-position", 1.0),
        ),
    )
    # This legacy map says the opposite (a->position 0, b->position 2).
    legacy = IndexMaps((0, None, 2), (0, None, 2))

    with (
        patch(
            "proof_video.animation.scene_helpers.Transform",
            side_effect=lambda a, b: (a, b),
        ),
        patch("proof_video.animation.scene_helpers.FadeOut"),
        patch("proof_video.animation.scene_helpers.FadeIn"),
        patch(
            "proof_video.animation.scene_helpers.AnimationGroup", side_effect=parallel
        ),
        patch("proof_video.animation.scene_helpers.Succession", side_effect=sequence),
    ):
        animations = _mapped_rows_animations([source], [target], legacy, transition)

    assert animations == [
        (
            "sequence",
            (
                "parallel",
                (old_a, new_a),
                (old_b, new_b),
            ),
        )
    ]


def test_certified_declaration_colon_is_not_reintroduced_as_a_new_symbol() -> None:
    old_name, old_colon, old_type = object(), object(), object()
    new_name, new_colon, new_type = object(), object(), object()
    source = SimpleNamespace(
        proof_row_key="hyp-f:0",
        proof_tokens=("f", ":", r"\mathbb{R}"),
        proof_token_spans=((0, 1), (2, 3), (4, 14)),
        proof_token_mobjects=(old_name, old_colon, old_type),
        proof_char_span=(0, 14),
    )
    target = SimpleNamespace(
        proof_row_key="hyp-f:0",
        proof_tokens=("f", ":", r"\mathbb{R}"),
        proof_token_spans=((0, 1), (2, 3), (4, 14)),
        proof_token_mobjects=(new_name, new_colon, new_type),
        proof_char_span=(0, 14),
    )
    transition = SemanticTransition(
        source=SemanticExpression(
            (
                SemanticExpressionNode(
                    "name", kind="declaration", latex_spans=(SemanticSpan(0, 1),)
                ),
                SemanticExpressionNode(
                    "colon",
                    kind="declaration-punctuation",
                    latex_spans=(SemanticSpan(2, 3),),
                ),
                SemanticExpressionNode(
                    "type", kind="const", latex_spans=(SemanticSpan(4, 14),)
                ),
            )
        ),
        target=SemanticExpression(
            (
                SemanticExpressionNode(
                    "name2", kind="declaration", latex_spans=(SemanticSpan(0, 1),)
                ),
                SemanticExpressionNode(
                    "colon2",
                    kind="declaration-punctuation",
                    latex_spans=(SemanticSpan(2, 3),),
                ),
                SemanticExpressionNode(
                    "type2", kind="const", latex_spans=(SemanticSpan(4, 14),)
                ),
            )
        ),
        edges=(
            SemanticTransitionEdge(
                "name", "name2", "verified-binder-introduction", 1.0
            ),
            SemanticTransitionEdge(
                "colon", "colon2", "verified-binder-introduction", 1.0
            ),
            SemanticTransitionEdge(
                "type", "type2", "verified-binder-introduction", 1.0
            ),
        ),
    )

    with (
        patch(
            "proof_video.animation.scene_helpers.Transform",
            side_effect=lambda a, b: (a, b),
        ),
        patch("proof_video.animation.scene_helpers.FadeOut") as fade_out,
        patch("proof_video.animation.scene_helpers.FadeIn") as fade_in,
        patch(
            "proof_video.animation.scene_helpers.AnimationGroup", side_effect=parallel
        ),
        patch("proof_video.animation.scene_helpers.Succession", side_effect=sequence),
    ):
        animations = _mapped_rows_animations([source], [target], None, transition)

    assert animations == [
        (
            "sequence",
            (
                "parallel",
                (old_name, new_name),
                (old_colon, new_colon),
                (old_type, new_type),
            ),
        )
    ]
    fade_out.assert_not_called()
    fade_in.assert_not_called()


def test_overlapping_semantic_nodes_keep_edges_but_reject_ambiguous_glyphs() -> None:
    transition = SemanticTransition(
        source=SemanticExpression(
            (
                SemanticExpressionNode("outer", latex_spans=(SemanticSpan(0, 1),)),
                SemanticExpressionNode("inner", latex_spans=(SemanticSpan(0, 1),)),
            )
        ),
        target=SemanticExpression(
            (
                SemanticExpressionNode("left", latex_spans=(SemanticSpan(0, 1),)),
                SemanticExpressionNode("right", latex_spans=(SemanticSpan(2, 3),)),
            )
        ),
        edges=(
            SemanticTransitionEdge("outer", "left"),
            SemanticTransitionEdge("inner", "right"),
        ),
    )

    pairs = _semantic_token_pairs(
        ((0, 1),), ("x",), ((0, 1), (2, 3)), ("x", "x"), transition
    )

    # Both logical edges remain in the model, but a single SVG glyph cannot
    # safely fly to two destinations, so neither correspondence is guessed.
    assert len(transition.edges) == 2
    assert pairs == []


def test_verified_atomic_rewrite_morphs_different_glyphs() -> None:
    transition = SemanticTransition(
        source=SemanticExpression(
            (SemanticExpressionNode("old-f", latex_spans=(SemanticSpan(0, 1),)),)
        ),
        target=SemanticExpression(
            (SemanticExpressionNode("new-g", latex_spans=(SemanticSpan(0, 1),)),)
        ),
        edges=(
            SemanticTransitionEdge("old-f", "new-g", "verified-rewrite-position", 0.9),
        ),
    )

    assert _semantic_token_pairs(((0, 1),), ("f",), ((0, 1),), ("g",), transition) == [
        (0, 0)
    ]


def test_uncertified_leaf_identity_and_defeq_do_not_move_equal_glyphs() -> None:
    transition = SemanticTransition(
        source=SemanticExpression(
            (
                SemanticExpressionNode(
                    "source-leaf",
                    kind="fvar",
                    path=(0, 0),
                    latex_spans=(SemanticSpan(0, 1),),
                ),
                SemanticExpressionNode(
                    "source-parent",
                    kind="app",
                    path=(0,),
                    latex_spans=(SemanticSpan(0, 1),),
                ),
            )
        ),
        target=SemanticExpression(
            (
                SemanticExpressionNode(
                    "target-leaf",
                    kind="fvar",
                    path=(0, 0),
                    latex_spans=(SemanticSpan(0, 1),),
                ),
                SemanticExpressionNode(
                    "target-parent",
                    kind="app",
                    path=(1,),
                    latex_spans=(SemanticSpan(2, 3),),
                ),
            )
        ),
        edges=(
            SemanticTransitionEdge("source-leaf", "target-leaf", "same-fvar", 1.0),
            SemanticTransitionEdge(
                "source-parent", "target-parent", "defeq-normal-form", 0.95
            ),
        ),
    )

    pairs = _semantic_token_pairs(
        ((0, 1),), ("f",), ((0, 1), (2, 3)), ("f", "f"), transition
    )

    assert pairs == []


def test_exact_composite_beats_conflicting_equal_leaf_symbols() -> None:
    tokens = ("f", "(", "x", ")", "f", "(", "x", ")")
    spans = tuple((index, index + 1) for index in range(len(tokens)))
    transition = SemanticTransition(
        source=SemanticExpression(
            (
                SemanticExpressionNode(
                    "old-app",
                    kind="app",
                    path=("0", "1", "1"),
                    latex_spans=(SemanticSpan(0, 4),),
                ),
                SemanticExpressionNode(
                    "old-f",
                    kind="fvar",
                    identity="f",
                    path=("0", "1", "1", "0"),
                    latex_spans=(SemanticSpan(0, 1),),
                ),
            )
        ),
        target=SemanticExpression(
            (
                SemanticExpressionNode(
                    "new-app",
                    kind="app",
                    path=("0", "1"),
                    latex_spans=(SemanticSpan(0, 4),),
                ),
                SemanticExpressionNode(
                    "new-f-wrong",
                    kind="fvar",
                    identity="f",
                    path=("0", "2", "0"),
                    latex_spans=(SemanticSpan(4, 5),),
                ),
            )
        ),
        edges=(
            SemanticTransitionEdge(
                "old-app", "new-app", "verified-structural-expression", 1.0
            ),
            SemanticTransitionEdge("old-f", "new-f-wrong", "same-identity", 1.0),
        ),
    )

    pairs = _semantic_token_pairs(spans, tokens, spans, tokens, transition)
    assert pairs[:4] == [(0, 0), (1, 1), (2, 2), (3, 3)]
    assert (0, 4) not in pairs


def test_verified_application_shell_preserves_both_parentheses() -> None:
    source_tokens = ("f", "(", "t", ")")
    target_tokens = ("f", "(", "f", "(", "x", ")", ")")
    source_spans = tuple((index, index + 1) for index in range(4))
    target_spans = tuple((index, index + 1) for index in range(7))
    transition = SemanticTransition(
        source=SemanticExpression(
            (
                SemanticExpressionNode(
                    "old-app",
                    kind="app",
                    path=("0", "1", "0"),
                    # Exported span intentionally stops before the closing paren.
                    latex_spans=(SemanticSpan(0, 3),),
                ),
                SemanticExpressionNode(
                    "old-arg",
                    kind="bvar",
                    parent_id="old-app",
                    path=("0", "1", "0", "1"),
                    latex_spans=(SemanticSpan(2, 3),),
                ),
            )
        ),
        target=SemanticExpression(
            (
                SemanticExpressionNode(
                    "new-app",
                    kind="app",
                    path=("0", "0"),
                    latex_spans=(SemanticSpan(0, 6),),
                ),
                SemanticExpressionNode(
                    "new-arg",
                    kind="app",
                    parent_id="new-app",
                    path=("0", "0", "1"),
                    latex_spans=(SemanticSpan(2, 6),),
                ),
            )
        ),
        edges=(
            SemanticTransitionEdge(
                "old-app", "new-app", "verified-structural-shell", 1.0
            ),
        ),
    )

    pairs = _semantic_token_pairs(
        source_spans,
        source_tokens,
        target_spans,
        target_tokens,
        transition,
    )
    assert pairs == [(0, 0), (1, 1), (3, 6)]


def test_infix_shell_does_not_claim_function_head_from_left_operand() -> None:
    source_tokens = ("f", "(", "t", ")", r"\leq", "t")
    target_tokens = ("f", "(", "f", "(", "x", ")", ")", r"\leq", "f", "(", "x", ")")
    source_spans = tuple((index, index + 1) for index in range(len(source_tokens)))
    target_spans = tuple((index, index + 1) for index in range(len(target_tokens)))
    transition = SemanticTransition(
        source=SemanticExpression(
            (
                SemanticExpressionNode(
                    "old-relation",
                    kind="app",
                    path=("0",),
                    latex_spans=(SemanticSpan(0, 6),),
                ),
                SemanticExpressionNode(
                    "old-left",
                    kind="app",
                    parent_id="old-relation",
                    path=("0", "0"),
                    latex_spans=(SemanticSpan(0, 3),),
                ),
                SemanticExpressionNode(
                    "old-argument",
                    kind="bvar",
                    parent_id="old-left",
                    path=("0", "0", "1"),
                    latex_spans=(SemanticSpan(2, 3),),
                ),
                SemanticExpressionNode(
                    "old-right",
                    kind="bvar",
                    parent_id="old-relation",
                    path=("0", "1"),
                    latex_spans=(SemanticSpan(5, 6),),
                ),
            )
        ),
        target=SemanticExpression(
            (
                SemanticExpressionNode(
                    "new-relation",
                    kind="app",
                    path=("0",),
                    latex_spans=(SemanticSpan(0, 12),),
                ),
                SemanticExpressionNode(
                    "new-left",
                    kind="app",
                    parent_id="new-relation",
                    path=("0", "0"),
                    latex_spans=(SemanticSpan(0, 6),),
                ),
                SemanticExpressionNode(
                    "new-argument",
                    kind="app",
                    parent_id="new-left",
                    path=("0", "0", "1"),
                    latex_spans=(SemanticSpan(2, 6),),
                ),
                SemanticExpressionNode(
                    "new-right",
                    kind="app",
                    parent_id="new-relation",
                    path=("0", "1"),
                    latex_spans=(SemanticSpan(8, 12),),
                ),
            )
        ),
        edges=(
            SemanticTransitionEdge(
                "old-relation", "new-relation", "verified-structural-shell", 1.0
            ),
            SemanticTransitionEdge(
                "old-left", "new-left", "verified-structural-shell", 1.0
            ),
        ),
    )

    pairs = _semantic_token_pairs(
        source_spans,
        source_tokens,
        target_spans,
        target_tokens,
        transition,
    )

    # The outer application stays whole and the relation stays stationary.
    # In particular, the relation node must not claim the operand's f merely
    # because Lean represents both infix relations and calls as applications.
    assert pairs == [(0, 0), (1, 1), (3, 6), (4, 7)]


def test_unchanged_fallback_row_moves_without_disassembling_glyphs() -> None:
    source = SimpleNamespace(proof_latex_source=r"f : \mathbb{R} \to \mathbb{R}")
    target = SimpleNamespace(proof_latex_source=r"f : \mathbb{R} \to \mathbb{R}")

    with patch(
        "proof_video.animation.scene_helpers.Transform", return_value="whole-row"
    ) as transform:
        animation = _fallback_row_animation(source, target)

    assert animation == "whole-row"
    transform.assert_called_once_with(source, target)


def test_write_speed_controls_glyph_reveal_time_and_rows_are_sequential() -> None:
    scene = object.__new__(ProofScene)
    scene.chars_per_second = 10.0
    scene.settle_seconds = 0.45
    scene.play = Mock()
    scene.wait = Mock()
    first = object()
    second = object()
    first_reveal = SimpleNamespace(mobject=object())
    second_reveal = SimpleNamespace(mobject=object())

    with (
        patch(
            "proof_video.scene._glyph_reveal", side_effect=[first_reveal, second_reveal]
        ),
        patch("proof_video.scene._glyph_count", side_effect=[10, 30]),
        patch("proof_video.scene.Succession", return_value="combined") as succession,
    ):
        animations = scene._write_rows([first, second])

    assert animations == [first_reveal, second_reveal]
    scene.play.assert_called_once_with("combined", run_time=4.0)
    succession.assert_called_once_with(first_reveal, second_reveal, lag_ratio=1)
    scene.wait.assert_called_once_with(0.45)


def test_glyph_reveal_uses_true_stroke_writes_in_sequence() -> None:
    glyphs = [object(), object(), object()]
    with (
        patch("proof_video.animation.scene_helpers._leaf_glyphs", return_value=glyphs),
        patch(
            "proof_video.animation.scene_helpers.Write",
            side_effect=lambda glyph: ("write", glyph),
        ) as write,
        patch(
            "proof_video.animation.scene_helpers.Succession", return_value="succession"
        ) as succession,
    ):
        result = _glyph_reveal(object())

    assert result == "succession"
    assert [call.args[0] for call in write.call_args_list] == glyphs
    succession.assert_called_once_with(
        *(("write", glyph) for glyph in glyphs),
        lag_ratio=1,
    )


def test_similar_dormant_branch_transforms_as_one_block() -> None:
    old = block(
        "old-lineage",
        row("hyp-f:0"),
        row("hyp-hf:0"),
        row("hyp-x:0"),
        row("target:0"),
    )
    new_hypothesis = row("hyp-result:0")
    new = block(
        "new-lineage",
        row("hyp-f:0"),
        row("hyp-hf:0"),
        new_hypothesis,
        row("target:0"),
    )
    # These maps belong to the new Lean lineage, not to the visually similar
    # block from the preceding frame. A fuzzy block match must ignore them.
    new.proof_latex_index_maps = IndexMaps(
        source_to_target=(0,),
        target_to_source=(0,),
    )

    with (
        patch("proof_video.scene._mapped_row_animations", return_value=None) as mapped,
        patch("proof_video.scene.FadeOut", return_value="fade"),
        patch("proof_video.animation.scene_helpers.FadeOut", return_value="fade"),
        patch("proof_video.animation.scene_helpers.FadeIn", return_value="appear"),
        patch(
            "proof_video.animation.scene_helpers.Succession", side_effect=sequence
        ) as succession,
    ):
        animations, new_rows = ProofScene._row_transition_parts(
            TaggedGroup([old]), TaggedGroup([new])
        )

    assert animations == [
        ("sequence", "fade", "appear"),
        ("sequence", "fade", "appear"),
        ("sequence", "fade", "appear"),
        "fade",
    ]
    assert new_rows == [new_hypothesis]
    assert succession.call_count == 3
    assert mapped.call_count == 3
    assert all(call.args[2] is None for call in mapped.call_args_list)


def test_protected_assumption_expression_is_copied_into_new_conclusion() -> None:
    source_token = object()
    target_token = object()
    source = SimpleNamespace(
        proof_row_key="hyp-hf:0",
        proof_tokens=("P",),
        proof_token_spans=((0, 1),),
        proof_token_mobjects=(source_token,),
        proof_char_span=(0, 1),
    )
    target = SimpleNamespace(
        proof_row_key="target:0",
        proof_tokens=("P",),
        proof_token_spans=((0, 1),),
        proof_token_mobjects=(target_token,),
        proof_char_span=(2, 3),
    )
    transition = SemanticTransition(
        source=SemanticExpression(
            (SemanticExpressionNode("premise", latex_spans=(SemanticSpan(0, 1),)),)
        ),
        target=SemanticExpression(
            (SemanticExpressionNode("conclusion", latex_spans=(SemanticSpan(2, 3),)),)
        ),
        edges=(
            SemanticTransitionEdge(
                "premise", "conclusion", "verified-premise-copy", 1.0
            ),
        ),
    )

    with (
        patch(
            "proof_video.animation.scene_helpers.TransformFromCopy",
            side_effect=lambda old, new: ("copy", old, new),
        ),
        patch("proof_video.animation.scene_helpers.FadeOut") as fade_out,
        patch("proof_video.animation.scene_helpers.FadeIn") as fade_in,
        patch(
            "proof_video.animation.scene_helpers.AnimationGroup", side_effect=parallel
        ),
        patch("proof_video.animation.scene_helpers.Succession", side_effect=sequence),
    ):
        animations = _mapped_rows_animations(
            [source],
            [target],
            None,
            transition,
            protected_source_bases={"hyp-hf"},
        )

    assert animations == [
        ("sequence", ("parallel", ("copy", source_token, target_token)))
    ]
    fade_out.assert_not_called()
    fade_in.assert_not_called()


def test_structural_rule_can_copy_a_persistent_context_token() -> None:
    transition = SemanticTransition(
        source=SemanticExpression(
            (
                SemanticExpressionNode(
                    "proof-context-1/p",
                    kind="fvar",
                    path=("context", 1, "0"),
                    latex_spans=(SemanticSpan(0, 1),),
                ),
            )
        ),
        target=SemanticExpression(
            (
                SemanticExpressionNode(
                    "proof-context-1/p",
                    kind="fvar",
                    path=("context", 1, "0"),
                    latex_spans=(SemanticSpan(0, 1),),
                ),
                SemanticExpressionNode(
                    "new-p",
                    kind="fvar",
                    latex_spans=(SemanticSpan(2, 3),),
                ),
            )
        ),
        edges=(
            SemanticTransitionEdge(
                "proof-context-1/p",
                "proof-context-1/p",
                "same-proof-context",
                1.0,
            ),
            SemanticTransitionEdge(
                "proof-context-1/p",
                "new-p",
                "verified-structural-expression",
                1.0,
            ),
        ),
    )

    assert _semantic_token_pairs(
        ((0, 1),),
        ("P",),
        ((0, 1), (2, 3)),
        ("P", "P"),
        transition,
    ) == [(0, 0), (0, 1)]


def test_consumed_context_fact_moves_to_conclusion_without_copying() -> None:
    transition = SemanticTransition(
        source=SemanticExpression(
            (
                SemanticExpressionNode(
                    "proof-context-7/left",
                    kind="app",
                    path=("context", 7, "0"),
                    latex_spans=(SemanticSpan(0, 4),),
                ),
            )
        ),
        target=SemanticExpression(
            (
                SemanticExpressionNode(
                    "new-left",
                    kind="app",
                    path=("0",),
                    latex_spans=(SemanticSpan(0, 4),),
                ),
            )
        ),
        edges=(
            SemanticTransitionEdge(
                "proof-context-7/left",
                "new-left",
                "verified-premise-transfer",
                1.0,
            ),
        ),
    )

    plan = _semantic_transition_plan(
        ((0, 1), (1, 2), (2, 3), (3, 4)),
        ("A", "(", "x", ")"),
        ((0, 1), (1, 2), (2, 3), (3, 4)),
        ("A", "(", "x", ")"),
        transition,
    )
    assert plan is not None and plan.valid
    assert len(plan.selected) == 1
    assert plan.selected[0].role.value == "preserve"
