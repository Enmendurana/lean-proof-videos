from __future__ import annotations

from collections import Counter
from html import escape
import json
from pathlib import Path
from typing import Any

from proof_video.cache import write_json
from proof_video.latex import parse_goal_state
from proof_video.models import Goal, IndexMaps, Movie, SemanticTransition
from proof_video.presentation.debug import build_canonical_transition_debug
from proof_video.presentation.rows import context_presentation_rows


SCHEMA_VERSION = 2
BLOCK_SIMILARITY_THRESHOLD = 0.35
_LEANTEX_FALLBACK_MARKER = r"\operatorname{Lean}\left[\text{"


def build_transition_map(movie: Movie) -> dict[str, Any]:
    """Describe the semantic identities that the renderer can preserve.

    This intentionally follows the renderers' semantic timeline: duplicate
    states are removed, the canonical section covers the whole live goal
    forest, and the old block report retains its first-focus projection only
    for ABI 1--4 compatibility. Legacy glyph-shape matching happens inside
    Manim and therefore cannot provide deterministic character edges here.
    """
    frames = tuple(frame for frame in movie.semantic_frames() if frame.display_goals)
    transitions = [
        _transition(source, target)
        for source, target in zip(frames, frames[1:], strict=False)
    ]
    modes = Counter(
        block["mappingMode"]
        for transition in transitions
        for block in transition["blocks"]
    )
    canonical = [item["canonical"] for item in transitions]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "theorem": movie.theorem_name,
        "scope": {
            "timeline": "semantic_frames",
            "canonicalGoals": "all_live_goals",
            "legacyBlockProjection": "first_focused_goal",
            "blockSimilarityThreshold": BLOCK_SIMILARITY_THRESHOLD,
        },
        "summary": {
            "transitions": len(transitions),
            "semanticTransitions": modes["semantic_transition"],
            "legacyCharacterMaps": modes["legacy_character_map"],
            "legacyShapeFallbacks": modes["legacy_shape_fallback"],
            "writtenBlocks": modes["write"],
            "removedBlocks": modes["fade"],
            "canonicalTransitions": sum(
                bool(item.get("available")) for item in canonical
            ),
            "validCanonicalTransitions": sum(
                bool(item.get("validation", {}).get("valid")) for item in canonical
            ),
            "canonicalFallbacks": sum(
                bool(item.get("presentation", {}).get("fallback", {}).get("used"))
                for item in canonical
            ),
        },
        "transitions": transitions,
    }


def write_transition_debug(path: Path, movie: Movie) -> tuple[Path, Path]:
    """Write the complete transition map and a standalone human viewer."""

    json_path = path.with_suffix(".json")
    payload = build_transition_map(movie)
    write_json(json_path, payload)
    html_path = write_transition_debug_html(path, payload, movie.theorem_name)
    return json_path, html_path


def write_transition_debug_html(
    path: Path, payload: dict[str, Any], theorem_name: str
) -> Path:
    """Write a standalone viewer for an already serialized transition map."""

    html_path = path.with_suffix(".html")

    sections: list[str] = []
    for index, transition in enumerate(payload["transitions"], start=1):
        canonical = transition["canonical"]
        valid = canonical.get("validation", {}).get("valid", False)
        fallback = (
            canonical.get("presentation", {}).get("fallback", {}).get("used", False)
        )
        css_class = "bad" if not valid else ("warn" if fallback else "ok")
        label = (
            f"{index}. frame {transition['fromFrame']}→{transition['toFrame']} · "
            f"{transition['tactic'] or '(unlabelled action)'}"
        )
        pretty = escape(json.dumps(transition, indent=2, ensure_ascii=False))
        sections.append(
            f"<details class='{css_class}'><summary>{escape(label)}</summary>"
            f"<pre>{pretty}</pre></details>"
        )

    summary = escape(json.dumps(payload["summary"], indent=2, ensure_ascii=False))
    html_path.write_text(
        "<!doctype html><meta charset='utf-8'>"
        f"<title>Canonical transition debug · {escape(theorem_name)}</title>"
        "<style>body{font:15px system-ui;max-width:1500px;margin:2rem auto;"
        "padding:0 1rem;background:#090b12;color:#eef1f8}"
        "details{margin:.5rem 0;border-left:5px solid #8792aa;padding:.5rem 1rem;"
        "background:#121827}details.ok{border-color:#72f59a}"
        "details.warn{border-color:#ffd166}details.bad{border-color:#ff7777}"
        "summary{cursor:pointer;font-weight:650}pre{white-space:pre-wrap;"
        "overflow-wrap:anywhere;color:#dce3f5}</style>"
        f"<h1>{escape(theorem_name)}</h1><p>Canonical ABI transition audit. "
        "Green entries replay exactly; amber entries use an explicit visual fallback; "
        "red entries failed validation.</p>"
        f"<pre>{summary}</pre>{''.join(sections)}",
        encoding="utf-8",
    )
    return html_path


