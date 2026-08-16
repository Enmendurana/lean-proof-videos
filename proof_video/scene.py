from __future__ import annotations

from pathlib import Path

from manim import (
    DOWN,
    FadeOut,
    LEFT,
    Line,
    RIGHT,
    UP,
    MathTex,
    MovingCameraScene,
    Succession,
    VGroup,
    Write,
    config,
)

from proof_video.models import Frame, Movie
from proof_video.rendering.pacing import (
    DEFAULT_VISIBLE_GLYPHS_PER_SECOND,
    minimum_visible_action_frames,
)
from proof_video.animation.semantic import (
    _row_base_key,
    _semantic_mapped_target_row_bases,
    _stable_visual_rows,
)


from proof_video.animation.scene_helpers import (
    _glyph_reveal,
    _similar_block_pairs,
    _continues_visual_block,
    _glyph_count,
    _mapped_row_animations,
    _fallback_row_animation,
    _mapped_rows_animations,
    _goal_latex,
    _initial_context_lines,
    _safe_mathtex,
    _wrapped_math_rows_with_spans,
)

BLACKBOARD = "#000000"
CHALK = "#F1F0E8"
DIM_CHALK = "#AAA99F"


class ProofScene(MovingCameraScene):
    """Write a Lean proof down an effectively infinite blackboard."""

    board_margin_x = 0.62
    board_margin_y = 0.55
    line_gap = 0.34

    def __init__(
        self,
        movie: Movie,
        chars_per_second: float = DEFAULT_VISIBLE_GLYPHS_PER_SECOND,
        transition_seconds: float = 0.65,
        settle_seconds: float = 0.45,
        audio: Path | None = None,
        **kwargs,
    ) -> None:
        self.movie = movie
        self.chars_per_second = chars_per_second
        self.transition_seconds = transition_seconds
        self.settle_seconds = settle_seconds
        self.audio = audio
        super().__init__(**kwargs)

    def construct(self) -> None:
        self.camera.background_color = BLACKBOARD
        if self.audio:
            self.add_sound(str(self.audio), gain=-8)

        frames = tuple(frame for frame in self.movie.semantic_frames() if frame.display_goals)
        if not frames:
            return

        current = self._step_block(frames[0])
        self._place_next(current, None)
        initial_focus = VGroup(current, self._make_qed(current)) if len(frames) == 1 else current
        initial_width = self._camera_width_for(initial_focus)
        self._camera_frame().set_width(initial_width).move_to(
            self._camera_center_for(initial_focus, initial_width)
        )
        initial_reveal = _glyph_reveal(current)
        self.play(initial_reveal, run_time=self._write_time(current))
        self.remove(initial_reveal.mobject)
        self.add(current)
        self.wait(self.settle_seconds)

        board_history: list[VGroup] = [current]
        for position, frame in enumerate(frames[1:], start=1):
            target = self._step_block(frame)
            source = current.copy()
            continues_block = _continues_visual_block(current, target)
            if continues_block:
                self._place_continuation_pair(source, target, current)
                # The source copy becomes the animated identity of the block;
                # leaving the settled old state underneath would produce the
                # duplicated blocks/ghost glyphs visible in the bug report.
                self.remove(current)
            else:
                self._place_transition_pair(source, target, current)
            target_left = target.get_left()[0]
            target_center_y = target.get_center()[1]
            self.add(source)
            focus = target
            camera_width = self._camera_width_for(focus)
            camera_center = self._camera_center_for(focus, camera_width)

            # This is the Manim equivalent of upstream's s1_to_s2/s2_to_s1
            # reuse: equal mathematical tokens persist and move, removed ones
            # shrink away, and introduced ones grow into place.
            transition_animations, new_rows = self._row_transition_parts(source, target)
            # Manim temporarily promotes animated descendants of a VGroup and
            # can hide their non-animated siblings.  Overlay exact, certified
            # same-row copies for the duration of the play call so stable
            # assumptions never blink merely because the target is changing.
            stable_overlays = [
                row.copy() for row in _stable_visual_rows(source, target)
            ]
            self.add(*stable_overlays)
            self.play(
                *transition_animations,
                self._camera_frame().animate.move_to(camera_center).set_width(camera_width),
                run_time=self.transition_seconds,
            )

            # TransformMatchingTex temporarily restructures source/target
            # families. Replace its result with a clean equivalent block so
            # those animation helper groups cannot pollute later camera bboxes.
            clean = self._step_block(frame)
            self._shift_block_to(clean, target_left, target_center_y)
            preserved = board_history[:-1] if continues_block else board_history
            self._write_new_rows_on_clean_board(clean, new_rows, preserved)
            # Individual Transform/Grow/Shrink animations can temporarily
            # promote nested MathTex tokens to top-level scene mobjects. A
            # targeted remove is therefore insufficient and leaves ghost
            # copies behind. Rebuild the settled board from authoritative
            # clean blocks after every transition.
            self.remove(*tuple(self.mobjects))
            self.add(*preserved, clean)
            current = clean

            qed = self._make_qed(current) if position == len(frames) - 1 else None
            clean_focus = VGroup(current, qed) if qed is not None else current
            clean_width = self._camera_width_for(clean_focus)
            self._camera_frame().set_width(clean_width).move_to(
                self._camera_center_for(clean_focus, clean_width)
            )
            if continues_block:
                board_history[-1] = current
            else:
                board_history.append(current)
            # The camera only travels downward. Removing distant off-screen
            # SVGs keeps long proofs fast without changing the visible board.
            if len(board_history) > 3:
                self.remove(board_history.pop(0))

            if qed is not None:
                self._write_qed(qed)

        if len(frames) == 1:
            self._write_qed(self._make_qed(current))

        self.wait(0.8)

    def _construct_proof_trace(self) -> None:
        """Render certified rows as a growing Fitch-style blackboard.

        This is intentionally separate from legacy goal-state morphing.  A
        ProofTrace row is immutable evidence: it is written once, remains in
        its scope, and later rows cite it rather than replacing it with an
        unrelated goal snapshot.
        """
        trace = self.movie.proof_trace
        assert trace is not None
        steps = trace.presentation_steps()
        if not steps:
            return

        history: list[VGroup] = []
        previous = None
        for position, step in enumerate(steps):
            block = self._proof_trace_row(step.display_latex, step.depth)
            self._place_next(block, previous)
            block.shift(RIGHT * (min(step.depth, 8) * 0.34))
            focus = block
            qed = self._make_qed(block) if position == len(steps) - 1 else None
            if qed is not None:
                focus = VGroup(block, qed)
            width = self._camera_width_for(focus)
            center = self._camera_center_for(focus, width)
            if previous is None:
                self._camera_frame().set_width(width).move_to(center)

            formula = getattr(block, "proof_formula_group", block)
            decoration = [] if formula is block else [
                item for item in block if item is not formula
            ]
            if decoration:
                self.add(*decoration)
            reveal = _glyph_reveal(formula)
            write_time = self._write_time(formula)
            if position == len(steps) - 1:
                write_time *= 2.5
            animations = [reveal]
            if previous is not None:
                animations.append(
                    self._camera_frame().animate.set_width(width).move_to(center)
                )
            self.play(
                *animations,
                run_time=max(
                    write_time,
                    min(0.45, self.transition_seconds) if previous is not None else 0,
                ),
            )
            self.remove(reveal.mobject)
            self.add(block)
            history.append(block)
            previous = block
            if len(history) > 6:
                self.remove(history.pop(0))
            if qed is not None:
                self._write_qed(qed)
            else:
                self.wait(min(self.settle_seconds, 0.3))
        self.wait(0.8)

    def _proof_trace_row(self, latex: str, depth: int) -> VGroup:
        rows = VGroup(*(row for row, _start, _end in _wrapped_math_rows_with_spans(
            latex, color=CHALK
        )))
        rows.arrange(DOWN, aligned_edge=LEFT, buff=0.16)
        if depth <= 0:
            rows.proof_formula_group = rows
            return rows
        scope_bar = Line(
            rows.get_corner(UP + LEFT) + LEFT * 0.18 + UP * 0.04,
            rows.get_corner(DOWN + LEFT) + LEFT * 0.18 + DOWN * 0.04,
            color=DIM_CHALK,
            stroke_width=2,
        )
        block = VGroup(scope_bar, rows)
        block.proof_formula_group = rows
        return block

    def _step_block(self, frame: Frame) -> VGroup:
        blocks = VGroup()
        # Animate only the currently focused goal. Lean may return both a new
        # proof obligation and a dormant continuation (for example `have h`);
        # rendering both would duplicate nearly the whole blackboard. The
        # continuation re-enters this same visual block after the obligation
        # is solved, at which point only its genuinely new row is written.
        for goal_index, goal in enumerate(frame.display_goals[:1]):
            rows = VGroup()
            block_cursor = 0
            context_lines = _initial_context_lines(goal)
            context_names = [
                hypothesis.key or hypothesis.name
                for hypothesis in goal.latex_context
            ]
            for context_index, source in enumerate(context_lines):
                name = (
                    context_names[context_index]
                    if context_index < len(context_names)
                    else f"context-{context_index}"
                )
                wrapped = _wrapped_math_rows_with_spans(source, color=DIM_CHALK)
                for wrap_index, (row, start, end) in enumerate(wrapped):
                    row.proof_row_key = f"hyp-{name}:{wrap_index}"
                    row.proof_char_span = (block_cursor + start, block_cursor + end)
                    rows.add(row)
                block_cursor += len(source) + 1
            source = r"\vdash\;" + _goal_latex(goal)
            wrapped = _wrapped_math_rows_with_spans(source, color=CHALK)
            for wrap_index, (row, start, end) in enumerate(wrapped):
                row.proof_row_key = f"target:{wrap_index}"
                row.proof_char_span = (block_cursor + start, block_cursor + end)
                rows.add(row)

            rows.arrange(DOWN, aligned_edge=LEFT, buff=0.16)
            rows.proof_block_key = goal.lineage_id or f"goal-{goal_index}"
            rows.proof_latex_index_maps = goal.latex_index_maps
            rows.proof_semantic_transition = goal.semantic_transition
            blocks.add(rows)

        blocks.arrange(DOWN, aligned_edge=LEFT, buff=0.28)
        return blocks

    @staticmethod
    def _row_transition_parts(source: VGroup, target: VGroup):
        """Transform existing rows only inside their original goal block.

        Rows newly introduced by a tactic are written glyph by glyph.  They
        never borrow matching glyphs from another goal block or another row.
        """
        old_blocks = {
            getattr(block, "proof_block_key", f"old-block-{i}"): block
            for i, block in enumerate(source)
        }
        new_blocks = {
            getattr(block, "proof_block_key", f"new-block-{i}"): block
            for i, block in enumerate(target)
        }
        animations = []
        new_rows = []

        exact_keys = old_blocks.keys() & new_blocks.keys()
        block_pairs = [(old_blocks[key], new_blocks[key], True) for key in exact_keys]
        unmatched_old = [old_blocks[key] for key in old_blocks.keys() - exact_keys]
        unmatched_new = [new_blocks[key] for key in new_blocks.keys() - exact_keys]

        # When focus returns to a dormant proof branch, its lineage was absent
        # from the immediately preceding frame even though the mathematical
        # block is mostly the same. Pair exactly one old/new block by row
        # similarity. Never do this during a split with an existing exact pair:
        # additional goals must still be written as genuinely new blocks.
        if not exact_keys and unmatched_old and unmatched_new:
            fuzzy_pairs = _similar_block_pairs(unmatched_old, unmatched_new)
            block_pairs.extend((old, new, False) for old, new in fuzzy_pairs)
            paired_old = {id(old) for old, _new in fuzzy_pairs}
            paired_new = {id(new) for _old, new in fuzzy_pairs}
            unmatched_old = [block for block in unmatched_old if id(block) not in paired_old]
            unmatched_new = [block for block in unmatched_new if id(block) not in paired_new]

        for old_block, new_block, use_index_maps in block_pairs:
            old = {
                getattr(row, "proof_row_key", f"old-row-{i}"): row
                for i, row in enumerate(old_block)
            }
            new = {
                getattr(row, "proof_row_key", f"new-row-{i}"): row
                for i, row in enumerate(new_block)
            }
            if use_index_maps:
                # An assumption/definition whose stable semantic row key and
                # canonical LaTeX are unchanged is literally the same line on
                # the blackboard.  Keep the settled source row visible and do
                # not decompose it into hundreds of token animations.  This
                # both reflects the proof context and prevents a complex
                # target rewrite from dimming the entire sequent.
                stable_row_keys = {
                    key
                    for key in old.keys() & new.keys()
                    if getattr(old[key], "proof_latex_source", None) is not None
                    and getattr(old[key], "proof_latex_source", None)
                    == getattr(new[key], "proof_latex_source", None)
                }
                # Upstream animates one persistent object per matched
                # character across the complete goal state. Do the same at
                # rendered-token granularity across this entire block, so a
                # surviving term may move between wrapped rows. Rows whose
                # semantic key is wholly new retain our requested chalk-write
                # entrance instead of appearing all at once.
                old_bases = {
                    _row_base_key(key)
                    for key in old
                    if key not in stable_row_keys
                }
                semantic_transition = getattr(
                    new_block, "proof_semantic_transition", None
                )
                mapped_bases = set(old_bases)
                if semantic_transition is not None:
                    # ``intro x`` moves the quantified binder into a brand-new
                    # context row.  A row is visually new, but the binder and
                    # its type are not new mathematical objects.  Include only
                    # such proof-connected rows in the token transform; rows
                    # with no semantic predecessor are still chalk-written.
                    mapped_bases.update(
                        _semantic_mapped_target_row_bases(
                            list(old_block),
                            list(new_block),
                            semantic_transition,
                        )
                    )
                mapped_target_rows = [
                    row
                    for row in new_block
                    if getattr(row, "proof_row_key", "") not in stable_row_keys
                    if _row_base_key(getattr(row, "proof_row_key", ""))
                    in mapped_bases
                ]
                mapped = _mapped_rows_animations(
                    # Stable context rows are also legitimate proof sources:
                    # applying a hypothesis copies its certified expression
                    # into the conclusion while leaving the hypothesis fixed.
                    # The mapper marks these rows as protected clone sources.
                    list(old_block),
                    mapped_target_rows,
                    getattr(new_block, "proof_latex_index_maps", None),
                    semantic_transition,
                    protected_source_bases={
                        _row_base_key(key) for key in stable_row_keys
                    },
                )
                if mapped is not None:
                    animations.extend(mapped)
                    new_rows.extend(
                        row
                        for row in new_block
                        if getattr(row, "proof_row_key", "") not in stable_row_keys
                        if _row_base_key(getattr(row, "proof_row_key", ""))
                        not in mapped_bases
                    )
                    continue
            for row_key in old.keys() & new.keys():
                mapped = _mapped_row_animations(
                    old[row_key],
                    new[row_key],
                    (
                        getattr(new_block, "proof_latex_index_maps", None)
                        if use_index_maps
                        else None
                    ),
                    (
                        getattr(new_block, "proof_semantic_transition", None)
                        if use_index_maps
                        else None
                    ),
                )
                if mapped is not None:
                    animations.extend(mapped)
                else:
                    animations.append(_fallback_row_animation(old[row_key], new[row_key]))
            for row_key in old.keys() - new.keys():
                animations.append(FadeOut(old[row_key]))
            for row_key in new.keys() - old.keys():
                new_rows.append(new[row_key])

        for block in unmatched_old:
            animations.append(FadeOut(block))
        for block in unmatched_new:
            for row in block:
                new_rows.append(row)
        return animations, new_rows

    @staticmethod
    def _row_transition_animations(source: VGroup, target: VGroup):
        """Compatibility helper returning the complete animation list."""
        animations, new_rows = ProofScene._row_transition_parts(source, target)
        return [*animations, *(_glyph_reveal(row) for row in new_rows)]

    def _place_next(self, block: VGroup, previous: VGroup | None) -> None:
        if previous is None:
            self._shift_block_to(block, left_x=0, center_y=0)
            return
        center_y = previous.get_bottom()[1] - self.line_gap - block.height / 2
        self._shift_block_to(block, left_x=0, center_y=center_y)

    def _place_transition_pair(
        self, source: VGroup, target: VGroup, previous: VGroup
    ) -> None:
        height = max(source.height, target.height)
        center_y = previous.get_bottom()[1] - self.line_gap - height / 2
        self._shift_block_to(source, left_x=0, center_y=center_y)
        self._shift_block_to(target, left_x=0, center_y=center_y)

    def _place_continuation_pair(
        self, source: VGroup, target: VGroup, previous: VGroup
    ) -> None:
        """Keep an evolving proof block at one stable upper-left anchor."""
        left_x = previous.get_left()[0]
        top_y = previous.get_top()[1]
        self._shift_block_top_left_to(source, left_x, top_y)
        self._shift_block_top_left_to(target, left_x, top_y)

    @staticmethod
    def _shift_block_to(block: VGroup, left_x: float, center_y: float) -> None:
        """Translate a token group without Manim's aligned-edge bbox mutation."""
        block.shift(
            [
                left_x - block.get_left()[0],
                center_y - block.get_center()[1],
                0,
            ]
        )

    @staticmethod
    def _shift_block_top_left_to(block: VGroup, left_x: float, top_y: float) -> None:
        block.shift(
            [
                left_x - block.get_left()[0],
                top_y - block.get_top()[1],
                0,
            ]
        )

    def _camera_width_for(self, focus) -> float:
        """Fit fixed-size chalk glyphs by zooming the camera, never the text."""
        aspect_ratio = config.frame_width / config.frame_height
        return max(focus.width / 0.82, focus.height * aspect_ratio / 0.62)

    def _camera_frame(self):
        """Return the animatable camera mobject for Cairo or OpenGL."""
        return getattr(self.camera, "frame", self.camera)

    def _camera_center_for(self, focus, camera_width: float):
        left_margin = camera_width * 0.09
        center_x = focus.get_left()[0] - left_margin + camera_width / 2
        return [center_x, focus.get_center()[1], 0]

    def _write_time(self, mobject) -> float:
        minimum = minimum_visible_action_frames(round(config.frame_rate)) / config.frame_rate
        return max(minimum, _glyph_count(mobject) / self.chars_per_second)

    def _write_rows(self, rows: list) -> list:
        """Write newly introduced rows sequentially at a true glyph rate."""
        animations = [_glyph_reveal(row) for row in rows]
        if animations:
            # One Manim play still executes the row reveals strictly in order,
            # but produces one partial movie instead of starting FFmpeg once
            # per row.  This is a large win for premise-rich kernel steps and
            # does not change their visual timing.
            total_time = sum(self._write_time(row) for row in rows)
            self.play(Succession(*animations, lag_ratio=1), run_time=total_time)
            # A completed formula needs a readable settled frame before the
            # next token transform starts. Without this hold, a viewer can
            # pause on the final typing prefix (for example just before
            # ``= 0``) and mistake it for the exported mathematical goal.
            self.wait(self.settle_seconds)
        return animations

    def _write_new_rows_on_clean_board(
        self, clean: VGroup, animated_new_rows: list, preserved: list[VGroup]
    ) -> None:
        """Write new rows while every unchanged clean row remains visible."""
        if not animated_new_rows:
            return
        new_keys = {
            getattr(row, "proof_row_key", None)
            for row in animated_new_rows
        }
        clean_rows = [row for block in clean for row in block]
        rows_to_write = [
            row
            for row in clean_rows
            if getattr(row, "proof_row_key", None) in new_keys
        ]
        write_ids = {id(row) for row in rows_to_write}
        stable_rows = [row for row in clean_rows if id(row) not in write_ids]

        # End the temporary transformation family before the next play call.
        # Otherwise Manim can omit nested transformed glyphs from the static
        # frame while Write is running.
        self.remove(*tuple(self.mobjects))
        self.add(*preserved, *stable_rows)
        self._write_rows(rows_to_write)

    def _make_qed(self, block: VGroup) -> MathTex:
        square = _safe_mathtex(r"\square", font_size=32, color=CHALK)
        square.next_to(block, RIGHT, buff=0.28).align_to(block, DOWN)
        return square

    def _write_qed(self, square: MathTex) -> None:
        self.play(
            Write(square, lag_ratio=0.2),
            run_time=1.2,
        )


