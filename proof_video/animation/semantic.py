"""Pure semantic token correspondence for proof animations.

This module has no Manim dependency.  It turns Lean-certified AST edges into
a globally consistent token transition plan consumed by both renderers.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, replace

from proof_video.presentation.model import SemanticVisualPlan, VisualPrimitiveKind
from proof_video.proof.correspondence import EntityKind
from proof_video.proof.schema import (
    SemanticExpression,
    SemanticTransition,
    SemanticTransitionEdge,
    has_native_canonical_observation,
)
from proof_video.transition_plan import (
    TokenPair,
    TransitionCandidate,
    TransitionPlan,
    TransitionRole,
    solve_transition_plan,
)

STRUCTURAL_SHELL_REASONS = frozenset(
    {
        "verified-structural-shell",
        "verified-premise-branch-shell",
    }
)


_VISUAL_RELATION = {
    VisualPrimitiveKind.KEEP: "preserve",
    VisualPrimitiveKind.MOVE: "preserve",
    VisualPrimitiveKind.COPY: "copy",
    VisualPrimitiveKind.REWRITE: "rewrite",
    VisualPrimitiveKind.SPLIT: "split",
    VisualPrimitiveKind.MERGE: "merge",
}


@dataclass(frozen=True)
class RendererTransitionSource:
    """One goal card participating in a renderer transition.

    Token indices and semantic spans are local to this card.  The shared
    compiler below assigns deterministic global offsets; renderers therefore
    never have to guess which same-looking parent supplied a target glyph.
    """

    goal_id: str
    token_spans: tuple[tuple[int, int], ...]
    token_texts: tuple[str, ...]
    expression: SemanticExpression


def visual_source_goal_ids(
    visual_plan: SemanticVisualPlan | None,
    target_goal_id: str,
) -> tuple[str, ...]:
    """Return every certified source goal connected to ``target_goal_id``.

    Ordering follows the immutable visual plan.  This is goal-card routing,
    not token matching: text, geometry and tactic names never participate.
    """

    if visual_plan is None:
        return ()
    anchors = {item.anchor_id: item for item in visual_plan.anchors}
    result: list[str] = []
    for primitive in visual_plan.primitives:
        if primitive.used_fallback or primitive.kind not in {
            VisualPrimitiveKind.KEEP,
            VisualPrimitiveKind.MOVE,
            VisualPrimitiveKind.COPY,
            VisualPrimitiveKind.REWRITE,
            VisualPrimitiveKind.SPLIT,
            VisualPrimitiveKind.MERGE,
        }:
            continue
        targets_goal = any(
            anchor_id in anchors and anchors[anchor_id].entity.goal_id == target_goal_id
            for anchor_id in primitive.target_anchor_ids
        )
        if not targets_goal:
            continue
        result.extend(
            anchors[anchor_id].entity.goal_id
            for anchor_id in primitive.source_anchor_ids
            if anchor_id in anchors and anchors[anchor_id].entity.goal_id
        )
    return tuple(dict.fromkeys(result))


def authoritative_frame_visual_plan(frame) -> SemanticVisualPlan | None:
    """Return a plan only when it came from a native canonical observation.

    ABI 1--4 traces are upgraded in memory with best-effort states so existing
    tools can inspect them.  Their renderer correspondence, however, may have
    occurrence paths that cannot be resolved by the new canonical anchors.
    They deliberately stay on the legacy compatibility bridge.  ABI 5 states
    have ``legacyState=false`` and use the canonical plan exclusively.
    """

    visual_plan = getattr(frame, "visual_plan", None)
    state = getattr(frame, "proof_state", None)
    if visual_plan is None or state is None:
        return None
    if not has_native_canonical_observation(frame):
        return None
    return visual_plan


@dataclass(frozen=True)
class _TokenSpanIndex:
    """Search immutable, ordered renderer-token spans without rescanning them."""

    spans: tuple[tuple[int, int], ...]
    starts: tuple[int, ...]
    ends: tuple[int, ...]

    @classmethod
    def build(cls, spans) -> "_TokenSpanIndex":
        frozen = tuple(spans)
        return cls(
            spans=frozen,
            starts=tuple(start for start, _end in frozen),
            ends=tuple(end for _start, end in frozen),
        )

    def overlapping(self, semantic_spans) -> list[int]:
        if not semantic_spans or any(not span.valid for span in semantic_spans):
            return []
        matches: set[int] = set()
        for semantic_span in semantic_spans:
            # Token spans are emitted in source order and do not overlap.
            # ``bisect_right`` skips every token ending at/before the semantic
            # start; the forward scan then stops at the semantic end.
            first = bisect_right(self.ends, semantic_span.start)
            for index in range(first, len(self.spans)):
                if self.starts[index] >= semantic_span.end:
                    break
                matches.add(index)
        return sorted(matches)


def _row_base_key(key: str) -> str:
    """Remove only a generated line-wrap suffix from a semantic row key."""
    return key.rsplit(":", 1)[0]


def _is_symbolic_latex_token(token: str) -> bool:
    """Whether a rendered token is mathematical syntax, not an identifier."""
    if not token:
        return False
    # Grouped commands render names, types, numerals or textual constants as
    # one token. Their identity must continue to come from expression nodes.
    if token.startswith(
        (
            r"\mathbb{",
            r"\mathbf{",
            r"\mathrm{",
            r"\text{",
            r"\operatorname{",
        )
    ):
        return False
    if all(character.isalnum() or character in "_'" for character in token):
        return False
    return True


def _supplement_logically_stable_syntax_pairs(
    semantic_pairs,
    source_spans,
    source_positions,
    source_token_texts,
    target_spans,
    target_positions,
    target_token_texts,
    transition: SemanticTransition,
):
    """Preserve unchanged syntax only when a Lean edge owns both tokens.

    Parent expression spans naturally contain punctuation and operators that
    are not standalone Lean ``Expr`` nodes: commas, parentheses, dots,
    relation signs, arithmetic operators, and connective commands.  Equal
    screen positions alone are insufficient identity.  This supplement
    therefore requires both an exact row-local token identity and at least one
    semantic transition edge whose source and target nodes cover the pair.
    """
    result = list(semantic_pairs)
    used_source = {source_index for source_index, _ in result}
    used_target = {target_index for _, target_index in result}
    source_nodes = {node.node_id: node for node in transition.source.nodes}
    target_nodes = {node.node_id: node for node in transition.target.nodes}

    source_by_identity: dict[tuple, list[int]] = {}
    target_by_identity: dict[tuple, list[int]] = {}
    for index, (position, token_text) in enumerate(
        zip(source_positions, source_token_texts, strict=True)
    ):
        if index not in used_source and _is_symbolic_latex_token(token_text):
            source_by_identity.setdefault((*position, token_text), []).append(index)
    for index, (position, token_text) in enumerate(
        zip(target_positions, target_token_texts, strict=True)
    ):
        if index not in used_target and _is_symbolic_latex_token(token_text):
            target_by_identity.setdefault((*position, token_text), []).append(index)

    candidates = []
    for identity in source_by_identity.keys() & target_by_identity.keys():
        source_candidates = source_by_identity[identity]
        target_candidates = target_by_identity[identity]
        if len(source_candidates) != 1 or len(target_candidates) != 1:
            continue
        candidates.append((source_candidates[0], target_candidates[0]))

    logically_connected = set()
    for edge in transition.edges:
        source_node = source_nodes.get(edge.source_node_id)
        target_node = target_nodes.get(edge.target_node_id)
        if source_node is None or target_node is None:
            continue
        covered_source = set(
            _tokens_in_semantic_spans(source_spans, source_node.latex_spans)
        )
        covered_target = set(
            _tokens_in_semantic_spans(target_spans, target_node.latex_spans)
        )
        for pair in candidates:
            if pair[0] in covered_source and pair[1] in covered_target:
                logically_connected.add(pair)

    for source_index, target_index in candidates:
        if (source_index, target_index) not in logically_connected:
            continue
        used_source.add(source_index)
        used_target.add(target_index)
        result.append((source_index, target_index))
    return result


def _collect_row_token_data(rows):
    """Flatten rendered rows while retaining each token's semantic row."""
    global_spans = []
    structural_positions = []
    token_texts = []
    tokens = []
    for row_index, row in enumerate(rows):
        spans = getattr(row, "proof_token_spans", None)
        row_token_texts = getattr(row, "proof_tokens", None)
        row_tokens = getattr(row, "proof_token_mobjects", None)
        row_span = getattr(row, "proof_char_span", None)
        if (
            spans is None
            or row_token_texts is None
            or row_tokens is None
            or row_span is None
        ):
            return None
        global_spans.extend(
            (row_span[0] + start, row_span[0] + end) for start, end in spans
        )
        row_key = _row_base_key(getattr(row, "proof_row_key", f"row-{row_index}"))
        structural_positions.extend((row_key, start, end) for start, end in spans)
        token_texts.extend(row_token_texts)
        tokens.extend(row_tokens)
    return global_spans, structural_positions, token_texts, tokens


