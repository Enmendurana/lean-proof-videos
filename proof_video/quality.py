from __future__ import annotations

from collections import Counter, defaultdict
from html import escape
import re
from pathlib import Path
from typing import Any, Iterable

from proof_video.cache import write_json
from proof_video.models import Movie


_LEAN_FALLBACK = r"\operatorname{Lean}\left[\text{"
_IMPLEMENTATION_NAME = re.compile(
    r"(?:\\operatorname\{)?(?:[A-Z][A-Za-z0-9_]*\.)+[A-Za-z_][A-Za-z0-9_]*"
)


def _hybrid_results(raw: dict[str, Any]) -> Iterable[tuple[str, int, dict[str, Any], dict[str, Any]]]:
    for chapter in raw.get("chapters", ()):
        theorem = str(chapter.get("theoremName", ""))
        for action_index, action in enumerate(chapter.get("movie", {}).get("actions", ())):
            for goal_action in action.get("goalActions", ()):
                for result in goal_action.get("results", ()):
                    yield theorem, action_index, goal_action, result


def _node_key(node: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(node.get("kind", "")),
        str(node.get("identity", "")),
        str(node.get("fingerprint", "")),
    )


def _append_latex_issues(
    scope: str,
    fragments: Iterable[str],
    errors: list[str],
    warnings: list[str],
) -> None:
    for latex in fragments:
        if _LEAN_FALLBACK in latex:
            errors.append(f"{scope}: unhandled Lean expression reached LaTeX")
        elif _IMPLEMENTATION_NAME.search(latex):
            warnings.append(f"{scope}: possible implementation name in LaTeX: {latex}")


def build_quality_report(raw: dict[str, Any]) -> dict[str, Any]:
    """Check animation invariants that are stricter than kernel validity.

    The kernel audit proves mathematics. This pass proves that the visual
    choreography does not discard a uniquely identifiable persistent object
    or silently fall back to Lean implementation text.
    """

    errors: list[str] = []
    warnings: list[str] = []
    adapters: Counter[str] = Counter()
    checked_transitions = 0
    persistent_objects = 0

    if str(raw.get("schemaVersion", "")).startswith("3"):
        for theorem, action_index, goal_action, result in _hybrid_results(raw):
            explanation = goal_action.get("explanation", {})
            fingerprint = str(goal_action.get("proofFingerprint", ""))
            if explanation and str(explanation.get("certificateFingerprint", "")) != fingerprint:
                errors.append(
                    f"{theorem} action {action_index}: tactic explanation certificate differs"
                )
            adapter = str(explanation.get("adapter", "legacy-unexplained"))
            adapters[adapter] += 1
            transition = result.get("semanticTransition")
            if not transition:
                errors.append(f"{theorem} action {action_index}: semantic transition missing")
                continue
            checked_transitions += 1
            source_nodes = transition.get("sourceNodes", ())
            target_nodes = transition.get("targetNodes", ())
            source_groups: dict[tuple[str, str, str], list[str]] = defaultdict(list)
            target_groups: dict[tuple[str, str, str], list[str]] = defaultdict(list)
            for node in source_nodes:
                key = _node_key(node)
                if key[1]:
                    source_groups[key].append(str(node.get("id", "")))
            for node in target_nodes:
                key = _node_key(node)
                if key[1]:
                    target_groups[key].append(str(node.get("id", "")))
            edges = {
                (str(edge.get("sourceNodeId", "")), str(edge.get("targetNodeId", "")))
                for edge in transition.get("edges", ())
            }
            for key in source_groups.keys() & target_groups.keys():
                sources = source_groups[key]
                targets = target_groups[key]
                if len(sources) != 1 or len(targets) != 1:
                    continue
                persistent_objects += 1
                if (sources[0], targets[0]) not in edges:
                    errors.append(
                        f"{theorem} action {action_index}: uniquely persistent "
                        f"{key[0]} {key[1]} has no certified visual edge"
                    )

        for chapter in raw.get("chapters", ()):
            movie = chapter.get("movie", {})
            latex_fragments: list[str] = []
            start = movie.get("startGoal", {})
            latex_fragments.append(str(start.get("latexTarget", "")))
            latex_fragments.extend(
                str(item.get("latex", "")) for item in start.get("latexContext", ())
            )
            for _theorem, _index, _action, result in _hybrid_results(
                {"schemaVersion": "3.0", "chapters": [chapter]}
            ):
                goal = result.get("goal", {})
                latex_fragments.append(str(goal.get("latexTarget", "")))
                latex_fragments.extend(
                    str(item.get("latex", "")) for item in goal.get("latexContext", ())
                )
            _append_latex_issues(
                str(chapter.get("theoremName", "")),
                latex_fragments,
                errors,
                warnings,
            )

    return {
        "schemaVersion": 1,
        "valid": not errors,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
        "summary": {
            "checkedTransitions": checked_transitions,
            "persistentObjects": persistent_objects,
            "tacticAdapters": dict(sorted(adapters.items())),
        },
    }


