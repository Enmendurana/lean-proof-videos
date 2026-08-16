"""Temporal safety rules for the visible proof-DAG frontier."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable

from proof_video.proof.schema import ProofStep


@dataclass(frozen=True)
class TemporalFrontierIssue:
    current_step_id: int
    context_step_id: int
    reason: str

    def message(self) -> str:
        return (
            f"step {self.current_step_id} displays context row "
            f"{self.context_step_id} {self.reason}"
        )


def is_temporally_available(row: ProofStep, current: ProofStep) -> bool:
    """Whether ``row`` has been kernel-checked before ``current`` is shown."""

    return row.kernel_checked and row.id < current.id


def temporal_frontier_issues(
    states: Iterable[tuple[ProofStep, tuple[ProofStep, ...]]],
) -> tuple[TemporalFrontierIssue, ...]:
    """Audit that no displayed context row comes from the proof's future."""

    issues: list[TemporalFrontierIssue] = []
    for current, context in states:
        for row in context:
            if row.id >= current.id:
                issues.append(
                    TemporalFrontierIssue(
                        current.id,
                        row.id,
                        "before that row has been completed",
                    )
                )
            elif not row.kernel_checked:
                issues.append(
                    TemporalFrontierIssue(
                        current.id,
                        row.id,
                        "without a kernel certificate",
                    )
                )
    return tuple(issues)
