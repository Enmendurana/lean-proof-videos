from proof_video.models import (
    Movie,
    ProofStep,
    SemanticExpressionNode,
    SemanticSpan,
    _occurrence_edges,
    _proof_sequent_latex,
    _proof_sequent_transition,
    _structural_rule_edges,
)
from proof_video.animation.latex import _split_latex_lines
from proof_video.animation.scene_helpers import _goal_latex, _initial_context_lines
from proof_video.proof.branch_provenance import premise_branch_edges


def _proof_step(
    step_id: int,
    proposition: str,
    *,
    kind: str = "elimination",
    rule: str = "forall-elimination",
    premises: tuple[int, ...] = (),
    binder_name: str | None = None,
    nodes: tuple[SemanticExpressionNode, ...] = (),
) -> ProofStep:
    return ProofStep(
        id=step_id,
        scope_id="root",
        parent_scope_id=None,
        depth=0,
        kind=kind,
        rule=rule,
        premises=premises,
        proposition_latex=proposition,
        proposition_lean=proposition,
        display_latex=proposition,
        proof_fingerprint=f"proof-{step_id}",
        proposition_fingerprint=f"prop-{step_id}",
        proof_path="root",
        binder_name=binder_name,
        semantic_nodes=nodes,
    )


def test_direct_premise_subformula_is_a_certified_copy_edge() -> None:
    inner = r"\forall y : \mathbb{R},\ P(x,y)"
    premise_latex = rf"\forall x : \mathbb{{R}},\ {inner}"
    premise = _proof_step(
        1,
        premise_latex,
        kind="assumption",
        binder_name="hf",
        nodes=(
            SemanticExpressionNode(
                "premise-root",
                kind="forall",
                latex_spans=(SemanticSpan(0, len(premise_latex)),),
            ),
            SemanticExpressionNode(
                "premise-inner",
                kind="forall",
                fingerprint="inner-proposition",
                parent_id="premise-root",
                latex_spans=(
                    SemanticSpan(
                        premise_latex.index(inner),
                        premise_latex.index(inner) + len(inner),
                    ),
                ),
            ),
        ),
    )
    previous = _proof_step(
        2,
        "Q",
        kind="theorem-application",
        rule="theorem-application",
        nodes=(
            SemanticExpressionNode(
                "old-root", kind="fvar", latex_spans=(SemanticSpan(0, 1),)
            ),
        ),
    )
    target = _proof_step(
        3,
        inner,
        premises=(1,),
        nodes=(
            SemanticExpressionNode(
                "target-root",
                kind="forall",
                fingerprint="inner-proposition",
                latex_spans=(SemanticSpan(0, len(inner)),),
            ),
        ),
    )
    transition = _proof_sequent_transition(
        previous, (premise,), target, (premise,), {1: "hf"}
    )
    assert transition is not None
    edge = next(
        edge for edge in transition.edges if edge.reason == "verified-premise-copy"
    )
    source = next(
        node for node in transition.source.nodes if node.node_id == edge.source_node_id
    )
    source_latex = _proof_sequent_latex(previous, (premise,), {1: "hf"})
    assert "".join(source_latex[s.start : s.end] for s in source.latex_spans) == inner
    assert edge.target_node_id == "target-root"


def test_direct_premise_rhs_atom_survives_opaque_theorem_application() -> None:
    source_latex = r"\min(0,s) \leq s"
    target_latex = r"\min(0,s)-1 < s"
    source_left_s = source_latex.index("s")
    source_right_s = source_latex.rindex("s")
    target_left_s = target_latex.index("s")
    target_right_s = target_latex.rindex("s")
    source = _proof_step(
        219,
        source_latex,
        kind="theorem-application",
        rule="theorem-application",
        premises=(209,),
        nodes=(
            SemanticExpressionNode(
                "source-left-s",
                kind="fvar",
                identity="fvar:s",
                path=("0", "0", "1"),
                latex_spans=(SemanticSpan(source_left_s, source_left_s + 1),),
            ),
            SemanticExpressionNode(
                "source-right-s",
                kind="fvar",
                identity="fvar:s",
                path=("0", "1"),
                latex_spans=(SemanticSpan(source_right_s, source_right_s + 1),),
            ),
        ),
    )
    target = _proof_step(
        220,
        target_latex,
        kind="theorem-application",
        rule="theorem-application",
        premises=(219,),
        nodes=(
            SemanticExpressionNode(
                "target-left-s",
                kind="fvar",
                identity="fvar:s",
                path=("0", "0", "0", "1"),
                latex_spans=(SemanticSpan(target_left_s, target_left_s + 1),),
            ),
            SemanticExpressionNode(
                "target-right-s",
                kind="fvar",
                identity="fvar:s",
                path=("0", "1"),
                latex_spans=(SemanticSpan(target_right_s, target_right_s + 1),),
            ),
        ),
    )

    transition = _proof_sequent_transition(source, (), target, (), {})

    assert transition is not None
    direct_edges = {
        (edge.source_node_id, edge.target_node_id)
        for edge in transition.edges
        if edge.reason == "verified-direct-premise-atom"
    }
    assert direct_edges == {("source-right-s", "target-right-s")}