def _stable_visual_rows(source_block, target_block):
    """Rows that are provably identical by block key, row key and LaTeX."""
    source_blocks = {
        getattr(block, "proof_block_key", f"source-block-{index}"): block
        for index, block in enumerate(source_block)
    }
    target_blocks = {
        getattr(block, "proof_block_key", f"target-block-{index}"): block
        for index, block in enumerate(target_block)
    }
    stable = []
    for block_key in source_blocks.keys() & target_blocks.keys():
        source_rows = {
            getattr(row, "proof_row_key", f"source-row-{index}"): row
            for index, row in enumerate(source_blocks[block_key])
        }
        target_rows = {
            getattr(row, "proof_row_key", f"target-row-{index}"): row
            for index, row in enumerate(target_blocks[block_key])
        }
        for row_key in source_rows.keys() & target_rows.keys():
            source = source_rows[row_key]
            target = target_rows[row_key]
            source_latex = getattr(source, "proof_latex_source", None)
            if source_latex is not None and source_latex == getattr(
                target, "proof_latex_source", None
            ):
                stable.append(source)
    return stable


def _semantic_mapped_target_row_bases(
    source_rows,
    target_rows,
    semantic_transition: SemanticTransition | None,
    visual_plan: SemanticVisualPlan | None = None,
    *,
    source_goal_id: str = "",
    target_goal_id: str = "",
) -> set[str]:
    """Return new target rows reached by an actual semantic token edge."""
    source_data = _collect_row_token_data(source_rows)
    target_data = _collect_row_token_data(target_rows)
    if source_data is None or target_data is None:
        return set()
    source_global, _source_positions, source_texts, _source_tokens = source_data
    target_global, target_positions, target_texts, _target_tokens = target_data
    pairs = _semantic_token_pairs(
        source_global,
        source_texts,
        target_global,
        target_texts,
        semantic_transition,
        visual_plan,
        source_goal_id=source_goal_id,
        target_goal_id=target_goal_id,
    )
    if not pairs:
        return set()
    return {target_positions[target_index][0] for _source_index, target_index in pairs}


