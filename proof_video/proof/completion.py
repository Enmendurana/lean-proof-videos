"""Renderer-independent evidence that a proof really reached ``no goals``.

The last *visible* proof state is not evidence of completion: renderers omit
Lean's empty closing frontier because there is no formula to draw.  This
module keeps that terminal observation as immutable data so previews,
chapter projections, and both rendering backends make the same QED decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class CompletionStatus(str, Enum):
    """What the authoritative trace says about its terminal goal frontier."""

    CERTIFIED_CLOSED = "certified-closed"
    OPEN = "open"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TerminalCompletion:
    """A terminal frontier observation, independent of presentation frames."""

    status: CompletionStatus = CompletionStatus.UNKNOWN
    source: str = "missing-terminal-frontier"
    action_index: int | None = None
    remaining_goal_ids: tuple[str, ...] = ()

    @property
    def certified_closed(self) -> bool:
        return self.status is CompletionStatus.CERTIFIED_CLOSED

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "source": self.source,
            "actionIndex": self.action_index,
            "remainingGoalIds": list(self.remaining_goal_ids),
            "certifiedClosed": self.certified_closed,
        }

    @classmethod
    def unknown(cls, source: str = "missing-terminal-frontier") -> "TerminalCompletion":
        return cls(source=source)


def source_tactic_completion(movie: dict[str, Any]) -> TerminalCompletion:
    """Read only Lean's explicit final ABI-5 action frontier.

    In ABI 1--4 an omitted ``afterState`` and an empty frontier are
    indistinguishable.  Reconstructing closure from the last visible frame or
    from an empty legacy ``results`` list is precisely the ambiguity that used
    to create false QED squares, so legacy/malformed input remains unknown.
    """

    actions = tuple(movie.get("actions", ()))
    if not actions:
        return TerminalCompletion.unknown("no-terminal-action")
    action_index = len(actions) - 1
    final_action = actions[action_index]
    canonical_abi = int(movie.get("canonicalAbi", 0))
    capabilities = {str(item) for item in movie.get("capabilities", ())}
    if canonical_abi < 5 or "ordered-action-frontiers" not in capabilities:
        return TerminalCompletion.unknown("non-authoritative-terminal-frontier")
    if "afterState" not in final_action:
        return TerminalCompletion.unknown("missing-terminal-after-state")
    frontier = tuple(final_action.get("afterState", ()))
    remaining = tuple(str(goal.get("goalId", "")) for goal in frontier)
    if remaining:
        return TerminalCompletion(
            status=CompletionStatus.OPEN,
            source="lean-ordered-action-frontier",
            action_index=action_index,
            remaining_goal_ids=remaining,
        )
    return TerminalCompletion(
        status=CompletionStatus.CERTIFIED_CLOSED,
        source="lean-ordered-action-frontier",
        action_index=action_index,
    )


def kernel_trace_completion(trace: Any) -> TerminalCompletion:
    """Use the strict schema-v2 final proof row as its terminal certificate."""

    steps = {step.id: step for step in trace.steps}
    final = steps.get(trace.final_step_id)
    if not trace.valid or final is None or not final.kernel_checked:
        return TerminalCompletion.unknown("invalid-kernel-proof-terminal")
    return TerminalCompletion(
        status=CompletionStatus.CERTIFIED_CLOSED,
        source="kernel-proof-trace-final-step",
        action_index=trace.final_step_id,
    )


def certify_hybrid_completion(
    manifest: dict[str, Any],
    final_chapter: dict[str, Any] | None,
    chapter_completion: TerminalCompletion,
    *,
    selected_chapter_count: int,
) -> TerminalCompletion:
    """Promote a main chapter's empty frontier only with hybrid certificates."""

    declared_chapters = manifest.get("chapters", manifest.get("chapterRefs", ()))
    if declared_chapters and selected_chapter_count != len(declared_chapters):
        return TerminalCompletion.unknown("hybrid-chapter-selection-is-partial")
    if final_chapter is None or not bool(final_chapter.get("isMain", False)):
        return TerminalCompletion.unknown("selected-chapters-do-not-end-in-main")
    validation = manifest.get("validation", {})
    chapter_validation = final_chapter.get("validation", {})
    certified = (
        bool(validation.get("valid", False))
        and bool(validation.get("dependencyOrderValid", False))
        and bool(validation.get("allChaptersKernelChecked", False))
        and bool(validation.get("noSorry", False))
        and bool(chapter_validation.get("valid", False))
        and bool(chapter_validation.get("kernelChecked", False))
        and bool(chapter_validation.get("noSorry", False))
    )
    if not certified:
        return TerminalCompletion.unknown("hybrid-kernel-certificate-incomplete")
    if chapter_completion.certified_closed:
        return TerminalCompletion(
            status=CompletionStatus.CERTIFIED_CLOSED,
            source="hybrid-main-kernel-and-ordered-frontier",
            action_index=chapter_completion.action_index,
        )
    return chapter_completion
