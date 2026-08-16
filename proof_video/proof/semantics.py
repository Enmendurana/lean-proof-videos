"""Construction of proof sequents and Lean-certified semantic edges."""

from __future__ import annotations

from proof_video.proof.branch_provenance import premise_branch_edges
from proof_video.proof.matching import (
    adapted_expression_path as _adapted_expression_path,
    common_path_prefix as _common_path_prefix,
    node_latex as _node_latex,
    path_without_sequent_prefix as _path_without_sequent_prefix,
    rendered_expression_key as _rendered_expression_key,
    span_extent as _span_extent,
)
from proof_video.proof.schema import (
    ProofStep,
    SemanticExpression,
    SemanticExpressionNode,
    SemanticSpan,
    SemanticTransition,
    SemanticTransitionEdge,
)

def _friendly_local_name(name: str) -> bool:
    """Keep user names, replace inaccessible hygienic/internal names."""
    if not name or name.startswith("_") or "@" in name or "." in name:
        return False
    return all(character.isalnum() or character in "_'" for character in name)

def _rename_definition_latex(source: str, original: str, alias: str) -> str:
    escaped = original.replace("_", r"\_")
    if escaped and source.startswith(escaped):
        return alias + source[len(escaped) :]
    return source

def _proof_context_line(step: ProofStep, aliases: dict[int, str]) -> tuple[str, int]:
    """Render a local declaration or an already-proved staging premise.

    The returned offset is where the proposition's own semantic tree starts.
    Derived proof facts are intentionally shown as bare formulas: generated
    step numbers/names would add no mathematics to the blackboard.
    """
    if step.kind == "definition":
        line = _rename_definition_latex(
            step.display_latex,
            step.binder_name or "",
            aliases[step.id],
        )
        return line, 0
    if step.kind in {"assumption", "eigenvariable"}:
        name = aliases[step.id].replace("_", r"\_")
        line = rf"{name} \;:\; {step.proposition_latex}"
        return line, len(line) - len(step.proposition_latex)
    return step.proposition_latex, 0

