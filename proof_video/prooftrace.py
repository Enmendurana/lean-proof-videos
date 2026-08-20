from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from proof_video.models import ProofTrace


class ProofTraceValidationError(ValueError):
    """The exported proof timeline is not safe to animate as a proof."""


@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    errors: tuple[str, ...]


def _schema_at_least(version: str, major: int, minor: int) -> bool:
    try:
        parts = version.split(".", 2)
        current = (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except (TypeError, ValueError):
        return False
    return current >= (major, minor)


def validate_trace(trace: "ProofTrace") -> ValidationReport:
    """Validate ordering, Fitch scope and the kernel flags of ProofTrace v2.

    Lean validates the expressions and final theorem type before export.  This
    second, deliberately independent pass protects the renderer from malformed
    or hand-edited JSON: no animation may cite a future/out-of-scope row.
    """

    errors: list[str] = []
    steps_by_id = {step.id: step for step in trace.steps}
    expected_ids = tuple(range(len(trace.steps)))
    actual_ids = tuple(step.id for step in trace.steps)
    if actual_ids != expected_ids:
        errors.append("step ids must be contiguous and ordered from zero")

    chapter_by_step = {step.id: trace.chapter_for_step(step.id) for step in trace.steps}
    if trace.chapters:
        expected_start = 0
        for index, chapter in enumerate(trace.chapters):
            if chapter.id != index:
                errors.append("chapter ids must be contiguous and ordered from zero")
            if chapter.start_step_id != expected_start:
                errors.append(
                    f"chapter {chapter.id} starts at {chapter.start_step_id}, "
                    f"expected {expected_start}"
                )
            next_start = (
                trace.chapters[index + 1].start_step_id
                if index + 1 < len(trace.chapters)
                else len(trace.steps)
            )
            if not chapter.start_step_id <= chapter.final_step_id < next_start:
                errors.append(f"chapter {chapter.id} has an invalid final step")
            expected_start = next_start
        if sum(chapter.is_main for chapter in trace.chapters) != 1:
            errors.append("hierarchical trace must contain exactly one main chapter")
        elif not trace.chapters[-1].is_main:
            errors.append("main theorem chapter must be last")

    scope_parents: dict[str, str | None] = {"root": None}
    for step in trace.steps:
        if step.parent_scope_id is None:
            scope_parents.setdefault(step.scope_id, None)
        if step.opens_scope:
            expected_parent = step.parent_scope_id or "root"
            previous = scope_parents.setdefault(step.opens_scope, expected_parent)
            if previous != expected_parent:
                errors.append(
                    f"scope {step.opens_scope!r} has conflicting parents "
                    f"{previous!r} and {expected_parent!r}"
                )

    def is_ancestor(candidate: str, scope: str) -> bool:
        seen: set[str] = set()
        current: str | None = scope
        while current is not None and current not in seen:
            if current == candidate:
                return True
            seen.add(current)
            current = scope_parents.get(current)
        return False

    for step in trace.steps:
        if not step.kernel_checked:
            errors.append(f"step {step.id} is not marked kernel-checked")
        if step.scope_id not in scope_parents:
            errors.append(f"step {step.id} uses unknown scope {step.scope_id!r}")
        if (
            _schema_at_least(trace.schema_version, 2, 2)
            and step.rule == "forall-elimination"
            and not (
                step.instantiation_binder_name
                and step.instantiation_value_latex
                and step.instantiation_value_lean
            )
        ):
            errors.append(
                f"step {step.id} forall elimination has no certified instantiation"
            )
        for premise_id in step.premises:
            premise = steps_by_id.get(premise_id)
            if premise is None:
                errors.append(f"step {step.id} cites missing premise {premise_id}")
                continue
            if premise_id >= step.id:
                errors.append(f"step {step.id} cites non-earlier premise {premise_id}")
            ordinarily_visible = is_ancestor(premise.scope_id, step.scope_id)
            discharged_here = bool(
                step.closes_scope and is_ancestor(step.closes_scope, premise.scope_id)
            )
            premise_chapter = chapter_by_step.get(premise.id)
            consumer_chapter = chapter_by_step.get(step.id)
            proved_local_theorem = bool(
                premise_chapter is not None
                and consumer_chapter is not None
                and premise.id == premise_chapter.final_step_id
                and premise_chapter.id < consumer_chapter.id
                and premise_chapter.theorem_name in consumer_chapter.dependencies
            )
            if (
                not ordinarily_visible
                and not discharged_here
                and not proved_local_theorem
            ):
                errors.append(
                    f"step {step.id} cites out-of-scope premise {premise_id} "
                    f"from {premise.scope_id!r}"
                )

    if trace.final_step_id not in steps_by_id:
        errors.append(f"final step {trace.final_step_id} does not exist")
    elif trace.final_step_id != len(trace.steps) - 1:
        errors.append("final theorem must be the final timeline row")
    if not trace.valid:
        errors.append("Lean-side validation is false")

    return ValidationReport(valid=not errors, errors=tuple(errors))


def require_valid_trace(trace: "ProofTrace") -> None:
    report = validate_trace(trace)
    if not report.valid:
        raise ProofTraceValidationError("; ".join(report.errors))
