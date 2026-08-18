from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from hashlib import sha256
from itertools import accumulate
from typing import Any

from proof_video.animation.latex import _latex_matching_token_spans
from proof_video.animation.semantic import (
    _semantic_transition_plan,
    _tokens_in_semantic_spans,
)
from proof_video.models import Movie
from proof_video.proof.schema import SemanticTransition
from proof_video.rendering.pacing import (
    DEFAULT_VISIBLE_GLYPHS_PER_SECOND,
    cinematic_edge_action_count,
    minimum_visible_action_frames,
    proof_action_pacing,
)
from proof_video.transition_plan import (
    TokenPair,
    TransitionCandidate,
    TransitionPlan,
    TransitionRole,
    solve_transition_plan,
)


_VISUAL_ROW_UNITS = 52


@dataclass(frozen=True)
class _NativeRow:
    """One row in Lean's unmodified goal state."""

    row_key: str
    kind: str
    latex: str
    stable_identity: str | None
    tokens: tuple[tuple[str, int, int], ...]
    first_token: int


@dataclass(frozen=True)
class _StateTokenData:
    native_spans: list[tuple[int, int]]
    native_texts: list[str]
    native_to_display: dict[int, int]
    display_texts: list[str]
    stable_rows: dict[str, tuple[str, tuple[str, ...], tuple[int, ...]]]
    conclusion_row_indices: tuple[int, ...]
    conclusion_formula_indices: tuple[int, ...]
    carried_formula_indices: tuple[int, ...]
    carried_formula_texts: tuple[str, ...]


@dataclass(frozen=True)
class _PresentationEntry:
    """One visual state and the certified transition that enters it."""

    frame: Any
    goal: Any
    transition_goal: Any | None
    tactic: str


@dataclass
class _PresentationStateBuilder:
    rows: list[dict[str, Any]]
    display_texts: list[str]
    native_to_display: dict[int, int]
    stable_rows: dict[str, tuple[str, tuple[str, ...], tuple[int, ...]]]
    cursor: int = 0

    def append_row(
        self,
        key: str,
        kind: str,
        latex: str,
        stable_identity: str | None,
        native_row: _NativeRow | None = None,
        native_part_start: int | None = None,
    ) -> tuple[int, ...]:
        tokens = _latex_matching_token_spans(latex)
        first_display_token = len(self.display_texts)
        chunks = _visual_token_chunks(tokens)
        for chunk_index, (first, after) in enumerate(chunks):
            chunk = tokens[first:after]
            chunk_start = chunk[0][1]
            chunk_end = chunk[-1][2]
            self.rows.append(
                {
                    "key": key if len(chunks) == 1 else f"{key}-wrap-{chunk_index}",
                    "kind": kind,
                    "latex": latex[chunk_start:chunk_end],
                    "globalStart": self.cursor + chunk_start,
                    "tokens": [
                        [token, start - chunk_start, end - chunk_start]
                        for token, start, end in chunk
                    ],
                }
            )
        self.display_texts.extend(token for token, _start, _end in tokens)
        if native_row is not None and native_part_start is not None:
            native_part = native_row.tokens[
                native_part_start : native_part_start + len(tokens)
            ]
            if tuple(token for token, _start, _end in native_part) == tuple(
                token for token, _start, _end in tokens
            ):
                for offset in range(len(tokens)):
                    self.native_to_display[
                        native_row.first_token + native_part_start + offset
                    ] = first_display_token + offset
        if stable_identity is not None and tokens:
            self.stable_rows[stable_identity] = (
                kind,
                tuple(token for token, _start, _end in tokens),
                tuple(range(first_display_token, first_display_token + len(tokens))),
            )
        self.cursor += len(latex) + 1
        return tuple(range(first_display_token, first_display_token + len(tokens)))


def _carried_conclusion_latex(previous_goal: Any, current_goal: Any) -> str | None:
    """Keep only an explicitly certified ``calc`` parent on the board.

    Earlier presentation code carried every non-rewrite conclusion forward.
    That incorrectly turned ordinary conclusions such as ``P → False`` into
    apparent hypotheses of the next inference. A previous conclusion is a
    separate proof row only when the extractor identifies the transition as a
    ``calc`` obligation; all other transitions are rendered in place from
    their certified semantic edges.
    """

    if not _transition_has_actual_visual_source(previous_goal, current_goal):
        return None
    previous = previous_goal.latex_target or previous_goal.state
    current = current_goal.latex_target or current_goal.state
    if not previous or previous == current:
        return None
    transition = current_goal.semantic_transition
    if not isinstance(transition, SemanticTransition):
        # Without the extractor's certified goal edge this would be only a
        # visual guess, so older traces keep their conservative behavior.
        return None
    if transition.adapter != "calc":
        return None
    return previous


