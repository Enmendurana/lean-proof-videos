"""Canonical, renderer-independent proof states.

The objects in this module are deliberately smaller than Lean's internal
metavariable context and richer than a pretty-printed goal.  They are the
stable boundary owned by this project: extractors may add evidence, and
renderers may add geometry, but neither is allowed to redefine identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from typing import Any, Iterable


ExpressionPath = tuple[str | int, ...]
PathSortKey = tuple[tuple[int, int | str], ...]


def expression_path_sort_key(path: ExpressionPath) -> PathSortKey:
    """Return a total, deterministic order for heterogeneous Lean paths.

    Lean-owned occurrence paths contain both child indices and named
    presentation components. Python deliberately refuses to compare an
    ``int`` with a ``str``; tagging segment kinds makes the order explicit.
    """

    return tuple(
        (0, segment) if isinstance(segment, int) else (1, segment) for segment in path
    )


class LocalKind(str, Enum):
    HYPOTHESIS = "hypothesis"
    DEFINITION = "definition"


@dataclass(frozen=True, order=True)
class SourceRange:
    """A half-open source range, when Lean can provide one."""

    file: str
    start_line: int
    start_column: int
    end_line: int
    end_column: int

    def __post_init__(self) -> None:
        start = (self.start_line, self.start_column)
        end = (self.end_line, self.end_column)
        if min(*start, *end) < 0 or end < start:
            raise ValueError(f"invalid half-open source range {start!r}..{end!r}")

    @classmethod
    def from_json(cls, value: dict[str, Any] | None) -> SourceRange | None:
        if not value:
            return None
        return cls(
            file=str(value.get("file", "")),
            start_line=int(value.get("startLine", 0)),
            start_column=int(value.get("startColumn", 0)),
            end_line=int(value.get("endLine", 0)),
            end_column=int(value.get("endColumn", 0)),
        )


@dataclass(frozen=True, order=True)
class CharacterSpan:
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError(f"invalid half-open span [{self.start}, {self.end})")

    @classmethod
    def from_json(
        cls, value: dict[str, Any] | list[int] | tuple[int, int]
    ) -> CharacterSpan:
        if isinstance(value, dict):
            return cls(int(value["start"]), int(value["end"]))
        return cls(int(value[0]), int(value[1]))


@dataclass(frozen=True)
class ExprOccurrence:
    """One occurrence in an elaborated expression tree.

    ``occurrence_id`` is local to one expression owner. ``lean_identity`` is
    meaningful only for atoms for which Lean has an identity (notably free
    variables, metavariables and constants).  Repeated rendered symbols are
    therefore distinct even when all other display fields coincide.
    """

    occurrence_id: str
    kind: str
    path: ExpressionPath
    fingerprint: str
    lean_identity: str = ""
    type_fingerprint: str = ""
    parent_id: str | None = None
    aliases: tuple[str, ...] = ()
    latex_spans: tuple[CharacterSpan, ...] = ()
    source_range: SourceRange | None = None

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> ExprOccurrence:
        raw_path = value.get("path", ())
        if isinstance(raw_path, str):
            path: tuple[str | int, ...] = tuple(
                int(part) if part.isdigit() else part
                for part in raw_path.split(".")
                if part != ""
            )
        else:
            path = tuple(raw_path)
        raw_spans = value.get("latexSpans", value.get("spans", ()))
        return cls(
            occurrence_id=str(value.get("id", value.get("occurrenceId", ""))),
            kind=str(value.get("kind", "")),
            path=path,
            fingerprint=str(value.get("fingerprint", "")),
            lean_identity=str(value.get("identity", value.get("leanIdentity", ""))),
            type_fingerprint=str(value.get("typeFingerprint", "")),
            parent_id=value.get("parentId"),
            aliases=tuple(str(item) for item in value.get("aliases", ())),
            latex_spans=tuple(CharacterSpan.from_json(span) for span in raw_spans),
            source_range=SourceRange.from_json(value.get("sourceRange")),
        )

    @property
    def structural_key(self) -> tuple[str, str, str, tuple[str | int, ...]]:
        return self.kind, self.fingerprint, self.type_fingerprint, self.path


@dataclass(frozen=True)
class Expression:
    """An elaborated expression and its occurrence tree."""

    expression_id: str
    fingerprint: str
    lean: str = ""
    latex: str = ""
    type_fingerprint: str = ""
    occurrences: tuple[ExprOccurrence, ...] = ()
    source_range: SourceRange | None = None

    @classmethod
    def from_json(cls, value: dict[str, Any] | None) -> Expression | None:
        if not value:
            return None
        return cls(
            expression_id=str(value.get("id", value.get("expressionId", ""))),
            fingerprint=str(value.get("fingerprint", "")),
            lean=str(value.get("lean", "")),
            latex=str(value.get("latex", "")),
            type_fingerprint=str(value.get("typeFingerprint", "")),
            occurrences=tuple(
                ExprOccurrence.from_json(item)
                for item in value.get("occurrences", value.get("semanticNodes", ()))
            ),
            source_range=SourceRange.from_json(value.get("sourceRange")),
        )

    def occurrence(self, occurrence_id: str) -> ExprOccurrence | None:
        return next(
            (item for item in self.occurrences if item.occurrence_id == occurrence_id),
            None,
        )

    @property
    def canonical_key(self) -> tuple[Any, ...]:
        return (
            self.fingerprint,
            self.type_fingerprint,
            tuple(
                (
                    item.kind,
                    item.path,
                    item.fingerprint,
                    item.lean_identity,
                    item.type_fingerprint,
                    item.parent_id,
                )
                for item in self.occurrences
            ),
        )


@dataclass(frozen=True)
class LocalDecl:
    """A declaration in a goal's ordered local context."""

    decl_id: str
    user_name: str
    type_expr: Expression
    value_expr: Expression | None = None
    binder_info: str = "default"
    dependencies: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    source_range: SourceRange | None = None
    is_proof: bool = False
    presentation_visible: bool = True
    metadata: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> LocalDecl:
        type_expr = Expression.from_json(value.get("type"))
        if type_expr is None:
            raise ValueError("canonical local declaration has no type expression")
        return cls(
            decl_id=str(value.get("id", value.get("fvarId", ""))),
            user_name=str(value.get("userName", value.get("name", ""))),
            type_expr=type_expr,
            value_expr=Expression.from_json(value.get("value")),
            binder_info=str(value.get("binderInfo", "default")),
            dependencies=tuple(str(item) for item in value.get("dependencies", ())),
            aliases=tuple(str(item) for item in value.get("aliases", ())),
            source_range=SourceRange.from_json(value.get("sourceRange")),
            is_proof=bool(value.get("isProof", False)),
            presentation_visible=bool(value.get("presentationVisible", True)),
            metadata=tuple(
                sorted(
                    (str(key), str(item))
                    for key, item in value.get("metadata", {}).items()
                )
            ),
        )

    @property
    def kind(self) -> LocalKind:
        return (
            LocalKind.DEFINITION
            if self.value_expr is not None
            else LocalKind.HYPOTHESIS
        )


