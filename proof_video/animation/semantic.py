"""Pure semantic token correspondence for proof animations.

This module has no Manim dependency.  It turns Lean-certified AST edges into
a globally consistent token transition plan consumed by both renderers.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from proof_video.proof.schema import SemanticTransition
from proof_video.transition_plan import (
    TokenPair,
    TransitionCandidate,
    TransitionRole,
    solve_transition_plan,
)

STABLE_STRUCTURAL_TOKENS = frozenset({":", r"\vdash"})
STRUCTURAL_SHELL_REASONS = frozenset({
    "verified-structural-shell",
    "verified-premise-branch-shell",
})


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
    if token.startswith((
        r"\mathbb{",
        r"\mathbf{",
        r"\mathrm{",
        r"\text{",
        r"\operatorname{",
    )):
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
        covered_source = set(_tokens_in_semantic_spans(
            source_spans, source_node.latex_spans
        ))
        covered_target = set(_tokens_in_semantic_spans(
            target_spans, target_node.latex_spans
        ))
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
        row_key = _row_base_key(
            getattr(row, "proof_row_key", f"row-{row_index}")
        )
        structural_positions.extend(
            (row_key, start, end) for start, end in spans
        )
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
    semantic_transition: SemanticTransition,
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
    )
    if not pairs:
        return set()
    return {
        target_positions[target_index][0]
        for _source_index, target_index in pairs
    }

def _supplement_stable_structural_pairs(
    semantic_pairs,
    source_positions,
    source_token_texts,
    target_positions,
    target_token_texts,
):
    """Keep unchanged board punctuation without inventing expression identity.

    Lean expression nodes do not include the colon inserted between a local
    hypothesis name and its type.  Such punctuation may persist only when its
    token text, semantic row key, and exact row-local character span all agree.
    This deliberately cannot move a colon to another row or another position.
    """
    result = list(semantic_pairs)
    used_source = {source_index for source_index, _target_index in result}
    used_target = {target_index for _source_index, target_index in result}

    source_by_identity: dict[tuple, list[int]] = {}
    target_by_identity: dict[tuple, list[int]] = {}
    for index, (position, token_text) in enumerate(
        zip(source_positions, source_token_texts, strict=True)
    ):
        if token_text in STABLE_STRUCTURAL_TOKENS:
            source_by_identity.setdefault((*position, token_text), []).append(index)
    for index, (position, token_text) in enumerate(
        zip(target_positions, target_token_texts, strict=True)
    ):
        if token_text in STABLE_STRUCTURAL_TOKENS:
            target_by_identity.setdefault((*position, token_text), []).append(index)

    for identity in source_by_identity.keys() & target_by_identity.keys():
        source_candidates = source_by_identity[identity]
        target_candidates = target_by_identity[identity]
        if len(source_candidates) != 1 or len(target_candidates) != 1:
            continue
        source_index = source_candidates[0]
        target_index = target_candidates[0]
        if source_index in used_source or target_index in used_target:
            continue
        used_source.add(source_index)
        used_target.add(target_index)
        result.append((source_index, target_index))
    return result

def _semantic_token_pairs(
    source_token_spans,
    source_token_texts,
    target_token_spans,
    target_token_texts,
    transition: SemanticTransition | None,
) -> list[tuple[int, int]] | None:
    """Compile Lean edges into a globally validated strict token plan.

    Equality of text, SVG shape, LaTeX position, or a SymPy expression is
    never sufficient here.  Only certified Lean correspondences become
    physical moves; every unresolved target token is deliberately written as
    new by ``_phased_token_transition``.
    """
    plan = _semantic_transition_plan(
        source_token_spans,
        source_token_texts,
        target_token_spans,
        target_token_texts,
        transition,
    )
    return list(plan.pairs) if plan is not None and plan.valid else ([] if transition is not None else None)

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
    source_nodes = {node.node_id: node for node in transition.source.nodes if node.node_id}
    target_nodes = {node.node_id: node for node in transition.target.nodes if node.node_id}
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
        has_atomic_function_head = any(
            group == [ordered_owned[0]] for group in child_token_groups
        ) if ordered_owned else False
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
        exact_composite = (
            len(source_indices) > 1
            and source_sequence == target_sequence
        )
        certified = edge.reason.startswith("verified-") or (
            edge.reason == "same-proof-context"
            and edge.source_node_id == edge.target_node_id
        )
        source_context_id = (
            source_node.path[1]
            if len(source_node.path) >= 2
            and source_node.path[0] == "context"
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
            if edge.reason == "verified-premise-copy"
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
                source_occurrences.setdefault(source_token_texts[index], []).append(index)
            for index in target_indices:
                target_occurrences.setdefault(target_token_texts[index], []).append(index)
            pairs = tuple(
                TokenPair(source_occurrences[token][0], target_occurrences[token][0])
                for token in sorted(
                    source_occurrences.keys() & target_occurrences.keys(),
                    key=lambda item: source_occurrences[item][0],
                )
                if len(source_occurrences[token]) == 1
                and len(target_occurrences[token]) == 1
            )
        elif (
            len(source_indices) == 1
            and len(target_indices) == 1
            and certified
        ):
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
            tuple((pair,) for pair in pairs)
            if split_structural_shell
            else (pairs,)
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
