"""Public trace models and routing to the appropriate presentation planner."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

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
    "_friendly_local_name",
    "_occurrence_edges",
    "_proof_sequent_latex",
    "_proof_sequent_transition",
    "_rename_definition_latex",
    "_structural_rule_edges",
]


def _scoped_goal(goal: Goal, prefix: str) -> Goal:
    return replace(
        goal,
        goal_id=prefix + goal.goal_id,
        lineage_id=prefix + goal.lineage_id,
        parent_goal_id=(
            prefix + goal.parent_goal_id
            if goal.parent_goal_id is not None
            else None
        ),
    )


def _source_tactic_movie(value: dict[str, Any]) -> tuple[Frame, ...]:
    """Follow Lean goal identities and the extractor's depth-first action order."""

    from proof_video.proof.hybrid_normalization import normalize_source_tactic_movie

    value = normalize_source_tactic_movie(value)
    next_lineage = 1
    goals = [Goal.from_json(value["startGoal"], lineage_id="goal-0")]
    frames = [
        Frame(index=0, tactic="", goals=tuple(goals), focus_goals=tuple(goals))
    ]

    for index, action in enumerate(value.get("actions", []), start=1):
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
                    lineage_id = f"goal-{next_lineage}"
                    next_lineage += 1
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
        frames.append(
            Frame(
                index=index,
                tactic=action.get("tacticText", ""),
                goals=tuple(goals),
                # A closing action focuses the next live goal. Its transition
                # is validated against ``parent_goal_id`` by the renderer; a
                # sibling never inherits a transition from the wrong source.
                focus_goals=tuple(affected if affected else goals[:1]),
            )
        )
    return tuple(frames)


@dataclass(frozen=True)
class Movie:
    theorem_name: str
    frames: tuple[Frame, ...]
    proof_trace: ProofTrace | None = None
    hybrid_trace: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> "Movie":
        schema = str(value.get("schemaVersion", ""))
        if schema.startswith("3") and "chapters" in value:
            return cls.from_hybrid_chapters(value, value.get("chapters", ()))

        if schema.startswith("2") and "steps" in value:
            trace = ProofTrace.from_json(value)
            from proof_video.proof.presentation import build_certified_proof_frames
            from proof_video.prooftrace import require_valid_trace

            require_valid_trace(trace)
            return cls(
                theorem_name=trace.theorem_name,
                frames=build_certified_proof_frames(trace),
                proof_trace=trace,
            )

        return cls(
            theorem_name=value.get("theoremName", "Lean theorem"),
            frames=_source_tactic_movie(value),
        )

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
        for chapter_index, chapter in enumerate(chapters):
            chapter_movie = cls.from_json(chapter["movie"])
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
                    )
                )
        marker = {
            "schemaVersion": manifest.get("schemaVersion", "3.0"),
            "theoremName": manifest.get("theoremName", "Lean theorem"),
            "validation": validation,
        }
        return cls(
            theorem_name=str(manifest.get("theoremName", "Lean theorem")),
            frames=tuple(frames),
            hybrid_trace=manifest if "chapters" in manifest else marker,
        )

    def semantic_frames(self) -> tuple[Frame, ...]:
        """Remove only identical complete Lean goal states, preserving order."""

        result: list[Frame] = []
        previous: tuple[tuple[str, tuple[tuple[str, str], ...], str], ...] | None = None
        for frame in self.frames:
            display_goals = frame.display_goals
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
                )
            )
            previous = signature
        return tuple(result)

    def visible_frames(self) -> tuple[Frame, ...]:
        """Backward-compatible alias."""

        return self.semantic_frames()