@dataclass(frozen=True)
class GoalState:
    goal_id: str
    lineage_id: str
    locals: tuple[LocalDecl, ...]
    target: Expression
    parent_goal_id: str | None = None
    branch_kind: str = ""
    branch_index: int | None = None
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        ids = [item.decl_id for item in self.locals]
        if len(ids) != len(set(ids)):
            raise ValueError(f"goal {self.goal_id} contains duplicate local identities")

    def local(self, decl_id: str) -> LocalDecl | None:
        return next((item for item in self.locals if item.decl_id == decl_id), None)


@dataclass(frozen=True)
class ProofState:
    """An ordered finite forest of goals."""

    goals: tuple[GoalState, ...]
    focus: tuple[str, ...] = ()
    schema_version: str = "1.0"
    metadata: tuple[tuple[str, str], ...] = field(default=(), compare=False)

    def __post_init__(self) -> None:
        goal_ids = [goal.goal_id for goal in self.goals]
        if len(goal_ids) != len(set(goal_ids)):
            raise ValueError("proof state contains duplicate goal identities")
        known = set(goal_ids)
        for goal in self.goals:
            if goal.parent_goal_id is not None and goal.parent_goal_id in known:
                raise ValueError(
                    f"live goal {goal.goal_id} cannot have live parent {goal.parent_goal_id}"
                )
        if any(goal_id not in known for goal_id in self.focus):
            raise ValueError("focused goal does not exist in proof state")
        if len(self.focus) != len(set(self.focus)):
            raise ValueError("proof state repeats a focused goal")

    def goal(self, goal_id: str) -> GoalState | None:
        return next((item for item in self.goals if item.goal_id == goal_id), None)

    @property
    def goal_order(self) -> tuple[str, ...]:
        return tuple(goal.goal_id for goal in self.goals)

    def canonical_payload(self) -> dict[str, Any]:
        """Return a deterministic, presentation-independent JSON value."""

        def source_range(value: SourceRange | None) -> Any:
            if value is None:
                return None
            return {
                "file": value.file,
                "startLine": value.start_line,
                "startColumn": value.start_column,
                "endLine": value.end_line,
                "endColumn": value.end_column,
            }

        def expression(expr: Expression | None) -> Any:
            if expr is None:
                return None
            return {
                "id": expr.expression_id,
                "fingerprint": expr.fingerprint,
                "type": expr.type_fingerprint,
                "occurrences": [
                    {
                        "id": node.occurrence_id,
                        "kind": node.kind,
                        "path": list(node.path),
                        "fingerprint": node.fingerprint,
                        "identity": node.lean_identity,
                        "type": node.type_fingerprint,
                        "parent": node.parent_id,
                        "aliases": list(node.aliases),
                        "sourceRange": source_range(node.source_range),
                    }
                    for node in expr.occurrences
                ],
                "sourceRange": source_range(expr.source_range),
            }

        return {
            "schemaVersion": self.schema_version,
            "goals": [
                {
                    "id": goal.goal_id,
                    "lineage": goal.lineage_id,
                    "parent": goal.parent_goal_id,
                    "branchKind": goal.branch_kind,
                    "branchIndex": goal.branch_index,
                    "locals": [
                        {
                            "id": local.decl_id,
                            "name": local.user_name,
                            "binder": local.binder_info,
                            "dependencies": list(local.dependencies),
                            "aliases": list(local.aliases),
                            "proof": local.is_proof,
                            "presentationVisible": local.presentation_visible,
                            "sourceRange": source_range(local.source_range),
                            "metadata": list(local.metadata),
                            "type": expression(local.type_expr),
                            "value": expression(local.value_expr),
                        }
                        for local in goal.locals
                    ],
                    "target": expression(goal.target),
                    "metadata": list(goal.metadata),
                }
                for goal in self.goals
            ],
            "focus": list(self.focus),
        }

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            self.canonical_payload(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def expression_dependencies(expression: Expression | None) -> frozenset[str]:
    if expression is None:
        return frozenset()
    return frozenset(
        occurrence.lean_identity.removeprefix("fvar:")
        for occurrence in expression.occurrences
        if occurrence.lean_identity.startswith("fvar:")
    )


def validate_expression_tree(
    expression: Expression,
    *,
    check_latex_spans: bool = True,
    check_tree_paths: bool = True,
) -> tuple[str, ...]:
    errors: list[str] = []
    by_id = {node.occurrence_id: node for node in expression.occurrences}
    if len(by_id) != len(expression.occurrences):
        errors.append(f"{expression.expression_id}: duplicate occurrence id")
    paths: set[tuple[str | int, ...]] = set()
    for node in expression.occurrences:
        if check_tree_paths and node.path in paths:
            errors.append(
                f"{expression.expression_id}/{node.occurrence_id}: duplicate occurrence path {node.path!r}"
            )
        paths.add(node.path)
        if node.parent_id is not None and node.parent_id not in by_id:
            errors.append(
                f"{expression.expression_id}/{node.occurrence_id}: missing parent {node.parent_id}"
            )
        if node.parent_id is not None and node.parent_id in by_id:
            parent = by_id[node.parent_id]
            if check_tree_paths and (
                node.path[: len(parent.path)] != parent.path or node.path == parent.path
            ):
                errors.append(
                    f"{expression.expression_id}/{node.occurrence_id}: path is not below parent {node.parent_id}"
                )
        for span in node.latex_spans:
            if check_latex_spans and span.end > len(expression.latex):
                errors.append(
                    f"{expression.expression_id}/{node.occurrence_id}: LaTeX span exceeds expression"
                )

        seen: set[str] = set()
        current = node
        while current.parent_id is not None and current.parent_id in by_id:
            if current.occurrence_id in seen:
                errors.append(
                    f"{expression.expression_id}/{node.occurrence_id}: cyclic parent relation"
                )
                break
            seen.add(current.occurrence_id)
            current = by_id[current.parent_id]
    return tuple(errors)


def validate_state(state: ProofState) -> tuple[str, ...]:
    errors: list[str] = []
    branch_positions: dict[tuple[str, int], str] = {}
    for goal in state.goals:
        if goal.branch_index is not None and goal.branch_index < 0:
            errors.append(f"{goal.goal_id}: negative branch index")
        if goal.branch_index is not None and goal.parent_goal_id is None:
            errors.append(f"{goal.goal_id}: branch index has no parent goal")
        if goal.parent_goal_id is not None and goal.branch_index is not None:
            branch_position = (goal.parent_goal_id, goal.branch_index)
            previous = branch_positions.get(branch_position)
            if previous is not None:
                errors.append(
                    f"{goal.goal_id}: duplicate branch index {goal.branch_index} "
                    f"for parent {goal.parent_goal_id} (already used by {previous})"
                )
            else:
                branch_positions[branch_position] = goal.goal_id
        strict_spans = ("legacyState", "true") not in goal.metadata
        errors.extend(
            validate_expression_tree(
                goal.target,
                check_latex_spans=strict_spans,
                check_tree_paths=strict_spans,
            )
        )
        local_ids = {local.decl_id for local in goal.locals}
        preceding: set[str] = set()
        for local in goal.locals:
            errors.extend(
                validate_expression_tree(
                    local.type_expr,
                    check_latex_spans=strict_spans,
                    check_tree_paths=strict_spans,
                )
            )
            if local.value_expr is not None:
                errors.extend(
                    validate_expression_tree(
                        local.value_expr,
                        check_latex_spans=strict_spans,
                        check_tree_paths=strict_spans,
                    )
                )
            unknown = set(local.dependencies) - local_ids
            # This field stores only local free-variable dependencies; globals
            # live in expression constants.  Every dependency must therefore
            # resolve in the same context and precede the dependent local.
            if unknown:
                errors.append(
                    f"{goal.goal_id}/{local.decl_id}: unresolved local dependencies {sorted(unknown)}"
                )
            forward = set(local.dependencies) - preceding
            if local.decl_id in forward:
                errors.append(
                    f"{goal.goal_id}/{local.decl_id}: self-dependent local declaration"
                )
            elif forward:
                errors.append(
                    f"{goal.goal_id}/{local.decl_id}: dependency is not earlier in context {sorted(forward)}"
                )
            preceding.add(local.decl_id)
    return tuple(errors)


def alpha_structural_key(expression: Expression) -> tuple[Any, ...]:
    """A deterministic typed tree key that ignores occurrence identifiers.

    Lean's own structural fingerprint remains authoritative.  This key is a
    conservative fallback for migrated traces whose node ids were namespaced
    differently; it never discards node kind, type or tree position.
    """

    return tuple(
        (
            node.kind,
            node.path,
            node.fingerprint,
            node.type_fingerprint,
            node.lean_identity
            if not node.lean_identity.startswith("bvar:")
            else "bvar",
        )
        for node in expression.occurrences
    )


def ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
