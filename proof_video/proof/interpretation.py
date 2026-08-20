"""Semantic interpretation of an observed canonical state delta.

Interpretations are explanatory labels, never proof evidence.  They are
derived from typed effects; tactic text is retained only as an optional hint
for narration and cannot change the classification.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from proof_video.proof.correspondence import EntityKind
from proof_video.proof.effects import (
    ContextEffectKind,
    GoalEffectKind,
    ProofTransition,
    TargetEffectKind,
)


class SemanticEvent(str, Enum):
    IDENTITY = "identity"
    INTRODUCTION = "introduction"
    REVERSION = "reversion"
    SPECIALIZATION = "specialization"
    REPLACEMENT = "replacement"
    SUBSTITUTION = "substitution"
    REWRITING = "rewriting"
    PRESENTATION_CHANGE = "presentation-change"
    REFINEMENT = "refinement"
    BRANCH_CREATION = "branch-creation"
    GOAL_MERGE = "goal-merge"
    GOAL_CLOSURE = "goal-closure"
    GOAL_CREATION = "goal-creation"
    GOAL_REORDERING = "goal-reordering"
    GOAL_FOCUS = "goal-focus"
    CONTEXT_CHANGE = "context-change"
    COMPOSITE = "composite"


class AutomationPolicy(str, Enum):
    COLLAPSED = "collapsed"
    EXPANDED = "expanded"
    SELECTED = "selected"


@dataclass(frozen=True)
class TransitionInterpretation:
    primary: SemanticEvent
    secondary: tuple[SemanticEvent, ...] = ()
    automation_policy: AutomationPolicy = AutomationPolicy.EXPANDED
    tactic_hint: str = ""


def interpret_transition(transition: ProofTransition) -> TransitionInterpretation:
    """Classify a morphism by its normalized effects, not tactic spelling."""

    delta = transition.normalized()
    if delta.is_identity:
        return TransitionInterpretation(
            SemanticEvent.IDENTITY,
            tactic_hint=delta.metadata.tactic_text,
        )

    context = {effect.kind for effect in delta.context_effects}
    targets = {effect.kind for effect in delta.target_effects}
    goals = {effect.kind for effect in delta.goal_effects}
    events: list[SemanticEvent] = []

    if GoalEffectKind.SPLIT in goals:
        events.append(SemanticEvent.BRANCH_CREATION)
    if GoalEffectKind.MERGE in goals:
        events.append(SemanticEvent.GOAL_MERGE)
    if GoalEffectKind.CLOSE in goals:
        events.append(SemanticEvent.GOAL_CLOSURE)
    if GoalEffectKind.CREATE in goals:
        events.append(SemanticEvent.GOAL_CREATION)
    if GoalEffectKind.REORDER in goals:
        events.append(SemanticEvent.GOAL_REORDERING)
    if GoalEffectKind.FOCUS in goals:
        events.append(SemanticEvent.GOAL_FOCUS)

    if ContextEffectKind.REPLACE_LOCAL in context:
        events.append(SemanticEvent.REPLACEMENT)
    context_substitution = any(effect.entity_ids for effect in delta.context_effects)
    if TargetEffectKind.SUBSTITUTE_ENTITY in targets or context_substitution:
        events.append(SemanticEvent.SUBSTITUTION)

    added = bool(
        context
        & {
            ContextEffectKind.ADD_LOCAL,
            ContextEffectKind.ADD_LOCAL_DEFINITION,
        }
    )
    removed = ContextEffectKind.REMOVE_LOCAL in context
    target_changed = bool(
        targets
        & {
            TargetEffectKind.REWRITE,
            TargetEffectKind.REWRITE_SUBEXPRESSION,
            TargetEffectKind.SUBSTITUTE_ENTITY,
        }
    )
    binder_intro = any(
        len(edge.sources) == len(edge.targets) == 1
        and edge.sources[0].kind is EntityKind.OCCURRENCE
        and edge.targets[0].kind is EntityKind.LOCAL
        and any("binder" in item.lower() for item in edge.evidence)
        for edge in delta.correspondence.edges
    )
    binder_revert = any(
        len(edge.sources) == len(edge.targets) == 1
        and edge.sources[0].kind is EntityKind.LOCAL
        and edge.targets[0].kind is EntityKind.OCCURRENCE
        and any("binder" in item.lower() for item in edge.evidence)
        for edge in delta.correspondence.edges
    )
    if added and target_changed and binder_intro:
        events.append(SemanticEvent.INTRODUCTION)
    elif (
        removed
        and target_changed
        and TargetEffectKind.SUBSTITUTE_ENTITY not in targets
        and binder_revert
    ):
        events.append(SemanticEvent.REVERSION)
    elif ContextEffectKind.UPDATE_LOCAL_TYPE in context and not context_substitution:
        events.append(SemanticEvent.SPECIALIZATION)

    if targets & {
        TargetEffectKind.REWRITE,
        TargetEffectKind.REWRITE_SUBEXPRESSION,
    }:
        events.append(SemanticEvent.REWRITING)
    if TargetEffectKind.CHANGE_PRESENTATION in targets:
        events.append(SemanticEvent.PRESENTATION_CHANGE)
    if context and not any(
        item in events
        for item in {
            SemanticEvent.INTRODUCTION,
            SemanticEvent.REVERSION,
            SemanticEvent.REPLACEMENT,
            SemanticEvent.SPECIALIZATION,
            SemanticEvent.SUBSTITUTION,
        }
    ):
        events.append(SemanticEvent.CONTEXT_CHANGE)
    if GoalEffectKind.PRESERVE in goals and not events:
        events.append(SemanticEvent.REFINEMENT)

    events = list(dict.fromkeys(events)) or [SemanticEvent.COMPOSITE]
    policy = (
        AutomationPolicy.COLLAPSED
        if len(delta.effects) > 12
        else AutomationPolicy.SELECTED
        if len(delta.effects) > 5
        else AutomationPolicy.EXPANDED
    )
    return TransitionInterpretation(
        primary=events[0],
        secondary=tuple(events[1:]),
        automation_policy=policy,
        tactic_hint=delta.metadata.tactic_text,
    )