def _transition_has_actual_visual_source(
    previous_goal: Any | None, current_goal: Any
) -> bool:
    """Whether a Lean transition starts at the goal visible in the prior frame.

    A tactic such as ``calc`` may create several sibling goals at once.  Each
    sibling transition is certified from their common parent, not from the
    sibling that happens to be rendered immediately before it.  Reusing that
    stale transition is precisely what made equal ``f`` glyphs jump between
    unrelated expressions.  Proof-term sequents have no ``parent_goal_id``
    because their transitions are already emitted in linear proof-DAG order.
    """

    parent_goal_id = current_goal.parent_goal_id
    transition = current_goal.semantic_transition
    goal_diff = (
        transition.goal_diff
        if isinstance(transition, SemanticTransition)
        else None
    )
    if goal_diff is not None:
        return (
            previous_goal is not None
            and goal_diff.source_goal_id == previous_goal.goal_id
            and goal_diff.target_goal_id == current_goal.goal_id
        )
    return (
        previous_goal is not None
        and (parent_goal_id is None or parent_goal_id == previous_goal.goal_id)
    )


def _remap_semantic_plan(
    plan: TransitionPlan | None,
    *,
    source_map: dict[int, int],
    target_map: dict[int, int],
    source_count: int,
    target_count: int,
) -> TransitionPlan | None:
    """Move a Lean-native plan onto the expanded presentation board."""

    if plan is None or not plan.valid:
        return plan
    candidates: list[TransitionCandidate] = []
    for candidate in plan.selected:
        pairs = tuple(
            TokenPair(source_map[pair.source], target_map[pair.target])
            for pair in candidate.pairs
            if pair.source in source_map and pair.target in target_map
        )
        if pairs:
            candidates.append(replace(candidate, pairs=pairs))
    return TransitionPlan(
        source_count=source_count,
        target_count=target_count,
        selected=tuple(candidates),
        created_targets=tuple(
            target_map[target]
            for target in plan.created_targets
            if target in target_map
        ),
        deleted_sources=tuple(
            source_map[source]
            for source in plan.deleted_sources
            if source in source_map
        ),
        valid=True,
        errors=plan.errors,
        rejected_candidates=plan.rejected_candidates,
    )


def _token_subsequence_start(
    whole: tuple[tuple[str, int, int], ...],
    part: tuple[tuple[str, int, int], ...],
) -> int | None:
    """Return the unique token-level occurrence of ``part`` in ``whole``."""

    if not part or len(part) > len(whole):
        return None
    part_text = tuple(token for token, _start, _end in part)
    matches = [
        start
        for start in range(len(whole) - len(part) + 1)
        if tuple(
            token for token, _begin, _end in whole[start : start + len(part)]
        )
        == part_text
    ]
    return matches[0] if len(matches) == 1 else None