def test_premise_transfer_preserves_body_when_notation_folds_to_negation() -> None:
    body = "P < Q"
    source_latex = body + r" \implies \text{False}"
    target_prefix = r"\mathop{\neg} "
    target_latex = target_prefix + body
    source = _proof_step(
        1,
        source_latex,
        rule="implies-introduction",
        nodes=(
            SemanticExpressionNode(
                "source-root",
                kind="app",
                fingerprint="definitionally-not",
                path=("0",),
                latex_spans=(SemanticSpan(0, len(source_latex)),),
            ),
            SemanticExpressionNode(
                "source-body",
                kind="app",
                fingerprint="body",
                parent_id="source-root",
                path=("0", "0"),
                latex_spans=(SemanticSpan(0, len(body)),),
            ),
        ),
    )
    target = _proof_step(
        2,
        target_latex,
        kind="theorem-application",
        rule="theorem-application",
        premises=(1,),
        nodes=(
            SemanticExpressionNode(
                "target-root",
                kind="app",
                fingerprint="definitionally-not",
                path=("0",),
                latex_spans=(SemanticSpan(0, len(target_latex)),),
            ),
            SemanticExpressionNode(
                "target-body",
                kind="app",
                fingerprint="body",
                parent_id="target-root",
                path=("0", "1"),
                latex_spans=(
                    SemanticSpan(len(target_prefix), len(target_latex)),
                ),
            ),
        ),
    )

    transition = _proof_sequent_transition(source, (), target, (), {})

    assert transition is not None
    transfers = {
        (edge.source_node_id, edge.target_node_id)
        for edge in transition.edges
        if edge.reason == "verified-premise-transfer"
    }
    assert ("source-body", "target-body") in transfers
    assert ("source-root", "target-root") not in transfers


def test_hidden_premise_branch_copies_atom_from_its_actual_assumption() -> None:
    hx = _proof_step(
        23,
        "x < 0",
        kind="assumption",
        rule="assume",
        binder_name="hx",
        nodes=(
            SemanticExpressionNode(
                "hx-root",
                kind="app",
                fingerprint="lt-x-zero",
                path=("0",),
                latex_spans=(SemanticSpan(0, 5),),
            ),
            SemanticExpressionNode(
                "hx-x",
                kind="fvar",
                identity="fvar:x",
                fingerprint="x",
                parent_id="hx-root",
                path=("0", "0"),
                latex_spans=(SemanticSpan(0, 1),),
            ),
        ),
    )
    unrelated = _proof_step(
        24,
        "f(x) < 0",
        kind="assumption",
        rule="assume",
        binder_name="h1",
        nodes=(
            SemanticExpressionNode(
                "other-x",
                kind="fvar",
                identity="fvar:x",
                fingerprint="x",
                path=("0", "0"),
                latex_spans=(SemanticSpan(2, 3),),
            ),
        ),
    )
    hidden = _proof_step(
        195,
        "x - 0 < 0",
        kind="theorem-application",
        rule="theorem-application",
        premises=(23,),
        nodes=(
            SemanticExpressionNode(
                "hidden-sub",
                kind="app",
                fingerprint="x-minus-zero",
                path=("0", "0"),
                latex_spans=(SemanticSpan(0, 5),),
            ),
            SemanticExpressionNode(
                "hidden-x",
                kind="fvar",
                identity="fvar:x",
                fingerprint="x",
                parent_id="hidden-sub",
                path=("0", "0", "0"),
                latex_spans=(SemanticSpan(0, 1),),
            ),
        ),
    )
    previous = _proof_step(194, "Q", rule="theorem-application")
    target_latex = "0 < (x - 0)"
    target = _proof_step(
        197,
        target_latex,
        kind="theorem-application",
        rule="theorem-application",
        premises=(195,),
        nodes=(
            SemanticExpressionNode(
                "target-sub",
                kind="app",
                fingerprint="x-minus-zero",
                path=("0", "1"),
                latex_spans=(SemanticSpan(5, 10),),
            ),
            SemanticExpressionNode(
                "target-x",
                kind="fvar",
                identity="fvar:x",
                fingerprint="x",
                parent_id="target-sub",
                path=("0", "1", "0"),
                latex_spans=(SemanticSpan(5, 6),),
            ),
        ),
    )

    transition = _proof_sequent_transition(
        previous,
        (hx, unrelated),
        target,
        (hx, unrelated),
        {23: "hx", 24: "h1"},
        proof_ancestors=frozenset({23}),
        premise_branches=((hidden, frozenset({23})),),
    )

    assert transition is not None
    branch_edges = [
        edge
        for edge in transition.edges
        if edge.reason == "verified-premise-branch-copy"
    ]
    assert any(
        edge.source_node_id == "proof-context-23/hx-x"
        and edge.target_node_id == "target-x"
        for edge in branch_edges
    )
    assert not any(
        edge.source_node_id == "proof-context-24/other-x"
        for edge in branch_edges
    )