def semantic_mapped_target_row_bases_from_sources(
    source_groups,
    target_rows,
    semantic_transition: SemanticTransition | None,
    visual_plan: SemanticVisualPlan | None,
    *,
    target_goal_id: str,
) -> set[str]:
    """Return target rows reached from any certified source goal card."""

    target_data = _collect_row_token_data(target_rows)
    if target_data is None:
        return set()
    target_global, target_positions, target_texts, _target_tokens = target_data
    renderer_sources: list[RendererTransitionSource] = []
    for goal_id, rows, expression in source_groups:
        source_data = _collect_row_token_data(rows)
        if source_data is None:
            return set()
        source_global, _positions, source_texts, _tokens = source_data
        renderer_sources.append(
            RendererTransitionSource(
                goal_id=goal_id,
                token_spans=tuple(source_global),
                token_texts=tuple(source_texts),
                expression=expression,
            )
        )
    plan = compile_renderer_transition_plan_from_sources(
        tuple(renderer_sources),
        target_global,
        target_texts,
        semantic_transition,
        visual_plan,
        target_goal_id=target_goal_id,
    )
    if plan is None or not plan.valid:
        return set()
    return {
        target_positions[target_index][0] for _source_index, target_index in plan.pairs
    }


def _semantic_token_pairs(
    source_token_spans,
    source_token_texts,
    target_token_spans,
    target_token_texts,
    transition: SemanticTransition | None,
    visual_plan: SemanticVisualPlan | None = None,
    *,
    source_goal_id: str = "",
    target_goal_id: str = "",
) -> list[tuple[int, int]] | None:
    """Compile Lean edges into a globally validated strict token plan.

    Equality of text, SVG shape, LaTeX position, or a SymPy expression is
    never sufficient here.  Only certified Lean correspondences become
    physical moves; every unresolved target token is deliberately written as
    new by ``_phased_token_transition``.
    """
    plan = compile_renderer_transition_plan(
        source_token_spans,
        source_token_texts,
        target_token_spans,
        target_token_texts,
        transition,
        visual_plan,
        source_goal_id=source_goal_id,
        target_goal_id=target_goal_id,
    )
    return (
        list(plan.pairs)
        if plan is not None and plan.valid
        else ([] if transition is not None else None)
    )


