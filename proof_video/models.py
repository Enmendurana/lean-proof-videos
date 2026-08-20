"""Public trace models and routing to the appropriate presentation planner."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from proof_video.proof.correspondence import (
    CorrespondenceEdge,
    ExplicitGoalEdge,
    RelationKind,
    canonical_edge_from_json,
)
from proof_video.proof.completion import (
    TerminalCompletion,
    certify_hybrid_completion,
    kernel_trace_completion,
    source_tactic_completion,
)
from proof_video.proof.schema import (
    Frame,
    Goal,
    GoalDiffEvidence,
    IndexMaps,
    LatexHypothesis,
    ProofStep,
    RuleAnnotation,
    SemanticExpression,
    SemanticExpressionNode,
    SemanticSpan,
    SemanticTransition,
    SemanticTransitionEdge,
)
from proof_video.proof.semantics import (
    _friendly_local_name,
    _occurrence_edges,
    _proof_sequent_latex,
    _proof_sequent_transition,
    _rename_definition_latex,
    _structural_rule_edges,
)
from proof_video.proof.trace import ProofTrace


__all__ = [
    "Frame",
    "Goal",
    "GoalDiffEvidence",
    "IndexMaps",
    "LatexHypothesis",
    "Movie",
    "ProofStep",
    "RuleAnnotation",
    "ProofTrace",
    "SemanticExpression",
    "SemanticExpressionNode",
    "SemanticSpan",
    "SemanticTransition",
    "SemanticTransitionEdge",
    "TerminalCompletion",
    "_friendly_local_name",
    "_occurrence_edges",
    "_proof_sequent_latex",
    "_proof_sequent_transition",
    "_rename_definition_latex",
    "_structural_rule_edges",
]


def _scoped_goal(goal: Goal, prefix: str) -> Goal:
    semantic_transition = goal.semantic_transition
    if (
        isinstance(semantic_transition, SemanticTransition)
        and semantic_transition.goal_diff is not None
    ):
        semantic_transition = replace(
            semantic_transition,
            goal_diff=replace(
                semantic_transition.goal_diff,
                source_goal_id=prefix + semantic_transition.goal_diff.source_goal_id,
                target_goal_id=prefix + semantic_transition.goal_diff.target_goal_id,
            ),
        )
    return replace(
        goal,
        goal_id=prefix + goal.goal_id,
        lineage_id=prefix + goal.lineage_id,
        parent_goal_id=(
            prefix + goal.parent_goal_id if goal.parent_goal_id is not None else None
        ),
        semantic_transition=semantic_transition,
    )


def _scoped_correspondence_edge(
    edge: CorrespondenceEdge, prefix: str
) -> CorrespondenceEdge:
    def scoped(ref):
        return replace(ref, goal_id=prefix + ref.goal_id)

    return replace(
        edge,
        sources=tuple(scoped(ref) for ref in edge.sources),
        targets=tuple(scoped(ref) for ref in edge.targets),
    )


def _source_tactic_movie(value: dict[str, Any]) -> tuple[Frame, ...]:
    """Read Lean's ordered before/after frontiers without reconstructing time.

    ABI 5 exports the complete live goal frontier at every action boundary.
    Older traces keep the previous goal-action replay path below.  The two
    formats share the same public ``Frame`` model; the migration boundary is
    isolated here rather than leaking version checks into planners/renderers.
    """

    from proof_video.proof.hybrid_normalization import normalize_source_tactic_movie

    value = normalize_source_tactic_movie(value)
    canonical_abi = int(value.get("canonicalAbi", 0))
    capabilities = tuple(str(item) for item in value.get("capabilities", ()))
    next_lineage = 1
    goals = [Goal.from_json(value["startGoal"], lineage_id="goal-0")]
    frames = [
        Frame(
            index=0,
            tactic="",
            goals=tuple(goals),
            focus_goals=tuple(goals),
            canonical_abi=canonical_abi,
            capabilities=capabilities,
        )
    ]
    lineage_by_goal = {goals[0].goal_id: goals[0].lineage_id}

    def fresh_lineage() -> str:
        nonlocal next_lineage
        result = f"goal-{next_lineage}"
        next_lineage += 1
        return result

    def relation_kind(
        sources: tuple[str, ...], targets: tuple[str, ...], hint: str
    ) -> RelationKind:
        if not sources:
            return RelationKind.CREATE
        if not targets:
            return RelationKind.REMOVE
        if len(sources) == 1 and len(targets) > 1:
            return RelationKind.SPLIT
        if len(sources) > 1 and len(targets) == 1:
            return RelationKind.MERGE
        if hint == "rewrite":
            return RelationKind.REWRITE
        return RelationKind.PRESERVE

    for index, action in enumerate(value.get("actions", []), start=1):
        if "afterState" in action:
            observed_before = tuple(action.get("beforeState", ()))
            if observed_before:
                observed_ids = tuple(str(item["goalId"]) for item in observed_before)
                current_ids = tuple(goal.goal_id for goal in goals)
                if observed_ids != current_ids:
                    raise ValueError(
                        "Lean action beforeState does not equal the preceding "
                        f"afterState: {observed_ids!r} != {current_ids!r}"
                    )
                for raw_goal, current in zip(observed_before, goals, strict=True):
                    observed = Goal.from_json(
                        raw_goal,
                        lineage_id=current.lineage_id,
                        parent_goal_id=current.parent_goal_id,
                    )
                    canonical_changed = (
                        observed.canonical_locals != current.canonical_locals
                        or observed.canonical_target != current.canonical_target
                    )
                    legacy_changed = (
                        observed.state != current.state
                        or observed.latex_target != current.latex_target
                        or observed.latex_context != current.latex_context
                    )
                    # ABI 5 makes the structured state authoritative.  Lean's
                    # pretty-printer may choose a different but equivalent
                    # spelling at the next InfoTree boundary; treating that
                    # view change as a hidden proof action manufactured the
                    # duplicate rows this architecture is meant to remove.
                    canonical_available = (
                        observed.canonical_target is not None
                        and current.canonical_target is not None
                    )
                    if canonical_changed or (
                        (canonical_abi < 5 or not canonical_available)
                        and legacy_changed
                    ):
                        raise ValueError(
                            "Lean action beforeState changed without an action "
                            f"boundary for goal {current.goal_id}"
                        )
            raw_lineage = action.get("goalLineage", ())
            explicit_goal_edges = tuple(
                ExplicitGoalEdge(
                    source_goal_ids=tuple(
                        str(item) for item in edge.get("sourceGoalIds", ())
                    ),
                    target_goal_ids=tuple(
                        str(item) for item in edge.get("targetGoalIds", ())
                    ),
                    reason=str(edge.get("relation", "observed-frontier")),
                    relation=relation_kind(
                        tuple(str(item) for item in edge.get("sourceGoalIds", ())),
                        tuple(str(item) for item in edge.get("targetGoalIds", ())),
                        str(edge.get("relation", "")),
                    ),
                )
                for edge in raw_lineage
            )
            target_lineages: dict[str, str] = {}
            target_parents: dict[str, str] = {}
            target_branches: dict[str, tuple[str, int]] = {}
            affected_ids: list[str] = []
            for edge in explicit_goal_edges:
                affected_ids.extend(edge.target_goal_ids)
                if len(edge.source_goal_ids) == 1 and len(edge.target_goal_ids) == 1:
                    source_id = edge.source_goal_ids[0]
                    target_lineages[edge.target_goal_ids[0]] = (
                        lineage_by_goal.get(source_id) or fresh_lineage()
                    )
                elif len(edge.source_goal_ids) == 1 and len(edge.target_goal_ids) > 1:
                    source_id = edge.source_goal_ids[0]
                    for branch_index, target_id in enumerate(edge.target_goal_ids):
                        target_lineages[target_id] = fresh_lineage()
                        target_parents[target_id] = source_id
                        target_branches[target_id] = ("split", branch_index)
                elif edge.target_goal_ids:
                    for target_id in edge.target_goal_ids:
                        target_lineages[target_id] = fresh_lineage()

            result_evidence: dict[str, dict[str, Any]] = {}
            for goal_action in action.get("goalActions", ()):
                for result in goal_action.get("results", ()):
                    raw_goal = result.get("goal", {})
                    goal_id = str(raw_goal.get("goalId", ""))
                    if goal_id:
                        result_evidence[goal_id] = result

            observed_goals: list[Goal] = []
            for raw_goal in action.get("afterState", ()):
                goal_id = str(raw_goal["goalId"])
                lineage = (
                    target_lineages.get(goal_id)
                    or lineage_by_goal.get(goal_id)
                    or fresh_lineage()
                )
                evidence = result_evidence.get(goal_id, {})
                parsed = Goal.from_json(
                    raw_goal,
                    lineage_id=lineage,
                    parent_goal_id=target_parents.get(goal_id),
                    index_maps=evidence.get("indexMaps"),
                    latex_index_maps=evidence.get("latexIndexMaps"),
                    semantic_transition=evidence.get(
                        "semanticTransition", raw_goal.get("semanticTransition")
                    ),
                )
                branch_kind, branch_index = target_branches.get(
                    goal_id,
                    (parsed.branch_kind, parsed.branch_index),
                )
                observed_goals.append(
                    replace(
                        parsed,
                        branch_kind=branch_kind,
                        branch_index=branch_index,
                    )
                )
            goals = observed_goals
            lineage_by_goal = {goal.goal_id: goal.lineage_id for goal in observed_goals}
            by_id = {goal.goal_id: goal for goal in observed_goals}
            focus_ids = tuple(str(item) for item in action.get("focusAfter", ()))
            focus_goals = tuple(by_id[item] for item in focus_ids if item in by_id)
            if not focus_goals and goals:
                focus_goals = tuple(
                    by_id[item] for item in affected_ids if item in by_id
                ) or tuple(goals[:1])
            frames.append(
                Frame(
                    index=index,
                    tactic=action.get("tacticText", ""),
                    goals=tuple(goals),
                    focus_goals=focus_goals,
                    goal_lineage=explicit_goal_edges,
                    canonical_correspondence=tuple(
                        canonical_edge_from_json(edge)
                        for edge in action.get("canonicalCorrespondence", ())
                    ),
                    canonical_abi=canonical_abi,
                    capabilities=capabilities,
                )
            )
            continue

        # ABI 1--4 migration: reconstruct the frontier from per-goal results.
        affected: list[Goal] = []
        for goal_action in action.get("goalActions", []):
            start_id = goal_action["startGoalId"]
            position = next(
                (i for i, goal in enumerate(goals) if goal.goal_id == start_id),
                None,
            )
            parent_lineage = (
                goals[position].lineage_id
                if position is not None
                else f"goal-{next_lineage}"
            )
            replacement: list[Goal] = []
            for result_index, result in enumerate(goal_action.get("results", [])):
                if result_index == 0:
                    lineage_id = parent_lineage
                else:
                    lineage_id = fresh_lineage()
                replacement.append(
                    Goal.from_json(
                        result["goal"],
                        lineage_id=lineage_id,
                        parent_goal_id=start_id,
                        index_maps=result.get("indexMaps"),
                        latex_index_maps=result.get("latexIndexMaps"),
                        semantic_transition=result.get("semanticTransition"),
                    )
                )
            affected.extend(replacement)
            if position is not None:
                goals[position : position + 1] = replacement
        lineage_by_goal = {goal.goal_id: goal.lineage_id for goal in goals}
        frames.append(
            Frame(
                index=index,
                tactic=action.get("tacticText", ""),
                goals=tuple(goals),
                # A closing action focuses the next live goal. Its transition
                # is validated against ``parent_goal_id`` by the renderer; a
                # sibling never inherits a transition from the wrong source.
                focus_goals=tuple(affected if affected else goals[:1]),
                canonical_abi=canonical_abi,
                capabilities=capabilities,
            )
        )
    return tuple(frames)


@dataclass(frozen=True)
class Movie:
    theorem_name: str
    frames: tuple[Frame, ...]
    proof_trace: ProofTrace | None = None
    hybrid_trace: dict[str, Any] | None = None
    terminal_completion: TerminalCompletion = TerminalCompletion()

    @property
    def certified_closed(self) -> bool:
        """Whether authoritative proof evidence certifies an empty frontier."""

        return self.terminal_completion.certified_closed

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> "Movie":
        schema = str(value.get("schemaVersion", ""))
        if schema.startswith(("3", "4")) and "chapters" in value:
            return cls.from_hybrid_chapters(value, value.get("chapters", ()))

        if schema.startswith("2") and "steps" in value:
            trace = ProofTrace.from_json(value)
            from proof_video.proof.presentation import build_certified_proof_frames
            from proof_video.prooftrace import require_valid_trace

            require_valid_trace(trace)
            movie = cls(
                theorem_name=trace.theorem_name,
                frames=build_certified_proof_frames(trace),
                proof_trace=trace,
                terminal_completion=kernel_trace_completion(trace),
            )
            return movie.with_canonical_timeline()

        movie = cls(
            theorem_name=value.get("theoremName", "Lean theorem"),
            frames=_source_tactic_movie(value),
            terminal_completion=source_tactic_completion(value),
        )
        return movie.with_canonical_timeline()

    @classmethod
    def from_hybrid_chapters(cls, manifest: dict[str, Any], chapters) -> "Movie":
        """Build the render timeline while releasing raw chapters promptly."""

        validation = manifest.get("validation", {})
        if not validation.get("valid", False):
            errors = "; ".join(str(item) for item in validation.get("errors", ()))
            raise ValueError(
                f"invalid hybrid Lean trace: {errors or 'validation failed'}"
            )
        frames: list[Frame] = []
        chapter_count = 0
        final_chapter: dict[str, Any] | None = None
        final_chapter_completion = TerminalCompletion.unknown("no-hybrid-chapter")
        for chapter_index, chapter in enumerate(chapters):
            chapter_count += 1
            chapter_movie = cls.from_json(chapter["movie"])
            final_chapter = chapter
            final_chapter_completion = chapter_movie.terminal_completion
            prefix = f"chapter-{chapter_index}/"
            for frame in chapter_movie.frames:
                goals = tuple(_scoped_goal(goal, prefix) for goal in frame.goals)
                focus_goals = tuple(
                    _scoped_goal(goal, prefix) for goal in frame.focus_goals
                )
                if not goals and not focus_goals:
                    continue
                frames.append(
                    Frame(
                        index=len(frames),
                        tactic=frame.tactic,
                        goals=goals,
                        focus_goals=focus_goals,
                        goal_lineage=tuple(
                            replace(
                                edge,
                                source_goal_ids=tuple(
                                    prefix + item for item in edge.source_goal_ids
                                ),
                                target_goal_ids=tuple(
                                    prefix + item for item in edge.target_goal_ids
                                ),
                            )
                            for edge in frame.goal_lineage
                        ),
                        canonical_correspondence=tuple(
                            _scoped_correspondence_edge(edge, prefix)
                            for edge in frame.canonical_correspondence
                        ),
                        canonical_abi=frame.canonical_abi,
                        capabilities=frame.capabilities,
                    )
                )
        marker = {
            "schemaVersion": manifest.get("schemaVersion", "3.0"),
            "theoremName": manifest.get("theoremName", "Lean theorem"),
            "validation": validation,
        }
        movie = cls(
            theorem_name=str(manifest.get("theoremName", "Lean theorem")),
            frames=tuple(frames),
            hybrid_trace=manifest if "chapters" in manifest else marker,
            terminal_completion=certify_hybrid_completion(
                manifest,
                final_chapter,
                final_chapter_completion,
                selected_chapter_count=chapter_count,
            ),
        )
        return movie.with_canonical_timeline()

    def with_canonical_timeline(self) -> "Movie":
        """Attach the single authoritative state/delta model to every frame."""

        from proof_video.proof.adapters import attach_canonical_timeline

        return replace(self, frames=attach_canonical_timeline(self.frames))

    def semantic_frames(self) -> tuple[Frame, ...]:
        """Remove only identical complete Lean goal states, preserving order."""

        result: list[Frame] = []
        previous: object | None = None
        for frame in self.frames:
            display_goals = frame.display_goals
            signature: object
            if frame.proof_state is not None:
                # A declaration replacement can look textually identical yet
                # be a real Lean state change.  Conversely, presentation text
                # must never manufacture a mathematical step.
                canonical_goal_ids = set(frame.proof_state.goal_order)
                if all(goal.goal_id in canonical_goal_ids for goal in display_goals):
                    signature = frame.proof_state.fingerprint
                else:
                    # A migrated v1 trace may carry a focused dormant branch
                    # outside its live-goal list. Preserve that observable
                    # state until the trace is re-extracted as canonical v4.
                    signature = (
                        frame.proof_state.fingerprint,
                        tuple(
                            (
                                goal.goal_id,
                                goal.lineage_id,
                                goal.latex_state(),
                            )
                            for goal in display_goals
                        ),
                    )
            else:
                signature = tuple(
                    (
                        goal.lineage_id,
                        tuple(
                            (hypothesis.name, hypothesis.latex)
                            for hypothesis in goal.latex_context
                        ),
                        goal.latex_target
                        if goal.latex_target is not None
                        else goal.state,
                    )
                    for goal in display_goals
                )
            if signature == previous:
                continue
            result.append(
                Frame(
                    index=len(result),
                    tactic=frame.tactic,
                    goals=frame.goals,
                    focus_goals=frame.focus_goals,
                    proof_state=frame.proof_state,
                    proof_transition=frame.proof_transition,
                    visual_plan=frame.visual_plan,
                    goal_lineage=frame.goal_lineage,
                    canonical_correspondence=frame.canonical_correspondence,
                    canonical_abi=frame.canonical_abi,
                    capabilities=frame.capabilities,
                    terminal_completion=frame.terminal_completion,
                )
            )
            previous = signature
        from proof_video.proof.adapters import attach_canonical_timeline

        canonical = list(attach_canonical_timeline(tuple(result)))
        # Lean's certified closing frontier is intentionally not renderable.
        # Attach its terminal signal to the last visible state without
        # pretending that this state itself is the empty frontier.
        for index, frame in enumerate(canonical):
            if frame.terminal_completion is not None:
                canonical[index] = replace(frame, terminal_completion=None)
        terminal_index = next(
            (
                index
                for index in range(len(canonical) - 1, -1, -1)
                if canonical[index].display_goals
            ),
            None,
        )
        if terminal_index is not None:
            canonical[-1] = replace(canonical[-1], terminal_completion=None)
            canonical[terminal_index] = replace(
                canonical[terminal_index],
                terminal_completion=self.terminal_completion,
            )
        return tuple(canonical)

    def visible_frames(self) -> tuple[Frame, ...]:
        """Backward-compatible alias."""

        return self.semantic_frames()