def test_semanticless_administrative_branch_preserves_unique_unowned_atom() -> None:
    hx = _proof_step(
        23,
        "x < 0",
        kind="assumption",
        rule="assume",
        binder_name="hx",
        nodes=(
            SemanticExpressionNode(
                "hx-root",
                kind="app",
                fingerprint="lt-x-zero",
                path=("0",),
                latex_spans=(SemanticSpan(0, 5),),
            ),
            SemanticExpressionNode(
                "hx-x",
                kind="fvar",
                identity="fvar:x",
                parent_id="hx-root",
                path=("0", "0"),
                latex_spans=(SemanticSpan(0, 1),),
            ),
        ),
    )
    h1_latex = "f(x) < 0"
    h1 = _proof_step(
        24,
        h1_latex,
        kind="assumption",
        rule="assume",
        binder_name="h1",
        nodes=(
            SemanticExpressionNode(
                "h1-fx",
                kind="app",
                fingerprint="fx",
                path=("0", "0"),
                latex_spans=(SemanticSpan(0, 4),),
            ),
            SemanticExpressionNode(
                "h1-x",
                kind="fvar",
                identity="fvar:x",
                parent_id="h1-fx",
                path=("0", "0", "1"),
                latex_spans=(SemanticSpan(2, 3),),
            ),
        ),
    )
    hidden_hx = _proof_step(
        196,
        "",
        kind="theorem-application",
        rule="theorem-application",
        premises=(23,),
    )
    hidden_h1 = _proof_step(
        197,
        "",
        kind="theorem-application",
        rule="theorem-application",
        premises=(24,),
    )
    target_latex = r"0 < (x - 0) \cdot (f(x) - 0)"
    left_x = target_latex.index("x")
    fx_start = target_latex.index("f(x)")
    target = _proof_step(
        198,
        target_latex,
        kind="theorem-application",
        rule="theorem-application",
        premises=(196, 197),
        nodes=(
            SemanticExpressionNode(
                "target-left-x",
                kind="fvar",
                identity="fvar:x",
                path=("0", "1", "0", "1", "0", "1"),
                latex_spans=(SemanticSpan(left_x, left_x + 1),),
            ),
            SemanticExpressionNode(
                "target-fx",
                kind="app",
                fingerprint="fx",
                path=("0", "1", "1", "0", "1"),
                latex_spans=(SemanticSpan(fx_start, fx_start + 4),),
            ),
            SemanticExpressionNode(
                "target-inner-x",
                kind="fvar",
                identity="fvar:x",
                parent_id="target-fx",
                path=("0", "1", "1", "0", "1", "1"),
                latex_spans=(SemanticSpan(fx_start + 2, fx_start + 3),),
            ),
        ),
    )
    previous = _proof_step(183, "Q", rule="theorem-application")

    transition = _proof_sequent_transition(
        previous,
        (hx, h1),
        target,
        (hx, h1),
        {23: "hx", 24: "h1"},
        proof_ancestors=frozenset({23, 24, 196, 197}),
        premise_branches=(
            (hidden_hx, frozenset({23})),
            (hidden_h1, frozenset({24})),
        ),
    )

    assert transition is not None
    edges = {
        (edge.source_node_id, edge.target_node_id, edge.reason)
        for edge in transition.edges
    }
    assert (
        "proof-context-24/h1-fx",
        "target-fx",
        "verified-premise-copy",
    ) in edges
    assert (
        "proof-context-23/hx-x",
        "target-left-x",
        "verified-premise-branch-atom",
    ) in edges
    assert not any(
        source == "proof-context-24/h1-x" and target_id == "target-left-x"
        for source, target_id, _reason in edges
    )


