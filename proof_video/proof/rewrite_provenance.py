"""Directed expression provenance supplied by equality premises.

This module answers a narrower question than generic AST matching: when a
carrier proposition changes one subtree from ``old`` to ``new``, did a
certified visible equality premise state exactly ``old = new`` (or the reverse)?
If so, the displayed occurrence on the result side of that equality is the
origin of ``new``.  An equal-looking atom inside ``old`` is deliberately not
eligible.
"""

from __future__ import annotations

from dataclasses import dataclass

from proof_video.proof.matching import (
    node_latex,
    path_without_sequent_prefix,
    rendered_expression_key,
    span_extent,
)
from proof_video.proof.schema import ProofStep, SemanticExpressionNode


_EQUALITY_MARKERS = ("=", r"\Leftrightarrow", r"\iff", "⇔")


@dataclass(frozen=True)
class DirectedRewriteOrigin:
    """A visible occurrence certified as the result side of a rewrite."""

    source_node: SemanticExpressionNode
    witness_step_id: int
    direction: str


def _same_expression(
    first: SemanticExpressionNode,
    first_latex: str,
    second: SemanticExpressionNode,
    second_latex: str,
) -> bool:
    first_text = rendered_expression_key(node_latex(first, first_latex))
    second_text = rendered_expression_key(node_latex(second, second_latex))
    if first_text and first_text == second_text:
        return True
    if first.fingerprint and first.fingerprint == second.fingerprint:
        return True
    return bool(
        first.identity
        and first.identity == second.identity
        and first.kind in {"bvar", "fvar", "mvar", "const", "literal"}
        and second.kind in {"bvar", "fvar", "mvar", "const", "literal"}
    )


def _top_level_equality_sides(
    step: ProofStep,
) -> tuple[SemanticExpressionNode, SemanticExpressionNode] | None:
    """Return the two operands of a rendered top-level Eq/Iff proposition."""

    by_path: dict[tuple[str | int, ...], list[SemanticExpressionNode]] = {}
    for node in step.semantic_nodes:
        by_path.setdefault(path_without_sequent_prefix(node.path), []).append(node)

    roots = sorted(
        (path for path in by_path if path),
        key=lambda path: (len(path), path),
    )
    for root in roots:
        left_nodes = by_path.get((*root, "0"), ())
        right_nodes = by_path.get((*root, "1"), ())
        if not left_nodes or not right_nodes:
            continue
        left = max(left_nodes, key=span_extent)
        right = max(right_nodes, key=span_extent)
        if not left.latex_spans or not right.latex_spans:
            continue
        left_end = max(span.end for span in left.latex_spans)
        right_start = min(span.start for span in right.latex_spans)
        if left_end > right_start:
            continue
        separator = step.proposition_latex[left_end:right_start]
        if any(marker in separator for marker in _EQUALITY_MARKERS):
            return left, right
    return None


def _visible_occurrence(
    step_node: SemanticExpressionNode,
    visible_nodes: tuple[SemanticExpressionNode, ...],
    source_sequent: str,
    step_latex: str,
) -> SemanticExpressionNode | None:
    """Locate the on-board occurrence of one node from a visible proof step."""

    step_path = path_without_sequent_prefix(step_node.path)
    candidates = [
        node
        for node in visible_nodes
        if path_without_sequent_prefix(node.path) == step_path
        and node.kind == step_node.kind
    ]
    if not candidates:
        return None
    exact_suffix = [
        node for node in candidates if node.node_id.endswith(step_node.node_id)
    ]
    if exact_suffix:
        candidates = exact_suffix
    rendered = [
        node
        for node in candidates
        if rendered_expression_key(node_latex(node, source_sequent))
        == rendered_expression_key(node_latex(step_node, step_latex))
    ]
    if rendered:
        candidates = rendered
    return max(candidates, key=lambda node: (span_extent(node), node.node_id))


def directed_rewrite_origins(
    *,
    old_node: SemanticExpressionNode,
    new_node: SemanticExpressionNode,
    old_latex: str,
    new_latex: str,
    visible_leaf_ids: frozenset[int],
    visible_steps_by_id: dict[int, ProofStep],
    visible_nodes_by_leaf: dict[int, tuple[SemanticExpressionNode, ...]],
    source_sequent: str,
) -> tuple[DirectedRewriteOrigin, ...]:
    """Find result-side occurrences for a certified directed subtree rewrite.

    Both directions are supported.  The whole equality operand must match the
    old and new subtree; matching a descendant with the same glyph is never
    enough.  Several actual proof premises may justify the same rewrite, so
    all certified alternatives are returned for the global planner.
    """

    if _same_expression(old_node, old_latex, new_node, new_latex):
        return ()

    result: list[DirectedRewriteOrigin] = []
    for leaf_id in sorted(visible_leaf_ids):
        step = visible_steps_by_id.get(leaf_id)
        if step is None:
            continue
        sides = _top_level_equality_sides(step)
        if sides is None:
            continue
        left, right = sides
        directions = (
            (left, right, "left-to-right"),
            (right, left, "right-to-left"),
        )
        for premise_old, premise_new, direction in directions:
            if not _same_expression(
                old_node, old_latex, premise_old, step.proposition_latex
            ):
                continue
            if not _same_expression(
                new_node, new_latex, premise_new, step.proposition_latex
            ):
                continue
            visible = _visible_occurrence(
                premise_new,
                visible_nodes_by_leaf.get(leaf_id, ()),
                source_sequent,
                step.proposition_latex,
            )
            if visible is not None:
                result.append(DirectedRewriteOrigin(visible, leaf_id, direction))

    # Keep the contract deterministic without collapsing distinct witnesses.
    return tuple(
        sorted(
            result,
            key=lambda item: (
                item.witness_step_id,
                item.direction,
                item.source_node.node_id,
            ),
        )
    )
