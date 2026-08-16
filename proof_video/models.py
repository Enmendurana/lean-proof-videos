from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from proof_video.proof.schema import (
    Frame,
    Goal,
    IndexMaps,
    LatexHypothesis,
    ProofStep,
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
from proof_video.proof.trace import ProofTrace, _LEXICAL_CONTEXT_KINDS


# Compact public schema facade.  Implementations live in ``proof.schema`` and
# ``proof.semantics``; these explicit re-exports preserve the existing API.
__all__ = [
    "Frame",
    "Goal",
    "IndexMaps",
    "LatexHypothesis",
    "Movie",
    "ProofStep",
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


def _proof_definition_aliases(
    ids: set[int] | frozenset[int],
    steps_by_id: dict[int, ProofStep],
    previous_context: tuple[ProofStep, ...],
) -> frozenset[int]:
    definitions = {
        steps_by_id[item].proposition_lean
        for item in ids
        if item in steps_by_id
        and steps_by_id[item].kind == "proof-definition"
        and steps_by_id[item].proposition_lean
    }
    return frozenset(
        candidate.id
        for candidate in previous_context
        if candidate.proposition_lean in definitions
    )


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


@dataclass(frozen=True)
class Movie:
    theorem_name: str
    frames: tuple[Frame, ...]
    proof_trace: ProofTrace | None = None
    hybrid_trace: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> "Movie":
        if str(value.get("schemaVersion", "")).startswith("3") and "chapters" in value:
            return cls.from_hybrid_chapters(value, value.get("chapters", ()))
        if str(value.get("schemaVersion", "")).startswith("2") and "steps" in value:
            trace = ProofTrace.from_json(value)
            from proof_video.prooftrace import require_valid_trace

            require_valid_trace(trace)
            aliases: dict[int, str] = {}
            hypothesis_count = 0
            variable_count = 0
            for binder in trace.steps:
                if binder.kind not in {"assumption", "eigenvariable", "definition"}:
                    continue
                original = binder.binder_name or ""
                if _friendly_local_name(original):
                    aliases[binder.id] = original
                elif binder.kind == "assumption":
                    hypothesis_count += 1
                    aliases[binder.id] = f"h{hypothesis_count}"
                else:
                    variable_count += 1
                    aliases[binder.id] = f"v{variable_count}"

            frames: list[Frame] = []
            steps_by_id = {step.id: step for step in trace.steps}
            ancestor_cache: dict[int, frozenset[int]] = {}

            def proof_ancestors(step_id: int) -> frozenset[int]:
                cached = ancestor_cache.get(step_id)
                if cached is not None:
                    return cached
                result: set[int] = set()
                pending = list(steps_by_id[step_id].premises)
                while pending:
                    premise = pending.pop()
                    if premise in result:
                        continue
                    result.add(premise)
                    dependency = steps_by_id.get(premise)
                    if dependency is not None:
                        cached_dependency = ancestor_cache.get(premise)
                        if cached_dependency is not None:
                            result.update(cached_dependency)
                        else:
                            pending.extend(dependency.premises)
                frozen = frozenset(result)
                ancestor_cache[step_id] = frozen
                return frozen

            previous_step: ProofStep | None = None
            previous_context: tuple[ProofStep, ...] = ()
            rendered_premises = trace.rendered_premise_map()
            rendered_premise_branches = trace.rendered_premise_branches()
            for step, context_steps in trace.rigorous_states(render_only=True):
                ancestors = proof_ancestors(step.id)
                rendered_premise_ids = frozenset(rendered_premises[step.id])
                rendered_definition_aliases = _proof_definition_aliases(
                    rendered_premise_ids, steps_by_id, previous_context
                )
                ancestor_definition_aliases = _proof_definition_aliases(
                    ancestors, steps_by_id, previous_context
                )

                context = tuple(
                    LatexHypothesis(
                        name=(
                            aliases[binder.id]
                            if binder.kind in _LEXICAL_CONTEXT_KINDS
                            else ""
                        ),
                        latex=binder.proposition_latex,
                        key=f"proof-context-{binder.id}",
                        raw_latex=(
                            _rename_definition_latex(
                                binder.display_latex,
                                binder.binder_name or "",
                                aliases[binder.id],
                            )
                            if binder.kind == "definition"
                            else (
                                binder.proposition_latex
                                if binder.kind not in _LEXICAL_CONTEXT_KINDS
                                else None
                            )
                        ),
                    )
                    for binder in context_steps
                )
                goal = Goal(
                    goal_id=f"proof-step-{step.id}",
                    state=step.proposition_lean,
                    latex_target=step.proposition_latex,
                    latex_context=context,
                    lineage_id="proof-sequent",
                    semantic_transition=_proof_sequent_transition(
                        previous_step,
                        previous_context,
                        step,
                        context_steps,
                        aliases,
                        ancestors,
                        frozenset(
                            steps_by_id[premise].proposition_fingerprint
                            for premise in rendered_premise_ids
                            if premise in steps_by_id
                        ),
                        frozenset(
                            steps_by_id[ancestor].proposition_fingerprint
                            for ancestor in ancestors
                            if ancestor in steps_by_id
                        ),
                        frozenset(
                            {
                                *rendered_premise_ids,
                                *rendered_definition_aliases,
                            }
                        ),
                        ancestor_definition_aliases,
                        tuple(
                            (
                                steps_by_id[premise],
                                frozenset(branch),
                            )
                            for premise, branch in rendered_premise_branches[step.id]
                            if premise in steps_by_id
                        ),
                        steps_by_id,
                    ),
                )
                frames.append(
                    Frame(
                        index=step.id,
                        tactic=step.rule,
                        goals=(goal,),
                        focus_goals=(goal,),
                    )
                )
                previous_step = step
                previous_context = context_steps
            return cls(
                theorem_name=trace.theorem_name,
                frames=tuple(frames),
                proof_trace=trace,
            )
        # Older source-tactic traces remain valid proof evidence, but their
        # presentation payload predates fallback-LaTeX span remapping and the
        # unique-identity edge completion emitted by the current extractor.
        # Normalize only the renderer-facing copy; the strict audit continues
        # to consume the original immutable trace document.
        from proof_video.proof.hybrid_normalization import (
            normalize_source_tactic_movie,
        )

        value = normalize_source_tactic_movie(value)
        next_lineage = 1
        goals = [Goal.from_json(value["startGoal"], lineage_id="goal-0")]
        frames = [Frame(index=0, tactic="", goals=tuple(goals), focus_goals=tuple(goals))]

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
                    # If an action closes its focused goal, switch to the next
                    # active goal exactly as the upstream animation does.
                    focus_goals=tuple(affected if affected else goals[:1]),
                )
            )

        return cls(theorem_name=value.get("theoremName", "Lean theorem"), frames=tuple(frames))

    @classmethod
    def from_hybrid_chapters(
        cls,
        manifest: dict[str, Any],
        chapters,
    ) -> "Movie":
        """Build the render timeline while releasing each raw chapter promptly."""

        validation = manifest.get("validation", {})
        if not validation.get("valid", False):
            errors = "; ".join(str(item) for item in validation.get("errors", ()))
            raise ValueError(f"invalid hybrid Lean trace: {errors or 'validation failed'}")
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
        """Keep upstream action order, removing only identical full goal states.

        A state includes every semantic LaTeX hypothesis and target for every
        active goal.  This preserves tactics that change local hypotheses even
        when the final target itself stays unchanged.
        """
        result: list[Frame] = []
        previous: tuple[tuple[str, tuple[tuple[str, str], ...], str], ...] | None = None

        for frame in self.frames:
            display_goals = frame.display_goals
            signature = tuple(
                (
                    goal.lineage_id,
                    tuple((hypothesis.name, hypothesis.latex) for hypothesis in goal.latex_context),
                    goal.latex_target if goal.latex_target is not None else goal.state,
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

    # Backwards-compatible name for callers created before full semantic state
    # preservation was introduced.
    def visible_frames(self) -> tuple[Frame, ...]:
        return self.semantic_frames()
