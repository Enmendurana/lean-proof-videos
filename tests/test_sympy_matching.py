from proof_video.models import SemanticExpressionNode, SemanticSpan
from proof_video.sympy_matching import (
    canonical_ast_signature,
    sympy_ast_token_proposals,
)


def test_sympy_ast_normalizes_factoring_and_commutative_order() -> None:
    expanded = canonical_ast_signature(r"2\cdot b+2\cdot a")
    factored = canonical_ast_signature(r"2\cdot(a+b)")
    assert expanded is not None
    assert expanded == factored
    assert canonical_ast_signature("a+b") == canonical_ast_signature("b+a")
    assert canonical_ast_signature("a+b") != canonical_ast_signature("a-b")


def test_sympy_never_claims_lean_quantifiers_or_relations() -> None:
    assert canonical_ast_signature(r"\forall x : \mathbb{R},\ f(x)=0") is None
    assert canonical_ast_signature(r"f(x)\leq 0") is None


def test_sympy_proposes_unique_atoms_across_factorization() -> None:
    source_tokens = ("2", r"\cdot", "b", "+", "2", r"\cdot", "a")
    target_tokens = ("2", r"\cdot", "(", "a", "+", "b", ")")
    source = SemanticExpressionNode(
        "expanded", kind="app", latex_spans=(SemanticSpan(0, 7),)
    )
    target = SemanticExpressionNode(
        "factored", kind="app", latex_spans=(SemanticSpan(0, 7),)
    )

    proposals = sympy_ast_token_proposals(
        (source,),
        (target,),
        tuple((index, index + 1) for index in range(7)),
        source_tokens,
        tuple((index, index + 1) for index in range(7)),
        target_tokens,
    )
    pairs = {(proposal.source_index, proposal.target_index) for proposal in proposals}

    assert (2, 5) in pairs  # b remains b
    assert (6, 3) in pairs  # a remains a
    assert (3, 4) in pairs  # the unique plus remains the same operation
    assert all(source_index not in {0, 4} for source_index, _ in pairs)


def test_sympy_never_moves_a_bare_function_head() -> None:
    source_tokens = ("f", "(", "x", ")", "+", "a")
    target_tokens = ("a", "+", "f", "(", "x", ")")
    source = SemanticExpressionNode(
        "source", kind="app", latex_spans=(SemanticSpan(0, 6),)
    )
    target = SemanticExpressionNode(
        "target", kind="app", latex_spans=(SemanticSpan(0, 6),)
    )
    proposals = sympy_ast_token_proposals(
        (source,),
        (target,),
        tuple((index, index + 1) for index in range(6)),
        source_tokens,
        tuple((index, index + 1) for index in range(6)),
        target_tokens,
    )
    pairs = {(proposal.source_index, proposal.target_index) for proposal in proposals}
    assert (0, 2) not in pairs