def test_contracted_derivation_composes_carrier_rewrite_from_multiple_rows() -> None:
    equality = "x+(t-x)=t"
    carrier = "f(x+(t-x))\\leq(t-x)f(x)+F"
    distribution = "(t-x)f(x)=t f(x)-x f(x)"
    source_sequent = "\n".join((equality, carrier, distribution))
    branch_latex = "f(t)\\leq t f(x)-x f(x)+F"
    target_sequent = rf"\forall t, {branch_latex}"

    def span(source: str, fragment: str, start: int = 0) -> SemanticSpan:
        offset = source.index(fragment, start)
        return SemanticSpan(offset, offset + len(fragment))

    carrier_offset = len(equality) + 1
    distribution_offset = carrier_offset + len(carrier) + 1
    nested_t = equality.index("t")
    result_t = equality.rindex("t")
    source_nodes = (
        SemanticExpressionNode(
            "proof-context-5/nested-t",
            kind="fvar",
            identity="fvar:t",
            fingerprint="t",
            path=("context", 5, "0", "0", "1"),
            latex_spans=(SemanticSpan(nested_t, nested_t + 1),),
        ),
        SemanticExpressionNode(
            "proof-context-5/result-t",
            kind="fvar",
            identity="fvar:t",
            fingerprint="t",
            path=("context", 5, "0", "1"),
            latex_spans=(SemanticSpan(result_t, result_t + 1),),
        ),
        SemanticExpressionNode(
            "proof-context-11/carrier-root",
            kind="app",
            fingerprint="old-carrier",
            path=("context", 11, "0"),
            latex_spans=(
                SemanticSpan(carrier_offset, carrier_offset + len(carrier)),
            ),
        ),
        SemanticExpressionNode(
            "proof-context-11/carrier-fcall",
            kind="app",
            fingerprint="old-fcall",
            parent_id="proof-context-11/carrier-root",
            path=("context", 11, "0", "0"),
            latex_spans=(
                SemanticSpan(
                    carrier_offset,
                    carrier_offset + len("f(x+(t-x))"),
                ),
            ),
        ),
        SemanticExpressionNode(
            "proof-context-11/carrier-f",
            kind="fvar",
            identity="fvar:f",
            fingerprint="f",
            parent_id="proof-context-11/carrier-fcall",
            path=("context", 11, "0", "0", "0"),
            latex_spans=(SemanticSpan(carrier_offset, carrier_offset + 1),),
        ),
        SemanticExpressionNode(
            "proof-context-11/carrier-argument",
            kind="app",
            fingerprint="old-argument",
            parent_id="proof-context-11/carrier-fcall",
            path=("context", 11, "0", "0", "1"),
            latex_spans=(
                SemanticSpan(
                    carrier_offset + carrier.index("x+(t-x)"),
                    carrier_offset + carrier.index("x+(t-x)") + len("x+(t-x)"),
                ),
            ),
        ),
        SemanticExpressionNode(
            "proof-step-13/distributed",
            kind="app",
            fingerprint="source-elaboration",
            path=("0", "1"),
            latex_spans=(
                SemanticSpan(
                    distribution_offset + distribution.index("t f(x)-x f(x)"),
                    distribution_offset + len(distribution),
                ),
            ),
        ),
    )
    branch_nodes = (
        SemanticExpressionNode(
            "branch-root",
            kind="app",
            fingerprint="branch",
            path=("0",),
            latex_spans=(SemanticSpan(0, len(branch_latex)),),
        ),
        SemanticExpressionNode(
            "branch-fcall",
            kind="app",
            fingerprint="new-fcall",
            parent_id="branch-root",
            path=("0", "0"),
            latex_spans=(span(branch_latex, "f(t)"),),
        ),
        SemanticExpressionNode(
            "branch-t",
            kind="fvar",
            identity="fvar:t",
            fingerprint="t",
            parent_id="branch-fcall",
            path=("0", "0", "1"),
            latex_spans=(span(branch_latex, "t"),),
        ),
        SemanticExpressionNode(
            "branch-f",
            kind="fvar",
            identity="fvar:f",
            fingerprint="f",
            parent_id="branch-fcall",
            path=("0", "0", "0"),
            latex_spans=(SemanticSpan(0, 1),),
        ),
        SemanticExpressionNode(
            "branch-distributed",
            kind="app",
            fingerprint="branch-elaboration",
            parent_id="branch-root",
            path=("0", "1", "0"),
            latex_spans=(span(branch_latex, "t f(x)-x f(x)"),),
        ),
    )
    target_nodes = (
        SemanticExpressionNode(
            "target-body",
            kind="app",
            fingerprint="abstracted-branch",
            path=("0", "1"),
            latex_spans=(span(target_sequent, branch_latex),),
        ),
        SemanticExpressionNode(
            "target-fcall",
            kind="app",
            fingerprint="abstracted-fcall",
            parent_id="target-body",
            path=("0", "1", "0"),
            latex_spans=(span(target_sequent, "f(t)"),),
        ),
        SemanticExpressionNode(
            "target-t",
            kind="bvar",
            identity="bvar:0",
            fingerprint="bound-t",
            parent_id="target-fcall",
            path=("0", "1", "0", "1"),
            latex_spans=(span(target_sequent, "t", target_sequent.index("f(t)")),),
        ),
        SemanticExpressionNode(
            "target-f",
            kind="fvar",
            identity="fvar:f",
            fingerprint="f",
            parent_id="target-fcall",
            path=("0", "1", "0", "0"),
            latex_spans=(span(target_sequent, "f"),),
        ),
        SemanticExpressionNode(
            "target-distributed",
            kind="app",
            fingerprint="abstracted-distribution",
            parent_id="target-body",
            path=("0", "1", "1", "0"),
            latex_spans=(span(target_sequent, "t f(x)-x f(x)"),),
        ),
    )
    hidden = _proof_step(
        18,
        branch_latex,
        kind="theorem-application",
        rule="theorem-application",
        premises=(5, 11, 13),
        nodes=branch_nodes,
    )
    equality_step = _proof_step(
        5,
        equality,
        kind="theorem-application",
        rule="theorem-application",
        nodes=(
            SemanticExpressionNode(
                "equality-root",
                kind="app",
                path=("0",),
                latex_spans=(SemanticSpan(0, len(equality)),),
            ),
            SemanticExpressionNode(
                "equality-left",
                kind="app",
                fingerprint="old-argument",
                parent_id="equality-root",
                path=("0", "0"),
                latex_spans=(SemanticSpan(0, equality.index("=")),),
            ),
            SemanticExpressionNode(
                "equality-right",
                kind="fvar",
                identity="fvar:t",
                fingerprint="t",
                parent_id="equality-root",
                path=("0", "1"),
                latex_spans=(SemanticSpan(result_t, result_t + 1),),
            ),
        ),
    )

    edges = premise_branch_edges(
        premise_branches=((hidden, frozenset({5, 11, 13})),),
        source_nodes=source_nodes,
        target_nodes=target_nodes,
        conclusion_targets=target_nodes,
        source_sequent=source_sequent,
        target_sequent=target_sequent,
        existing_edges=(),
        target_rule="forall-introduction",
        visible_steps_by_id={5: equality_step},
    )
    keys = {
        (edge.source_node_id, edge.target_node_id, edge.reason)
        for edge in edges
    }
    assert (
        "proof-context-11/carrier-fcall",
        "target-fcall",
        "verified-premise-branch-shell",
    ) in keys
    assert (
        "proof-step-13/distributed",
        "target-distributed",
        "verified-premise-branch-copy",
    ) in keys
    assert (
        "proof-context-5/result-t",
        "target-t",
        "verified-directed-equality-result",
    ) in keys
    assert not any(
        source == "proof-context-5/nested-t" and target == "target-t"
        for source, target, _reason in keys
    )