def visual_primitive_payload(
    visual_plan: SemanticVisualPlan | None,
) -> list[dict[str, object]]:
    """Serialize renderer-neutral control primitives without adding semantics.

    Token geometry is deliberately absent.  Remotion may use this payload for
    goal split/focus/layout effects, while Manim reads the same primitive kinds
    before pairing blocks.  The source remains the immutable canonical plan.
    """

    if visual_plan is None:
        return []
    anchors = {item.anchor_id: item for item in visual_plan.anchors}
    return [
        {
            "id": primitive.primitive_id,
            "kind": primitive.kind.value,
            "sourceAnchors": list(primitive.source_anchor_ids),
            "targetAnchors": list(primitive.target_anchor_ids),
            "sourceSlots": [
                list(anchors[item].slot)
                for item in primitive.source_anchor_ids
                if item in anchors
            ],
            "targetSlots": [
                list(anchors[item].slot)
                for item in primitive.target_anchor_ids
                if item in anchors
            ],
            "persistentIds": list(primitive.persistent_ids),
            "scope": primitive.scope,
            "fallback": primitive.fallback_reason,
        }
        for primitive in visual_plan.primitives
    ]


def _entity_node_ids(entity, nodes) -> tuple[str, ...]:
    """Resolve a canonical layout entity to observed renderer-span nodes."""

    if entity.kind is EntityKind.OCCURRENCE:
        return (entity.occurrence_id,)
    if entity.kind is EntityKind.LOCAL:
        prefix = f"context/{entity.local_id}/"
        matches = tuple(
            node.node_id
            for node in nodes
            if node.kind == "declaration"
            and (
                node.identity == f"fvar:{entity.local_id}"
                or node.node_id.startswith(prefix)
            )
        )
        return matches[:1]
    if entity.kind in {EntityKind.LOCAL_TYPE, EntityKind.LOCAL_VALUE}:
        prefix = (
            f"context/{entity.local_id}"
            if entity.kind is EntityKind.LOCAL_TYPE
            else f"local/{entity.local_id}/value"
        )
        candidates = tuple(
            node
            for node in nodes
            if node.node_id == prefix or node.node_id.startswith(prefix + "/")
            if node.kind not in {"declaration", "declaration-punctuation"}
        )
        roots = tuple(node.node_id for node in candidates if node.parent_id is None)
        return roots[:1] or tuple(node.node_id for node in candidates[:1])
    if entity.kind is EntityKind.TARGET:
        candidates = tuple(
            node
            for node in nodes
            if not node.path or node.path[0] != "context"
            if node.kind != "sequent-punctuation"
        )
        roots = tuple(node.node_id for node in candidates if node.parent_id is None)
        return roots[:1] or tuple(node.node_id for node in candidates[:1])
    return ()