def _merge_stable_proof_rows(
    base_plan: TransitionPlan | None,
    source_count: int,
    target_count: int,
    source_rows: dict[str, tuple[str, tuple[str, ...], tuple[int, ...]]],
    target_rows: dict[str, tuple[str, tuple[str, ...], tuple[int, ...]]],
    semantic_transition: SemanticTransition | None,
    mandatory_candidates: tuple[TransitionCandidate, ...] = (),
) -> TransitionPlan | None:
    """Add exact, durable proof-row identities to a semantic token plan.

    Lean semantic edges describe the part changed by the current tactic. They
    intentionally need not repeat every untouched local declaration. The
    renderer must nevertheless keep those declarations on the board. A row is
    eligible here only when its durable Lean identity, rendered kind and full
    token sequence are all unchanged. This is proof-state identity, not a
    same-glyph heuristic.

    The target row uses the goal lineage as its durable identity. Hypothesis
    rows are included only when the trace supplied a stable key or a transition
    edge proves the same Lean free-variable identity on both sides; a display
    name alone is deliberately insufficient.
    """

    source_rows = dict(source_rows)
    target_rows = dict(target_rows)
    if isinstance(semantic_transition, SemanticTransition):
        source_nodes = {
            node.node_id: node for node in semantic_transition.source.nodes
        }
        target_nodes = {
            node.node_id: node for node in semantic_transition.target.nodes
        }
        for edge in semantic_transition.edges:
            source_node = source_nodes.get(edge.source_node_id)
            target_node = target_nodes.get(edge.target_node_id)
            if source_node is None or target_node is None:
                continue
            if (
                source_node.kind != "declaration"
                or target_node.kind != "declaration"
                or not source_node.identity
                or source_node.identity != target_node.identity
                or len(source_node.path) < 3
                or len(target_node.path) < 3
                or source_node.path[0] != "context"
                or target_node.path[0] != "context"
                or source_node.path[2] != "name"
                or target_node.path[2] != "name"
                or edge.reason not in {
                    "same-identity",
                    "same-proof-context",
                    "verified-stable-declaration",
                }
            ):
                continue
            source_slot = source_rows.get(f"context-slot:{source_node.path[1]}")
            target_slot = target_rows.get(f"context-slot:{target_node.path[1]}")
            if source_slot is None or target_slot is None:
                continue
            durable_identity = f"hypothesis:{source_node.identity}"
            source_rows[durable_identity] = source_slot
            target_rows[durable_identity] = target_slot

    stable_candidates: list[TransitionCandidate] = list(mandatory_candidates)
    for identity in sorted(source_rows.keys() & target_rows.keys()):
        source_kind, source_tokens, source_indices = source_rows[identity]
        target_kind, target_tokens, target_indices = target_rows[identity]
        if (
            source_kind != target_kind
            or source_tokens != target_tokens
            or len(source_indices) != len(target_indices)
            or not source_indices
        ):
            continue
        stable_candidates.append(
            TransitionCandidate(
                candidate_id=f"stable-proof-row:{identity}",
                source_node_id=f"stable-proof-row:{identity}",
                target_node_id=f"stable-proof-row:{identity}",
                role=TransitionRole.PRESERVE,
                reason="verified-stable-proof-row",
                pairs=tuple(
                    TokenPair(source, target)
                    for source, target in zip(
                        source_indices, target_indices, strict=True
                    )
                ),
                certified=True,
                exact_composite=True,
                source_kind="proof-row",
                target_kind="proof-row",
            )
        )

    protected_sources = {
        pair.source for candidate in stable_candidates for pair in candidate.pairs
    }
    protected_targets = {
        pair.target for candidate in stable_candidates for pair in candidate.pairs
    }
    candidates: list[TransitionCandidate] = []
    if base_plan is not None and base_plan.valid:
        for candidate in base_plan.selected:
            if candidate.target_indices & protected_targets:
                # The unchanged proof row owns these target tokens. An
                # overlapping lower-level semantic edge would make the same
                # on-board object blink or be drawn twice.
                continue
            if (
                candidate.role != TransitionRole.COPY
                and candidate.source_indices & protected_sources
            ):
                # A premise that remains in the next proof state can feed a
                # new conclusion only by COPY. It must never be consumed as a
                # rewrite merely because the extractor omitted that role.
                candidate = replace(candidate, role=TransitionRole.COPY)
            candidates.append(candidate)
    candidates.extend(stable_candidates)
    if not candidates:
        return base_plan
    result = solve_transition_plan(source_count, target_count, tuple(candidates))
    selected_ids = {candidate.candidate_id for candidate in result.selected}
    if result.valid and all(
        candidate.candidate_id in selected_ids for candidate in stable_candidates
    ):
        return result
    # Stable declarations are a hard board invariant. If a future semantic
    # adapter emits an irreconcilable graph, retain every certified unchanged
    # row and safely write/fade only the rest instead of dropping premises.
    return solve_transition_plan(
        source_count, target_count, tuple(stable_candidates)
    )


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


_PROOF_STORAGE_REASONS = frozenset({
    "verified-live-fact-storage",
    "verified-proof-definition-storage",
})


