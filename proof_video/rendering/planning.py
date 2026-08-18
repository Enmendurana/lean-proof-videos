"""Renderer selection, duration planning, and content-addressed cache keys."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from proof_video.cache import file_digest, stable_hash
from proof_video.proof.schema import Frame, Goal, IndexMaps
from proof_video.rendering.pacing import maximum_visible_write_speed

_MODULE_ROOT = Path(__file__).parents[1]

def _segment_key(
    frames: tuple[Frame, ...],
    index: int,
    chars_per_second: float,
    transition_seconds: float,
    width: int,
    height: int,
    fps: int,
    renderer: str,
) -> str:
    scene_path = _MODULE_ROOT / "scene.py"
    model_path = _MODULE_ROOT / "models.py"
    sympy_path = _MODULE_ROOT / "sympy_matching.py"
    window = frames[max(0, index - 3) : index + 1]
    return stable_hash(
        "proof-segment-v3",
        [_frame_payload(frame) for frame in window],
        index == len(frames) - 1,
        {
            "charsPerSecond": round(chars_per_second, 6),
            "transitionSeconds": transition_seconds,
        },
        {"width": width, "height": height, "fps": fps, "renderer": renderer},
        file_digest([scene_path, model_path, sympy_path]),
    )

def _chunk_key(
    frames: tuple[Frame, ...],
    start: int,
    end: int,
    chars_per_second: float,
    transition_seconds: float,
    width: int,
    height: int,
    fps: int,
) -> str:
    scene_path = _MODULE_ROOT / "scene.py"
    model_path = _MODULE_ROOT / "models.py"
    sympy_path = _MODULE_ROOT / "sympy_matching.py"
    # Earlier context is part of the first settled board at a chunk boundary.
    window = frames[max(0, start - 3) : end]
    return stable_hash(
        "proof-chunk-v1",
        [_frame_payload(frame) for frame in window],
        {"start": start, "end": end, "total": len(frames)},
        {
            "charsPerSecond": round(chars_per_second, 6),
            "transitionSeconds": transition_seconds,
        },
        {"width": width, "height": height, "fps": fps, "renderer": "cairo"},
        file_digest([scene_path, model_path, sympy_path]),
    )

def _full_key(
    frames: tuple[Frame, ...],
    chars_per_second: float,
    transition_seconds: float,
    width: int,
    height: int,
    fps: int,
    renderer: str,
) -> str:
    scene_path = _MODULE_ROOT / "scene.py"
    model_path = _MODULE_ROOT / "models.py"
    latex_path = _MODULE_ROOT / "latex.py"
    sympy_path = _MODULE_ROOT / "sympy_matching.py"
    render_path = _MODULE_ROOT / "render.py"
    return stable_hash(
        "proof-full-v1",
        [_frame_payload(frame) for frame in frames],
        {
            "charsPerSecond": round(chars_per_second, 6),
            "transitionSeconds": transition_seconds,
        },
        {"width": width, "height": height, "fps": fps, "renderer": renderer},
        file_digest([scene_path, model_path, latex_path, sympy_path, render_path]),
    )

def _frame_payload(frame: Frame) -> dict[str, Any]:
    return {
        "tactic": frame.tactic,
        "goals": [_goal_payload(goal) for goal in frame.display_goals],
    }

def effective_write_speed(
    frames: tuple[Frame, ...],
    *,
    requested: float,
    max_duration: float | None,
    transition_seconds: float,
    fps: int,
) -> float:
    """Plan typing speed without making an entire proof step disappear."""
    visible_speed_cap = maximum_visible_write_speed(fps)
    requested = min(requested, visible_speed_cap)
    if max_duration is None:
        return requested
    fixed_duration = (
        max(0, len(frames) - 1) * transition_seconds
        + len(frames) / fps
        + 2.0  # final QED plus closing hold
    )
    if fixed_duration >= max_duration:
        raise SystemExit(
            "--max-duration is too short for fixed camera/formula transitions; "
            "increase the limit instead of accelerating block changes."
        )
    typing_budget = max_duration - fixed_duration
    required = estimate_new_glyphs(frames) / typing_budget
    if required > visible_speed_cap:
        raise SystemExit(
            "--max-duration would require writing faster than the configured "
            f"visible-density ceiling ({visible_speed_cap:.0f} glyphs/second at "
            f"{fps} FPS); increase or remove the duration limit."
        )
    return max(requested, required)

def estimate_new_glyphs(frames: tuple[Frame, ...]) -> int:
    """Conservatively count text introduced as entirely new semantic rows."""
    previous_keys: set[tuple[str, str]] = set()
    total = 0
    for frame in frames:
        rows = _semantic_rows(frame)
        for key, latex in rows.items():
            if key not in previous_keys:
                total += _estimated_visible_characters(latex)
        previous_keys = set(rows)
    return max(1, total)

def _semantic_rows(frame: Frame) -> dict[tuple[str, str], str]:
    rows: dict[tuple[str, str], str] = {}
    for goal_index, goal in enumerate(frame.display_goals[:3]):
        lineage = goal.lineage_id or f"goal-{goal_index}"
        for hypothesis in goal.latex_context:
            semantic_key = hypothesis.key or hypothesis.name
            rows[(lineage, f"hyp-{semantic_key}")] = hypothesis.render_latex()
        rows[(lineage, "target")] = goal.latex_target or goal.state
    return rows

def _estimated_visible_characters(source: str) -> int:
    # LaTeX commands usually produce one visible symbol; braces and spacing
    # commands produce none. Over-counting identifiers is intentional because
    # this estimate protects the maximum-duration ceiling.
    commands = len(re.findall(r"\\[A-Za-z]+", source))
    without_commands = re.sub(r"\\[A-Za-z]+|[{}\\\s]", "", source)
    return max(1, commands + len(without_commands))

def _goal_payload(goal: Goal) -> dict[str, Any]:
    return {
        "lineage": goal.lineage_id,
        "parent": goal.parent_goal_id,
        "latex": goal.latex_state(),
        "latexMaps": _map_payload(goal.latex_index_maps),
        "semanticTransition": _semantic_transition_payload(goal.semantic_transition),
    }

def _semantic_transition_payload(transition):
    if transition is None:
        return None

    def node_payload(node):
        return {
            "id": node.node_id,
            "kind": node.kind,
            "identity": node.identity,
            "fingerprint": node.fingerprint,
            "parentId": node.parent_id,
            "path": node.path,
            "spans": tuple((span.start, span.end) for span in node.latex_spans),
        }

    return {
        "sourceNodes": tuple(node_payload(node) for node in transition.source.nodes),
        "targetNodes": tuple(node_payload(node) for node in transition.target.nodes),
        "edges": tuple(
            (edge.source_node_id, edge.target_node_id, edge.reason, edge.confidence)
            for edge in transition.edges
        ),
        "proofKind": transition.proof_kind,
        "adapter": transition.adapter,
        "proofFingerprint": transition.proof_fingerprint,
        "proofTerm": transition.proof_term,
        "proofDescendants": transition.proof_descendants,
        "proofPremises": transition.proof_premises,
        "proofConstants": transition.proof_constants,
        "goalDiff": (
            {
                "sourceGoalId": transition.goal_diff.source_goal_id,
                "targetGoalId": transition.goal_diff.target_goal_id,
                "sourceChangedPaths": transition.goal_diff.source_changed_paths,
                "targetChangedPaths": transition.goal_diff.target_changed_paths,
            }
            if transition.goal_diff is not None
            else None
        ),
        "fallbackReason": transition.fallback_reason,
    }

def _map_payload(index_maps: IndexMaps | None):
    if index_maps is None:
        return None
    return {
        "sourceToTarget": index_maps.source_to_target,
        "targetToSource": index_maps.target_to_source,
    }

def _preview_indices(frame_count: int) -> tuple[int, ...]:
    return tuple(sorted({0, frame_count // 2, frame_count - 1}))

def _resolve_renderer(requested: str) -> str:
    if requested != "auto":
        return requested
    try:
        import moderngl

        context = moderngl.create_standalone_context()
        context.release()
        return "opengl"
    except Exception:
        return "cairo"

def _opengl_safe_for_frame(frame: Frame) -> bool:
    """Avoid known driver stalls on unusually dense simultaneous goals."""
    return sum(len(goal.latex_state()) for goal in frame.display_goals) <= 360

def _opengl_safe_for_movie(frames: tuple[Frame, ...]) -> bool:
    return all(_opengl_safe_for_frame(frame) for frame in frames)
