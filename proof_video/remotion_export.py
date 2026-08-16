from __future__ import annotations

import math
import re
from collections.abc import Callable
from itertools import accumulate
from typing import Any

from proof_video.animation.latex import _latex_matching_token_spans
from proof_video.animation.semantic import _semantic_transition_plan
from proof_video.models import Movie
from proof_video.rendering.pacing import (
    DEFAULT_VISIBLE_GLYPHS_PER_SECOND,
    cinematic_edge_action_count,
    minimum_visible_action_frames,
    proof_action_pacing,
)


_VISUAL_ROW_UNITS = 52


def _visible_token_units(token: str) -> int:
    compact = re.sub(
        r"\\(?:mathbb|mathrm|mathbf|mathit|operatorname)\s*\{([^{}]*)\}",
        r"\1",
        token,
    )
    compact = re.sub(r"\\(?:left|right|quad|qquad)\b|\\[,;!]", "", compact)
    compact = re.sub(r"\\[A-Za-z]+", "x", compact)
    compact = compact.replace("{", "").replace("}", "")
    return max(1, len(compact))


def _visual_token_chunks(
    tokens: list[tuple[str, int, int]],
    maximum_units: int = _VISUAL_ROW_UNITS,
) -> tuple[tuple[int, int], ...]:
    """Balance a long formula over shallow logical breakpoints.

    This changes only presentation rows. Token order, original LaTeX spans and
    the certified semantic transition plan stay untouched.
    """

    if not tokens:
        return ()
    units = [_visible_token_units(token) for token, _start, _end in tokens]
    total = sum(units)
    # A single atomic token can itself be wider than the preferred visual row
    # (for example a long imported declaration name).  Such a token cannot be
    # split without corrupting its certified source span, so never request
    # more chunks than there are token boundaries.
    line_count = min(len(tokens), max(1, math.ceil(total / maximum_units)))
    if line_count == 1:
        return ((0, len(tokens)),)

    depths = [0]
    depth = 0
    for token, _start, _end in tokens:
        if token in {r"\right)", r"\right]", ")", "]"}:
            depth = max(0, depth - 1)
        elif token in {r"\left(", r"\left[", "(", "["}:
            depth += 1
        depths.append(depth)

    prefix = [0]
    for value in units:
        prefix.append(prefix[-1] + value)
    strong = {
        r"\implies", r"\Rightarrow", r"\iff", r"\Leftrightarrow",
        r"\Longleftrightarrow", "\u21d4", "\u2194",
    }
    medium = {",", ";", "=", "<", ">", r"\le", r"\leq", r"\ge", r"\geq"}
    chunks: list[tuple[int, int]] = []
    start = 0
    for line_index in range(line_count - 1):
        remaining_lines = line_count - line_index
        remaining_units = prefix[-1] - prefix[start]
        ideal = remaining_units / remaining_lines
        latest = len(tokens) - (remaining_lines - 1)
        candidates = []
        for boundary in range(start + 1, latest + 1):
            line_units = prefix[boundary] - prefix[start]
            if line_units > maximum_units * 1.2:
                break
            previous = tokens[boundary - 1][0]
            following = tokens[boundary][0] if boundary < len(tokens) else ""
            logical_bonus = (
                ideal * 0.34
                if previous in strong or following in strong
                else ideal * 0.16
                if previous in medium
                else 0
            )
            score = (
                abs(line_units - ideal)
                + depths[boundary] * ideal * 0.18
                - logical_bonus
            )
            candidates.append((score, boundary))
        boundary = min(candidates)[1] if candidates else start + 1
        chunks.append((start, boundary))
        start = boundary
    chunks.append((start, len(tokens)))
    return tuple(chunks)