def _visual_plan_edges(
    visual_plan: SemanticVisualPlan,
    transition: SemanticTransition,
    *,
    source_goal_id: str = "",
    target_goal_id: str = "",
) -> tuple[SemanticTransitionEdge, ...]:
    """Expand occurrence hyperedges only at the renderer-token boundary.

    The expansion is mechanical: no tactic text, glyph equality or geometry
    participates.  Non-occurrence controls (goal split/close/focus and row
    reorder) remain in :func:`visual_primitive_payload` and never manufacture
    token identity.  Diagnostic fallback primitives are likewise ineligible
    for physical movement.
    """

    anchors = {item.anchor_id: item for item in visual_plan.anchors}
    source_nodes = transition.source.nodes
    target_nodes = transition.target.nodes
    edges: list[SemanticTransitionEdge] = []
    for primitive in visual_plan.primitives:
        relation = _VISUAL_RELATION.get(primitive.kind)
        if relation is None or primitive.used_fallback:
            continue
        source_entities = tuple(
            anchors[item].entity
            for item in primitive.source_anchor_ids
            if item in anchors
            and (not source_goal_id or anchors[item].entity.goal_id == source_goal_id)
        )
        target_entities = tuple(
            anchors[item].entity
            for item in primitive.target_anchor_ids
            if item in anchors
            and (not target_goal_id or anchors[item].entity.goal_id == target_goal_id)
        )
        sources = tuple(
            node_id
            for entity in source_entities
            for node_id in _entity_node_ids(entity, source_nodes)
        )
        targets = tuple(
            node_id
            for entity in target_entities
            for node_id in _entity_node_ids(entity, target_nodes)
        )
        if not sources or not targets:
            continue
        reason = next(
            (item for item in primitive.evidence if item.startswith("verified-")),
            f"verified-canonical-{primitive.kind.value}",
        )
        for source in sources:
            for target in targets:
                edges.append(
                    SemanticTransitionEdge(
                        source_node_id=source,
                        target_node_id=target,
                        reason=reason,
                        confidence=primitive.confidence,
                        relation=relation,
                        provenance="canonical-visual-plan",
                    )
                )
    return tuple(edges)


def compile_renderer_transition_plan(
    source_token_spans,
    source_token_texts,
    target_token_spans,
    target_token_texts,
    transition: SemanticTransition | None,
    visual_plan: SemanticVisualPlan | None = None,
    *,
    source_goal_id: str = "",
    target_goal_id: str = "",
):
    """Compile the one authoritative visual plan for either renderer.

    ``SemanticTransition`` supplies occurrence spans for old SVG/KaTeX token
    layouts.  When ``visual_plan`` exists, its primitives completely replace
    the transition's legacy edge list; a conflicting old edge can therefore
    never steer a glyph.  Traces predating canonical states retain the narrow
    compatibility path through their original semantic edges.
    """

    if visual_plan is None:
        return _semantic_transition_plan(
            source_token_spans,
            source_token_texts,
            target_token_spans,
            target_token_texts,
            transition,
        )
    if transition is None:
        return solve_transition_plan(
            len(source_token_texts), len(target_token_texts), ()
        )
    canonical_transition = SemanticTransition(
        source=transition.source,
        target=transition.target,
        edges=_visual_plan_edges(
            visual_plan,
            transition,
            source_goal_id=source_goal_id,
            target_goal_id=target_goal_id,
        ),
        proof_kind=transition.proof_kind,
        adapter="canonical-visual-plan",
        proof_fingerprint=transition.proof_fingerprint,
        proof_term=transition.proof_term,
        proof_descendants=transition.proof_descendants,
        proof_premises=transition.proof_premises,
        proof_constants=transition.proof_constants,
        goal_diff=transition.goal_diff,
        fallback_reason=transition.fallback_reason,
    )
    return _semantic_transition_plan(
        source_token_spans,
        source_token_texts,
        target_token_spans,
        target_token_texts,
        canonical_transition,
    )


