"""Compatibility adapters from existing trace schemas to canonical states."""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from proof_video.presentation import (
    SemanticVisualPlan,
    VisualPrimitiveKind,
    plan_visual_transition,
)
from proof_video.presentation.rows import context_presentation_rows
from proof_video.proof.correspondence import (
    EntityKind,
    ExplicitOccurrenceEdge,
    MatchProvenance,
    RelationKind,
)
from proof_video.proof.diff import diff_proof_states
from proof_video.proof.effects import ProofTransition, TransitionMetadata
from proof_video.proof.schema import (
    Frame,
    Goal,
    SemanticExpression,
    SemanticExpressionNode,
    SemanticSpan,
    SemanticTransition,
    SemanticTransitionEdge,
    has_native_canonical_observation,
)
from proof_video.proof.state import (
    CharacterSpan,
    ExprOccurrence,
    Expression,
    GoalState,
    LocalDecl,
    ProofState,
    ordered_unique,
)


def _path(value: tuple[str | int, ...]) -> tuple[str | int, ...]:
    return tuple(
        int(part) if isinstance(part, str) and part.isdigit() else part
        for part in value
    )


def _occurrence(node: SemanticExpressionNode) -> ExprOccurrence:
    return ExprOccurrence(
        occurrence_id=node.node_id,
        kind=node.kind,
        path=_path(node.path),
        fingerprint=node.fingerprint,
        lean_identity=node.identity,
        parent_id=node.parent_id,
        latex_spans=tuple(
            CharacterSpan(span.start, span.end) for span in node.latex_spans
        ),
    )


def _root_fingerprint(nodes: tuple[SemanticExpressionNode, ...], fallback: str) -> str:
    if not nodes:
        return fallback
    roots = [node for node in nodes if node.parent_id is None]
    candidates = roots or list(nodes)
    root = min(candidates, key=lambda node: (len(node.path), node.node_id))
    return root.fingerprint or fallback


def _expression(
    expression_id: str,
    lean: str,
    latex: str,
    nodes: Iterable[SemanticExpressionNode],
) -> Expression:
    node_tuple = tuple(nodes)
    return Expression(
        expression_id=expression_id,
        fingerprint=_root_fingerprint(node_tuple, f"legacy:{lean or latex}"),
        lean=lean,
        latex=latex,
        occurrences=tuple(_occurrence(node) for node in node_tuple),
    )


def _context_index(node: SemanticExpressionNode) -> int | str | None:
    path = _path(node.path)
    if len(path) >= 2 and path[0] == "context":
        return path[1]
    return None


def _context_decl_id(
    goal: Goal, index: int, nodes: tuple[SemanticExpressionNode, ...]
) -> str:
    candidates = [
        node
        for node in nodes
        if _context_index(node) in {index, str(index)} and node.kind == "declaration"
    ]
    for node in candidates:
        if node.identity.startswith("fvar:"):
            return node.identity.removeprefix("fvar:")
        if "/" in node.node_id:
            pieces = node.node_id.split("/")
            if pieces[0] == "context" and len(pieces) >= 2:
                return pieces[1]
            if pieces[0].startswith("proof-context-"):
                return pieces[0].removeprefix("proof-context-")
    hypothesis = goal.latex_context[index]
    return hypothesis.key or f"{goal.lineage_id}:local:{index}:{hypothesis.name}"


def _legacy_locals(goal: Goal) -> tuple[LocalDecl, ...]:
    nodes = goal.semantic_nodes
    result = []
    for index, hypothesis in enumerate(goal.latex_context):
        decl_id = _context_decl_id(goal, index, nodes)
        type_nodes = tuple(
            node
            for node in nodes
            if _context_index(node) in {index, str(index)}
            and node.kind not in {"declaration", "declaration-punctuation"}
        )
        dependencies = ordered_unique(
            node.identity.removeprefix("fvar:")
            for node in type_nodes
            if node.identity.startswith("fvar:")
            and node.identity.removeprefix("fvar:") != decl_id
        )
        result.append(
            LocalDecl(
                decl_id=decl_id,
                user_name=hypothesis.name,
                type_expr=_expression(
                    f"{goal.goal_id}/local/{decl_id}/type",
                    "",
                    hypothesis.latex,
                    type_nodes,
                ),
                dependencies=dependencies,
                is_proof=bool(hypothesis.name),
                metadata=(("legacy", "true"),),
            )
        )
    return tuple(result)