def _proof_sequent_nodes(
    step: ProofStep,
    context_steps: tuple[ProofStep, ...],
    aliases: dict[int, str],
) -> tuple[SemanticExpressionNode, ...]:
    """Place proof-expression nodes in the canonical full-sequent string."""
    nodes: list[SemanticExpressionNode] = []
    offset = 0
    for binder in context_steps:
        line, proposition_local_offset = _proof_context_line(binder, aliases)
        nodes.append(
            SemanticExpressionNode(
                node_id=f"proof-context-{binder.id}",
                kind="proof-context",
                identity=f"proof-context:{binder.proof_fingerprint}",
                fingerprint=binder.proposition_fingerprint,
                path=("context", binder.id),
                latex_spans=(SemanticSpan(offset, offset + len(line)),),
            )
        )
        if binder.kind in {"assumption", "eigenvariable"}:
            # The rendered local declaration is itself semantic syntax.  Its
            # name and colon do not belong to the proposition Expr, but they
            # are the very objects consumed by forall/implies introduction.
            # Give them stable binder-owned nodes so ``x : A`` can move into
            # ``forall x : A, ...`` instead of writing a second, unrelated x.
            name = aliases[binder.id].replace("_", r"\_")
            colon_start = line.index(":")
            nodes.extend(
                (
                    SemanticExpressionNode(
                        node_id=f"proof-context-{binder.id}/binder",
                        kind="declaration",
                        identity=f"context-binder:{binder.id}",
                        fingerprint=binder.proof_fingerprint,
                        parent_id=f"proof-context-{binder.id}",
                        path=("context", binder.id, "binder"),
                        latex_spans=(SemanticSpan(offset, offset + len(name)),),
                    ),
                    SemanticExpressionNode(
                        node_id=f"proof-context-{binder.id}/binder-colon",
                        kind="declaration-punctuation",
                        identity=f"context-binder-colon:{binder.id}",
                        fingerprint=binder.proof_fingerprint,
                        parent_id=f"proof-context-{binder.id}",
                        path=("context", binder.id, "binder", "colon"),
                        latex_spans=(
                            SemanticSpan(
                                offset + colon_start,
                                offset + colon_start + 1,
                            ),
                        ),
                    ),
                )
            )
        # A context line is not an opaque label.  Its proposition keeps the
        # same expression tree that Lean attached to the binder, so a later
        # elimination can visibly copy a complete certified subexpression
        # (for example ``hf x``) out of the assumption instead of writing it
        # again from nowhere.  Prefix node ids/parents to keep occurrences in
        # different hypotheses distinct while retaining Lean identities and
        # fingerprints for structural matching.
        if binder.kind != "definition":
            proposition_offset = offset + proposition_local_offset
            context_prefix = f"proof-context-{binder.id}/"
            for node in binder.semantic_nodes:
                nodes.append(
                    SemanticExpressionNode(
                        node_id=context_prefix + node.node_id,
                        kind=node.kind,
                        identity=node.identity,
                        fingerprint=node.fingerprint,
                        parent_id=(
                            context_prefix + node.parent_id
                            if node.parent_id is not None
                            else f"proof-context-{binder.id}"
                        ),
                        path=("context", binder.id, *node.path),
                        latex_spans=tuple(
                            SemanticSpan(
                                span.start + proposition_offset,
                                span.end + proposition_offset,
                            )
                            for span in node.latex_spans
                        ),
                    )
                )
        offset += len(line) + 1

    turnstile = r"\vdash\;"
    nodes.append(
        SemanticExpressionNode(
            node_id="proof-target-turnstile",
            kind="sequent-punctuation",
            identity="sequent-punctuation:turnstile",
            fingerprint="sequent-punctuation:turnstile",
            path=("target", "turnstile"),
            latex_spans=(SemanticSpan(offset, offset + len(turnstile)),),
        )
    )
    target_offset = offset + len(turnstile)
    for node in step.semantic_nodes:
        nodes.append(
            SemanticExpressionNode(
                node_id=node.node_id,
                kind=node.kind,
                identity=node.identity,
                fingerprint=node.fingerprint,
                parent_id=node.parent_id,
                path=node.path,
                latex_spans=tuple(
                    SemanticSpan(span.start + target_offset, span.end + target_offset)
                    for span in node.latex_spans
                ),
            )
        )
    return tuple(nodes)

def _proof_sequent_latex(
    step: ProofStep,
    context_steps: tuple[ProofStep, ...],
    aliases: dict[int, str],
) -> str:
    lines = []
    for binder in context_steps:
        lines.append(_proof_context_line(binder, aliases)[0])
    lines.append(r"\vdash\;" + step.proposition_latex)
    return "\n".join(lines)

def _occurrence_edges(
    source_nodes: tuple[SemanticExpressionNode, ...],
    target_nodes: tuple[SemanticExpressionNode, ...],
    used_source: set[str],
    used_target: set[str],
    key,
    reason: str,
    confidence: float,
    target_key=None,
    allow_repeated: bool = True,
) -> list[SemanticTransitionEdge]:
    """Match repeated equal AST occurrences by their tree neighbourhood.

    A fingerprint may occur several times (two identical ``min(0,s)`` terms,
    many parentheses, repeated ``f(x)``).  Uniqueness-only matching threw all
    of that information away.  This deterministic bipartite pass gives
    priority to the longest shared Lean expression path and only then to
    nearby occurrence order; it therefore preserves logical occurrences
    without a per-symbol special case.
    """
    source_groups: dict[tuple[str, str], list[SemanticExpressionNode]] = {}
    target_groups: dict[tuple[str, str], list[SemanticExpressionNode]] = {}
    for node in source_nodes:
        value = key(node)
        if value and node.node_id not in used_source:
            source_groups.setdefault((node.kind, value), []).append(node)
    target_key = target_key or key
    for node in target_nodes:
        value = target_key(node)
        if value and node.node_id not in used_target:
            target_groups.setdefault((node.kind, value), []).append(node)

    result: list[SemanticTransitionEdge] = []
    for group_key in sorted(source_groups.keys() & target_groups.keys()):
        old = source_groups[group_key]
        new = target_groups[group_key]
        if not allow_repeated and (len(old) != 1 or len(new) != 1):
            continue
        candidates = []
        for source_index, source in enumerate(old):
            for target_index, target in enumerate(new):
                path_score = _common_path_prefix(source, target)
                depth_delta = abs(len(source.path) - len(target.path))
                extent_delta = abs(_span_extent(source) - _span_extent(target))
                order_delta = abs(source_index - target_index)
                candidates.append(
                    (
                        path_score,
                        -depth_delta,
                        -extent_delta,
                        -order_delta,
                        source.node_id,
                        target.node_id,
                    )
                )
        for _path, _depth, _extent, _order, source_id, target_id in sorted(
            candidates, reverse=True
        ):
            if source_id in used_source or target_id in used_target:
                continue
            used_source.add(source_id)
            used_target.add(target_id)
            result.append(
                SemanticTransitionEdge(source_id, target_id, reason, confidence)
            )
    return result