def _transition(source_frame, target_frame) -> dict[str, Any]:
    source = source_frame.display_goals[0]
    target = target_frame.display_goals[0]
    if source.lineage_id and source.lineage_id == target.lineage_id:
        blocks = [_paired_block(source, target, "same_lineage", 1.0, True)]
    else:
        confidence = _block_similarity(source, target)
        if confidence >= BLOCK_SIMILARITY_THRESHOLD:
            blocks = [
                _paired_block(
                    source,
                    target,
                    "dormant_branch_similarity",
                    confidence,
                    False,
                )
            ]
        else:
            blocks = [
                _unpaired_block(source, None, "removed_block", "fade"),
                _unpaired_block(None, target, "new_block", "write"),
            ]
    return {
        "fromFrame": source_frame.index,
        "toFrame": target_frame.index,
        "tactic": target_frame.tactic,
        "blocks": blocks,
        "canonical": _canonical_transition(source_frame, target_frame),
    }


def _canonical_transition(source_frame, target_frame) -> dict[str, Any]:
    before = source_frame.proof_state
    after = target_frame.proof_state
    transition = target_frame.proof_transition
    if before is None or after is None or transition is None:
        return {
            "available": False,
            "fallbackReason": "canonical state or transition is missing",
        }
    return {
        "available": True,
        **build_canonical_transition_debug(before, after, transition),
    }


def _paired_block(
    source: Goal,
    target: Goal,
    reason: str,
    confidence: float,
    allow_semantic_map: bool,
) -> dict[str, Any]:
    semantic_transition = target.semantic_transition if allow_semantic_map else None
    index_maps = target.latex_index_maps if allow_semantic_map else None
    if semantic_transition is not None:
        mapping_mode = "semantic_transition"
        mapping = _semantic_transition_mapping(semantic_transition)
    elif index_maps is not None:
        mapping_mode = "legacy_character_map"
        mapping = _legacy_character_mapping(index_maps)
    else:
        mapping_mode = "legacy_shape_fallback"
        fallback_reason = (
            "latex_index_maps_missing"
            if allow_semantic_map
            else "lineage_changed; semantic map belongs to a different Lean block"
        )
        mapping = {
            "proofKind": None,
            "adapter": None,
            "fallbackReason": fallback_reason,
            "edges": [],
            "unmappedSourceIds": [],
            "unmappedTargetIds": [],
        }
    return {
        "source": _goal_ref(source),
        "target": _goal_ref(target),
        "reason": reason,
        "confidence": confidence,
        "mappingMode": mapping_mode,
        **mapping,
    }


def _unpaired_block(
    source: Goal | None,
    target: Goal | None,
    reason: str,
    mapping_mode: str,
) -> dict[str, Any]:
    return {
        "source": _goal_ref(source) if source is not None else None,
        "target": _goal_ref(target) if target is not None else None,
        "reason": reason,
        "confidence": None,
        "mappingMode": mapping_mode,
        "proofKind": None,
        "adapter": None,
        "fallbackReason": None,
        "edges": [],
        "unmappedSourceIds": [],
        "unmappedTargetIds": [],
    }


