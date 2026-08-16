"""Renderer compatibility for older kernel-certified source-tactic traces.

The proof evidence is immutable.  This module upgrades only its presentation
payload: LeanTeX fallback wrappers become ordinary mathematical LaTeX and
unique, unchanged Lean expression identities receive the correspondence edge
that newer extractors emit directly.  Character spans are remapped together
with the text, so the semantic renderer never observes mixed coordinate
systems.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from proof_video.latex import lean_to_latex


_FALLBACK_PREFIX = r"\operatorname{Lean}\left[\text{"
_FALLBACK_SUFFIX = r"}\right]"


@dataclass(frozen=True)
class LatexBoundaryMap:
    """Monotone mapping from every old character boundary to the new text."""

    source_length: int
    target_length: int
    boundaries: tuple[int, ...]

    def span(self, start: int, end: int) -> tuple[int, int]:
        if not (0 <= start <= end <= self.source_length):
            return start, end
        mapped_start = self.boundaries[start]
        mapped_end = self.boundaries[end]
        if start < end and mapped_end <= mapped_start:
            mapped_end = min(self.target_length, mapped_start + 1)
        return mapped_start, mapped_end


def normalize_fallback_latex(source: str) -> tuple[str, LatexBoundaryMap]:
    """Replace every complete LeanTeX fallback and retain span coordinates."""

    replacements: list[tuple[int, int, str]] = []
    cursor = 0
    while (start := source.find(_FALLBACK_PREFIX, cursor)) >= 0:
        content_start = start + len(_FALLBACK_PREFIX)
        suffix_start = source.find(_FALLBACK_SUFFIX, content_start)
        if suffix_start < 0:
            break
        end = suffix_start + len(_FALLBACK_SUFFIX)
        replacements.append(
            (start, end, lean_to_latex(source[content_start:suffix_start]))
        )
        cursor = end

    if not replacements:
        identity = tuple(range(len(source) + 1))
        return source, LatexBoundaryMap(len(source), len(source), identity)

    pieces: list[str] = []
    boundaries = [0] * (len(source) + 1)
    old_cursor = 0
    new_cursor = 0
    for start, end, replacement in replacements:
        unchanged = source[old_cursor:start]
        pieces.append(unchanged)
        for position in range(old_cursor, start + 1):
            boundaries[position] = new_cursor + position - old_cursor
        new_cursor += len(unchanged)

        old_width = end - start
        new_width = len(replacement)
        pieces.append(replacement)
        for offset in range(old_width + 1):
            # Proportional interior boundaries conservatively retain nested
            # expression ownership when a legacy fallback changes width.
            boundaries[start + offset] = new_cursor + round(
                offset * new_width / old_width
            )
        new_cursor += new_width
        old_cursor = end

    tail = source[old_cursor:]
    pieces.append(tail)
    for position in range(old_cursor, len(source) + 1):
        boundaries[position] = new_cursor + position - old_cursor
    result = "".join(pieces)
    return result, LatexBoundaryMap(len(source), len(result), tuple(boundaries))


def _goal_latex_state(goal: dict[str, Any]) -> str:
    rows: list[str] = []
    for hypothesis in goal.get("latexContext", ()):
        raw_latex = hypothesis.get("rawLatex")
        if raw_latex is not None:
            rows.append(str(raw_latex))
            continue
        name = str(hypothesis.get("name", "")).replace("_", r"\_")
        rows.append(rf"{name} \;:\; {hypothesis.get('latex', '')}")
    rows.append(rf"\vdash\;{goal.get('latexTarget', '')}")
    return "\n".join(rows)


def _normalize_goal(goal: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(goal)
    # Absence of a LaTeX field is meaningful for legacy traces: ``Goal`` then
    # falls back to its Lean state.  Do not turn a missing field into an empty
    # string, otherwise two different legacy goals collapse into one visual
    # state during semantic-frame deduplication.
    if result.get("latexTarget") is not None:
        target, _mapping = normalize_fallback_latex(str(result["latexTarget"]))
        result["latexTarget"] = target
    for hypothesis in result.get("latexContext", ()):
        if hypothesis.get("latex") is not None:
            latex, _mapping = normalize_fallback_latex(str(hypothesis["latex"]))
            hypothesis["latex"] = latex
        if hypothesis.get("rawLatex") is not None:
            raw_latex, _mapping = normalize_fallback_latex(
                str(hypothesis["rawLatex"])
            )
            hypothesis["rawLatex"] = raw_latex
    return result


def _remap_nodes(nodes: list[dict[str, Any]], mapping: LatexBoundaryMap) -> None:
    for node in nodes:
        spans = node.get("latexSpans")
        if isinstance(spans, list):
            for span in spans:
                if not isinstance(span, dict):
                    continue
                start, end = mapping.span(int(span["start"]), int(span["end"]))
                span["start"] = start
                span["end"] = end
        span = node.get("latexSpan")
        if isinstance(span, dict):
            start, end = mapping.span(int(span["start"]), int(span["end"]))
            span["start"] = start
            span["end"] = end


def _node_key(node: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(node.get("kind", "")),
        str(node.get("identity", "")),
        str(node.get("fingerprint", "")),
    )


def _complete_unique_identity_edges(transition: dict[str, Any]) -> None:
    """Backfill only a logically unique unchanged Lean expression object."""

    source_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    target_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for node in transition.get("sourceNodes", ()):
        key = _node_key(node)
        if key[1]:
            source_groups.setdefault(key, []).append(node)
    for node in transition.get("targetNodes", ()):
        key = _node_key(node)
        if key[1]:
            target_groups.setdefault(key, []).append(node)
    edges = transition.setdefault("edges", [])
    existing = {
        (str(edge.get("sourceNodeId", "")), str(edge.get("targetNodeId", "")))
        for edge in edges
    }
    for key in sorted(source_groups.keys() & target_groups.keys()):
        sources = source_groups[key]
        targets = target_groups[key]
        if len(sources) != 1 or len(targets) != 1:
            continue
        pair = (str(sources[0].get("id", "")), str(targets[0].get("id", "")))
        if not all(pair) or pair in existing:
            continue
        edges.append(
            {
                "sourceNodeId": pair[0],
                "targetNodeId": pair[1],
                "reason": "verified-unique-expression-identity",
                "confidence": 1.0,
            }
        )
        existing.add(pair)


def _normalize_transition(
    transition: dict[str, Any],
    source_state: str,
    target_state: str,
) -> dict[str, Any]:
    result = deepcopy(transition)
    _normalized_source, source_mapping = normalize_fallback_latex(source_state)
    _normalized_target, target_mapping = normalize_fallback_latex(target_state)
    _remap_nodes(result.get("sourceNodes", []), source_mapping)
    _remap_nodes(result.get("targetNodes", []), target_mapping)
    _complete_unique_identity_edges(result)
    return result


def normalize_source_tactic_movie(value: dict[str, Any]) -> dict[str, Any]:
    """Return a renderer-facing copy without mutating certified trace JSON."""

    if "startGoal" not in value or "actions" not in value:
        return value
    result = deepcopy(value)
    raw_goals = [deepcopy(value["startGoal"])]
    result["startGoal"] = _normalize_goal(value["startGoal"])

    for raw_action, normalized_action in zip(
        value.get("actions", ()), result.get("actions", ()), strict=True
    ):
        for raw_goal_action, normalized_goal_action in zip(
            raw_action.get("goalActions", ()),
            normalized_action.get("goalActions", ()),
            strict=True,
        ):
            start_id = str(raw_goal_action.get("startGoalId", ""))
            position = next(
                (
                    index
                    for index, goal in enumerate(raw_goals)
                    if str(goal.get("goalId", "")) == start_id
                ),
                None,
            )
            source_goal = raw_goals[position] if position is not None else None
            raw_results = raw_goal_action.get("results", ())
            normalized_results = normalized_goal_action.get("results", ())
            replacement_goals: list[dict[str, Any]] = []
            for raw_result, normalized_result in zip(
                raw_results, normalized_results, strict=True
            ):
                raw_target = raw_result.get("goal", {})
                replacement_goals.append(deepcopy(raw_target))
                normalized_result["goal"] = _normalize_goal(raw_target)
                transition = raw_result.get("semanticTransition")
                if transition and source_goal is not None:
                    normalized_result["semanticTransition"] = _normalize_transition(
                        transition,
                        _goal_latex_state(source_goal),
                        _goal_latex_state(raw_target),
                    )
            if position is not None:
                raw_goals[position : position + 1] = replacement_goals
    return result
