"""Renderer-independent rows for an observed Lean goal.

The pretty-printed ``latexContext`` field predates canonical proof states and
contains only a local declaration's type.  ABI 5 additionally records the
optional value of a local ``let``/``set`` declaration.  Presentation must be
derived from that immutable declaration, otherwise a definition such as
``s : Real := ...`` is silently rendered as the unrelated variable
``s : Real``.

Both renderers consume the records in this module.  No renderer is allowed to
reconstruct a context row (or to decide whether a local is a definition) on
its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable
from typing import Any

from proof_video.latex import lean_to_latex
from proof_video.proof.state import CharacterSpan, Expression, LocalDecl


_TYPE_SEPARATOR = r" \;:\; "
_VALUE_SEPARATOR = r" \;\coloneqq\; "


def _expression_latex(expression: Expression) -> str:
    return expression.latex or lean_to_latex(expression.lean)


def _safe_local_name(name: str) -> str:
    # Lean user names are mathematical identifiers, not arbitrary LaTeX.
    # Preserve Unicode/math letters while protecting the most common TeX
    # control character used by generated and user-written local names.
    return name.replace("_", r"\_")


@dataclass(frozen=True)
class ContextPresentationRow:
    """One context line and the exact spans of its canonical components."""

    stable_key: str
    latex: str
    body_latex: str
    declaration_id: str = ""
    name_span: CharacterSpan | None = None
    type_span: CharacterSpan | None = None
    value_span: CharacterSpan | None = None
    local: LocalDecl | None = None

    @property
    def is_definition(self) -> bool:
        return self.local is not None and self.local.value_expr is not None


def _canonical_context_row(local: LocalDecl) -> ContextPresentationRow:
    name = _safe_local_name(local.user_name)
    type_latex = _expression_latex(local.type_expr)
    prefix = f"{name}{_TYPE_SEPARATOR}"
    latex = prefix + type_latex
    value_span = None
    if local.value_expr is not None:
        value_latex = _expression_latex(local.value_expr)
        value_start = len(latex) + len(_VALUE_SEPARATOR)
        latex += _VALUE_SEPARATOR + value_latex
        value_span = CharacterSpan(value_start, value_start + len(value_latex))
    return ContextPresentationRow(
        stable_key=local.decl_id,
        latex=latex,
        body_latex=type_latex,
        declaration_id=local.decl_id,
        name_span=CharacterSpan(0, len(name)),
        type_span=CharacterSpan(len(prefix), len(prefix) + len(type_latex)),
        value_span=value_span,
        local=local,
    )


def presentation_local_declarations(
    declarations: Iterable[LocalDecl],
) -> tuple[LocalDecl, ...]:
    """Project a complete Lean local context onto mathematical rows.

    Visibility is extractor evidence, not a downstream naming convention.
    Hidden declarations remain in :class:`ProofState`, correspondence and
    audit data; only their presentation row is omitted.
    """

    return tuple(local for local in declarations if local.presentation_visible)


def context_presentation_rows(goal: Any) -> tuple[ContextPresentationRow, ...]:
    """Return the unique renderer-facing context projection for ``goal``.

    The presence of ``canonical_target`` distinguishes an observed canonical
    state from a legacy goal whose context happens to be empty.  Canonical
    states use ``canonical_locals`` exclusively; legacy schemas retain their
    original ``rawLatex`` behavior.
    """

    if getattr(goal, "canonical_target", None) is not None:
        return tuple(
            _canonical_context_row(local)
            for local in presentation_local_declarations(
                getattr(goal, "canonical_locals", ())
            )
        )

    result = []
    for index, hypothesis in enumerate(getattr(goal, "latex_context", ())):
        result.append(
            ContextPresentationRow(
                stable_key=hypothesis.key or hypothesis.name or f"slot-{index}",
                latex=hypothesis.render_latex(),
                body_latex=hypothesis.latex,
            )
        )
    return tuple(result)


__all__ = [
    "ContextPresentationRow",
    "context_presentation_rows",
    "presentation_local_declarations",
]