class ProofSegmentScene(ProofScene):
    """Render one independently cacheable transition of a proof timeline."""

    def __init__(
        self,
        movie: Movie,
        segment_index: int,
        chars_per_second: float,
        transition_seconds: float = 0.65,
        **kwargs,
    ) -> None:
        self.segment_index = segment_index
        super().__init__(
            movie=movie,
            chars_per_second=chars_per_second,
            transition_seconds=transition_seconds,
            audio=None,
            **kwargs,
        )

    def construct(self) -> None:
        self.camera.background_color = BLACKBOARD
        frames = tuple(frame for frame in self.movie.semantic_frames() if frame.display_goals)
        if not frames or not 0 <= self.segment_index < len(frames):
            return

        start = max(0, self.segment_index - 3)
        window_frames = frames[start : self.segment_index + 1]
        blocks = [self._step_block(frame) for frame in window_frames]
        # Only the latest settled state of a continuing lineage is visible.
        # Keep older entries solely when they really are separate board blocks.
        history = []
        for block in blocks[:-1]:
            if history and _continues_visual_block(history[-1], block):
                history[-1] = block
            else:
                history.append(block)
        blocks = [*history, blocks[-1]]
        self._layout_segment_window(blocks)
        current = blocks[-1]
        is_final = self.segment_index == len(frames) - 1

        if self.segment_index == 0:
            focus = VGroup(current, self._make_qed(current)) if is_final else current
            width = self._camera_width_for(focus)
            self._camera_frame().set_width(width).move_to(
                self._camera_center_for(focus, width)
            )
            reveal = _glyph_reveal(current)
            self.play(reveal, run_time=self._write_time(current))
            self.remove(reveal.mobject)
            self.add(current)
            self.wait(self.settle_seconds)
        else:
            history = blocks[:-1]
            for block in history:
                self.add(block)
            previous = history[-1]
            start_width = self._camera_width_for(previous)
            self._camera_frame().set_width(start_width).move_to(
                self._camera_center_for(previous, start_width)
            )

            target = current
            clean = current.copy()
            source = previous.copy()
            continues_block = _continues_visual_block(previous, target)
            if continues_block:
                self.remove(previous)
                self._shift_block_top_left_to(
                    source,
                    target.get_left()[0],
                    target.get_top()[1],
                )
            else:
                self._shift_block_to(
                    source,
                    target.get_left()[0],
                    target.get_center()[1],
                )
            end_width = self._camera_width_for(target)
            end_center = self._camera_center_for(target, end_width)
            animations, new_rows = self._row_transition_parts(source, target)
            stable_overlays = [
                row.copy() for row in _stable_visual_rows(source, target)
            ]
            self.add(source, *stable_overlays)
            self.play(
                *animations,
                self._camera_frame().animate.move_to(end_center).set_width(end_width),
                run_time=self.transition_seconds,
            )
            preserved = history[:-1] if continues_block else history
            self._write_new_rows_on_clean_board(clean, new_rows, preserved)
            self.remove(*tuple(self.mobjects))
            self.add(*preserved, clean)
            current = clean

        if is_final:
            qed = self._make_qed(current)
            focus = VGroup(current, qed)
            width = self._camera_width_for(focus)
            self._camera_frame().set_width(width).move_to(
                self._camera_center_for(focus, width)
            )
            self._write_qed(qed)
            self.wait(0.8)
        else:
            # Encode one fully settled frame so independently concatenated
            # segments meet on the completed mathematical state.
            self.wait(1 / config.frame_rate)

    def _layout_segment_window(self, blocks: list[VGroup]) -> None:
        """Lay out continuations in place and genuine new blocks below them."""
        if not blocks:
            return
        self._shift_block_to(blocks[0], left_x=0, center_y=0)
        for previous, block in zip(blocks, blocks[1:], strict=False):
            if _continues_visual_block(previous, block):
                self._shift_block_top_left_to(
                    block,
                    previous.get_left()[0],
                    previous.get_top()[1],
                )
            else:
                self._place_next(block, previous)

        # Segment hashes must not depend on how many earlier states the local
        # reconstruction window contains.
        final = blocks[-1]
        delta = [-final.get_left()[0], -final.get_center()[1], 0]
        for block in blocks:
            block.shift(delta)


