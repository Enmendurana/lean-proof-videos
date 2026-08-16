"""Low-level Manim helpers for proof-row layout and semantic transitions."""

from __future__ import annotations

from manim import (
    AnimationGroup,
    FadeIn,
    FadeOut,
    MathTex,
    Mobject,
    RIGHT,
    Succession,
    Transform,
    TransformFromCopy,
    VGroup,
    Write,
)

from proof_video.latex import lean_to_latex, parse_goal_state
from proof_video.models import Goal, IndexMaps, SemanticTransition
from proof_video.animation.latex import (
    _latex_matching_token_spans,
    _normalize_unicode_math,
    _sanitize_leantex,
    _split_latex_lines,
    _unwrap_leantex_fallback,
)
from proof_video.animation.semantic import (
    _collect_row_token_data,
    _row_base_key,
    _semantic_token_pairs,
)


NEW_TOKEN_SLIDE_DISTANCE = 0.24
NEW_TOKEN_START_SCALE = 1.65


def _glyph_reveal(mobject):
    """Draw each mathematical glyph stroke-by-stroke, strictly in order."""
    glyphs = _leaf_glyphs(mobject)
    if not glyphs:
        return Write(mobject)
    return Succession(*(Write(glyph) for glyph in glyphs), lag_ratio=1)


def _similar_block_pairs(old_blocks: list, new_blocks: list) -> list[tuple]:
    """Greedily pair dormant-branch blocks by semantic row identity."""
    candidates = []
    for old_index, old in enumerate(old_blocks):
        old_keys = _block_row_keys(old)
        for new_index, new in enumerate(new_blocks):
            new_keys = _block_row_keys(new)
            denominator = max(1, len(old_keys), len(new_keys))
            score = len(old_keys & new_keys) / denominator
            if score >= 0.35:
                candidates.append((score, old_index, new_index))

    result = []
    used_old: set[int] = set()
    used_new: set[int] = set()
    for _score, old_index, new_index in sorted(candidates, reverse=True):
        if old_index in used_old or new_index in used_new:
            continue
        used_old.add(old_index)
        used_new.add(new_index)
        result.append((old_blocks[old_index], new_blocks[new_index]))
    return result


def _shares_proof_block(source, target) -> bool:
    """Whether two displayed states continue at least one Lean goal lineage."""
    source_keys = {
        getattr(block, "proof_block_key", None)
        for block in source
        if getattr(block, "proof_block_key", None) is not None
    }
    target_keys = {
        getattr(block, "proof_block_key", None)
        for block in target
        if getattr(block, "proof_block_key", None) is not None
    }
    return bool(source_keys & target_keys)


def _continues_visual_block(source, target) -> bool:
    """Keep exact or semantically similar focused goals in one board block."""
    return _shares_proof_block(source, target) or bool(
        _similar_block_pairs(list(source), list(target))
    )


def _block_row_keys(block) -> set[str]:
    # Ignore only the final wrapping suffix, so a long target remains the same
    # semantic row if its line wrapping changes after a camera transition.
    return {
        _row_base_key(getattr(row, "proof_row_key", f"row-{index}"))
        for index, row in enumerate(block)
    }


def _leaf_glyphs(mobject) -> list:
    return [
        member
        for member in mobject.get_family()
        if member.has_points()
        and not any(child.has_points() for child in member.submobjects)
    ]


def _glyph_count(mobject) -> int:
    """Count the actual visible vector glyphs used by the reveal animation."""
    return max(1, len(_leaf_glyphs(mobject)))


def _mapped_row_animations(
    source,
    target,
    index_maps: IndexMaps | None,
    semantic_transition: SemanticTransition | None = None,
):
    return _mapped_rows_animations(
        [source], [target], index_maps, semantic_transition
    )