def _staged_proof_use_payload(
    plan: TransitionPlan,
    conclusion_row_indices: tuple[int, ...],
    semantic_transition: SemanticTransition | None = None,
    source_data: _StateTokenData | None = None,
    target_data: _StateTokenData | None = None,
) -> dict[str, Any] | None:
    """Sequence storage and immediate use of one checked proof object.

    A Lean proof-valued ``let``/``have`` can both enter the live context and
    be eliminated by the same kernel step.  The transition plan consequently
    contains two certified destinations for tokens of the preceding
    conclusion: a storage edge and a non-consuming COPY into the new target.
    Rendering both at once looks like a logical jump.  This payload preserves
    the single audited proof edge but gives it three visual phases:

    1. move the completed proposition into its durable context row;
    2. clone its unchanged structure from that *new board position*;
    3. write only the genuinely new substitution terms.

    The trigger is proof provenance and selected AST hyperedges only.  It
    never depends on tactic spelling, binder names, or equal-looking glyphs.
    """

    pair_records = tuple(
        (candidate, pair)
        for candidate in plan.selected
        for pair in candidate.pairs
    )
    stored_target_by_source = {
        pair.source: pair.target
        for candidate, pair in pair_records
        if candidate.reason in _PROOF_STORAGE_REASONS
    }
    conclusion_targets = frozenset(conclusion_row_indices)
    if not stored_target_by_source or not conclusion_targets:
        return None

    pair_phases: list[int] = []
    pair_via_targets: list[int | None] = []
    chained_pairs = 0
    for candidate, pair in pair_records:
        is_conclusion = pair.target in conclusion_targets
        phase = 1 if is_conclusion else 0
        via_target = (
            stored_target_by_source.get(pair.source)
            if is_conclusion and candidate.role == TransitionRole.COPY
            else None
        )
        if via_target is not None:
            chained_pairs += 1
        pair_phases.append(phase)
        pair_via_targets.append(via_target)

    # A second phase is justified only if at least one target token is a
    # certified clone of the object stored in phase one.  Otherwise ordinary
    # transitions retain the established simultaneous animation.
    if not chained_pairs:
        return None

    substitution_ghosts: list[dict[str, Any]] = []
    if (
        semantic_transition is not None
        and source_data is not None
        and target_data is not None
    ):
        source_nodes = {
            node.node_id: node for node in semantic_transition.source.nodes
        }
        target_nodes = {
            node.node_id: node for node in semantic_transition.target.nodes
        }
        for edge in semantic_transition.edges:
            if edge.reason != "verified-forall-substitution":
                continue
            source_node = source_nodes.get(edge.source_node_id)
            target_node = target_nodes.get(edge.target_node_id)
            if source_node is None or target_node is None:
                continue
            source_native = _tokens_in_semantic_spans(
                source_data.native_spans, source_node.latex_spans
            )
            target_native = _tokens_in_semantic_spans(
                target_data.native_spans, target_node.latex_spans
            )
            source_display = tuple(
                source_data.native_to_display[index]
                for index in source_native
                if index in source_data.native_to_display
            )
            target_display = tuple(dict.fromkeys(
                target_data.native_to_display[index]
                for index in target_native
                if index in target_data.native_to_display
            ))
            if len(source_display) != 1 or not target_display:
                continue
            via_target = stored_target_by_source.get(source_display[0])
            if via_target is None:
                continue
            if any(index not in conclusion_targets for index in target_display):
                continue
            substitution_ghosts.append({
                "source": source_display[0],
                "viaTarget": via_target,
                "targetIndices": list(target_display),
            })

    return {
        # Storage and premise handwriting start on the same frame. Derivation
        # begins shortly before storage finishes, so the moving proposition
        # continuously forks into the next conclusion and the board never
        # presents a misleading premises-only beat.
        "phaseRanges": [[0.0, 0.34], [0.32, 0.66], [0.72, 1.0]],
        "pairPhases": pair_phases,
        "pairViaTargets": pair_via_targets,
        "createdPhases": [
            2 if target in conclusion_targets else 0
            for target in plan.created_targets
        ],
        "deletedPhases": [0 for _source in plan.deleted_sources],
        "substitutionGhosts": substitution_ghosts,
    }


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
    state_token_data: list[_StateTokenData] = []
    entries: list[_PresentationEntry] = []
    previous_goal: Any | None = None
    for frame in frames:
        goal = frame.display_goals[0]
        annotations = tuple(goal.rule_annotations)
        annotation = annotations[0] if len(annotations) == 1 else None
        if (
            previous_goal is not None
            and annotation is not None
            and annotation.source_step_id is not None
            and annotation.source_latex
        ):
            if annotation.presentation_goals:
                for presentation_goal in annotation.presentation_goals:
                    entries.append(
                        _PresentationEntry(
                            frame=frame,
                            goal=presentation_goal,
                            transition_goal=presentation_goal,
                            tactic="forall-premise-selection",
                        )
                    )
            transition_goal = replace(
                goal,
                semantic_transition=(
                    annotation.substitution_transition
                    or goal.semantic_transition
                ),
            )
        else:
            transition_goal = goal
        if not _transition_has_actual_visual_source(previous_goal, transition_goal):
            transition_goal = replace(
                transition_goal,
                semantic_transition=None,
                index_maps=None,
                latex_index_maps=None,
            )
        entries.append(
            _PresentationEntry(
                frame=frame,
                goal=goal,
                transition_goal=transition_goal,
                tactic=frame.tactic,
            )
        )
        previous_goal = goal
    if on_progress is not None:
        on_progress(0, len(entries))

    for state_index, entry in enumerate(entries):
        frame = entry.frame
        goal = entry.goal
        native_specs = [
            (
                f"hyp-{hypothesis.key or hypothesis.name}",
                "context",
                hypothesis.render_latex(),
                (
                    f"hypothesis:{hypothesis.key}"
                    if hypothesis.key
                    else f"context-slot:{context_index}"
                ),
            )
            for context_index, hypothesis in enumerate(goal.latex_context)
        ]
        native_specs.append(
            (
                "target",
                "target",
                rf"\vdash\;{goal.latex_target or goal.state}",
                f"target:{goal.lineage_id}" if goal.lineage_id else None,
            )
        )

        native_rows: list[_NativeRow] = []
        native_spans: list[tuple[int, int]] = []
        native_texts: list[str] = []
        native_cursor = 0
        for key, kind, latex, stable_identity in native_specs:
            tokens = tuple(_latex_matching_token_spans(latex))
            first_native_token = len(native_texts)
            native_rows.append(
                _NativeRow(
                    row_key=key,
                    kind=kind,
                    latex=latex,
                    stable_identity=stable_identity,
                    tokens=tokens,
                    first_token=first_native_token,
                )
            )
            native_spans.extend(
                (native_cursor + start, native_cursor + end)
                for _token, start, end in tokens
            )
            native_texts.extend(token for token, _start, _end in tokens)
            native_cursor += len(latex) + 1

        builder = _PresentationStateBuilder([], [], {}, {})

        # The display state is exactly Lean's current certified context. Do
        # not pin declarations merely because they occurred in the first
        # rendered proof-term frame: that frame can already be inside nested
        # lambdas, so doing so duplicated local variables such as ``x : R``.
        # Durable rows are preserved below by their proof identity, without
        # inventing additional context entries.
        context_rows = native_rows[:-1]
        carried_latex = (
            _carried_conclusion_latex(
                entries[state_index - 1].goal, goal
            )
            if state_index > 0
            else None
        )
        for native_row in context_rows:
            builder.append_row(
                native_row.row_key,
                native_row.kind,
                native_row.latex,
                native_row.stable_identity,
                native_row,
                0,
            )

        carried_formula_indices: tuple[int, ...] = ()
        if carried_latex is not None:
            carried_tokens = tuple(_latex_matching_token_spans(carried_latex))
            for hypothesis, native_row in zip(
                goal.latex_context, context_rows, strict=True
            ):
                if hypothesis.latex != carried_latex:
                    continue
                native_part_start = _token_subsequence_start(
                    native_row.tokens, carried_tokens
                )
                if native_part_start is None:
                    continue
                candidate_indices = tuple(
                    builder.native_to_display.get(
                        native_row.first_token + native_part_start + offset, -1
                    )
                    for offset in range(len(carried_tokens))
                )
                if candidate_indices and all(index >= 0 for index in candidate_indices):
                    carried_formula_indices = candidate_indices
                    break
            if not carried_formula_indices:
                digest = sha256(carried_latex.encode("utf-8")).hexdigest()[:16]
                carried_formula_indices = builder.append_row(
                    f"carried-conclusion-{digest}",
                    "context",
                    carried_latex,
                    None,
                )

        target_row = native_rows[-1]
        conclusion_row_indices = builder.append_row(
            target_row.row_key,
            target_row.kind,
            target_row.latex,
            target_row.stable_identity,
            target_row,
            0,
        )
        target_formula = goal.latex_target or goal.state
        target_formula_tokens = tuple(_latex_matching_token_spans(target_formula))
        target_formula_start = _token_subsequence_start(
            target_row.tokens, target_formula_tokens
        )
        conclusion_formula_indices = (
            tuple(
                builder.native_to_display[
                    target_row.first_token + target_formula_start + offset
                ]
                for offset in range(len(target_formula_tokens))
            )
            if target_formula_start is not None
            else ()
        )
        state_token_data.append(
            _StateTokenData(
                native_spans=native_spans,
                native_texts=native_texts,
                native_to_display=builder.native_to_display,
                display_texts=builder.display_texts,
                stable_rows=builder.stable_rows,
                conclusion_row_indices=conclusion_row_indices,
                conclusion_formula_indices=conclusion_formula_indices,
                carried_formula_indices=carried_formula_indices,
                carried_formula_texts=tuple(
                    token for token, _start, _end in _latex_matching_token_spans(
                        carried_latex or ""
                    )
                ),
            )
        )
        states.append(
            {
                "id": f"state-{state_index}",
                "proofFrameIndex": frame.index,
                "tactic": entry.tactic,
                "lineageId": goal.lineage_id,
                "rows": builder.rows,
            }
        )
        if on_progress is not None:
            on_progress(state_index + 1, len(entries))

    transition_plans: list[dict[str, Any] | None] = []
    for state_index in range(1, len(states)):
        source_data = state_token_data[state_index - 1]
        target_data = state_token_data[state_index]
        transition_goal = entries[state_index].transition_goal
        semantic_transition = (
            transition_goal.semantic_transition
            if transition_goal is not None
            else None
        )
        plan = _semantic_transition_plan(
            source_data.native_spans,
            source_data.native_texts,
            target_data.native_spans,
            target_data.native_texts,
            semantic_transition,
        )
        plan = _remap_semantic_plan(
            plan,
            source_map=source_data.native_to_display,
            target_map=target_data.native_to_display,
            source_count=len(source_data.display_texts),
            target_count=len(target_data.display_texts),
        )
        mandatory_candidates: tuple[TransitionCandidate, ...] = ()
        source_conclusion_texts = tuple(
            source_data.display_texts[index]
            for index in source_data.conclusion_formula_indices
        )
        if (
            source_data.conclusion_formula_indices
            and target_data.carried_formula_indices
            and source_conclusion_texts == target_data.carried_formula_texts
            and len(source_data.conclusion_formula_indices)
            == len(target_data.carried_formula_indices)
        ):
            mandatory_candidates = (
                TransitionCandidate(
                    candidate_id=f"carried-conclusion:{state_index - 1}",
                    source_node_id=f"conclusion:{state_index - 1}",
                    target_node_id=f"carried-conclusion:{state_index}",
                    role=TransitionRole.PRESERVE,
                    reason="certified-parent-conclusion",
                    pairs=tuple(
                        TokenPair(source, target)
                        for source, target in zip(
                            source_data.conclusion_formula_indices,
                            target_data.carried_formula_indices,
                            strict=True,
                        )
                    ),
                    certified=True,
                    exact_composite=True,
                    source_kind="proof-conclusion",
                    target_kind="carried-conclusion",
                ),
            )
        plan = _merge_stable_proof_rows(
            plan,
            len(source_data.display_texts),
            len(target_data.display_texts),
            source_data.stable_rows,
            target_data.stable_rows,
            semantic_transition,
            mandatory_candidates,
        )
        plan_payload = None
        if plan is not None and plan.valid:
            staging = _staged_proof_use_payload(
                plan,
                target_data.conclusion_row_indices,
                semantic_transition,
                source_data,
                target_data,
            )
            plan_payload = {
                "pairs": [
                    [pair.source, pair.target, 1 if candidate.role.value == "copy" else 0]
                    for candidate in plan.selected
                    for pair in candidate.pairs
                ],
                "created": list(plan.created_targets),
                "deleted": list(plan.deleted_sources),
                "staging": staging,
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
    phases = list(step_pacing.phases)
    # One kernel inference may deliberately expose several sequential visual
    # actions.  Give each action the same global step clock as an ordinary
    # transition; squeezing three phases into one duration is both harder to
    # read and creates the apparent speed jump this staging is meant to fix.
    durations = [
        duration * (3 if plan and plan.get("staging") else 1)
        for duration, plan in zip(durations, transition_plans, strict=True)
    ]
    if preview_seconds is not None:
        transition_budget = max(
            0,
            int(preview_seconds * fps) - initial_frames - final_hold_frames,
        )
        kept = 0
        used = 0
        for duration in durations:
            if kept and used + duration > transition_budget:
                break
            if not kept and duration > transition_budget:
                break
            used += duration
            kept += 1
        transition_plans = transition_plans[:kept]
        durations = durations[:kept]
        phases = phases[:kept]
        states = states[: kept + 1]
        transition_count = kept
        # Keep an opening preview at the requested wall-clock duration without
        # accelerating its final complete action. Any spare budget becomes a
        # still hold on the last fully rendered proof state.
        final_hold_frames += max(0, transition_budget - used)
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
        "rendererContract": "strict-proof-transition-v15-overlapped-proof-use",
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