class ProofChunkScene(ProofScene):
    """Render many consecutive logical segments in one long-lived scene."""

    def __init__(
        self,
        movie: Movie,
        start_index: int,
        end_index: int,
        chars_per_second: float,
        transition_seconds: float = 0.65,
        **kwargs,
    ) -> None:
        self.start_index = start_index
        self.end_index = end_index
        super().__init__(
            movie=movie,
            chars_per_second=chars_per_second,
            transition_seconds=transition_seconds,
            audio=None,
            **kwargs,
        )

    def construct(self) -> None:
        self.camera.background_color = BLACKBOARD
        frames = tuple(frame for frame in self.movie.semantic_frames() if frame.display_goals)
        if not frames:
            return
        start = max(0, min(self.start_index, len(frames)))
        end = max(start, min(self.end_index, len(frames)))
        if start == end:
            return

        if start == 0:
            current = self._step_block(frames[0])
            self._place_next(current, None)
            width = self._camera_width_for(current)
            self._camera_frame().set_width(width).move_to(
                self._camera_center_for(current, width)
            )
            reveal = _glyph_reveal(current)
            self.play(reveal, run_time=self._write_time(current))
            self.remove(reveal.mobject)
            self.add(current)
            self.wait(self.settle_seconds)
            board_history: list[VGroup] = [current]
            target_indices = range(1, end)
        else:
            window = frames[max(0, start - 3) : start]
            blocks = [self._step_block(frame) for frame in window]
            history: list[VGroup] = []
            for block in blocks:
                if history and _continues_visual_block(history[-1], block):
                    history[-1] = block
                else:
                    history.append(block)
            self._layout_chunk_window(history)
            board_history = history
            current = board_history[-1]
            self.add(*board_history)
            width = self._camera_width_for(current)
            self._camera_frame().set_width(width).move_to(
                self._camera_center_for(current, width)
            )
            target_indices = range(start, end)

        for index in target_indices:
            frame = frames[index]
            target = self._step_block(frame)
            source = current.copy()
            continues_block = _continues_visual_block(current, target)
            if continues_block:
                self._place_continuation_pair(source, target, current)
                self.remove(current)
            else:
                self._place_transition_pair(source, target, current)
            target_left = target.get_left()[0]
            target_center_y = target.get_center()[1]
            self.add(source)

            camera_width = self._camera_width_for(target)
            camera_center = self._camera_center_for(target, camera_width)
            animations, new_rows = self._row_transition_parts(source, target)
            overlays = [row.copy() for row in _stable_visual_rows(source, target)]
            self.add(*overlays)
            self.play(
                *animations,
                self._camera_frame().animate.move_to(camera_center).set_width(camera_width),
                run_time=self.transition_seconds,
            )

            clean = self._step_block(frame)
            self._shift_block_to(clean, target_left, target_center_y)
            preserved = board_history[:-1] if continues_block else board_history
            self._write_new_rows_on_clean_board(clean, new_rows, preserved)
            self.remove(*tuple(self.mobjects))
            self.add(*preserved, clean)
            current = clean
            if continues_block:
                board_history[-1] = current
            else:
                board_history.append(current)
            if len(board_history) > 3:
                self.remove(board_history.pop(0))

            if index == len(frames) - 1:
                qed = self._make_qed(current)
                focus = VGroup(current, qed)
                width = self._camera_width_for(focus)
                self._camera_frame().set_width(width).move_to(
                    self._camera_center_for(focus, width)
                )
                self._write_qed(qed)
                self.wait(0.8)

        if end < len(frames):
            self.wait(1 / config.frame_rate)

    def _layout_chunk_window(self, blocks: list[VGroup]) -> None:
        if not blocks:
            return
        self._shift_block_to(blocks[0], left_x=0, center_y=0)
        for previous, block in zip(blocks, blocks[1:], strict=False):
            if _continues_visual_block(previous, block):
                self._shift_block_top_left_to(
                    block,
                    previous.get_left()[0],
                    previous.get_top()[1],
                )
            else:
                self._place_next(block, previous)
        final = blocks[-1]
        delta = [-final.get_left()[0], -final.get_center()[1], 0]
        for block in blocks:
            block.shift(delta)