def compile_renderer_transition_plan_from_sources(
    sources: tuple[RendererTransitionSource, ...],
    target_token_spans,
    target_token_texts,
    transition: SemanticTransition | None,
    visual_plan: SemanticVisualPlan | None,
    *,
    target_goal_id: str,
) -> TransitionPlan | None:
    """Compile one global token plan from every certified parent goal card.

    A canonical goal merge is an n→1 hyperedge.  Each source expression still
    has card-local LaTeX coordinates, so compiling a concatenated expression
    would make equal offsets from different cards ambiguous.  Instead, this
    function compiles each source in its own coordinate space, shifts its
    selected candidates into one global source-token space, and invokes the
    same global solver once across all parents.

    The function intentionally accepts only explicit semantic expressions and
    visual-plan goal identities.  It performs no rendered-text, geometry,
    tactic-name, or theorem-specific source selection.
    """

    if not sources:
        return None
    if visual_plan is None:
        if len(sources) != 1:
            return None
        source = sources[0]
        return compile_renderer_transition_plan(
            source.token_spans,
            source.token_texts,
            target_token_spans,
            target_token_texts,
            transition,
            None,
            source_goal_id=source.goal_id,
            target_goal_id=target_goal_id,
        )
    if transition is None:
        return solve_transition_plan(
            sum(len(source.token_texts) for source in sources),
            len(target_token_texts),
            (),
        )

    candidates: list[TransitionCandidate] = []
    source_offset = 0
    for source in sources:
        local_transition = replace(transition, source=source.expression)
        local_plan = compile_renderer_transition_plan(
            source.token_spans,
            source.token_texts,
            target_token_spans,
            target_token_texts,
            local_transition,
            visual_plan,
            source_goal_id=source.goal_id,
            target_goal_id=target_goal_id,
        )
        if local_plan is not None and local_plan.valid:
            candidates.extend(
                replace(
                    candidate,
                    candidate_id=(
                        f"{source.goal_id}->{target_goal_id}:{candidate.candidate_id}"
                    ),
                    source_node_id=f"{source.goal_id}/{candidate.source_node_id}",
                    target_node_id=f"{target_goal_id}/{candidate.target_node_id}",
                    pairs=tuple(
                        TokenPair(pair.source + source_offset, pair.target)
                        for pair in candidate.pairs
                    ),
                )
                for candidate in local_plan.selected
            )
        source_offset += len(source.token_texts)

    return solve_transition_plan(
        source_offset,
        len(target_token_texts),
        tuple(candidates),
    )