def _fallback_row_animation(source, target):
    """Preserve stable tokens even when a trace has no semantic index map."""
    if (
        getattr(source, "proof_latex_source", None) is not None
        and getattr(source, "proof_latex_source", None)
        == getattr(target, "proof_latex_source", None)
        and _tokens_share_geometry(source, target)
    ):
        return Transform(source, target)

    source_texts = getattr(source, "proof_tokens", None)
    target_texts = getattr(target, "proof_tokens", None)
    source_tokens = getattr(source, "proof_token_mobjects", None)
    target_tokens = getattr(target, "proof_token_mobjects", None)
    if (
        source_texts is not None
        and target_texts is not None
        and source_tokens is not None
        and target_tokens is not None
    ):
        pairs = _stationary_text_pairs(
            source_texts,
            source_tokens,
            target_texts,
            target_tokens,
        )
        return _phased_token_transition(
            source_tokens,
            target_tokens,
            pairs,
        )

    # Old traces without token metadata retain the safe whole-row fallback.
    return Succession(FadeOut(source), FadeIn(target), lag_ratio=1)


def _mapped_rows_animations(
    source_rows,
    target_rows,
    index_maps: IndexMaps | None,
    semantic_transition: SemanticTransition | None = None,
    protected_source_bases: set[str] | None = None,
):
    """Reproduce upstream's persistent-character transition inside one block.

    Each identical matched rendered token is transformed independently, just
    as the Blender implementation repositions the same ``CharObj``. Removed
    and introduced tokens only fade; their geometry always remains at the
    fixed final font size. ``None`` means the trace predates semantic index
    maps or token spans are unavailable.
    """
    if index_maps is None and semantic_transition is None:
        return None

    source_data = _collect_row_token_data(source_rows)
    target_data = _collect_row_token_data(target_rows)
    if source_data is None or target_data is None:
        if semantic_transition is not None:
            # A single unusually complicated/wrapped MathTex row can fail to
            # expose token metadata.  Do not let that one row erase the whole
            # sequent: unchanged assumptions are still authoritative objects
            # with stable row keys and must remain on the board.  Degrade only
            # the affected row to a local cross-fade.
            return [_partial_semantic_rows_animation(source_rows, target_rows)]
        return None
    source_global, source_structural, source_token_texts, source_tokens = source_data
    target_global, target_structural, target_token_texts, target_tokens = target_data
    protected_source_bases = protected_source_bases or set()
    protected_source_indices = {
        index
        for index, position in enumerate(source_structural)
        if position[0] in protected_source_bases
    }

    # Expression identities are richer than flattened character maps: nodes
    # can cover several disjoint spans and overlapping nodes remain distinct.
    # Prefer them whenever they resolve to an unambiguous one-to-one set of
    # rendered tokens. If the semantic metadata is incomplete or ambiguous,
    # retain the older safe character-map path below.
    semantic_pairs = _semantic_token_pairs(
        source_global,
        source_token_texts,
        target_global,
        target_token_texts,
        semantic_transition,
    )
    if semantic_transition is not None:
        # Strict ProofTrace transitions do not fall back to flattened
        # character maps, same-position punctuation or SymPy.  Anything not
        # selected by the validated Lean plan is created/deleted explicitly.
        assert semantic_pairs is not None
        return [_phased_token_transition(
            source_tokens,
            target_tokens,
            semantic_pairs,
            source_structural,
            target_structural,
            protected_source_indices=protected_source_indices,
            copy_source_indices=protected_source_indices,
        )]
    if index_maps is None:
        return None

    used_source: set[int] = set()
    pairs: list[tuple[int, int]] = []

    for target_index, (target_start, target_end) in enumerate(target_global):
        mapped_positions = [
            index_maps.target_to_source[position]
            for position in range(
                max(0, target_start),
                min(target_end, len(index_maps.target_to_source)),
            )
            if index_maps.target_to_source[position] is not None
        ]
        if not mapped_positions:
            continue
        candidates = []
        for source_index, (source_start, source_end) in enumerate(source_global):
            if source_index in used_source:
                continue
            # Character maps can align repeated punctuation at different
            # semantic positions. Morphing unlike SVG paths (for example a
            # colon into a minus) stretches the path across the row for a few
            # frames. Only persistent, textually identical tokens may move.
            if source_token_texts[source_index] != target_token_texts[target_index]:
                continue
            score = sum(
                source_start <= position < source_end for position in mapped_positions
            )
            if score:
                candidates.append((score, source_index))
        if candidates:
            _score, source_index = max(candidates)
            used_source.add(source_index)
            pairs.append((source_index, target_index))

    # The trace mapping is the source of truth for object identity.  A mapped
    # token must remain the same object even when two transition paths cross;
    # geometry may influence a future curved path, but it must never discard a
    # logical correspondence emitted by Lean.
    return [_phased_token_transition(source_tokens, target_tokens, pairs)]