def _forall_substitution_target_paths(
    source_nodes: tuple[SemanticExpressionNode, ...],
    target_nodes: tuple[SemanticExpressionNode, ...],
) -> frozenset[tuple[str | int, ...]]:
    """Locate compound terms introduced for the eliminated bound variable.

    A path-preserving rule adapter knows that the old ``bvar:0`` occurrence
    becomes a term at one exact target path.  That term is new syntax supplied
    as the forall argument; equal glyphs elsewhere in the proof are not its
    provenance.  Atomic free variables may still come from their persistent
    declaration, but compound/literal substitutions own all descendants and
    must be written as a unit.
    """

    target_by_path: dict[
        tuple[str | int, ...], list[SemanticExpressionNode]
    ] = {}
    for node in target_nodes:
        if node.node_id.startswith("proof-context-"):
            continue
        target_by_path.setdefault(
            _path_without_sequent_prefix(node.path), []
        ).append(node)
    persistent_identities = {
        node.identity
        for node in source_nodes
        if node.node_id.startswith("proof-context-") and node.identity
    }
    result: set[tuple[str | int, ...]] = set()
    for source in source_nodes:
        if source.kind != "bvar" or source.identity != "bvar:0":
            continue
        mapped = _adapted_expression_path(
            _path_without_sequent_prefix(source.path), "forall-elimination"
        )
        if mapped is None:
            continue
        targets = target_by_path.get(mapped, ())
        if not targets:
            continue
        target = max(targets, key=_span_extent)
        persistent_atomic = (
            target.kind == "fvar"
            and bool(target.identity)
            and target.identity in persistent_identities
        )
        if not persistent_atomic:
            result.add(mapped)
    return frozenset(result)

def _path_is_inside(
    path: tuple[str | int, ...],
    roots: frozenset[tuple[str | int, ...]],
) -> bool:
    return any(len(path) >= len(root) and path[: len(root)] == root for root in roots)

