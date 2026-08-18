"""Proof-DAG-aware composition of a conclusion from several premise rows."""

from __future__ import annotations

from proof_video.proof.matching import (
    adapted_expression_path,
    node_latex,
    path_without_sequent_prefix,
    rendered_expression_key,
    span_extent,
)
from proof_video.proof.schema import (
    ProofStep,
    SemanticExpressionNode,
    SemanticTransitionEdge,
)
from proof_video.proof.rewrite_provenance import directed_rewrite_origins

_ATOMIC_KINDS = frozenset(
    {
        "bvar",
        "fvar",
        "mvar",
        "const",
        "literal",
        "declaration",
        "declaration-punctuation",
        "quantifier-symbol",
    }
)


def _belongs_to_visible_step(node_id: str, step_id: int) -> bool:
    """Recognize a target occurrence regardless of its chapter namespace.

    Context rows deliberately begin with ``proof-context-N/`` because their
    board ownership is part of the node identity.  A live conclusion keeps
    the extractor's globally unique ID, which in a hierarchical trace is
    ``chapter-K/proof-step-N/...``.  Provenance is about the proof-step segment,
    not whether that segment happens to be the first component of the ID.
    """

    marker = f"proof-step-{step_id}/"
    return node_id.startswith(marker) or f"/{marker}" in node_id


def _contains_path(
    outer: tuple[str | int, ...], inner: tuple[str | int, ...]
) -> bool:
    return len(inner) >= len(outer) and inner[: len(outer)] == outer


def _covered_by(
    node: SemanticExpressionNode, owners: list[SemanticExpressionNode]
) -> bool:
    return any(
        any(
            outer.start <= inner.start and inner.end <= outer.end
            for outer in owner.latex_spans
            for inner in node.latex_spans
        )
        for owner in owners
    )