def build_movie_quality_report(movie: Movie) -> dict[str, Any]:
    """Audit the actual renderer-facing states of a strict ProofTrace movie.

    Schema-v2 traces synthesize their semantic transitions while constructing
    ``Movie``. Auditing only the original JSON therefore reported zero checked
    transitions even though the strict renderer consumed hundreds of them.
    This pass checks the same objects that are handed to Remotion or Manim.
    """

    errors: list[str] = []
    warnings: list[str] = []
    adapters: Counter[str] = Counter()
    checked_transitions = 0
    persistent_objects = 0
    frames = movie.semantic_frames()
    for frame_index, frame in enumerate(frames):
        if not frame.display_goals:
            continue
        goal = frame.display_goals[0]
        _append_latex_issues(
            movie.theorem_name,
            (
                *(hypothesis.render_latex() for hypothesis in goal.latex_context),
                goal.latex_target or goal.state,
            ),
            errors,
            warnings,
        )
        if frame_index == 0:
            continue
        transition = goal.semantic_transition
        if transition is None:
            # Hybrid traces deliberately concatenate independently certified
            # theorem chapters.  A chapter's initial sequent has no tactic
            # predecessor in that chapter, so manufacturing a semantic edge
            # across two different theorems would be both visually misleading
            # and logically false.  The strict hybrid audit separately checks
            # chapter dependency order and each kernel certificate.
            previous_goals = frames[frame_index - 1].display_goals
            previous_lineage = (
                previous_goals[0].lineage_id if previous_goals else ""
            )
            current_scope = goal.lineage_id.partition("/")[0]
            previous_scope = previous_lineage.partition("/")[0]
            if (
                current_scope.startswith("chapter-")
                and previous_scope.startswith("chapter-")
                and current_scope != previous_scope
            ):
                continue
            errors.append(
                f"{movie.theorem_name} frame {frame.index}: semantic transition missing"
            )
            continue
        checked_transitions += 1
        adapters[transition.adapter or "proof-trace"] += 1
        source_groups: dict[tuple[str, str, str], list[str]] = defaultdict(list)
        target_groups: dict[tuple[str, str, str], list[str]] = defaultdict(list)
        for node in transition.source.nodes:
            key = (node.kind, node.identity, node.fingerprint)
            if node.identity:
                source_groups[key].append(node.node_id)
        for node in transition.target.nodes:
            key = (node.kind, node.identity, node.fingerprint)
            if node.identity:
                target_groups[key].append(node.node_id)
        edges = {
            (edge.source_node_id, edge.target_node_id)
            for edge in transition.edges
        }
        for key in source_groups.keys() & target_groups.keys():
            sources = source_groups[key]
            targets = target_groups[key]
            if len(sources) != 1 or len(targets) != 1:
                continue
            persistent_objects += 1
            if (sources[0], targets[0]) not in edges:
                errors.append(
                    f"{movie.theorem_name} frame {frame.index}: uniquely persistent "
                    f"{key[0]} {key[1]} has no certified visual edge"
                )

    return {
        "schemaVersion": 1,
        "valid": not errors,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
        "summary": {
            "checkedTransitions": checked_transitions,
            "persistentObjects": persistent_objects,
            "tacticAdapters": dict(sorted(adapters.items())),
        },
    }


def build_quality_report_chapters(
    metadata: dict[str, Any], chapters: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    """Aggregate the same checks without retaining every raw chapter."""

    errors: list[str] = []
    warnings: list[str] = []
    adapters: Counter[str] = Counter()
    checked_transitions = 0
    persistent_objects = 0
    for chapter in chapters:
        report = build_quality_report(
            {
                "schemaVersion": "3.0",
                "theoremName": metadata.get("theoremName", ""),
                "chapters": [chapter],
                "validation": metadata.get("validation", {}),
            }
        )
        errors.extend(report["errors"])
        warnings.extend(report["warnings"])
        summary = report["summary"]
        checked_transitions += int(summary["checkedTransitions"])
        persistent_objects += int(summary["persistentObjects"])
        adapters.update(summary["tacticAdapters"])
    return {
        "schemaVersion": 1,
        "valid": not errors,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
        "summary": {
            "checkedTransitions": checked_transitions,
            "persistentObjects": persistent_objects,
            "tacticAdapters": dict(sorted(adapters.items())),
        },
    }


def write_quality_report(path: Path, report: dict[str, Any]) -> tuple[Path, Path]:
    json_path = path.with_suffix(".qa.json")
    html_path = path.with_suffix(".qa.html")
    write_json(json_path, report)
    rows = "".join(
        f"<li class='{kind}'>{escape(message)}</li>"
        for kind in ("error", "warning")
        for message in report[f"{kind}s"]
    ) or "<li class='ok'>No semantic or presentation violations.</li>"
    summary = escape(str(report.get("summary", {})))
    html_path.write_text(
        "<!doctype html><meta charset='utf-8'><title>Proof video QA</title>"
        "<style>body{font:16px system-ui;max-width:1100px;margin:3rem auto;"
        "background:#111;color:#eee}.error{color:#ff7777}.warning{color:#ffd166}"
        ".ok{color:#7ee787}code{white-space:pre-wrap}</style>"
        f"<h1>Proof video QA: {'PASS' if report['valid'] else 'FAIL'}</h1>"
        f"<code>{summary}</code><ul>{rows}</ul>",
        encoding="utf-8",
    )
    return json_path, html_path


def require_quality_report(raw: dict[str, Any]) -> dict[str, Any]:
    report = build_quality_report(raw)
    if not report["valid"]:
        raise ValueError("proof video QA failed: " + "; ".join(report["errors"][:8]))
    return report


def require_movie_quality_report(movie: Movie) -> dict[str, Any]:
    report = build_movie_quality_report(movie)
    if not report["valid"]:
        raise ValueError("proof video QA failed: " + "; ".join(report["errors"][:8]))
    return report


def require_quality_report_chapters(
    metadata: dict[str, Any], chapters: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    report = build_quality_report_chapters(metadata, chapters)
    if not report["valid"]:
        raise ValueError("proof video QA failed: " + "; ".join(report["errors"][:8]))
    return report
