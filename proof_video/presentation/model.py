"""Public renderer-independent visual-plan data model."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from proof_video.proof.correspondence import EntityRef


class VisualPrimitiveKind(str, Enum):
    KEEP = "keep"
    MOVE = "move"
    COPY = "copy"
    REWRITE = "rewrite"
    CREATE = "create"
    REMOVE = "remove"
    SPLIT = "split"
    MERGE = "merge"
    CLOSE = "close"
    FOCUS = "focus"
    REORDER = "reorder"


class AnchorSide(str, Enum):
    BEFORE = "before"
    AFTER = "after"


class LayoutRowKind(str, Enum):
    GOAL = "goal"
    CONTEXT = "context"
    TARGET = "target"


@dataclass(frozen=True, order=True)
class LayoutAnchor:
    """A semantic layout address, intentionally free of pixel coordinates."""

    anchor_id: str
    persistent_id: str
    side: AnchorSide
    entity: EntityRef
    goal_index: int
    row_kind: LayoutRowKind
    row_index: int
    expression_path: tuple[str | int, ...] = ()

    @property
    def slot(self) -> tuple[int, LayoutRowKind, int, tuple[str | int, ...]]:
        return self.goal_index, self.row_kind, self.row_index, self.expression_path


@dataclass(frozen=True)
class VisualPrimitive:
    primitive_id: str
    kind: VisualPrimitiveKind
    source_anchor_ids: tuple[str, ...] = ()
    target_anchor_ids: tuple[str, ...] = ()
    persistent_ids: tuple[str, ...] = ()
    scope: str = ""
    provenance: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    confidence: float = 1.0
    fallback_reason: str = ""

    @property
    def used_fallback(self) -> bool:
        return bool(self.fallback_reason)


@dataclass(frozen=True)
class PlanDiagnostic:
    code: str
    message: str
    primitive_id: str = ""
    entity_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticVisualPlan:
    before_fingerprint: str
    after_fingerprint: str
    anchors: tuple[LayoutAnchor, ...]
    primitives: tuple[VisualPrimitive, ...]
    diagnostics: tuple[PlanDiagnostic, ...] = ()
    schema_version: str = "1.0"

    def primitives_of_kind(
        self, kind: VisualPrimitiveKind
    ) -> tuple[VisualPrimitive, ...]:
        return tuple(item for item in self.primitives if item.kind is kind)

    def anchor(self, anchor_id: str) -> LayoutAnchor | None:
        return next(
            (item for item in self.anchors if item.anchor_id == anchor_id),
            None,
        )


def validate_visual_plan(plan: SemanticVisualPlan) -> tuple[str, ...]:
    errors: list[str] = []
    anchor_ids = [item.anchor_id for item in plan.anchors]
    if len(anchor_ids) != len(set(anchor_ids)):
        errors.append("layout plan contains duplicate anchor ids")
    known = set(anchor_ids)
    primitive_ids = [item.primitive_id for item in plan.primitives]
    if len(primitive_ids) != len(set(primitive_ids)):
        errors.append("layout plan contains duplicate primitive ids")
    for primitive in plan.primitives:
        if "text-fallback" in primitive.provenance and primitive.kind not in {
            VisualPrimitiveKind.CREATE,
            VisualPrimitiveKind.REMOVE,
        }:
            errors.append(
                f"{primitive.primitive_id}: rendered-text equality cannot certify "
                "physical continuity"
            )
        if not primitive.persistent_ids:
            errors.append(f"{primitive.primitive_id}: no persistent entity id")
        missing = (
            set(primitive.source_anchor_ids) | set(primitive.target_anchor_ids)
        ) - known
        if missing:
            errors.append(
                f"{primitive.primitive_id}: missing anchors {sorted(missing)}"
            )
        source_count = len(primitive.source_anchor_ids)
        target_count = len(primitive.target_anchor_ids)
        if primitive.kind in {
            VisualPrimitiveKind.KEEP,
            VisualPrimitiveKind.MOVE,
            VisualPrimitiveKind.REWRITE,
        } and not (source_count and target_count):
            errors.append(
                f"{primitive.primitive_id}: bidirectional primitive is one-sided"
            )
        if primitive.kind is VisualPrimitiveKind.COPY and not (
            source_count == 1 and target_count >= 1
        ):
            errors.append(f"{primitive.primitive_id}: copy is not 1→n")
        if primitive.kind is VisualPrimitiveKind.SPLIT and not (
            source_count == 1 and target_count > 1
        ):
            errors.append(f"{primitive.primitive_id}: split is not 1→n")
        if primitive.kind is VisualPrimitiveKind.MERGE and not (
            source_count > 1 and target_count == 1
        ):
            errors.append(f"{primitive.primitive_id}: merge is not n→1")
        if primitive.kind is VisualPrimitiveKind.CREATE and not (
            not source_count and target_count
        ):
            errors.append(f"{primitive.primitive_id}: creation has source entities")
        if primitive.kind in {
            VisualPrimitiveKind.REMOVE,
            VisualPrimitiveKind.CLOSE,
        } and not (source_count and not target_count):
            errors.append(f"{primitive.primitive_id}: removal has target entities")
        if primitive.kind is VisualPrimitiveKind.FOCUS and not (
            source_count or target_count
        ):
            errors.append(f"{primitive.primitive_id}: focus has no goal")
        if primitive.kind is VisualPrimitiveKind.REORDER and not (
            source_count or target_count
        ):
            errors.append(f"{primitive.primitive_id}: empty reorder")
    return tuple(errors)