def premise_branch_edges(
    *,
    premise_branches: tuple[tuple[ProofStep, frozenset[int]], ...],
    source_nodes: tuple[SemanticExpressionNode, ...],
    target_nodes: tuple[SemanticExpressionNode, ...],
    conclusion_targets: tuple[SemanticExpressionNode, ...],
    source_sequent: str,
    target_sequent: str,
    existing_edges: tuple[SemanticTransitionEdge, ...],
    target_rule: str,
    visible_steps_by_id: dict[int, ProofStep] | None = None,
) -> tuple[SemanticTransitionEdge, ...]:
    """Project a contracted proof-DAG slice onto visible expression origins.

    One hidden direct premise can itself combine several visible leaves.  We
    first anchor that hidden premise in the displayed conclusion, then choose
    the most structurally continuous leaf as its carrier.  Exact composite
    expressions may come from *any* certified leaf, while shell syntax comes
    only from the carrier.  Thus a conclusion can reuse an inequality from one
    row and a rewritten right-hand side from another without globally matching
    equal-looking glyphs.
    """

    target_by_id = {node.node_id: node for node in target_nodes}
    source_by_id = {node.node_id: node for node in source_nodes}
    owned_targets = [
        target_by_id[edge.target_node_id]
        for edge in existing_edges
        if edge.target_node_id in target_by_id
        and span_extent(target_by_id[edge.target_node_id]) > 1
    ]
    # Only a cross-row edge consumes source glyph ownership. Persistent
    # same-context edges cover whole rows and must not hide atoms that a
    # certified premise branch is entitled to copy into the conclusion.
    conclusion_edges = tuple(
        edge
        for edge in existing_edges
        if not edge.target_node_id.startswith("proof-context-")
    )
    owned_sources = [
        source_by_id[edge.source_node_id]
        for edge in conclusion_edges
        if edge.source_node_id in source_by_id
        and span_extent(source_by_id[edge.source_node_id]) > 1
    ]
    claimed_target_ids = {edge.target_node_id for edge in conclusion_edges}
    available_target_atoms: dict[
        tuple[str, str], list[SemanticExpressionNode]
    ] = {}
    for node in target_nodes:
        if (
            node.node_id.startswith("proof-context-")
            or node.node_id in claimed_target_ids
            or node.kind not in _ATOMIC_KINDS
            or not node.identity
            or _covered_by(node, owned_targets)
        ):
            continue
        available_target_atoms.setdefault((node.kind, node.identity), []).append(node)
    administrative_atom_candidates: set[tuple[str, str]] = set()
    result: list[SemanticTransitionEdge] = []

    for branch_step, visible_leaf_ids in premise_branches:
        if not visible_leaf_ids:
            continue
        branch_sources_by_leaf: dict[int, tuple[SemanticExpressionNode, ...]] = {}
        for leaf in visible_leaf_ids:
            context_prefix = f"proof-context-{leaf}/"
            branch_sources_by_leaf[leaf] = tuple(
                node
                for node in source_nodes
                if (
                    node.node_id.startswith(context_prefix)
                    and len(node.path) >= 3
                    and node.path[0] == "context"
                )
                or (
                    _belongs_to_visible_step(node.node_id, leaf)
                    and node.path
                    and node.path[0] != "context"
                )
            )
        branch_sources = tuple(
            node
            for nodes in branch_sources_by_leaf.values()
            for node in nodes
        )
        if not branch_sources:
            continue

        if not branch_step.semantic_nodes:
            # Some kernel-checked theorem applications are deliberately
            # contracted out of the presentation and have no renderable
            # proposition of their own. Their proof-DAG branch is still exact.
            # Recover a crossing atom only when, after composite ownership is
            # removed, that branch contains exactly one source and the result
            # exactly one target with the same Lean identity. This handles
            # ``hx : x < 0`` feeding the unique uncovered ``x`` in a new
            # inequality without pairing any of the many equal-looking x's.
            available_source_atoms: dict[
                tuple[str, str], list[SemanticExpressionNode]
            ] = {}
            for node in branch_sources:
                if (
                    node.kind not in _ATOMIC_KINDS
                    or not node.identity
                    or _covered_by(node, owned_sources)
                ):
                    continue
                available_source_atoms.setdefault(
                    (node.kind, node.identity), []
                ).append(node)
            for key in available_source_atoms.keys() & available_target_atoms.keys():
                sources = available_source_atoms[key]
                targets = available_target_atoms[key]
                if len(sources) == 1 and len(targets) == 1:
                    administrative_atom_candidates.add(
                        (sources[0].node_id, targets[0].node_id)
                    )
            continue

        branch_nodes = tuple(branch_step.semantic_nodes)

        branch_by_path_kind = {
            (path_without_sequent_prefix(node.path), node.kind): node
            for node in branch_nodes
        }
        carrier_scores: list[tuple[int, int]] = []
        for leaf, leaf_nodes in branch_sources_by_leaf.items():
            score = 0
            for source_node in leaf_nodes:
                branch_node = branch_by_path_kind.get(
                    (path_without_sequent_prefix(source_node.path), source_node.kind)
                )
                if branch_node is None:
                    continue
                source_text = rendered_expression_key(
                    node_latex(source_node, source_sequent)
                )
                branch_text = rendered_expression_key(
                    node_latex(branch_node, branch_step.proposition_latex)
                )
                same_atom = bool(
                    source_node.kind in _ATOMIC_KINDS
                    and source_node.identity
                    and source_node.identity == branch_node.identity
                )
                if (
                    source_text
                    and source_text == branch_text
                    or same_atom
                    or (
                        source_node.fingerprint
                        and source_node.fingerprint == branch_node.fingerprint
                    )
                ):
                    score += max(1, span_extent(branch_node))
            carrier_scores.append((score, leaf))
        carrier_leaf = max(carrier_scores, default=(0, -1))[1]
        carrier_sources = branch_sources_by_leaf.get(carrier_leaf, ())
        carrier_by_path_kind = {
            (path_without_sequent_prefix(node.path), node.kind): node
            for node in carrier_sources
        }
        carrier_by_path: dict[
            tuple[str | int, ...], list[SemanticExpressionNode]
        ] = {}
        for node in carrier_sources:
            carrier_by_path.setdefault(
                path_without_sequent_prefix(node.path), []
            ).append(node)

        anchors: list[
            tuple[int, SemanticExpressionNode, SemanticExpressionNode]
        ] = []
        for branch_node in branch_nodes:
            if span_extent(branch_node) <= 1:
                continue
            branch_text = node_latex(branch_node, branch_step.proposition_latex)
            for target_node in conclusion_targets:
                branch_path = path_without_sequent_prefix(branch_node.path)
                target_path = path_without_sequent_prefix(target_node.path)
                mapped_path = adapted_expression_path(branch_path, target_rule)
                structural_anchor = bool(
                    mapped_path == target_path
                    and (
                        branch_node.kind == target_node.kind
                        or (
                            target_rule == "forall-introduction"
                            and branch_node.kind == "fvar"
                            and target_node.kind == "bvar"
                        )
                    )
                )
                exact_anchor = bool(
                    branch_node.kind == target_node.kind
                    and branch_node.fingerprint
                    and branch_node.fingerprint == target_node.fingerprint
                )
                if not (structural_anchor or exact_anchor):
                    continue
                target_text = node_latex(target_node, target_sequent)
                if (
                    structural_anchor
                    or (
                        branch_text
                        and rendered_expression_key(branch_text)
                        == rendered_expression_key(target_text)
                    )
                ):
                    anchors.append(
                        (span_extent(target_node), branch_node, target_node)
                    )

        anchored_targets: list[SemanticExpressionNode] = []
        for _extent, branch_anchor, target_anchor in sorted(
            anchors,
            key=lambda item: (item[0], item[1].node_id, item[2].node_id),
            reverse=True,
        ):
            if any(
                _contains_path(
                    path_without_sequent_prefix(existing.path),
                    path_without_sequent_prefix(target_anchor.path),
                )
                for existing in anchored_targets
            ):
                continue
            anchored_targets.append(target_anchor)
            branch_anchor_path = path_without_sequent_prefix(branch_anchor.path)
            target_anchor_path = path_without_sequent_prefix(target_anchor.path)
            target_descendants = {
                (path_without_sequent_prefix(node.path), node.kind): node
                for node in target_nodes
                if not node.node_id.startswith("proof-context-")
            }
            candidates: list[tuple[int, str, str, str]] = []

            for branch_node in branch_nodes:
                branch_path = path_without_sequent_prefix(branch_node.path)
                if not _contains_path(branch_anchor_path, branch_path):
                    continue
                suffix = branch_path[len(branch_anchor_path) :]
                target_path = (*target_anchor_path, *suffix)
                target_node = target_descendants.get((target_path, branch_node.kind))
                if target_node is None and (
                    target_rule == "forall-introduction"
                    and branch_node.kind == "fvar"
                ):
                    target_node = target_descendants.get((target_path, "bvar"))
                if target_node is None or _covered_by(target_node, owned_targets):
                    continue
                branch_text = rendered_expression_key(
                    node_latex(branch_node, branch_step.proposition_latex)
                )
                target_text = rendered_expression_key(
                    node_latex(target_node, target_sequent)
                )
                carrier_at_path = carrier_by_path.get(branch_path, ())
                carrier_expression = (
                    max(carrier_at_path, key=span_extent)
                    if carrier_at_path
                    else None
                )
                directed_origins = (
                    directed_rewrite_origins(
                        old_node=carrier_expression,
                        new_node=branch_node,
                        old_latex=source_sequent,
                        new_latex=branch_step.proposition_latex,
                        visible_leaf_ids=visible_leaf_ids,
                        visible_steps_by_id=visible_steps_by_id,
                        visible_nodes_by_leaf=branch_sources_by_leaf,
                        source_sequent=source_sequent,
                    )
                    if carrier_expression is not None and visible_steps_by_id
                    else ()
                )
                if directed_origins:
                    # A proof equality determines the occurrence identity of
                    # this new subtree.  Do not also offer equal-looking atoms
                    # from the replaced side; geometry must not overrule the
                    # direction of a certified rewrite.
                    for origin in directed_origins:
                        candidates.append(
                            (
                                span_extent(origin.source_node),
                                origin.source_node.node_id,
                                target_node.node_id,
                                "verified-directed-equality-result",
                            )
                        )
                    continue
                for source_node in branch_sources:
                    same_fingerprint = bool(
                        branch_node.fingerprint
                        and source_node.fingerprint == branch_node.fingerprint
                    )
                    same_atom = bool(
                        branch_node.kind in _ATOMIC_KINDS
                        and branch_node.identity
                        and source_node.identity == branch_node.identity
                    )
                    if source_node.kind != branch_node.kind:
                        continue
                    source_text = rendered_expression_key(
                        node_latex(source_node, source_sequent)
                    )
                    exact_rendered_expression = bool(
                        branch_node.kind not in _ATOMIC_KINDS
                        and source_text
                        and source_text == branch_text == target_text
                    )
                    if same_fingerprint or same_atom or exact_rendered_expression:
                        candidates.append(
                            (
                                span_extent(source_node),
                                source_node.node_id,
                                target_node.node_id,
                                (
                                    "verified-premise-branch-copy"
                                ),
                            )
                        )

                carrier_node = carrier_by_path_kind.get(
                    (branch_path, branch_node.kind)
                )
                if (
                    carrier_node is not None
                    and branch_node.kind not in _ATOMIC_KINDS
                    and rendered_expression_key(
                        node_latex(carrier_node, source_sequent)
                    ) != target_text
                ):
                    candidates.append(
                        (
                            span_extent(carrier_node),
                            carrier_node.node_id,
                            target_node.node_id,
                            "verified-premise-branch-shell",
                        )
                    )

            emitted: set[tuple[str, str, str]] = set()
            for _source_extent, source_id, target_id, reason in sorted(
                candidates, reverse=True
            ):
                key = (source_id, target_id, reason)
                if key in emitted:
                    continue
                result.append(
                    SemanticTransitionEdge(
                        source_id,
                        target_id,
                        reason,
                        1.0,
                    )
                )
                emitted.add(key)

    # A target reached from more than one semantic-less branch is ambiguous:
    # retain no physical move rather than choosing by row position. Likewise,
    # one source offered to several targets cannot identify an occurrence.
    sources_by_target: dict[str, set[str]] = {}
    targets_by_source: dict[str, set[str]] = {}
    for source_id, target_id in administrative_atom_candidates:
        sources_by_target.setdefault(target_id, set()).add(source_id)
        targets_by_source.setdefault(source_id, set()).add(target_id)
    for source_id, target_id in sorted(administrative_atom_candidates):
        if (
            len(sources_by_target[target_id]) == 1
            and len(targets_by_source[source_id]) == 1
        ):
            result.append(
                SemanticTransitionEdge(
                    source_id,
                    target_id,
                    "verified-premise-branch-atom",
                    1.0,
                )
            )

    return tuple(result)