def _partial_semantic_rows_animation(source_rows, target_rows):
    """Preserve stable rows when one row lacks render-token metadata.

    Semantic correctness lives above SVG tokenization.  If TeX produces an
    SVG family that cannot be split reliably, stable row identity is still a
    safe, generic fallback.  Only changed/added/removed rows cross-fade; the
    common context never performs a whole-board fade-to-black.
    """
    source_by_key = {
        getattr(row, "proof_row_key", f"source-row-{index}"): row
        for index, row in enumerate(source_rows)
    }
    target_by_key = {
        getattr(row, "proof_row_key", f"target-row-{index}"): row
        for index, row in enumerate(target_rows)
    }
    animations = []
    for key in source_by_key.keys() & target_by_key.keys():
        source = source_by_key[key]
        target = target_by_key[key]
        source_latex = getattr(source, "proof_latex_source", None)
        target_latex = getattr(target, "proof_latex_source", None)
        if source_latex is not None and source_latex == target_latex:
            animations.append(Transform(source, target))
        else:
            animations.append(AnimationGroup(FadeOut(source), FadeIn(target)))
    animations.extend(
        FadeOut(source_by_key[key])
        for key in source_by_key.keys() - target_by_key.keys()
    )
    animations.extend(
        FadeIn(target_by_key[key])
        for key in target_by_key.keys() - source_by_key.keys()
    )
    return AnimationGroup(*animations) if animations else AnimationGroup()


def _unmapped_semantic_rows_animation(source_rows, target_rows):
    """Safe no-guess fallback when semantic spans cannot reach SVG tokens."""
    phases = []
    if source_rows:
        phases.append(AnimationGroup(*(FadeOut(row) for row in source_rows)))
    if target_rows:
        phases.append(AnimationGroup(*(FadeIn(row) for row in target_rows)))
    if not phases:
        return AnimationGroup()
    return Succession(*phases, lag_ratio=1)


def _contiguous_pair_runs(
    stationary_pairs,
    source_positions,
    target_positions,
    copy_source_indices=None,
):
    """Group adjacent semantic identities that remain one rendered phrase."""
    if source_positions is None or target_positions is None:
        return [[pair] for pair in stationary_pairs]
    copy_source_indices = copy_source_indices or set()
    runs = []
    for pair in sorted(stationary_pairs):
        if not runs:
            runs.append([pair])
            continue
        previous_source, previous_target = runs[-1][-1]
        source_index, target_index = pair
        same_source_row = (
            source_positions[previous_source][0] == source_positions[source_index][0]
        )
        same_target_row = (
            target_positions[previous_target][0] == target_positions[target_index][0]
        )
        same_copy_mode = (
            (previous_source in copy_source_indices)
            == (source_index in copy_source_indices)
        )
        if (
            source_index == previous_source + 1
            and target_index == previous_target + 1
            and same_source_row
            and same_target_row
            and same_copy_mode
        ):
            runs[-1].append(pair)
        else:
            runs.append([pair])
    return runs