def test_forall_elimination_moves_body_from_direct_context_premise() -> None:
    inner = r"\forall y : \mathbb{R},\ P(x,y)"
    premise_latex = rf"\forall x : \mathbb{{R}},\ {inner}"
    inner_start = premise_latex.index(inner)
    premise = _proof_step(
        1,
        premise_latex,
        kind="assumption",
        rule="assumption",
        binder_name="hf",
        nodes=(
            SemanticExpressionNode(
                "premise-root",
                kind="forall",
                path=("0",),
                latex_spans=(SemanticSpan(0, len(premise_latex)),),
            ),
            SemanticExpressionNode(
                "premise-inner",
                kind="forall",
                parent_id="premise-root",
                path=("0", "1"),
                latex_spans=(SemanticSpan(inner_start, inner_start + len(inner)),),
            ),
        ),
    )
    unrelated = _proof_step(
        9,
        rf"\forall z : \mathbb{{R}},\ {inner}",
        kind="theorem-application",
        rule="theorem-application",
        nodes=(
            SemanticExpressionNode(
                "unrelated-inner",
                kind="forall",
                path=("0", "1"),
                latex_spans=(SemanticSpan(24, 24 + len(inner)),),
            ),
        ),
    )
    target = _proof_step(
        10,
        inner,
        kind="elimination",
        rule="forall-elimination",
        premises=(1,),
        nodes=(
            SemanticExpressionNode(
                "target-root",
                kind="forall",
                path=("0",),
                latex_spans=(SemanticSpan(0, len(inner)),),
            ),
        ),
    )

    transition = _proof_sequent_transition(
        unrelated, (premise,), target, (premise,), {1: "hf"}
    )
    assert transition is not None
    assert any(
        edge.source_node_id == "proof-context-1/premise-inner"
        and edge.target_node_id == "target-root"
        and edge.reason == "verified-structural-expression"
        for edge in transition.edges
    )
    assert not any(
        edge.source_node_id == "unrelated-inner"
        and edge.target_node_id == "target-root"
        for edge in transition.edges
    )


def test_forall_compound_substitution_is_new_not_borrowed_from_equal_context() -> None:
    context_latex = "f(x)"
    context = _proof_step(
        1,
        context_latex,
        kind="theorem-application",
        rule="theorem-application",
        nodes=(
            SemanticExpressionNode(
                "context-term",
                kind="app",
                fingerprint="same-compound-term",
                path=("0",),
                latex_spans=(SemanticSpan(0, len(context_latex)),),
            ),
        ),
    )
    source_latex = r"\forall t : \mathbb{R},\ f(t)"
    body_start = source_latex.index("f(t)")
    source = _proof_step(
        2,
        source_latex,
        nodes=(
            SemanticExpressionNode(
                "source-forall", kind="forall", path=("0",),
                latex_spans=(SemanticSpan(0, len(source_latex)),),
            ),
            SemanticExpressionNode(
                "source-body", kind="app", parent_id="source-forall", path=("0", "1"),
                latex_spans=(SemanticSpan(body_start, body_start + 4),),
            ),
            SemanticExpressionNode(
                "source-function", kind="fvar", identity="function-f",
                parent_id="source-body", path=("0", "1", "0"),
                latex_spans=(SemanticSpan(body_start, body_start + 1),),
            ),
            SemanticExpressionNode(
                "source-binder-use", kind="bvar", identity="bvar:0",
                parent_id="source-body", path=("0", "1", "1"),
                latex_spans=(SemanticSpan(body_start + 2, body_start + 3),),
            ),
        ),
    )
    target_latex = "f(f(x))"
    target = _proof_step(
        3,
        target_latex,
        premises=(2,),
        nodes=(
            SemanticExpressionNode(
                "target-body", kind="app", path=("0",),
                latex_spans=(SemanticSpan(0, len(target_latex)),),
            ),
            SemanticExpressionNode(
                "target-function", kind="fvar", identity="function-f",
                parent_id="target-body", path=("0", "0"),
                latex_spans=(SemanticSpan(0, 1),),
            ),
            SemanticExpressionNode(
                "target-substitution", kind="app", fingerprint="same-compound-term",
                parent_id="target-body", path=("0", "1"),
                latex_spans=(SemanticSpan(2, 6),),
            ),
        ),
    )

    transition = _proof_sequent_transition(
        source,
        (context,),
        target,
        (context,),
        {},
        proof_ancestors=frozenset({1, 2}),
    )

    assert transition is not None
    assert not any(
        edge.source_node_id == "proof-context-1/context-term"
        and edge.target_node_id == "target-substitution"
        for edge in transition.edges
    )
    assert any(
        edge.source_node_id == "source-function"
        and edge.target_node_id == "target-function"
        for edge in transition.edges
    )


def test_conclusion_can_be_composed_from_several_premise_rows() -> None:
    def premise(step_id: int, formula: str, fingerprint: str, name: str) -> ProofStep:
        return _proof_step(
            step_id,
            formula,
            kind="assumption",
            rule="assumption",
            binder_name=name,
            nodes=(
                SemanticExpressionNode(
                    f"{name}-expression",
                    kind="app",
                    fingerprint=fingerprint,
                    path=("0",),
                    latex_spans=(SemanticSpan(0, len(formula)),),
                ),
            ),
        )

    first = premise(1, "A(x)", "formula-a", "ha")
    second = premise(2, "B(x)", "formula-b", "hb")
    previous = _proof_step(
        3,
        "C(x)",
        kind="theorem-application",
        rule="theorem-application",
        nodes=(
            SemanticExpressionNode(
                "previous-c",
                kind="app",
                fingerprint="formula-c",
                path=("0",),
                latex_spans=(SemanticSpan(0, 4),),
            ),
        ),
    )
    conclusion = "A(x) + B(x) + C(x)"
    target = _proof_step(
        4,
        conclusion,
        kind="theorem-application",
        rule="theorem-application",
        premises=(1, 2, 3),
        nodes=tuple(
            SemanticExpressionNode(
                f"target-{letter.lower()}",
                kind="app",
                fingerprint=f"formula-{letter.lower()}",
                path=("0", str(index)),
                latex_spans=(
                    SemanticSpan(
                        conclusion.index(f"{letter}(x)"),
                        conclusion.index(f"{letter}(x)") + 4,
                    ),
                ),
            )
            for index, letter in enumerate("ABC")
        ),
    )

    transition = _proof_sequent_transition(
        previous,
        (first, second),
        target,
        (first, second),
        {1: "ha", 2: "hb"},
    )
    assert transition is not None
    edge_keys = {
        (edge.source_node_id, edge.target_node_id, edge.reason)
        for edge in transition.edges
    }
    assert {
        (
            "proof-context-1/ha-expression",
            "target-a",
            "verified-premise-copy",
        ),
        (
            "proof-context-2/hb-expression",
            "target-b",
            "verified-premise-copy",
        ),
        ("previous-c", "target-c", "verified-premise-transfer"),
    } <= edge_keys


