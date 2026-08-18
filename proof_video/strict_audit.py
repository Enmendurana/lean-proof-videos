from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from proof_video.models import Movie
from proof_video.proof.frontier import temporal_frontier_issues


def _certified_edge(edge) -> bool:
    return edge.reason.startswith("verified-") or (
        edge.reason == "same-proof-context"
        and edge.source_node_id == edge.target_node_id
    )


def build_hybrid_audit(raw: dict[str, Any]) -> dict[str, Any]:
    return build_hybrid_audit_chapters(raw, raw.get("chapters", ()))


def build_hybrid_audit_chapters(
    metadata: dict[str, Any], chapters: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    """Audit source-tactic chapters and their kernel certificates.

    The tactic timeline is a backwards goal reduction, while each chapter's
    final declaration proof is independently checked by Lean's kernel.  This
    audit therefore validates both layers instead of pretending tactic goals
    are forward proof-term premises.
    """

    errors: list[str] = []
    validation = metadata.get("validation", {})
    if not validation.get("valid", False):
        errors.extend(str(item) for item in validation.get("errors", ()))
        if not errors:
            errors.append("hybrid trace validation failed")
    seen: set[str] = set()
    action_count = 0
    certified_actions = 0
    semantic_edges = 0
    chapter_count = 0
    last_is_main = False
    for expected_id, chapter in enumerate(chapters):
        chapter_count += 1
        last_is_main = bool(chapter.get("isMain", False))
        chapter_id = int(chapter.get("id", -1))
        theorem = str(chapter.get("theoremName", ""))
        if chapter_id != expected_id:
            errors.append(f"chapter id {chapter_id} is not contiguous at {expected_id}")
        if not theorem:
            errors.append(f"chapter {chapter_id} has no theorem name")
        for dependency in chapter.get("dependencies", ()):
            if str(dependency) not in seen:
                errors.append(
                    f"chapter {theorem} precedes or omits dependency {dependency}"
                )
        chapter_validation = chapter.get("validation", {})
        if not chapter_validation.get("kernelChecked", False):
            errors.append(f"chapter {theorem} is not kernel checked")
        if not chapter_validation.get("noSorry", False):
            errors.append(f"chapter {theorem} contains an unsafe placeholder")
        if not chapter.get("proofFingerprint"):
            errors.append(f"chapter {theorem} has no proof fingerprint")
        movie = chapter.get("movie", {})
        for action_index, action in enumerate(movie.get("actions", ())):
            for goal_action in action.get("goalActions", ()):
                action_count += 1
                fingerprint = str(goal_action.get("proofFingerprint", ""))
                proof_kind = str(goal_action.get("proofKind", ""))
                if not fingerprint or proof_kind in {"", "unassigned"}:
                    errors.append(
                        f"chapter {theorem} action {action_index} has no certified assignment"
                    )
                else:
                    certified_actions += 1
                for result in goal_action.get("results", ()):
                    transition = result.get("semanticTransition")
                    if not transition:
                        errors.append(
                            f"chapter {theorem} action {action_index} has no semantic transition"
                        )
                        continue
                    if str(transition.get("proofFingerprint", "")) != fingerprint:
                        errors.append(
                            f"chapter {theorem} action {action_index} transition certificate differs"
                        )
                    source_ids = {
                        str(node.get("id", ""))
                        for node in transition.get("sourceNodes", ())
                    }
                    target_ids = {
                        str(node.get("id", ""))
                        for node in transition.get("targetNodes", ())
                    }
                    for edge in transition.get("edges", ()):
                        semantic_edges += 1
                        if str(edge.get("sourceNodeId", "")) not in source_ids:
                            errors.append(
                                f"chapter {theorem} action {action_index} edge has missing source"
                            )
                        if str(edge.get("targetNodeId", "")) not in target_ids:
                            errors.append(
                                f"chapter {theorem} action {action_index} edge has missing target"
                            )

        seen.add(theorem)
    if chapter_count and not last_is_main:
        errors.append("main theorem chapter must be last")
    return {
        "schemaVersion": 1,
        "mode": "kernel-certified-source-tactics",
        "theorem": metadata.get("theoremName", ""),
        "valid": not errors,
        "errors": errors,
        "summary": {
            "chapters": chapter_count,
            "goalActions": action_count,
            "certifiedGoalActions": certified_actions,
            "semanticEdges": semantic_edges,
        },
    }


def require_hybrid_audit(raw: dict[str, Any]) -> dict[str, Any]:
    audit = build_hybrid_audit(raw)
    if not audit["valid"]:
        raise ValueError("strict hybrid proof audit failed: " + "; ".join(audit["errors"][:8]))
    return audit


def require_hybrid_audit_chapters(
    metadata: dict[str, Any], chapters: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    audit = build_hybrid_audit_chapters(metadata, chapters)
    if not audit["valid"]:
        raise ValueError("strict hybrid proof audit failed: " + "; ".join(audit["errors"][:8]))
    return audit


def build_strict_audit(movie: Movie) -> dict[str, Any]:
    """Audit the proof timeline before any SVG or Manim work begins."""

    errors: list[str] = []
    transitions = []
    trace = movie.proof_trace
    if trace is None:
        return {
            "schemaVersion": 1,
            "mode": "strict-proof-term",
            "valid": False,
            "errors": ["strict mode requires ProofTrace v2"],
            "summary": {},
            "transitions": [],
        }

    rigorous_steps = trace.rigorous_steps()
    render_steps = trace.render_steps()
    frame_ids = tuple(frame.index for frame in movie.frames)
    render_ids = tuple(step.id for step in render_steps)
    if frame_ids != render_ids:
        errors.append(
            "render timeline does not contain every mathematical inference exactly once"
        )

    states = trace.rigorous_states(render_only=True)
    frontier_issues = temporal_frontier_issues(states)
    errors.extend(issue.message() for issue in frontier_issues)
    rendered_premises = trace.rendered_premise_map()
    steps_by_id = {step.id: step for step in trace.steps}
    certified_instantiations = 0
    for step in render_steps:
        if not step.instantiation_value_latex:
            continue
        certified_instantiations += 1
        # The checked binder/value pair remains in immutable trace evidence.
        # It intentionally need not become an administrative ``x := value``
        # blackboard row: the semantic edge animates the substitution in
        # place, while validation still rejects missing instantiation data.
    premise_failures = 0
    for (source_step, source_context), (target_step, target_context) in zip(
        states, states[1:], strict=False
    ):
        visible_source_ids = {source_step.id, *(item.id for item in source_context)}
        # A run of certified binders may be introduced between two rendered
        # inference rows. They are written as the leading rows of the target
        # state before its conclusion, so they are available premises even
        # though no separate mathematical-inference frame represents them.
        introduced_context_ids = {
            item.id
            for item in target_context
            if item.kind
            in {"assumption", "eigenvariable", "definition", "proof-definition"}
            and source_step.id < item.id < target_step.id
        }
        # A contracted implication/forall introduction may create and
        # discharge its binder entirely between two rendered mathematical
        # rows.  It is not legal to show that binder in the earlier frame,
        # but it remains an explicit kernel-checked premise of the target
        # rule in the authoritative trace.
        contracted_discharged_ids = {
            item.id
            for item in trace.steps
            if item.kind in {"assumption", "eigenvariable"}
            and item.opens_scope is not None
            and item.opens_scope == target_step.closes_scope
            and source_step.id < item.id < target_step.id
        }
        available_ids = (
            visible_source_ids
            | introduced_context_ids
            | contracted_discharged_ids
        )
        visible_fingerprints = {
            steps_by_id[item].proposition_fingerprint
            for item in available_ids
            if item in steps_by_id
        }
        visible_lean_propositions = {
            steps_by_id[item].proposition_lean
            for item in available_ids
            if item in steps_by_id and steps_by_id[item].proposition_lean
        }
        missing = sorted(
            premise
            for premise in rendered_premises[target_step.id]
            if premise not in available_ids
            and (
                premise not in steps_by_id
                or not steps_by_id[premise].proposition_fingerprint
                or steps_by_id[premise].proposition_fingerprint
                not in visible_fingerprints
            )
            and not (
                premise in steps_by_id
                and steps_by_id[premise].proposition_lean
                in visible_lean_propositions
            )
        )
        if missing:
            premise_failures += 1
            errors.append(
                f"step {target_step.id} has non-visible contracted premises {missing}"
            )

    accepted_edges = 0
    rejected_edges = 0
    for source_frame, target_frame in zip(
        movie.frames, movie.frames[1:], strict=False
    ):
        target = target_frame.display_goals[0]
        transition = target.semantic_transition
        transition_errors: list[str] = []
        rejected_edge_rows = []
        reason_counts: Counter[str] = Counter()
        if transition is None:
            transition_errors.append("semantic transition is missing")
        else:
            if transition.proof_kind != "certified-proof-term":
                transition_errors.append(
                    f"uncertified proof kind {transition.proof_kind!r}"
                )
            source_ids = {node.node_id for node in transition.source.nodes}
            target_ids = {node.node_id for node in transition.target.nodes}
            for node in (*transition.source.nodes, *transition.target.nodes):
                if not node.node_id:
                    transition_errors.append("semantic node has no id")
                if any(not span.valid for span in node.latex_spans):
                    transition_errors.append(f"node {node.node_id} has an invalid span")
            for edge in transition.edges:
                reason_counts[edge.reason] += 1
                certified = _certified_edge(edge)
                if certified:
                    accepted_edges += 1
                else:
                    rejected_edges += 1
                    transition_errors.append(
                        f"uncertified edge reason {edge.reason!r}: "
                        f"{edge.source_node_id} -> {edge.target_node_id}"
                    )
                if edge.source_node_id not in source_ids:
                    transition_errors.append(
                        f"edge cites missing source node {edge.source_node_id}"
                    )
                if edge.target_node_id not in target_ids:
                    transition_errors.append(
                        f"edge cites missing target node {edge.target_node_id}"
                    )
                if not certified:
                    rejected_edge_rows.append(
                        {
                            "sourceNodeId": edge.source_node_id,
                            "targetNodeId": edge.target_node_id,
                            "reason": edge.reason,
                        }
                    )
        if transition_errors:
            errors.extend(
                f"transition {source_frame.index}->{target_frame.index}: {message}"
                for message in transition_errors
            )
        transitions.append(
            {
                "fromStep": source_frame.index,
                "toStep": target_frame.index,
                "rule": target_frame.tactic,
                "valid": not transition_errors,
                "errors": transition_errors,
                "certifiedEdgeCount": sum(reason_counts.values()) - len(rejected_edge_rows),
                "rejectedEdges": rejected_edge_rows,
                "reasonCounts": dict(sorted(reason_counts.items())),
            }
        )

    hidden_administrative = [
        {
            "step": step.id,
            "reason": trace.administrative_reason(step),
            "rule": step.rule,
            "theorem": step.theorem_name,
        }
        for step in rigorous_steps
        if trace.administrative_reason(step) is not None
    ]
    return {
        "schemaVersion": 1,
        "mode": "strict-proof-term",
        "theorem": movie.theorem_name,
        "valid": not errors,
        "errors": errors,
        "summary": {
            "kernelTraceSteps": len(trace.steps),
            "kernelInferenceSteps": len(rigorous_steps),
            "contextOnlySteps": len(trace.steps) - len(rigorous_steps),
            "hiddenAdministrativeSteps": len(hidden_administrative),
            "renderedInferenceSteps": len(movie.frames),
            "transitions": max(0, len(movie.frames) - 1),
            "premiseCoverageFailures": premise_failures,
            "temporalFrontierFailures": len(frontier_issues),
            "certifiedSemanticEdges": accepted_edges,
            "uncertifiedSemanticEdges": rejected_edges,
            "certifiedForallInstantiations": certified_instantiations,
        },
        "hiddenAdministrative": hidden_administrative,
        "transitions": transitions,
    }


def require_strict_audit(movie: Movie) -> dict[str, Any]:
    audit = build_strict_audit(movie)
    if not audit["valid"]:
        preview = "; ".join(audit["errors"][:8])
        raise ValueError(f"strict transition audit failed: {preview}")
    return audit