def _legacy_target(goal: Goal) -> Expression:
    target_nodes = tuple(
        node
        for node in goal.semantic_nodes
        if _context_index(node) is None and node.kind != "sequent-punctuation"
    )
    return _expression(
        f"{goal.goal_id}/target",
        goal.latex_target or goal.state,
        goal.latex_target or goal.state,
        target_nodes,
    )


_PHYSICAL_PROVENANCE = frozenset(
    {
        MatchProvenance.LEAN_IDENTITY,
        MatchProvenance.ALIAS,
        MatchProvenance.LEAN_DEFEQ,
        MatchProvenance.EXPLICIT,
        MatchProvenance.TYPED_STRUCTURE,
        MatchProvenance.SOURCE_CONTINUITY,
    }
)


def _canonical_visual_edges(
    visual_plan: SemanticVisualPlan,
    *,
    target_goal_id: str,
    source_node_ids: frozenset[str],
    target_node_ids: frozenset[str],
) -> tuple[SemanticTransitionEdge, ...]:
    """Project canonical occurrence hyperedges into the legacy token bridge.

    The canonical relation remains authoritative and many-to-many.  The old
    token renderer currently accepts pairwise edges, so this adapter expands
    a hyperedge only at its boundary.  Relation/provenance stay attached,
    allowing both renderers to distinguish copying from consuming movement.
    Structural/text fallbacks intentionally never become physical moves.
    """

    result: list[SemanticTransitionEdge] = []
    anchors = {item.anchor_id: item for item in visual_plan.anchors}
    relation_for_kind = {
        VisualPrimitiveKind.KEEP: RelationKind.PRESERVE,
        VisualPrimitiveKind.MOVE: RelationKind.PRESERVE,
        VisualPrimitiveKind.COPY: RelationKind.COPY,
        VisualPrimitiveKind.REWRITE: RelationKind.REWRITE,
        VisualPrimitiveKind.SPLIT: RelationKind.SPLIT,
        VisualPrimitiveKind.MERGE: RelationKind.MERGE,
    }
    for primitive in visual_plan.primitives:
        relation = relation_for_kind.get(primitive.kind)
        if relation is None or primitive.used_fallback:
            continue
        sources = tuple(
            anchors[item].entity
            for item in primitive.source_anchor_ids
            if item in anchors
            and anchors[item].entity.kind is EntityKind.OCCURRENCE
            and anchors[item].entity.occurrence_id in source_node_ids
        )
        targets = tuple(
            anchors[item].entity
            for item in primitive.target_anchor_ids
            if item in anchors
            and anchors[item].entity.kind is EntityKind.OCCURRENCE
            and anchors[item].entity.goal_id == target_goal_id
            and anchors[item].entity.occurrence_id in target_node_ids
        )
        if not sources or not targets:
            continue
        provenance = next(
            (
                item
                for item in primitive.provenance
                if item in {value.value for value in _PHYSICAL_PROVENANCE}
            ),
            "semantic-plan",
        )
        reason = next(
            (item for item in primitive.evidence if item.startswith("verified-")),
            f"verified-visual-plan-{provenance}",
        )
        for source in sources:
            for target in targets:
                result.append(
                    SemanticTransitionEdge(
                        source_node_id=source.occurrence_id,
                        target_node_id=target.occurrence_id,
                        reason=reason,
                        confidence=primitive.confidence,
                        relation=relation.value,
                        provenance=provenance,
                    )
                )
    return tuple(result)