def _semantic_transition_mapping(
    transition: SemanticTransition,
) -> dict[str, Any]:
    mapped_source = {edge.source_node_id for edge in transition.edges}
    mapped_target = {edge.target_node_id for edge in transition.edges}
    return {
        "proofKind": transition.proof_kind or None,
        "adapter": transition.adapter or None,
        "proofFingerprint": transition.proof_fingerprint or None,
        "proofTerm": transition.proof_term or None,
        "proofDescendants": list(transition.proof_descendants),
        "fallbackReason": transition.fallback_reason,
        # Preserve source order, overlaps and duplicate edges exactly as Lean
        # emitted them; these are proof semantics, not a set of character IDs.
        "edges": [
            {
                "sourceNodeId": edge.source_node_id,
                "targetNodeId": edge.target_node_id,
                "reason": edge.reason or None,
                "confidence": edge.confidence,
            }
            for edge in transition.edges
        ],
        "unmappedSourceIds": [
            node.node_id
            for node in transition.source.nodes
            if node.node_id not in mapped_source
        ],
        "unmappedTargetIds": [
            node.node_id
            for node in transition.target.nodes
            if node.node_id not in mapped_target
        ],
    }


def _legacy_character_mapping(index_maps: IndexMaps) -> dict[str, Any]:
    forward = {
        (source, target)
        for source, target in enumerate(index_maps.source_to_target)
        if target is not None
    }
    reverse = {
        (source, target)
        for target, source in enumerate(index_maps.target_to_source)
        if source is not None
    }
    edges = []
    for source, target in sorted(forward | reverse):
        reciprocal = (source, target) in forward and (source, target) in reverse
        edges.append(
            {
                "sourceNodeId": f"char:{source}",
                "targetNodeId": f"char:{target}",
                "reason": "legacy_character_map",
                "confidence": 1.0 if reciprocal else 0.75,
            }
        )
    return {
        "proofKind": "legacy_character_correspondence",
        "adapter": "latex-index-map-v1",
        "fallbackReason": "semantic_transition_missing",
        "edges": edges,
        "unmappedSourceIds": [
            f"char:{index}"
            for index, target in enumerate(index_maps.source_to_target)
            if target is None
        ],
        "unmappedTargetIds": [
            f"char:{index}"
            for index, source in enumerate(index_maps.target_to_source)
            if source is None
        ],
    }


def _goal_ref(goal: Goal) -> dict[str, Any]:
    presentation_rows = context_presentation_rows(goal)
    latex_parts = [row.latex for row in presentation_rows]
    if goal.latex_target is not None:
        latex_parts.append(goal.latex_target)
    if not goal.latex_target and not presentation_rows:
        notation_source = "legacy_lean_state"
    elif any(_LEANTEX_FALLBACK_MARKER in part for part in latex_parts):
        notation_source = "semantic_latex_with_legacy_expression_fallback"
    else:
        notation_source = "semantic_latex"
    return {
        "goalId": goal.goal_id,
        "lineage": goal.lineage_id,
        "parentGoalId": goal.parent_goal_id,
        "rowKeys": sorted(_row_keys(goal)),
        "notationSource": notation_source,
    }


def _block_similarity(source: Goal, target: Goal) -> float:
    source_keys = _row_keys(source)
    target_keys = _row_keys(target)
    denominator = max(1, len(source_keys), len(target_keys))
    return len(source_keys & target_keys) / denominator


def _row_keys(goal: Goal) -> set[str]:
    presentation_rows = context_presentation_rows(goal)
    if presentation_rows or goal.canonical_target is not None:
        context_keys = {f"hyp-{row.stable_key}" for row in presentation_rows}
    else:
        state = parse_goal_state(goal.state)
        context_keys = {
            f"hyp-context-{index}" for index, _context in enumerate(state.context)
        }
    return {*context_keys, "target"}