def build_remotion_timeline(
    movie: Movie,
    *,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    chars_per_second: float = DEFAULT_VISIBLE_GLYPHS_PER_SECOND,
    max_duration: float | None = None,
    preview_seconds: float | None = None,
    preview_tail_seconds: float | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Export a renderer-neutral, proof-certified visual timeline."""

    if fps <= 0:
        raise ValueError("fps must be greater than zero")
    if chars_per_second <= 0:
        raise ValueError("chars_per_second must be greater than zero")
    # Writing density may be arbitrarily high. The only hard visual invariant
    # is that every certified proof step receives a visible frame interval.
    effective_write_speed = chars_per_second
    cruise_step_frames = max(
        minimum_visible_action_frames(fps),
        math.ceil(
            (fps / 3.0)
            * DEFAULT_VISIBLE_GLYPHS_PER_SECOND
            / effective_write_speed
        ),
    )
    initial_frames = max(1, round(1.0 * fps))
    # Keep the previously requested long, animated QED celebration. It is not
    # used to calibrate the proof to any fixed total duration.
    celebration_frames = max(1, round(5.0 * fps))
    # Once the actual end of the proof is visible, leave the completed board
    # untouched for three additional seconds after the QED/wave animation.
    # An opening preview does not reach QED and therefore keeps its old timing.
    completion_hold_frames = (
        max(1, round(3.0 * fps))
        if preview_seconds is None or preview_tail_seconds is not None
        else 0
    )
    final_hold_frames = celebration_frames + completion_hold_frames
    all_frames = tuple(frame for frame in movie.semantic_frames() if frame.display_goals)
    frames = all_frames
    if preview_seconds is not None and preview_tail_seconds is not None:
        raise ValueError("Choose either an opening preview or a tail preview, not both.")
    preview_window = preview_seconds or preview_tail_seconds
    if preview_window is not None:
        preview_budget = max(0, int(preview_window * fps) - final_hold_frames)
        if preview_tail_seconds is not None:
            # A long cinematic closing has a fixed total budget. Looking only
            # for the first candidate that fits would therefore collapse the
            # tail preview to its final still. Keep the complete closing arc;
            # the frame budget below compresses it proportionally when needed.
            preview_state_count = min(
                len(all_frames),
                cinematic_edge_action_count(
                    fps=fps, cruise_frames=cruise_step_frames
                ) + 1,
            )
        else:
            preview_state_count = 1
            for candidate_count in range(2, len(all_frames) + 1):
                candidate_pacing = proof_action_pacing(
                    candidate_count - 1,
                    fps=fps,
                    slow_opening=True,
                    slow_closing=False,
                    cruise_frames=cruise_step_frames,
                )
                if initial_frames + candidate_pacing.total_frames > preview_budget:
                    # Opening previews include the transition crossing the time
                    # boundary and shorten only that selected pacing window.
                    preview_state_count = candidate_count
                    break
                preview_state_count = candidate_count
        frames = (
            frames[-preview_state_count:]
            if preview_tail_seconds is not None
            else frames[:preview_state_count]
        )
    states: list[dict[str, Any]] = []
    state_token_data: list[tuple[list[tuple[int, int]], list[str]]] = []
    if on_progress is not None:
        on_progress(0, len(frames))

    for state_index, frame in enumerate(frames):
        goal = frame.display_goals[0]
        row_sources = [
            (f"hyp-{hypothesis.key or hypothesis.name}", "context", hypothesis.render_latex())
            for hypothesis in goal.latex_context
        ]
        row_sources.append(("target", "target", rf"\vdash\;{goal.latex_target or goal.state}"))
        rows = []
        global_spans: list[tuple[int, int]] = []
        token_texts: list[str] = []
        cursor = 0
        for key, kind, latex in row_sources:
            tokens = _latex_matching_token_spans(latex)
            chunks = _visual_token_chunks(tokens)
            for chunk_index, (first, after) in enumerate(chunks):
                chunk = tokens[first:after]
                chunk_start = chunk[0][1]
                chunk_end = chunk[-1][2]
                rows.append(
                    {
                        "key": (
                            key
                            if len(chunks) == 1
                            else f"{key}-wrap-{chunk_index}"
                        ),
                        "kind": kind,
                        "latex": latex[chunk_start:chunk_end],
                        "globalStart": cursor + chunk_start,
                        "tokens": [
                            [token, start - chunk_start, end - chunk_start]
                            for token, start, end in chunk
                        ],
                    }
                )
            global_spans.extend((cursor + start, cursor + end) for _token, start, end in tokens)
            token_texts.extend(token for token, _start, _end in tokens)
            cursor += len(latex) + 1
        state_token_data.append((global_spans, token_texts))
        states.append(
            {
                "id": f"state-{state_index}",
                "proofFrameIndex": frame.index,
                "tactic": frame.tactic,
                "lineageId": goal.lineage_id,
                "rows": rows,
            }
        )
        if on_progress is not None:
            on_progress(state_index + 1, len(frames))

    transition_plans: list[dict[str, Any] | None] = []
    for state_index in range(1, len(states)):
        source_spans, source_texts = state_token_data[state_index - 1]
        target_spans, target_texts = state_token_data[state_index]
        goal = frames[state_index].display_goals[0]
        plan = _semantic_transition_plan(
            source_spans,
            source_texts,
            target_spans,
            target_texts,
            goal.semantic_transition,
        )
        plan_payload = None
        if plan is not None and plan.valid:
            plan_payload = {
                "pairs": [
                    [pair.source, pair.target, 1 if candidate.role.value == "copy" else 0]
                    for candidate in plan.selected
                    for pair in candidate.pairs
                ],
                "created": list(plan.created_targets),
                "deleted": list(plan.deleted_sources),
            }
        transition_plans.append(plan_payload)

    transition_count = len(transition_plans)
    reaches_proof_end = bool(
        frames and all_frames and frames[-1] is all_frames[-1]
    )
    duration_ceiling = preview_window if preview_window is not None else max_duration
    available = (
        int(duration_ceiling * fps) - initial_frames - final_hold_frames
        if duration_ceiling is not None
        else None
    )
    step_pacing = proof_action_pacing(
        transition_count,
        fps=fps,
        slow_opening=preview_tail_seconds is None,
        slow_closing=reaches_proof_end,
        frame_budget=max(0, available) if available is not None else None,
        cruise_frames=cruise_step_frames,
    )
    durations = list(step_pacing.durations)
    phases = step_pacing.phases
    # The step clock is authoritative for all visible activity. Opening and
    # closing steps therefore move and write more deliberately, while cruise
    # steps do both quickly. Sharing the complete interval prevents a finished
    # transform from leaving a temporal hole before handwriting catches up.
    active_ends = tuple(1.0 for _ in durations)
    move_ends = active_ends
    write_starts = tuple(0.0 for _ in durations)
    write_ends = active_ends

    transition_starts = tuple(accumulate(durations, initial=initial_frames))
    transitions = [
        {
            "fromState": index,
            "toState": index + 1,
            "startFrame": transition_starts[index],
            "durationFrames": durations[index],
            "pacing": phases[index],
            "moveEnd": move_ends[index],
            "writeStart": write_starts[index],
            "writeEnd": write_ends[index],
            # The authoritative Lean graph remains in the trace and strict
            # audit. The browser consumes only this validated executable plan.
            "semantic": None,
            "plan": transition_plans[index],
        }
        for index in range(transition_count)
    ]
    transition_frames = cruise_step_frames

    duration = (
        initial_frames
        + sum(durations)
        + final_hold_frames
    )
    return {
        "schemaVersion": 1,
        "rendererContract": "strict-proof-transition-v1",
        "theorem": movie.theorem_name,
        "width": width,
        "height": height,
        "fps": fps,
        "durationInFrames": duration,
        "initialFrames": initial_frames,
        "transitionFrames": transition_frames,
        "writeSpeed": effective_write_speed,
        "pacingProfile": "ten-second-endpoint-plateaus-v14",
        "celebrationFrames": celebration_frames,
        "completionHoldFrames": completion_hold_frames,
        "showQed": bool(frames and all_frames and frames[-1] is all_frames[-1]),
        "edgeReasons": [],
        "states": states,
        "transitions": transitions,
    }
