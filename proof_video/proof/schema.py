"""Stable data contracts shared by proof extraction and renderers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from proof_video.proof.effects import ProofTransition
from proof_video.proof.state import Expression, LocalDecl, ProofState

if TYPE_CHECKING:
    from proof_video.presentation.model import SemanticVisualPlan
    from proof_video.proof.completion import TerminalCompletion

from proof_video.proof.correspondence import CorrespondenceEdge, ExplicitGoalEdge


@dataclass(frozen=True)
class LatexHypothesis:
    name: str
    latex: str
    key: str = ""
    raw_latex: str | None = None

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> "LatexHypothesis":
        return cls(
            name=value.get("name", ""),
            latex=value["latex"],
            key=str(value.get("key", "")),
            raw_latex=value.get("rawLatex"),
        )

    def render_latex(self) -> str:
        if self.raw_latex is not None:
            return self.raw_latex
        safe_name = self.name.replace("_", r"\_")
        return rf"{safe_name} \;:\; {self.latex}"


@dataclass(frozen=True)
class RuleAnnotation:
    """Certified in-place presentation of a rule application.

    ``source_step_id`` identifies the checked proof that is being applied.
    The two transitions split selection/storage from substitution without
    inventing an administrative ``x := value`` row on the board.
    """

    key: str
    latex: str
    rule: str
    source_step_id: int | None = None
    source_latex: str = ""
    source_lean: str = ""
    selection_transition: SemanticTransition | None = None
    substitution_transition: SemanticTransition | None = None
    presentation_goals: tuple["Goal", ...] = ()


@dataclass(frozen=True)
class IndexMaps:
    """Stable character identities emitted by the upstream Lean matcher."""

    source_to_target: tuple[int | None, ...]
    target_to_source: tuple[int | None, ...]

    @classmethod
    def from_json(cls, value: dict[str, Any] | None) -> "IndexMaps | None":
        if not value:
            return None
        return cls(
            source_to_target=tuple(value.get("s1_to_s2", ())),
            target_to_source=tuple(value.get("s2_to_s1", ())),
        )


@dataclass(frozen=True)
class SemanticSpan:
    """Half-open character range in the rendered full-goal LaTeX string."""

    start: int
    end: int

    @classmethod
    def from_json(cls, value: dict[str, Any] | list[int] | tuple[int, int]):
        if isinstance(value, dict):
            return cls(start=int(value["start"]), end=int(value["end"]))
        return cls(start=int(value[0]), end=int(value[1]))

    @property
    def valid(self) -> bool:
        return 0 <= self.start < self.end


@dataclass(frozen=True)
class SemanticExpressionNode:
    node_id: str
    kind: str = ""
    identity: str = ""
    fingerprint: str = ""
    parent_id: str | None = None
    path: tuple[str | int, ...] = ()
    latex_spans: tuple[SemanticSpan, ...] = ()

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> "SemanticExpressionNode":
        raw_spans = value.get("latexSpans", value.get("spans", ()))
        if not raw_spans and value.get("latexSpan") is not None:
            raw_spans = (value["latexSpan"],)
        raw_path = value.get("path", ())
        path = (
            tuple(raw_path.split(".")) if isinstance(raw_path, str) else tuple(raw_path)
        )
        return cls(
            node_id=str(value.get("id", value.get("nodeId", ""))),
            kind=str(value.get("kind", "")),
            identity=str(value.get("identity", "")),
            fingerprint=str(value.get("fingerprint", "")),
            parent_id=value.get("parentId"),
            path=path,
            latex_spans=tuple(SemanticSpan.from_json(span) for span in raw_spans),
        )


@dataclass(frozen=True)
class SemanticExpression:
    nodes: tuple[SemanticExpressionNode, ...] = ()

    @classmethod
    def from_json(cls, value: dict[str, Any] | list[dict[str, Any]] | None):
        if not value:
            return cls()
        raw_nodes = value.get("nodes", ()) if isinstance(value, dict) else value
        return cls(tuple(SemanticExpressionNode.from_json(node) for node in raw_nodes))


@dataclass(frozen=True)
class SemanticTransitionEdge:
    source_node_id: str
    target_node_id: str
    reason: str = ""
    confidence: float | None = None
    relation: str = ""
    provenance: str = ""

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> "SemanticTransitionEdge":
        return cls(
            source_node_id=str(value.get("sourceNodeId", value.get("source", ""))),
            target_node_id=str(value.get("targetNodeId", value.get("target", ""))),
            reason=str(value.get("reason", "")),
            confidence=(
                float(value["confidence"])
                if value.get("confidence") is not None
                else None
            ),
            relation=str(value.get("relation", "")),
            provenance=str(value.get("provenance", "")),
        )


@dataclass(frozen=True)
class GoalDiffEvidence:
    """Lean ``TacticInfo`` lineage and official structural Expr diff paths."""

    source_goal_id: str
    target_goal_id: str
    source_changed_paths: tuple[str, ...] = ()
    target_changed_paths: tuple[str, ...] = ()

    @classmethod
    def from_json(cls, value: dict[str, Any] | None) -> "GoalDiffEvidence | None":
        if not value:
            return None
        return cls(
            source_goal_id=str(value.get("sourceGoalId", "")),
            target_goal_id=str(value.get("targetGoalId", "")),
            source_changed_paths=tuple(
                str(item) for item in value.get("sourceChangedPaths", ())
            ),
            target_changed_paths=tuple(
                str(item) for item in value.get("targetChangedPaths", ())
            ),
        )


@dataclass(frozen=True)
class SemanticTransition:
    source: SemanticExpression
    target: SemanticExpression
    # A tuple intentionally preserves edge order, duplicates and overlaps.
    # Unlike a character-index array this can represent multiple logical
    # correspondences involving the same expression range.
    edges: tuple[SemanticTransitionEdge, ...]
    proof_kind: str = ""
    adapter: str = ""
    proof_fingerprint: str = ""
    proof_term: str = ""
    proof_descendants: tuple[str, ...] = ()
    proof_premises: tuple[str, ...] = ()
    proof_constants: tuple[str, ...] = ()
    goal_diff: GoalDiffEvidence | None = None
    fallback_reason: str | None = None

    @classmethod
    def from_json(cls, value: dict[str, Any] | None) -> "SemanticTransition | None":
        if not value:
            return None
        return cls(
            source=SemanticExpression.from_json(
                value.get("source", value.get("sourceNodes"))
            ),
            target=SemanticExpression.from_json(
                value.get("target", value.get("targetNodes"))
            ),
            edges=tuple(
                SemanticTransitionEdge.from_json(edge)
                for edge in value.get("edges", ())
            ),
            proof_kind=str(value.get("proofKind", "")),
            adapter=str(value.get("adapter", "")),
            proof_fingerprint=str(value.get("proofFingerprint", "")),
            proof_term=str(value.get("proofTerm", "")),
            proof_descendants=tuple(
                str(item) for item in value.get("proofDescendants", ())
            ),
            proof_premises=tuple(str(item) for item in value.get("proofPremises", ())),
            proof_constants=tuple(
                str(item) for item in value.get("proofConstants", ())
            ),
            goal_diff=GoalDiffEvidence.from_json(value.get("goalDiff")),
            fallback_reason=value.get("fallbackReason"),
        )


@dataclass(frozen=True)
class Goal:
    goal_id: str
    state: str
    latex_target: str | None = None
    latex_context: tuple[LatexHypothesis, ...] = ()
    lineage_id: str = ""
    parent_goal_id: str | None = None
    index_maps: IndexMaps | None = None
    latex_index_maps: IndexMaps | None = None
    semantic_transition: SemanticTransition | None = None
    rule_annotations: tuple[RuleAnnotation, ...] = ()
    semantic_nodes: tuple[SemanticExpressionNode, ...] = ()
    canonical_locals: tuple[LocalDecl, ...] = ()
    canonical_target: Expression | None = None
    branch_kind: str = ""
    branch_index: int | None = None

    @classmethod
    def from_json(
        cls,
        value: dict[str, Any],
        *,
        lineage_id: str = "",
        parent_goal_id: str | None = None,
        index_maps: dict[str, Any] | None = None,
        latex_index_maps: dict[str, Any] | None = None,
        semantic_transition: dict[str, Any] | None = None,
    ) -> "Goal":
        parsed_transition = SemanticTransition.from_json(semantic_transition)
        semantic_nodes = tuple(
            SemanticExpressionNode.from_json(node)
            for node in value.get("semanticNodes", ())
        )
        if not semantic_nodes and parsed_transition is not None:
            semantic_nodes = parsed_transition.target.nodes
        return cls(
            goal_id=value["goalId"],
            state=value["state"],
            latex_target=value.get("latexTarget"),
            latex_context=tuple(
                LatexHypothesis.from_json(item)
                for item in value.get("latexContext", [])
            ),
            lineage_id=lineage_id,
            parent_goal_id=parent_goal_id,
            index_maps=IndexMaps.from_json(index_maps),
            latex_index_maps=IndexMaps.from_json(latex_index_maps),
            semantic_transition=parsed_transition,
            rule_annotations=tuple(
                RuleAnnotation(
                    key=str(item.get("key", "")),
                    latex=str(item.get("latex", "")),
                    rule=str(item.get("rule", "")),
                )
                for item in value.get("ruleAnnotations", ())
            ),
            semantic_nodes=semantic_nodes,
            canonical_locals=tuple(
                LocalDecl.from_json(item) for item in value.get("canonicalLocals", ())
            ),
            canonical_target=Expression.from_json(value.get("canonicalTarget")),
            branch_kind=str(value.get("branchKind", "")),
            branch_index=(
                int(value["branchIndex"])
                if value.get("branchIndex") is not None
                else None
            ),
        )

    def latex_state(self) -> str:
        context = [hypothesis.render_latex() for hypothesis in self.latex_context]
        return "\n".join(context + [rf"\vdash\;{self.latex_target or ''}"])


@dataclass(frozen=True)
class Frame:
    index: int
    tactic: str
    goals: tuple[Goal, ...]
    focus_goals: tuple[Goal, ...] = ()
    proof_state: ProofState | None = None
    proof_transition: ProofTransition | None = None
    visual_plan: SemanticVisualPlan | None = None
    goal_lineage: tuple[ExplicitGoalEdge, ...] = ()
    canonical_correspondence: tuple[CorrespondenceEdge, ...] = ()
    canonical_abi: int = 0
    capabilities: tuple[str, ...] = ()
    terminal_completion: TerminalCompletion | None = None

    @property
    def display_goals(self) -> tuple[Goal, ...]:
        return self.focus_goals or self.goals


def has_native_canonical_observation(frame: Frame) -> bool:
    """Whether ``frame`` carries a complete native canonical frontier.

    ABI 5 is authoritative only when every live goal contains Lean's
    structured target expression.  Keeping this predicate in the schema
    prevents adapters, QA and renderers from silently choosing different
    routes for a partially migrated trace.  The empty frontier is complete;
    it is the canonical representation of a closing action.
    """

    return frame.canonical_abi >= 5 and all(
        goal.canonical_target is not None for goal in frame.goals
    )


@dataclass(frozen=True)
class ProofStep:
    id: int
    scope_id: str
    parent_scope_id: str | None
    depth: int
    kind: str
    rule: str
    premises: tuple[int, ...]
    proposition_latex: str
    proposition_lean: str
    display_latex: str
    proof_fingerprint: str
    proposition_fingerprint: str
    proof_path: str
    theorem_name: str | None = None
    binder_name: str | None = None
    instantiation_binder_name: str | None = None
    instantiation_value_latex: str | None = None
    instantiation_value_lean: str | None = None
    opens_scope: str | None = None
    closes_scope: str | None = None
    kernel_checked: bool = False
    uses_local_context: bool = False
    is_typeclass: bool = False
    semantic_nodes: tuple[SemanticExpressionNode, ...] = ()

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> "ProofStep":
        return cls(
            id=int(value["id"]),
            scope_id=str(value["scopeId"]),
            parent_scope_id=value.get("parentScopeId"),
            depth=int(value.get("depth", 0)),
            kind=str(value.get("kind", "")),
            rule=str(value.get("rule", "")),
            premises=tuple(int(item) for item in value.get("premises", ())),
            proposition_latex=str(value["propositionLatex"]),
            proposition_lean=str(value.get("propositionLean", "")),
            display_latex=str(value.get("displayLatex", value["propositionLatex"])),
            proof_fingerprint=str(value.get("proofFingerprint", "")),
            proposition_fingerprint=str(value.get("propositionFingerprint", "")),
            proof_path=str(value.get("proofPath", "")),
            theorem_name=value.get("theoremName"),
            binder_name=value.get("binderName"),
            instantiation_binder_name=value.get("instantiationBinderName"),
            instantiation_value_latex=value.get("instantiationValueLatex"),
            instantiation_value_lean=value.get("instantiationValueLean"),
            opens_scope=value.get("opensScope"),
            closes_scope=value.get("closesScope"),
            kernel_checked=bool(value.get("kernelChecked", False)),
            uses_local_context=bool(value.get("usesLocalContext", False)),
            is_typeclass=bool(value.get("isTypeclass", False)),
            semantic_nodes=tuple(
                SemanticExpressionNode.from_json(node)
                for node in value.get("semanticNodes", ())
            ),
        )