def _semantic_transition_plan(
    source_token_spans,
    source_token_texts,
    target_token_spans,
    target_token_texts,
    transition: SemanticTransition | None,
):
    """Return the audited plan so non-Manim renderers use identical logic."""
    if transition is None:
        return None
    source_nodes = {
        node.node_id: node for node in transition.source.nodes if node.node_id
    }
    target_nodes = {
        node.node_id: node for node in transition.target.nodes if node.node_id
    }
    if not source_nodes or not target_nodes or not transition.edges:
        return solve_transition_plan(
            len(source_token_texts), len(target_token_texts), ()
        )

    source_children: dict[str, list] = {}
    target_children: dict[str, list] = {}
    for node in transition.source.nodes:
        if node.parent_id:
            source_children.setdefault(node.parent_id, []).append(node)
    for node in transition.target.nodes:
        if node.parent_id:
            target_children.setdefault(node.parent_id, []).append(node)

    source_span_index = _TokenSpanIndex.build(source_token_spans)
    target_span_index = _TokenSpanIndex.build(target_token_spans)

    def balanced_indices(node, span_index, token_texts):
        indices = span_index.overlapping(node.latex_spans)
        if not indices:
            return []
        opening = {"(", "[", r"\left(", r"\left[", r"\big(", r"\big["}
        closing = {")", "]", r"\right)", r"\right]", r"\big)", r"\big]"}
        balance = sum(token_texts[index] in opening for index in indices)
        balance -= sum(token_texts[index] in closing for index in indices)
        if balance > 0:
            # LeanTeX occurrence spans sometimes stop immediately before the
            # closing application delimiter. Extend only across closing
            # syntax, never across another identifier/operator.
            for index in range(max(indices) + 1, len(token_texts)):
                if token_texts[index] not in closing:
                    break
                indices.append(index)
                balance -= 1
                if balance == 0:
                    break
        return indices

    def shell_indices(node, children, span_index, token_texts):
        """Tokens owned by an AST constructor rather than a direct child.

        These are rendered syntax such as application parentheses and binary
        operators. Pairing them is safe only for a verified structural edge
        between the same constructor paths; they are never globally matched
        merely because another parenthesis looks the same.
        """
        owned = balanced_indices(node, span_index, token_texts)
        direct_children = children.get(node.node_id, ())
        if not direct_children:
            return []
        child_tokens = {
            index
            for child in direct_children
            for index in balanced_indices(child, span_index, token_texts)
        }
        ordered_owned = sorted(owned)
        opening = {"(", "[", r"\left(", r"\left[", r"\big(", r"\big["}
        child_token_groups = [
            balanced_indices(child, span_index, token_texts)
            for child in direct_children
        ]
        has_atomic_function_head = (
            any(group == [ordered_owned[0]] for group in child_token_groups)
            if ordered_owned
            else False
        )
        if (
            node.kind == "app"
            and len(ordered_owned) >= 2
            and token_texts[ordered_owned[1]] in opening
            and has_atomic_function_head
        ):
            # The function head and its delimiters form one application
            # shell only when an immediate child proves that the first token
            # really is the atomic function head.  A relation is also encoded
            # as an ``app`` by Lean; its left operand may happen to start with
            # ``f(``, but that f belongs to the operand, not to the relation.
            # Keeping the ownership distinction prevents a substitution from
            # coupling that f to the unchanged infix operator (for example ≤).
            child_tokens.discard(ordered_owned[0])
        return [index for index in owned if index not in child_tokens]

    candidates = []
    for edge_index, edge in enumerate(transition.edges):
        source_node = source_nodes.get(edge.source_node_id)
        target_node = target_nodes.get(edge.target_node_id)
        if source_node is None or target_node is None:
            continue
        if edge.reason in STRUCTURAL_SHELL_REASONS:
            source_indices = shell_indices(
                source_node,
                source_children,
                source_span_index,
                source_token_texts,
            )
            target_indices = shell_indices(
                target_node,
                target_children,
                target_span_index,
                target_token_texts,
            )
        else:
            source_indices = balanced_indices(
                source_node, source_span_index, source_token_texts
            )
            target_indices = balanced_indices(
                target_node, target_span_index, target_token_texts
            )
        if not source_indices or not target_indices:
            continue
        source_sequence = [source_token_texts[index] for index in source_indices]
        target_sequence = [target_token_texts[index] for index in target_indices]
        exact_composite = len(source_indices) > 1 and source_sequence == target_sequence
        certified = edge.reason.startswith("verified-") or (
            edge.reason == "same-proof-context"
            and edge.source_node_id == edge.target_node_id
        )
        source_context_id = (
            source_node.path[1]
            if len(source_node.path) >= 2 and source_node.path[0] == "context"
            else None
        )
        crosses_from_persistent_context = (
            edge.source_node_id.startswith("proof-context-")
            and not edge.target_node_id.startswith("proof-context-")
            and source_context_id is not None
            and any(
                node_id == f"proof-context-{source_context_id}"
                or node_id.startswith(f"proof-context-{source_context_id}/")
                for node_id in target_nodes
            )
        )
        source_is_stored_elsewhere = any(
            other.source_node_id == edge.source_node_id
            and other.target_node_id != edge.target_node_id
            and other.reason
            in {
                "verified-live-fact-storage",
                "verified-proof-definition-storage",
            }
            for other in transition.edges
        )
        role = (
            TransitionRole.COPY
            if edge.relation in {"copy", "split"}
            or edge.reason == "verified-premise-copy"
            or crosses_from_persistent_context
            or source_is_stored_elsewhere
            else (
                TransitionRole.REWRITE
                if source_sequence != target_sequence
                else TransitionRole.PRESERVE
            )
        )
        if source_sequence == target_sequence:
            pairs = tuple(
                TokenPair(source_index, target_index)
                for source_index, target_index in zip(
                    source_indices, target_indices, strict=True
                )
            )
        elif edge.reason in STRUCTURAL_SHELL_REASONS:
            # The AST path adapter certifies that these tokens belong to the
            # same constructor shell. LeanTeX spans can omit one generated
            # closing delimiter on only one side, making the full sequences
            # unequal. Preserve syntax that is unique within both shells
            # (relation/operator/function head), never a repeated glyph.
            source_occurrences: dict[str, list[int]] = {}
            target_occurrences: dict[str, list[int]] = {}
            for index in source_indices:
                source_occurrences.setdefault(source_token_texts[index], []).append(
                    index
                )
            for index in target_indices:
                target_occurrences.setdefault(target_token_texts[index], []).append(
                    index
                )
            pairs = tuple(
                TokenPair(source_occurrences[token][0], target_occurrences[token][0])
                for token in sorted(
                    source_occurrences.keys() & target_occurrences.keys(),
                    key=lambda item: source_occurrences[item][0],
                )
                if len(source_occurrences[token]) == 1
                and len(target_occurrences[token]) == 1
            )
        elif len(source_indices) == 1 and len(target_indices) == 1 and certified:
            pairs = (TokenPair(source_indices[0], target_indices[0]),)
        else:
            # A composite rewrite needs an explicit child-level proof map.
            # Never mine its equal-looking glyphs by order.
            pairs = ()
        split_structural_shell = (
            edge.reason in STRUCTURAL_SHELL_REASONS
            and source_sequence != target_sequence
        )
        pair_groups = (
            tuple((pair,) for pair in pairs) if split_structural_shell else (pairs,)
        )
        for group_index, pair_group in enumerate(pair_groups):
            group_role = (
                TransitionRole.PRESERVE
                if split_structural_shell
                and len(pair_group) == 1
                and source_token_texts[pair_group[0].source]
                == target_token_texts[pair_group[0].target]
                else role
            )
            source_candidate_id = (
                f"{edge.source_node_id}#shell-{group_index}"
                if split_structural_shell
                else edge.source_node_id
            )
            target_candidate_id = (
                f"{edge.target_node_id}#shell-{group_index}"
                if split_structural_shell
                else edge.target_node_id
            )
            candidates.append(
                TransitionCandidate(
                    candidate_id=(
                        f"edge-{edge_index}.{group_index}:"
                        f"{edge.source_node_id}->{edge.target_node_id}"
                    ),
                    source_node_id=source_candidate_id,
                    target_node_id=target_candidate_id,
                    role=group_role,
                    reason=edge.reason,
                    pairs=pair_group,
                    certified=certified,
                    exact_composite=exact_composite and len(pair_groups) == 1,
                    source_kind=source_node.kind,
                    target_kind=target_node.kind,
                )
            )

    return solve_transition_plan(
        len(source_token_texts),
        len(target_token_texts),
        tuple(candidates),
    )


def _tokens_in_semantic_spans(token_spans, semantic_spans) -> list[int]:
    return _TokenSpanIndex.build(token_spans).overlapping(semantic_spans)