def _canonical_presentation_expression(goal: Goal) -> SemanticExpression:
    """Project canonical expressions into the exact renderer row coordinates.

    Canonical occurrence spans are expression-local.  Renderers operate on a
    newline-separated sequent containing declaration names, types, optional
    values, and the target.  Translating spans here keeps both renderer routes
    on one coordinate system and prevents a displayed definition from shifting
    every later semantic edge.
    """

    nodes: list[SemanticExpressionNode] = []

    def shifted_occurrences(
        expression: Expression,
        offset: int,
        path_prefix: tuple[str | int, ...],
    ) -> None:
        nodes.extend(
            SemanticExpressionNode(
                node_id=occurrence.occurrence_id,
                kind=occurrence.kind,
                identity=occurrence.lean_identity,
                fingerprint=occurrence.fingerprint,
                parent_id=occurrence.parent_id,
                path=(*path_prefix, *occurrence.path),
                latex_spans=tuple(
                    SemanticSpan(offset + span.start, offset + span.end)
                    for span in occurrence.latex_spans
                ),
            )
            for occurrence in expression.occurrences
        )

    cursor = 0
    presentation_rows = context_presentation_rows(goal)
    for local_index, row in enumerate(presentation_rows):
        local = row.local
        if local is None:  # canonical rows always retain their declaration
            continue
        context_path: tuple[str | int, ...] = ("context", local_index)
        if row.name_span is not None:
            nodes.append(
                SemanticExpressionNode(
                    node_id=f"context/{local.decl_id}/name",
                    kind="declaration",
                    identity=f"fvar:{local.decl_id}",
                    fingerprint=f"local:{local.decl_id}",
                    path=(*context_path, "name"),
                    latex_spans=(
                        SemanticSpan(
                            cursor + row.name_span.start,
                            cursor + row.name_span.end,
                        ),
                    ),
                )
            )
        if row.type_span is not None:
            type_offset = cursor + row.type_span.start
            nodes.append(
                SemanticExpressionNode(
                    node_id=f"context/{local.decl_id}/type",
                    kind="expression",
                    fingerprint=local.type_expr.fingerprint,
                    path=(*context_path, "type"),
                    latex_spans=(
                        SemanticSpan(
                            type_offset,
                            cursor + row.type_span.end,
                        ),
                    ),
                )
            )
            shifted_occurrences(
                local.type_expr,
                type_offset,
                (*context_path, "type"),
            )
        if local.value_expr is not None and row.value_span is not None:
            value_offset = cursor + row.value_span.start
            nodes.append(
                SemanticExpressionNode(
                    node_id=f"local/{local.decl_id}/value",
                    kind="expression",
                    fingerprint=local.value_expr.fingerprint,
                    path=(*context_path, "value"),
                    latex_spans=(
                        SemanticSpan(
                            value_offset,
                            cursor + row.value_span.end,
                        ),
                    ),
                )
            )
            shifted_occurrences(
                local.value_expr,
                value_offset,
                (*context_path, "value"),
            )
        cursor += len(row.latex) + 1

    target_latex = goal.latex_target or goal.state
    target_offset = cursor + len(r"\vdash\;")
    if goal.canonical_target is not None:
        nodes.append(
            SemanticExpressionNode(
                node_id=f"target/{goal.goal_id}",
                kind="expression",
                fingerprint=goal.canonical_target.fingerprint,
                path=("target",),
                latex_spans=(
                    SemanticSpan(target_offset, target_offset + len(target_latex)),
                ),
            )
        )
        shifted_occurrences(goal.canonical_target, target_offset, ("target",))
    return SemanticExpression(tuple(nodes))


def canonical_presentation_expression(goal: Goal) -> SemanticExpression:
    """Public, renderer-neutral projection of one canonical goal card."""

    return _canonical_presentation_expression(goal)