def _phased_token_transition(
    source_tokens,
    target_tokens,
    stationary_pairs,
    source_positions=None,
    target_positions=None,
    protected_source_indices=None,
    copy_source_indices=None,
):
    """Transform a changed row in parallel, with a soft oversized entrance."""
    protected_source_indices = protected_source_indices or set()
    copy_source_indices = copy_source_indices or set()
    stationary_source = {source_index for source_index, _target_index in stationary_pairs}
    stationary_target = {target_index for _source_index, target_index in stationary_pairs}
    removed = [
        token
        for index, token in enumerate(source_tokens)
        if index not in stationary_source and index not in protected_source_indices
    ]
    introduced = [
        token
        for index, token in enumerate(target_tokens)
        if index not in stationary_target
    ]
    animations = []
    if removed:
        animations.extend(FadeOut(token) for token in removed)
    # Tokens whose complete rendered boxes already agree are literally left
    # untouched. Animating ``Transform(x, x)`` still replaces SVG families
    # and causes the visible self-flicker reported for parentheses, colons,
    # relations and unchanged function applications.
    moving_pairs = [
        pair
        for pair in stationary_pairs
        if pair[0] in copy_source_indices
        or not (
            _has_bounds(source_tokens[pair[0]])
            and _has_bounds(target_tokens[pair[1]])
            and _tokens_share_geometry(
                source_tokens[pair[0]], target_tokens[pair[1]]
            )
        )
    ]
    if moving_pairs:
        for run in _contiguous_pair_runs(
            moving_pairs,
            source_positions,
            target_positions,
            copy_source_indices,
        ):
            source_run = [source_tokens[source_index] for source_index, _ in run]
            target_run = [target_tokens[target_index] for _, target_index in run]
            copy_mode = run[0][0] in copy_source_indices
            if (
                len(run) > 1
                and all(isinstance(token, Mobject) for token in source_run)
                and all(isinstance(token, Mobject) for token in target_run)
            ):
                # A preserved application such as ``f(x)`` must read as one
                # mathematical object in motion.  Transforming each glyph
                # independently makes the leading function symbol appear to
                # move alone even when every token has a valid Lean edge.
                transform = TransformFromCopy if copy_mode else Transform
                animations.append(transform(VGroup(*source_run), VGroup(*target_run)))
            else:
                transform = TransformFromCopy if copy_mode else Transform
                animations.extend(
                    transform(source_token, target_token)
                    for source_token, target_token in zip(
                        source_run, target_run, strict=True
                    )
                )
    if introduced:
        # Deliberately recreate the attractive part of the old oversized-glyph
        # effect, but only for genuinely new semantic objects.  FadeIn's
        # translucent interpolation makes the enlarged start read as soft;
        # it flies into place while shrinking, never enlarging a preserved
        # minus/f/function that belongs elsewhere in the expression.
        animations.extend(
            FadeIn(
                token,
                shift=RIGHT * NEW_TOKEN_SLIDE_DISTANCE,
                scale=NEW_TOKEN_START_SCALE,
            )
            for token in introduced
        )
    if not animations:
        return AnimationGroup()
    # Running removals, logical moves and entrances together avoids the blank
    # intermediate row that looked like a flash in sequential transitions.
    return Succession(AnimationGroup(*animations))


def _stationary_text_pairs(
    source_texts,
    source_tokens,
    target_texts,
    target_tokens,
) -> list[tuple[int, int]]:
    """Match equal tokens only when their complete rendered boxes coincide."""
    pairs = []
    used_source: set[int] = set()
    for target_index, (target_text, target_token) in enumerate(
        zip(target_texts, target_tokens, strict=True)
    ):
        for source_index, (source_text, source_token) in enumerate(
            zip(source_texts, source_tokens, strict=True)
        ):
            if source_index in used_source or source_text != target_text:
                continue
            if not _tokens_share_geometry(source_token, target_token):
                continue
            used_source.add(source_index)
            pairs.append((source_index, target_index))
            break
    return pairs


def _has_bounds(token) -> bool:
    return all(
        hasattr(token, method)
        for method in ("get_left", "get_right", "get_top", "get_bottom")
    )


def _tokens_share_geometry(source, target, tolerance: float = 0.025) -> bool:
    """Compare full 2D bounds, including height, not merely token centers."""
    bounds_methods = ("get_left", "get_right", "get_top", "get_bottom")
    if not _has_bounds(source) or not _has_bounds(target):
        # Lightweight test doubles and old third-party scene objects do not
        # expose geometry. Preserve their historical matching behavior.
        return True
    for method in bounds_methods:
        source_point = getattr(source, method)()
        target_point = getattr(target, method)()
        if any(
            abs(float(source_point[axis]) - float(target_point[axis])) > tolerance
            for axis in (0, 1)
        ):
            return False
    return True