def test_consumed_staged_premise_transfers_instead_of_copying() -> None:
    formula = "A(x)"
    premise = _proof_step(
        1,
        formula,
        kind="theorem-application",
        rule="theorem-application",
        nodes=(
            SemanticExpressionNode(
                "premise-a",
                kind="app",
                fingerprint="formula-a",
                path=("0",),
                latex_spans=(SemanticSpan(0, len(formula)),),
            ),
        ),
    )
    previous = _proof_step(
        2,
        "B",
        kind="theorem-application",
        rule="theorem-application",
        nodes=(
            SemanticExpressionNode(
                "previous-b", kind="fvar", latex_spans=(SemanticSpan(0, 1),)
            ),
        ),
    )
    target = _proof_step(
        3,
        formula,
        kind="theorem-application",
        rule="theorem-application",
        premises=(1, 2),
        nodes=(
            SemanticExpressionNode(
                "target-a",
                kind="app",
                fingerprint="formula-a",
                path=("0",),
                latex_spans=(SemanticSpan(0, len(formula)),),
            ),
        ),
    )

    transition = _proof_sequent_transition(
        previous, (premise,), target, (), {}
    )
    assert transition is not None
    assert any(
        edge.source_node_id == "proof-context-1/premise-a"
        and edge.target_node_id == "target-a"
        and edge.reason == "verified-premise-transfer"
        for edge in transition.edges
    )


def test_transitive_provenance_copies_only_fingerprint_certified_assumption() -> None:
    assumption_formula = r"A \leq B + f(f(x))"
    context = _proof_step(
        1,
        assumption_formula,
        kind="assumption",
        binder_name="hf",
        nodes=(
            SemanticExpressionNode(
                "context-ffx",
                kind="app",
                fingerprint="ffx",
                # LeanTeX can omit generated closing delimiters in this span.
                latex_spans=(
                    SemanticSpan(
                        assumption_formula.index("f(f(x"),
                        assumption_formula.index("f(f(x") + len("f(f(x"),
                    ),
                ),
            ),
        ),
    )
    algebra = r"t \cdot f(x) - x \cdot f(x)"
    previous = _proof_step(
        13,
        algebra,
        kind="theorem-application",
        rule="theorem-application",
        nodes=(
            SemanticExpressionNode(
                "old-algebra",
                kind="app",
                fingerprint="algebra",
                path=("0",),
                latex_spans=(SemanticSpan(0, len(algebra)),),
            ),
        ),
    )
    target_formula = algebra + r" + f(f(x))"
    ffx_start = target_formula.index("f(f(x))")
    target = _proof_step(
        19,
        target_formula,
        kind="theorem-application",
        rule="theorem-application",
        premises=(18,),
        nodes=(
            SemanticExpressionNode(
                "new-algebra",
                kind="app",
                fingerprint="algebra",
                path=("0", "0"),
                latex_spans=(SemanticSpan(0, len(algebra)),),
            ),
            SemanticExpressionNode(
                "new-ffx",
                kind="app",
                fingerprint="ffx",
                path=("0", "1"),
                latex_spans=(SemanticSpan(ffx_start, ffx_start + len("f(f(x))")),),
            ),
        ),
    )
    transition = _proof_sequent_transition(
        previous,
        (context,),
        target,
        (context,),
        {1: "hf"},
        proof_ancestors=frozenset({1, 13, 18}),
    )
    assert transition is not None
    assert any(
        edge.source_node_id.endswith("context-ffx")
        and edge.target_node_id == "new-ffx"
        and edge.reason == "verified-premise-copy"
        for edge in transition.edges
    )
    assert not any(
        edge.source_node_id == "old-algebra"
        and edge.target_node_id == "new-algebra"
        for edge in transition.edges
    )