def _structural_rule_edges(
    source_nodes: tuple[SemanticExpressionNode, ...],
    target_nodes: tuple[SemanticExpressionNode, ...],
    source_sequent: str,
    target_sequent: str,
    rule: str,
) -> list[SemanticTransitionEdge]:
    """Preserve exact occurrences through a rule's AST path transformation."""
    target_by_path: dict[tuple[tuple[str | int, ...], str], list[SemanticExpressionNode]] = {}
    for node in target_nodes:
        target_by_path.setdefault(
            (_path_without_sequent_prefix(node.path), node.kind), []
        ).append(node)

    candidates = []
    atomic_kinds = {
        "bvar",
        "fvar",
        "mvar",
        "const",
        "literal",
        "declaration",
        "declaration-punctuation",
        "quantifier-symbol",
    }
    for source in source_nodes:
        mapped_path = _adapted_expression_path(
            _path_without_sequent_prefix(source.path), rule
        )
        if mapped_path is None:
            continue
        targets = target_by_path.get((mapped_path, source.kind), ())
        if len(targets) != 1:
            continue
        target = targets[0]
        source_latex = _node_latex(source, source_sequent)
        target_latex = _node_latex(target, target_sequent)
        exact_expression = bool(
            source_latex
            and _rendered_expression_key(source_latex)
            == _rendered_expression_key(target_latex)
        )
        same_atom = (
            source.kind in atomic_kinds
            and target.kind in atomic_kinds
            and bool(source.identity)
            and source.identity == target.identity
        )
        structural_shell = (
            not exact_expression
            and source.kind not in atomic_kinds
            and target.kind not in atomic_kinds
        )
        if not exact_expression and not same_atom and not structural_shell:
            continue
        candidates.append(
            (
                int(exact_expression),
                _span_extent(source) + _span_extent(target),
                source,
                target,
            )
        )

    # Emit maximal exact expressions first. Descendant edges are still useful
    # trace evidence, but the renderer gives the composite edge ownership of
    # all its glyphs and therefore moves ``f(x)`` as one object.
    return [
        SemanticTransitionEdge(
            source.node_id,
            target.node_id,
            (
                "verified-structural-expression"
                if exact_expression
                else (
                    "verified-structural-atom"
                    if source.kind in atomic_kinds
                    else "verified-structural-shell"
                )
            ),
            1.0,
        )
        for exact_expression, _extent, source, target in sorted(
            candidates,
            key=lambda item: (item[0], item[1], item[2].node_id, item[3].node_id),
            reverse=True,
        )
    ]

def _direct_premise_atom_edges(
    source_nodes: tuple[SemanticExpressionNode, ...],
    target_nodes: tuple[SemanticExpressionNode, ...],
) -> list[SemanticTransitionEdge]:
    """Preserve logically identical atoms at the same premise/result path.

    Opaque theorem applications do not expose a natural-deduction path
    adapter.  They do, however, name their immediate Lean premises.  When an
    atom from one of those premises occurs at the identical expression path
    in the result and carries the same Lean identity, it is the same logical
    object rather than merely an equal-looking glyph.  Keeping this pass
    atomic avoids inventing correspondence between changed relation shells;
    exact compound subexpressions are handled by the certified premise-copy
    pass below.

    Multiple direct premises may certify the same target atom.  Retain every
    such edge so the global animation planner can select the visible source
    with the best temporal/row continuity instead of committing locally.
    """
    atomic_kinds = {
        "bvar",
        "fvar",
        "mvar",
        "const",
        "literal",
    }
    target_by_path_kind: dict[
        tuple[tuple[str | int, ...], str], list[SemanticExpressionNode]
    ] = {}
    for target in target_nodes:
        if target.kind not in atomic_kinds or not target.identity:
            continue
        target_by_path_kind.setdefault(
            (_path_without_sequent_prefix(target.path), target.kind), []
        ).append(target)

    result: list[SemanticTransitionEdge] = []
    for source in source_nodes:
        if source.kind not in atomic_kinds or not source.identity:
            continue
        targets = target_by_path_kind.get(
            (_path_without_sequent_prefix(source.path), source.kind), ()
        )
        for target in targets:
            if source.identity != target.identity:
                continue
            result.append(
                SemanticTransitionEdge(
                    source.node_id,
                    target.node_id,
                    "verified-direct-premise-atom",
                    1.0,
                )
            )
    return result


