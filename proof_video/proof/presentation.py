"""Project a certified proof DAG onto one live blackboard sequent.

This module deliberately contains no tactic-name or glyph-specific rules.  The
immutable :class:`ProofTrace` decides which kernel-checked rows are live; this
module only assigns stable display names and asks the semantic layer to derive
correspondences from proof identities and expression paths.

Keeping this projection separate from ``models.py`` is important: parsing a
trace, choosing its live frontier, and rendering an animation are different
responsibilities.  In particular, a completed conclusion is never hidden and
then reconstructed by synthetic ``forall`` presentation states.
"""

from __future__ import annotations

from proof_video.proof.schema import Frame, Goal, LatexHypothesis, ProofStep
from proof_video.proof.semantics import (
    _friendly_local_name,
    _proof_sequent_transition,
    _rename_definition_latex,
)
from proof_video.proof.trace import ProofTrace, _LEXICAL_CONTEXT_KINDS


def _display_aliases(trace: ProofTrace) -> dict[int, str]:
    aliases: dict[int, str] = {}
    hypothesis_count = 0
    variable_count = 0
    for binder in trace.steps:
        if binder.kind not in {
            "assumption",
            "eigenvariable",
            "definition",
            "proof-definition",
        }:
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
    return aliases


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


def _render_context(
    context_steps: tuple[ProofStep, ...], aliases: dict[int, str]
) -> tuple[LatexHypothesis, ...]:
    """Render exactly the certified live frontier, once per proof identity."""

    return tuple(
        LatexHypothesis(
            name=(
                aliases[binder.id]
                if binder.kind in _LEXICAL_CONTEXT_KINDS
                or binder.kind == "proof-definition"
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
                    None
                    if binder.kind == "proof-definition"
                    else (
                        binder.proposition_latex
                        if binder.kind not in _LEXICAL_CONTEXT_KINDS
                        else None
                    )
                )
            ),
        )
        for binder in context_steps
    )


def build_certified_proof_frames(trace: ProofTrace) -> tuple[Frame, ...]:
    """Build the rigorous visual timeline without synthetic logical steps.

    Every conclusion and context row comes from the trace.  Proof-producing
    lets remain visible under their stable proof identity for as long as the
    DAG frontier needs them.  The transition layer receives the exact direct
    premise branches, so it may copy subexpressions from several rows without
    pairing equal-looking glyphs.
    """

    aliases = _display_aliases(trace)
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
            if dependency is None:
                continue
            cached_dependency = ancestor_cache.get(premise)
            if cached_dependency is not None:
                result.update(cached_dependency)
            else:
                pending.extend(dependency.premises)
        frozen = frozenset(result)
        ancestor_cache[step_id] = frozen
        return frozen

    frames: list[Frame] = []
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
        semantic_transition = _proof_sequent_transition(
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
                (steps_by_id[premise], frozenset(branch))
                for premise, branch in rendered_premise_branches[step.id]
                if premise in steps_by_id
            ),
            steps_by_id,
        )
        goal = Goal(
            goal_id=f"proof-step-{step.id}",
            state=step.proposition_lean,
            latex_target=step.proposition_latex,
            latex_context=_render_context(context_steps, aliases),
            lineage_id="proof-sequent",
            semantic_transition=semantic_transition,
            semantic_nodes=(
                semantic_transition.target.nodes
                if semantic_transition is not None
                else ()
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

    return tuple(frames)