def test_forall_introduction_moves_old_body_and_local_declaration() -> None:
    real = r"\mathbb{R}"
    local_x = _proof_step(
        2,
        real,
        kind="assumption",
        rule="assumption",
        binder_name="x",
        nodes=(
            SemanticExpressionNode(
                "x-type",
                kind="const",
                identity="const:Real",
                fingerprint="real",
                path=("0",),
                latex_spans=(SemanticSpan(0, len(real)),),
            ),
        ),
    )
    body = "f(f(x))"
    previous = _proof_step(
        19,
        body,
        kind="introduction",
        rule="forall-introduction",
        nodes=(
            SemanticExpressionNode(
                "old-body",
                kind="app",
                fingerprint="body",
                path=("0",),
                latex_spans=(SemanticSpan(0, len(body)),),
            ),
        ),
    )
    target_latex = rf"\forall x : {real},\ {body}"
    binder_start = target_latex.index("x")
    colon_start = target_latex.index(":")
    domain_start = target_latex.index(real)
    body_start = target_latex.index(body)
    target = _proof_step(
        20,
        target_latex,
        kind="introduction",
        rule="forall-introduction",
        premises=(2, 19),
        nodes=(
            SemanticExpressionNode(
                "new-forall",
                kind="forall",
                path=("0",),
                latex_spans=(SemanticSpan(0, len(target_latex)),),
            ),
            SemanticExpressionNode(
                "new-binder",
                kind="declaration",
                parent_id="new-forall",
                path=("0", "binder"),
                latex_spans=(SemanticSpan(binder_start, binder_start + 1),),
            ),
            SemanticExpressionNode(
                "new-colon",
                kind="declaration-punctuation",
                parent_id="new-forall",
                path=("0", "binder", "colon"),
                latex_spans=(SemanticSpan(colon_start, colon_start + 1),),
            ),
            SemanticExpressionNode(
                "new-domain",
                kind="const",
                identity="const:Real",
                fingerprint="real",
                parent_id="new-forall",
                path=("0", "0"),
                latex_spans=(
                    SemanticSpan(domain_start, domain_start + len(real)),
                ),
            ),
            SemanticExpressionNode(
                "new-body",
                kind="app",
                fingerprint="body",
                parent_id="new-forall",
                path=("0", "1"),
                latex_spans=(SemanticSpan(body_start, body_start + len(body)),),
            ),
        ),
    )

    transition = _proof_sequent_transition(
        previous,
        (local_x,),
        target,
        (),
        {2: "x"},
    )
    assert transition is not None
    edge_keys = {
        (edge.source_node_id, edge.target_node_id, edge.reason)
        for edge in transition.edges
    }
    assert (
        "old-body",
        "new-body",
        "verified-structural-expression",
    ) in edge_keys
    assert {
        ("proof-context-2/binder", "new-binder"),
        ("proof-context-2/binder-colon", "new-colon"),
        ("proof-context-2/x-type", "new-domain"),
    } <= {
        (edge.source_node_id, edge.target_node_id)
        for edge in transition.edges
        if edge.reason == "verified-binder-introduction"
    }


def test_repeated_equal_subexpression_keeps_closest_ast_occurrence() -> None:
    source = (
        SemanticExpressionNode(
            "left-min",
            kind="app",
            fingerprint="min-0-s",
            path=("0", "0", "1", "0", "1"),
            latex_spans=(SemanticSpan(0, 9),),
        ),
        SemanticExpressionNode(
            "right-min",
            kind="app",
            fingerprint="min-0-s",
            path=("0", "1"),
            latex_spans=(SemanticSpan(16, 25),),
        ),
    )
    target = (
        SemanticExpressionNode(
            "new-left-min",
            kind="app",
            fingerprint="min-0-s",
            path=("0", "0", "1"),
            latex_spans=(SemanticSpan(0, 9),),
        ),
    )
    edges = _occurrence_edges(
        source,
        target,
        set(),
        set(),
        lambda node: node.fingerprint,
        "same-expression",
        0.98,
    )
    assert [(edge.source_node_id, edge.target_node_id) for edge in edges] == [
        ("left-min", "new-left-min")
    ]


def test_forall_elimination_preserves_rhs_by_ast_path_not_repeated_f_text() -> None:
    source_latex = r"\forall t, f(t) \leq f(x) \cdot f(2 \cdot f(x))"
    target_latex = r"f(f(x)) \leq f(x) \cdot f(2 \cdot f(x))"
    rhs = r"f(x) \cdot f(2 \cdot f(x))"
    old_start = source_latex.index(rhs)
    new_start = target_latex.index(rhs)
    source = (
        SemanticExpressionNode(
            "old-rhs",
            kind="app",
            path=("0", "1", "1"),
            latex_spans=(SemanticSpan(old_start, old_start + len(rhs)),),
        ),
        SemanticExpressionNode(
            "old-left-f",
            kind="fvar",
            identity="function-f",
            path=("0", "1", "0", "1", "0"),
            latex_spans=(SemanticSpan(source_latex.index("f(t)"), source_latex.index("f(t)") + 1),),
        ),
    )
    target = (
        SemanticExpressionNode(
            "new-rhs",
            kind="app",
            path=("0", "1"),
            latex_spans=(SemanticSpan(new_start, new_start + len(rhs)),),
        ),
        SemanticExpressionNode(
            "new-left-f",
            kind="fvar",
            identity="function-f",
            path=("0", "0", "1", "0"),
            latex_spans=(SemanticSpan(0, 1),),
        ),
    )
    edges = _structural_rule_edges(
        source,
        target,
        source_latex,
        target_latex,
        "forall-elimination",
    )
    assert any(
        edge.source_node_id == "old-rhs"
        and edge.target_node_id == "new-rhs"
        and edge.reason == "verified-structural-expression"
        for edge in edges
    )


def test_goal_timeline_replaces_and_closes_goals() -> None:
    raw = {
        "theoremName": "Demo.proof",
        "startGoal": {"goalId": "g1", "state": "⊢ A ∧ B"},
        "highlighting": [],
        "actions": [
            {
                "tacticText": "constructor",
                "goalActions": [
                    {
                        "startGoalId": "g1",
                        "startState": "⊢ A ∧ B",
                        "results": [
                            {"goal": {"goalId": "g2", "state": "⊢ A"}, "indexMaps": []},
                            {"goal": {"goalId": "g3", "state": "⊢ B"}, "indexMaps": []},
                        ],
                    }
                ],
            },
            {
                "tacticText": "assumption",
                "goalActions": [
                    {"startGoalId": "g2", "startState": "⊢ A", "results": []}
                ],
            },
        ],
    }
    movie = Movie.from_json(raw)
    assert movie.theorem_name == "Demo.proof"
    assert [goal.goal_id for goal in movie.frames[1].goals] == ["g2", "g3"]
    assert [goal.goal_id for goal in movie.frames[2].goals] == ["g3"]
    assert [goal.goal_id for goal in movie.frames[1].display_goals] == ["g2", "g3"]
    assert [goal.goal_id for goal in movie.frames[2].display_goals] == ["g3"]
    assert movie.frames[0].goals[0].lineage_id == movie.frames[1].goals[0].lineage_id
    assert movie.frames[1].goals[1].lineage_id != movie.frames[1].goals[0].lineage_id


