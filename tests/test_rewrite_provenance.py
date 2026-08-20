from proof_video.proof.rewrite_provenance import directed_rewrite_origins
from proof_video.proof.schema import ProofStep, SemanticExpressionNode, SemanticSpan


def _step(latex: str, nodes: tuple[SemanticExpressionNode, ...]) -> ProofStep:
    return ProofStep(
        id=7,
        scope_id="root",
        parent_scope_id=None,
        depth=0,
        kind="theorem-application",
        rule="theorem-application",
        premises=(),
        proposition_latex=latex,
        proposition_lean=latex,
        display_latex=latex,
        proof_fingerprint="proof-7",
        proposition_fingerprint="prop-7",
        proof_path="root",
        semantic_nodes=nodes,
    )


def test_reverse_rewrite_uses_the_actual_left_result_not_an_equal_descendant() -> None:
    equality = "u=v+u"
    step = _step(
        equality,
        (
            SemanticExpressionNode(
                "root",
                kind="app",
                path=("0",),
                latex_spans=(SemanticSpan(0, len(equality)),),
            ),
            SemanticExpressionNode(
                "left-u",
                kind="fvar",
                identity="u",
                path=("0", "0"),
                latex_spans=(SemanticSpan(0, 1),),
            ),
            SemanticExpressionNode(
                "right",
                kind="app",
                fingerprint="v-plus-u",
                path=("0", "1"),
                latex_spans=(SemanticSpan(2, len(equality)),),
            ),
            SemanticExpressionNode(
                "right-inner-u",
                kind="fvar",
                identity="u",
                path=("0", "1", "1"),
                latex_spans=(SemanticSpan(4, 5),),
            ),
        ),
    )
    source_nodes = (
        SemanticExpressionNode(
            "proof-context-7/left-u",
            kind="fvar",
            identity="u",
            path=("context", 7, "0", "0"),
            latex_spans=(SemanticSpan(0, 1),),
        ),
        SemanticExpressionNode(
            "proof-context-7/right",
            kind="app",
            fingerprint="v-plus-u",
            path=("context", 7, "0", "1"),
            latex_spans=(SemanticSpan(2, 5),),
        ),
        SemanticExpressionNode(
            "proof-context-7/right-inner-u",
            kind="fvar",
            identity="u",
            path=("context", 7, "0", "1", "1"),
            latex_spans=(SemanticSpan(4, 5),),
        ),
    )
    old = SemanticExpressionNode(
        "old",
        kind="app",
        fingerprint="v-plus-u",
        latex_spans=(SemanticSpan(2, 5),),
    )
    new = SemanticExpressionNode(
        "new",
        kind="fvar",
        identity="u",
        latex_spans=(SemanticSpan(0, 1),),
    )

    origins = directed_rewrite_origins(
        old_node=old,
        new_node=new,
        old_latex=equality,
        new_latex=equality,
        visible_leaf_ids=frozenset({7}),
        visible_steps_by_id={7: step},
        visible_nodes_by_leaf={7: source_nodes},
        source_sequent=equality,
    )

    assert [(item.source_node.node_id, item.direction) for item in origins] == [
        ("proof-context-7/left-u", "right-to-left")
    ]