def _unique_identity_atom_edges(
    source_nodes: tuple[SemanticExpressionNode, ...],
    target_nodes: tuple[SemanticExpressionNode, ...],
    existing_edges: tuple[SemanticTransitionEdge, ...],
) -> list[SemanticTransitionEdge]:
    """Preserve an unambiguous Lean object even when its AST path changes.

    A free-variable id denotes the same local object throughout its scope and
    a constant identity denotes the same global declaration.  If either side
    contains multiple occurrences, provenance and structural rules must choose
    between them; this fallback deliberately emits nothing rather than pairing
    equal-looking glyphs.  The rule therefore repairs only logically unique
    atoms and cannot permute repeated ``f``, ``x`` or punctuation.
    """

    kinds = {"fvar", "const"}
    source_groups: dict[tuple[str, str], list[SemanticExpressionNode]] = {}
    target_groups: dict[tuple[str, str], list[SemanticExpressionNode]] = {}
    for node in source_nodes:
        if node.kind in kinds and node.identity:
            source_groups.setdefault((node.kind, node.identity), []).append(node)
    for node in target_nodes:
        if node.kind in kinds and node.identity:
            target_groups.setdefault((node.kind, node.identity), []).append(node)
    existing = {
        (edge.source_node_id, edge.target_node_id)
        for edge in existing_edges
    }
    result = []
    for key in source_groups.keys() & target_groups.keys():
        sources = source_groups[key]
        targets = target_groups[key]
        if len(sources) != 1 or len(targets) != 1:
            continue
        pair = (sources[0].node_id, targets[0].node_id)
        if pair not in existing:
            result.append(
                SemanticTransitionEdge(
                    *pair,
                    "verified-unique-identity-atom",
                    1.0,
                )
            )
    return result