def _bridge_goal(
    previous: Frame,
    current: Frame,
    goal: Goal,
    transition: ProofTransition,
    visual_plan: SemanticVisualPlan,
) -> Goal:
    existing = goal.semantic_transition
    if existing is not None and not isinstance(existing, SemanticTransition):
        # Tests and third-party callers may attach an opaque renderer marker.
        # Canonical enrichment must remain non-invasive for such extensions.
        return goal
    goal_diff = existing.goal_diff if existing is not None else None
    source_goal_id = goal_diff.source_goal_id if goal_diff is not None else ""
    lineage_source_ids = tuple(
        source_id
        for edge in current.goal_lineage
        if goal.goal_id in edge.target_goal_ids
        for source_id in edge.source_goal_ids
    )
    candidate_source_ids = tuple(
        dict.fromkeys(
            (
                *((source_goal_id,) if source_goal_id else ()),
                *((goal.parent_goal_id,) if goal.parent_goal_id else ()),
                *lineage_source_ids,
            )
        )
    )
    source_goals = tuple(
        item
        for item in previous.goals
        if item.goal_id in candidate_source_ids
        or (not candidate_source_ids and item.lineage_id == goal.lineage_id)
    )
    source_goal = source_goals[0] if source_goals else None
    canonical_projection = (
        bool(source_goals)
        and all(item.canonical_target is not None for item in source_goals)
        and goal.canonical_target is not None
    )
    if canonical_projection:
        # ``SemanticTransition`` predates n→1 goal joins and has one source
        # expression field.  Keep every parent node here for diagnostics and
        # compatibility; the shared renderer compiler still evaluates each
        # card in its own local coordinate space.
        source_expression = SemanticExpression(
            tuple(
                node
                for source in source_goals
                for node in _canonical_presentation_expression(source).nodes
            )
        )
        target_expression = _canonical_presentation_expression(goal)
    elif existing is not None:
        source_expression = existing.source
        target_expression = existing.target
    else:
        source_expression = SemanticExpression(
            source_goal.semantic_nodes if source_goal is not None else ()
        )
        target_expression = SemanticExpression(goal.semantic_nodes)
    if not source_expression.nodes or not target_expression.nodes:
        return goal
    canonical_edges = _canonical_visual_edges(
        visual_plan,
        target_goal_id=goal.goal_id,
        source_node_ids=frozenset(item.node_id for item in source_expression.nodes),
        target_node_ids=frozenset(item.node_id for item in target_expression.nodes),
    )
    if not canonical_projection and not canonical_edges:
        # Compatibility traces still own their original renderer edges.  The
        # canonical row projection above is available only for ABI 5 goals;
        # without it, erasing an existing legacy edge would lose evidence.
        return goal
    # Extractor edges have already been consumed as evidence by the canonical
    # correspondence engine.  Feeding them to renderers a second time would
    # create a competing semantics and can reintroduce glyph-based motion.
    # Only the normalized visual plan crosses the renderer boundary.
    merged = {
        (edge.source_node_id, edge.target_node_id, edge.relation): edge
        for edge in canonical_edges
    }
    semantic = (
        replace(
            existing,
            source=source_expression,
            target=target_expression,
            edges=tuple(merged[key] for key in sorted(merged)),
        )
        if existing is not None
        else SemanticTransition(
            source=source_expression,
            target=target_expression,
            edges=tuple(merged[key] for key in sorted(merged)),
            proof_kind=transition.metadata.proof_kind,
            proof_fingerprint=transition.metadata.proof_fingerprint,
            adapter="canonical-state-delta",
        )
    )
    return replace(
        goal,
        semantic_transition=semantic,
        semantic_nodes=(
            target_expression.nodes if canonical_projection else goal.semantic_nodes
        ),
    )


def canonical_goal(goal: Goal) -> GoalState:
    return GoalState(
        goal_id=goal.goal_id,
        lineage_id=goal.lineage_id or goal.goal_id,
        parent_goal_id=goal.parent_goal_id,
        branch_kind=goal.branch_kind,
        branch_index=goal.branch_index,
        locals=goal.canonical_locals or _legacy_locals(goal),
        target=goal.canonical_target or _legacy_target(goal),
        metadata=(("legacyState", str(not bool(goal.canonical_target)).lower()),),
    )


def canonical_state(frame: Frame) -> ProofState:
    goals = tuple(canonical_goal(goal) for goal in frame.goals)
    known = {goal.goal_id for goal in goals}
    focus = tuple(goal.goal_id for goal in frame.focus_goals if goal.goal_id in known)
    return ProofState(
        goals=goals,
        focus=focus or tuple(goal.goal_id for goal in goals[:1]),
        metadata=(("frameIndex", str(frame.index)),),
    )