def test_upstream_index_maps_are_preserved_for_rendered_latex() -> None:
    raw = {
        "theoremName": "Demo.maps",
        "startGoal": {
            "goalId": "g1",
            "state": "⊢ A",
            "latexTarget": "A",
        },
        "actions": [
            {
                "tacticText": "change",
                "goalActions": [
                    {
                        "startGoalId": "g1",
                        "results": [
                            {
                                "goal": {
                                    "goalId": "g2",
                                    "state": "⊢ B",
                                    "latexTarget": "B",
                                },
                                "indexMaps": {
                                    "s1_to_s2": [0, None, 2],
                                    "s2_to_s1": [0, None, 2],
                                },
                                "latexIndexMaps": {
                                    "s1_to_s2": [0, 1, None],
                                    "s2_to_s1": [0, 1, None],
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }

    goal = Movie.from_json(raw).frames[1].display_goals[0]
    assert goal.parent_goal_id == "g1"
    assert goal.index_maps.source_to_target == (0, None, 2)
    assert goal.latex_index_maps.target_to_source == (0, 1, None)


def test_semantic_transition_preserves_overlapping_spans_and_edges() -> None:
    raw = {
        "theoremName": "Demo.semantic",
        "startGoal": {"goalId": "g1", "state": "old", "latexTarget": "a+b"},
        "actions": [{
            "tacticText": "ring",
            "goalActions": [{
                "startGoalId": "g1",
                "results": [{
                    "goal": {"goalId": "g2", "state": "new", "latexTarget": "b+a"},
                    "semanticTransition": {
                        "proofKind": "ring",
                        "adapter": "expr-tree-v1",
                        "sourceNodes": [
                            {"id": "whole", "kind": "add", "latexSpans": [{"start": 0, "end": 3}]},
                            {"id": "left", "kind": "term", "latexSpans": [[0, 1], [2, 3]]},
                        ],
                        "targetNodes": [
                            {"id": "whole2", "kind": "add", "latexSpans": [{"start": 0, "end": 3}]},
                        ],
                        "edges": [
                            {"sourceNodeId": "whole", "targetNodeId": "whole2"},
                            {"sourceNodeId": "left", "targetNodeId": "whole2"},
                            {"sourceNodeId": "left", "targetNodeId": "whole2"},
                        ],
                    },
                }],
            }],
        }],
    }

    transition = Movie.from_json(raw).frames[1].display_goals[0].semantic_transition
    assert transition is not None
    assert transition.proof_kind == "ring"
    assert transition.adapter == "expr-tree-v1"
    assert transition.source.nodes[1].latex_spans[1].start == 2
    assert [edge.source_node_id for edge in transition.edges] == [
        "whole", "left", "left"
    ]


def test_semantic_leantex_fields_are_loaded() -> None:
    raw = {
        "theoremName": "Demo.semantic",
        "startGoal": {
            "goalId": "g1",
            "state": "x : ℝ\n⊢ x / 2 = x * (2 : ℝ)⁻¹",
            "latexTarget": r"\frac{x}{2} = x \cdot 2^{-1}",
            "latexContext": [{"name": "x", "latex": r"\mathbb{R}"}],
        },
        "actions": [],
        "highlighting": [],
    }
    goal = Movie.from_json(raw).frames[0].goals[0]
    assert goal.latex_target == r"\frac{x}{2} = x \cdot 2^{-1}"
    assert goal.latex_context[0].latex == r"\mathbb{R}"
    assert _goal_latex(goal) == r"\frac{x}{2} = x \cdot 2^{-1}"
    assert _initial_context_lines(goal) == [r"x \;:\; \mathbb{R}"]


def test_semantic_frames_preserve_context_changes_and_renumber() -> None:
    raw = {
        "theoremName": "demo",
        "startGoal": {"goalId": "g1", "state": "goal A", "latexTarget": "A"},
        "highlighting": [],
        "actions": [
            {
                "tacticText": "change hidden context",
                "goalActions": [{
                    "startGoalId": "g1",
                    "results": [{"goal": {
                        "goalId": "g2", "state": "hidden context; goal A", "latexTarget": "A",
                        "latexContext": [{"name": "h", "latex": "P"}]
                    }}],
                }],
            },
            {
                "tacticText": "change target",
                "goalActions": [{
                    "startGoalId": "g2",
                    "results": [{"goal": {
                        "goalId": "g3", "state": "goal B", "latexTarget": "B"
                    }}],
                }],
            },
            {
                "tacticText": "close",
                "goalActions": [{"startGoalId": "g3", "results": []}],
            },
        ],
    }

    frames = Movie.from_json(raw).semantic_frames()

    assert [frame.index for frame in frames] == [0, 1, 2, 3]
    assert [tuple(goal.latex_target for goal in frame.goals) for frame in frames] == [
        ("A",),
        ("A",),
        ("B",),
        (),
    ]


def test_long_latex_is_wrapped_at_top_level_connectives() -> None:
    source = r"\forall n : \mathbb{N},\ n \geq N \land a^n + b = g \land b^n + a = g"
    lines = _split_latex_lines(source, target_chars=42)
    assert len(lines) > 1
    assert "".join(lines).replace(" ", "") == source.replace(" ", "")