def _proof_sequent_transition(
    source_step: ProofStep | None,
    source_context: tuple[ProofStep, ...],
    target_step: ProofStep,
    target_context: tuple[ProofStep, ...],
    aliases: dict[int, str],
    proof_ancestors: frozenset[int] | None = None,
    direct_premise_fingerprints: frozenset[str] | None = None,
    provenance_fingerprints: frozenset[str] | None = None,
    direct_provenance_aliases: frozenset[int] | None = None,
    provenance_aliases: frozenset[int] | None = None,
    premise_branches: tuple[tuple[ProofStep, frozenset[int]], ...] | None = None,
    proof_steps_by_id: dict[int, ProofStep] | None = None,
) -> SemanticTransition | None:
    if source_step is None:
        return None
    source_nodes_list = list(_proof_sequent_nodes(source_step, source_context, aliases))
    target_nodes = _proof_sequent_nodes(target_step, target_context, aliases)
    source_sequent = _proof_sequent_latex(source_step, source_context, aliases)
    target_sequent = _proof_sequent_latex(target_step, target_context, aliases)
    edges: list[SemanticTransitionEdge] = []
    direct_provenance_ids = set(target_step.premises)
    if direct_provenance_aliases:
        direct_provenance_ids.update(direct_provenance_aliases)
    if direct_premise_fingerprints:
        direct_provenance_ids.update(
            step.id
            for step in source_context
            if step.proposition_fingerprint in direct_premise_fingerprints
        )
    provenance_ids = set(direct_provenance_ids)
    if proof_ancestors is not None:
        provenance_ids.update(proof_ancestors)
    if provenance_aliases:
        provenance_ids.update(provenance_aliases)
    if provenance_fingerprints:
        provenance_ids.update(
            step.id
            for step in source_context
            if step.proposition_fingerprint in provenance_fingerprints
        )

    source_nodes = tuple(source_nodes_list)

    source_by_id = {node.node_id: node for node in source_nodes}
    target_by_id = {node.node_id: node for node in target_nodes}
    for node_id in source_by_id.keys() & target_by_id.keys():
        if node_id.startswith("proof-context-"):
            edges.append(
                SemanticTransitionEdge(node_id, node_id, "same-proof-context", 1.0)
            )
        elif node_id == "proof-target-turnstile":
            edges.append(
                SemanticTransitionEdge(
                    node_id,
                    node_id,
                    "verified-sequent-punctuation",
                    1.0,
                )
            )

    # A just-proved conclusion may be staged as a visible premise for the
    # following inference.  Its Expr occurrence ids remain exact; only their
    # board ownership changes from target to proof-context.
    stored_prefix = f"proof-context-{source_step.id}/"
    if any(binder.id == source_step.id for binder in target_context):
        for source_node in source_nodes:
            if source_node.node_id.startswith("proof-context-"):
                continue
            target_id = stored_prefix + source_node.node_id
            target_node = target_by_id.get(target_id)
            if target_node is None:
                continue
            if source_node.kind != target_node.kind:
                continue
            edges.append(
                SemanticTransitionEdge(
                    source_node.node_id,
                    target_id,
                    "verified-live-fact-storage",
                    1.0,
                )
            )

    # A proof-valued ``let``/``have`` is emitted immediately after the proof
    # of its value.  When administrative declaration rows are contracted,
    # the previous conclusion should become that new context row in the next
    # visible state.  Matching the certified proposition and its AST paths
    # preserves the whole formula without ever exposing the declaration in
    # the earlier frame where it did not yet exist.
    source_context_ids = {binder.id for binder in source_context}
    completed_aliases = [
        binder
        for binder in target_context
        if binder.kind == "proof-definition"
        and binder.id not in source_context_ids
        and binder.id > source_step.id
        and (
            (
                binder.proposition_fingerprint
                and binder.proposition_fingerprint
                == source_step.proposition_fingerprint
            )
            or (
                binder.proposition_lean
                and binder.proposition_lean == source_step.proposition_lean
            )
        )
    ]
    for alias in completed_aliases:
        alias_prefix = f"proof-context-{alias.id}/"
        alias_by_path_kind: dict[
            tuple[tuple[str | int, ...], str], list[SemanticExpressionNode]
        ] = {}
        for target_node in target_nodes:
            if not target_node.node_id.startswith(alias_prefix):
                continue
            alias_by_path_kind.setdefault(
                (
                    _path_without_sequent_prefix(target_node.path),
                    target_node.kind,
                ),
                [],
            ).append(target_node)
        for source_node in source_nodes:
            if source_node.node_id.startswith("proof-context-"):
                continue
            candidates = alias_by_path_kind.get(
                (
                    _path_without_sequent_prefix(source_node.path),
                    source_node.kind,
                ),
                (),
            )
            if len(candidates) != 1:
                continue
            target_node = candidates[0]
            source_text = _rendered_expression_key(
                _node_latex(source_node, source_sequent)
            )
            target_text = _rendered_expression_key(
                _node_latex(target_node, target_sequent)
            )
            if not source_text or source_text != target_text:
                continue
            edges.append(
                SemanticTransitionEdge(
                    source_node.node_id,
                    target_node.node_id,
                    "verified-proof-definition-storage",
                    1.0,
                )
            )

    used_source = {edge.source_node_id for edge in edges}
    used_target = {edge.target_node_id for edge in edges}

    regular_source = tuple(
        node for node in source_nodes if not node.node_id.startswith("proof-context-")
    )
    regular_target = tuple(
        node for node in target_nodes if not node.node_id.startswith("proof-context-")
    )
    # Apply a logical path adapter only to an actual direct premise of this
    # inference.  The previously displayed conclusion is often unrelated to
    # the next proof step, while the principal premise can live in any
    # persistent context row (for example ``hf`` in ``hf x``).  Treating the
    # previous bottom row as the source merely because it is geometrically
    # nearby creates plausible-looking but logically false permutations.
    structural_sources: list[tuple[SemanticExpressionNode, ...]] = []
    # A path adapter describes one primitive inference, so it may only start
    # at an *immediate* premise of that inference. ``direct_provenance_ids``
    # also contains visible aliases of hidden descendants; applying a forall
    # path shift to such an alias skips the intervening derivation and can
    # send an equal-looking symbol to a logically unrelated occurrence.
    if source_step.id in target_step.premises:
        structural_sources.append(regular_source)
    for premise_id in target_step.premises:
        prefix = f"proof-context-{premise_id}/"
        premise_nodes = tuple(
            node
            for node in source_nodes
            if node.node_id.startswith(prefix)
            and len(node.path) >= 3
            and node.path[:2] == ("context", premise_id)
            and node.path[2] == "0"
        )
        if premise_nodes:
            structural_sources.append(premise_nodes)

    structural_edges: list[SemanticTransitionEdge] = []
    substitution_target_paths: frozenset[tuple[str | int, ...]] = frozenset()
    if target_step.rule == "forall-elimination":
        substitution_target_paths = frozenset(
            path
            for premise_nodes in structural_sources
            for path in _forall_substitution_target_paths(
                premise_nodes, regular_target
            )
        )
    for premise_nodes in structural_sources:
        structural_edges.extend(
            _structural_rule_edges(
                premise_nodes,
                regular_target,
                source_sequent,
                target_sequent,
                target_step.rule,
            )
        )
        if target_step.rule == "theorem-application":
            structural_edges.extend(
                _direct_premise_atom_edges(premise_nodes, regular_target)
            )
    if substitution_target_paths:
        structural_edges = [
            edge
            for edge in structural_edges
            if not _path_is_inside(
                _path_without_sequent_prefix(target_by_id[edge.target_node_id].path),
                substitution_target_paths,
            )
        ]
    edges.extend(structural_edges)
    used_source.update(edge.source_node_id for edge in structural_edges)
    used_target.update(edge.target_node_id for edge in structural_edges)

    if target_step.rule == "forall-introduction":
        # Closing a local scope has two simultaneous, certified effects:
        # the old conclusion becomes the forall body (handled by the path
        # adapter above), and the disappearing local declaration becomes the
        # quantifier binder.  Model the latter explicitly across rows.
        target_context_ids = {binder.id for binder in target_context}
        removed_binders = [
            binder
            for binder in source_context
            if binder.kind in {"assumption", "eigenvariable"}
            and binder.id not in target_context_ids
        ]
        outer_foralls = [
            node
            for node in regular_target
            if node.kind == "forall" and node.path == ("0",)
        ]
        if len(removed_binders) == 1 and len(outer_foralls) == 1:
            removed = removed_binders[0]
            outer = outer_foralls[0]
            source_prefix = f"proof-context-{removed.id}"
            target_children = [
                node for node in regular_target if node.parent_id == outer.node_id
            ]
            target_binder = next(
                (node for node in target_children if node.kind == "declaration"),
                None,
            )
            target_colon = next(
                (
                    node
                    for node in target_children
                    if node.kind == "declaration-punctuation"
                ),
                None,
            )
            target_domain = next(
                (
                    node
                    for node in target_children
                    if node.path == (*outer.path, "0")
                ),
                None,
            )
            source_binder = source_by_id.get(f"{source_prefix}/binder")
            source_colon = source_by_id.get(f"{source_prefix}/binder-colon")
            source_domains = [
                node
                for node in source_nodes
                if node.node_id.startswith(source_prefix + "/")
                and node.path == ("context", removed.id, "0")
            ]
            source_domain = source_domains[0] if len(source_domains) == 1 else None
            binder_pairs = (
                (source_binder, target_binder),
                (source_colon, target_colon),
                (source_domain, target_domain),
            )
            for old, new in binder_pairs:
                if old is None or new is None:
                    continue
                old_latex = _node_latex(old, source_sequent)
                new_latex = _node_latex(new, target_sequent)
                if not old_latex or old_latex != new_latex:
                    continue
                edge = SemanticTransitionEdge(
                    old.node_id,
                    new.node_id,
                    "verified-binder-introduction",
                    1.0,
                )
                edges.append(edge)
                used_source.add(edge.source_node_id)
                used_target.add(edge.target_node_id)

    # If this proof step directly uses a local assumption, a maximal exact
    # subexpression may be copied from that persistent context row into the
    # conclusion.  The original remains on the board: this is a proof-backed
    # one-to-many visual relation, not a geometric guess.
    premise_context_prefixes = tuple(
        f"proof-context-{premise}/" for premise in provenance_ids
    )
    atomic_node_kinds = {
        "bvar",
        "fvar",
        "mvar",
        "const",
        "literal",
        "declaration",
        "declaration-punctuation",
        "quantifier-symbol",
    }
    target_context_ids = {binder.id for binder in target_context}
    premise_sources: list[tuple[SemanticExpressionNode, str]] = [
        (
            node,
            (
                "verified-premise-copy"
                if int(node.path[1]) in target_context_ids
                else "verified-premise-transfer"
            ),
        )
        for node in source_nodes
        if node.node_id.startswith(premise_context_prefixes)
        and len(node.path) >= 2
        and node.path[0] == "context"
        and node.kind not in atomic_node_kinds
        and _span_extent(node) > 1
    ]
    if source_step.id in direct_provenance_ids:
        # The previous conclusion is another direct Lean premise, not a
        # privileged row.  Let its certified subexpressions participate in
        # the same global composition as facts from any number of context
        # rows.  Unlike a persistent hypothesis this occurrence is consumed
        # by the transition, hence the distinct transfer reason.
        premise_sources.extend(
            (node, "verified-premise-transfer")
            for node in regular_source
            if node.kind not in atomic_node_kinds and _span_extent(node) > 1
        )
    conclusion_targets = tuple(
        node
        for node in target_nodes
        if not node.node_id.startswith("proof-context-")
        and node.kind not in atomic_node_kinds
        and _span_extent(node) > 1
        and not _path_is_inside(
            _path_without_sequent_prefix(node.path),
            substitution_target_paths,
        )
    )
    copy_candidates = []
    for source, reason in premise_sources:
        for target in conclusion_targets:
            if (
                not source.fingerprint
                or source.fingerprint != target.fingerprint
            ):
                continue
            # A premise-transfer edge owns a physical piece of chalk, so the
            # rendered subexpression must also be unchanged. Definitional
            # wrappers can share a Lean fingerprint while using different
            # notation (notably ``P -> False`` versus ``¬ P``). Selecting that
            # mismatched root suppresses the exact child ``P`` and forces the
            # renderer to rewrite everything. Skip the wrapper here; the
            # maximal exact descendant remains eligible below.
            source_text = _rendered_expression_key(
                _node_latex(source, source_sequent)
            )
            target_text = _rendered_expression_key(
                _node_latex(target, target_sequent)
            )
            if not source_text or source_text != target_text:
                continue
            copy_candidates.append(
                (
                    _span_extent(target),
                    _span_extent(source),
                    source.node_id,
                    target.node_id,
                    reason,
                )
            )
    # Keep maximal *target* expressions, but retain every certified source
    # candidate for each one.  The global hyperedge solver can then assemble
    # disjoint parts from several rows at once and choose a direct previous
    # occurrence over an older equal-looking copy.  Selecting a source here
    # would silently reintroduce the former one-row limitation.
    target_candidates: dict[
        str, list[tuple[int, int, str, str, str]]
    ] = {}
    for candidate in copy_candidates:
        target_candidates.setdefault(candidate[3], []).append(candidate)
    copied_target_nodes: list[SemanticExpressionNode] = []
    for target_id in sorted(
        target_candidates,
        key=lambda item: (_span_extent(target_by_id[item]), item),
        reverse=True,
    ):
        target_node = target_by_id[target_id]
        if any(
            any(
                outer.start <= inner.start and inner.end <= outer.end
                for outer in copied.latex_spans
                for inner in target_node.latex_spans
            )
            for copied in copied_target_nodes
        ):
            continue
        for _target_extent, _source_extent, source_id, _, reason in sorted(
            target_candidates[target_id], reverse=True
        ):
            edges.append(
                SemanticTransitionEdge(source_id, target_id, reason, 1.0)
            )
        copied_target_nodes.append(target_node)
        used_target.add(target_id)

    if premise_branches:
        edges.extend(
            premise_branch_edges(
                premise_branches=premise_branches,
                source_nodes=source_nodes,
                target_nodes=target_nodes,
                conclusion_targets=conclusion_targets,
                source_sequent=source_sequent,
                target_sequent=target_sequent,
                existing_edges=tuple(edges),
                target_rule=target_step.rule,
                visible_steps_by_id=proof_steps_by_id,
            )
        )

    edges.extend(
        _unique_identity_atom_edges(source_nodes, target_nodes, tuple(edges))
    )

    return SemanticTransition(
        source=SemanticExpression(source_nodes),
        target=SemanticExpression(target_nodes),
        edges=tuple(edges),
        proof_kind="certified-proof-term",
        adapter=target_step.rule,
        proof_fingerprint=target_step.proof_fingerprint,
        proof_term=target_step.proof_path,
        proof_descendants=tuple(str(item) for item in target_step.premises),
        fallback_reason=None if edges else "no shared certified expression identity",
    )