def _goal_latex(goal: Goal) -> str:
    if goal.latex_target:
        return goal.latex_target
    return lean_to_latex(parse_goal_state(goal.state).target)


def _initial_context_lines(goal: Goal) -> list[str]:
    if goal.latex_context:
        return [hypothesis.render_latex() for hypothesis in goal.latex_context]

    result = []
    for names, expression in parse_goal_state(goal.state).context:
        safe_names = names.replace("_", r"\_")
        result.append(rf"{safe_names} \;:\; {lean_to_latex(expression)}")
    return result


def _safe_mathtex(source: str, **kwargs) -> MathTex:
    source = _unwrap_leantex_fallback(source)
    source = _normalize_unicode_math(source)
    source = _sanitize_leantex(source)
    try:
        return MathTex(source, **kwargs)
    except Exception:
        plain = source.replace("\\", " ").replace("{", "(").replace("}", ")")
        replacements = {
            "_": " ",
            "^": " to the power of ",
            "%": " percent ",
            "&": " and ",
            "#": " number ",
            "$": " ",
            "~": " ",
        }
        for special, replacement in replacements.items():
            plain = plain.replace(special, replacement)
        plain = "".join(character if 32 <= ord(character) < 127 else "?" for character in plain)
        return MathTex(rf"\text{{{plain}}}", **kwargs)


def _wrapped_math_rows(source: str, color: str, maximum_width: float = 10.5) -> list[MathTex]:
    return [
        row
        for row, _start, _end in _wrapped_math_rows_with_spans(
            source, color, maximum_width
        )
    ]


def _wrapped_math_rows_with_spans(
    source: str, color: str, maximum_width: float = 10.5
) -> list[tuple[MathTex, int, int]]:
    """Wrap long semantic formulas without changing the fixed chalk font."""
    whole = _matching_mathtex(source, font_size=32, color=color)
    if whole.width <= maximum_width:
        return [(whole, 0, len(source))]

    pieces = _split_latex_lines(source)
    if len(pieces) == 1:
        return [(whole, 0, len(source))]

    result: list[tuple[MathTex, int, int]] = []
    cursor = 0
    for piece in pieces:
        start = source.find(piece, cursor)
        if start < 0:
            start = cursor
        end = start + len(piece)
        result.append((_matching_mathtex(piece, font_size=32, color=color), start, end))
        cursor = end
    return result


def _matching_mathtex(source: str, **kwargs) -> MathTex:
    """Create stable semantic token parts for TransformMatchingTex."""
    normalized = _sanitize_leantex(
        _normalize_unicode_math(_unwrap_leantex_fallback(source))
    )
    token_spans = _latex_matching_token_spans(normalized)
    tokens = [token for token, _start, _end in token_spans]
    if not token_spans:
        return _safe_mathtex(source, **kwargs)
    isolated = "".join(r"{{" + token + r"}}" for token in tokens)
    try:
        result = MathTex(isolated, **kwargs)
        _prune_empty_submobjects(result)
        if len(result.submobjects) == len(token_spans):
            result.proof_tokens = tuple(tokens)
            result.proof_token_spans = tuple(
                (start, end) for _token, start, end in token_spans
            )
            result.proof_token_mobjects = tuple(result.submobjects)
        result.proof_latex_source = normalized
        return result
    except Exception:
        result = _safe_mathtex(source, **kwargs)
        result.proof_latex_source = normalized
        return result


def _prune_empty_submobjects(mobject) -> None:
    """Drop invisible TeX separator groups that corrupt shifted bboxes."""
    for submobject in mobject.submobjects:
        _prune_empty_submobjects(submobject)
    mobject.submobjects = [
        submobject
        for submobject in mobject.submobjects
        if submobject.has_points() or any(member.has_points() for member in submobject.get_family())
    ]