def _explicit_edges(source: Frame, target: Frame) -> tuple[ExplicitOccurrenceEdge, ...]:
    # ABI 5 canonical frontiers already contain the elaborated expression
    # forest.  Their ``semanticTransition`` field is the old tactic-oriented
    # compatibility projection, not independent elaborator evidence.  Feeding
    # it back into the canonical matcher would let the legacy heuristic become
    # proof semantics again (and can permute repeated ``f``/``x`` atoms).
    # Keep that field only as a span bridge for old renderers.  Schemas without
    # canonical expressions still import it through the migration path below.
    native_canonical = has_native_canonical_observation(
        source
    ) and has_native_canonical_observation(target)
    if native_canonical:
        return ()

    source_by_lineage = {goal.lineage_id: goal for goal in source.goals}
    result: list[ExplicitOccurrenceEdge] = []
    for target_goal in target.goals:
        transition = target_goal.semantic_transition
        if not isinstance(transition, SemanticTransition):
            continue
        source_goal_id = (
            transition.goal_diff.source_goal_id
            if transition.goal_diff is not None
            else (
                source_by_lineage[target_goal.lineage_id].goal_id
                if target_goal.lineage_id in source_by_lineage
                else target_goal.parent_goal_id or ""
            )
        )
        for edge in transition.edges:
            result.append(
                ExplicitOccurrenceEdge(
                    edge.source_node_id,
                    edge.target_node_id,
                    edge.reason,
                    source_goal_id=source_goal_id,
                    target_goal_id=target_goal.goal_id,
                    confidence=edge.confidence if edge.confidence is not None else 1.0,
                    relation=(
                        RelationKind(edge.relation)
                        if edge.relation in {item.value for item in RelationKind}
                        else None
                    ),
                )
            )
    return tuple(result)


def attach_canonical_timeline(frames: tuple[Frame, ...]) -> tuple[Frame, ...]:
    """Attach one state and normalized morphism to every existing frame."""

    if not frames:
        return ()
    result: list[Frame] = []
    previous_frame: Frame | None = None
    previous_state: ProofState | None = None
    for frame in frames:
        state = canonical_state(frame)
        transition = None
        if previous_frame is not None and previous_state is not None:
            transition = diff_proof_states(
                previous_state,
                state,
                explicit_occurrence_edges=_explicit_edges(previous_frame, frame),
                explicit_goal_edges=frame.goal_lineage,
                explicit_entity_edges=frame.canonical_correspondence,
                metadata=TransitionMetadata(
                    tactic_text=frame.tactic,
                    proof_kind=next(
                        (
                            goal.semantic_transition.proof_kind
                            for goal in frame.goals
                            if isinstance(goal.semantic_transition, SemanticTransition)
                        ),
                        "",
                    ),
                    proof_fingerprint=next(
                        (
                            goal.semantic_transition.proof_fingerprint
                            for goal in frame.goals
                            if isinstance(goal.semantic_transition, SemanticTransition)
                        ),
                        "",
                    ),
                ),
            )
            visual_plan = plan_visual_transition(previous_state, state, transition)
        else:
            visual_plan = None
        goals = frame.goals
        focus_goals = frame.focus_goals
        if previous_frame is not None and transition is not None:
            goals = tuple(
                _bridge_goal(previous_frame, frame, goal, transition, visual_plan)
                for goal in frame.goals
            )
            by_id = {goal.goal_id: goal for goal in goals}
            focus_goals = tuple(
                by_id.get(goal.goal_id, goal) for goal in frame.focus_goals
            )
        enriched = replace(
            frame,
            goals=goals,
            focus_goals=focus_goals,
            proof_state=state,
            proof_transition=transition,
            visual_plan=visual_plan,
        )
        result.append(enriched)
        previous_frame = enriched
        previous_state = state
    return tuple(result)


def semantic_transition_edges(
    transition: SemanticTransition | None,
) -> tuple[ExplicitOccurrenceEdge, ...]:
    """Public migration helper for diagnostics and old trace readers."""

    if transition is None:
        return ()
    return tuple(
        ExplicitOccurrenceEdge(
            item.source_node_id,
            item.target_node_id,
            item.reason,
            confidence=item.confidence if item.confidence is not None else 1.0,
            relation=(
                RelationKind(item.relation)
                if item.relation in {value.value for value in RelationKind}
                else None
            ),
        )
        for item in transition.edges
    )
