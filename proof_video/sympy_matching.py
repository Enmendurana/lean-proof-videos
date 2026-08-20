from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from sympy import expand, srepr
from sympy.core.relational import Relational
from sympy.parsing.latex import parse_latex

from proof_video.models import SemanticExpressionNode


_FORBIDDEN_LATEX = (
    r"\forall",
    r"\exists",
    r"\mathbb",
    r"\operatorname",
    r"\vdash",
    r"\leq",
    r"\geq",
    r"\Rightarrow",
    r"\implies",
    "=",
    "<",
    ">",
)


@dataclass(frozen=True)
class SympyTokenProposal:
    source_index: int
    target_index: int
    source_node: SemanticExpressionNode
    target_node: SemanticExpressionNode
    span_size: int
    confidence: float = 0.72


@lru_cache(maxsize=4096)
def canonical_ast_signature(latex: str) -> str | None:
    """Return a canonical algebraic SymPy AST signature.

    This is deliberately narrower than Lean: quantified propositions, types,
    and relations remain exclusively Lean's responsibility. Strict parsing
    also prevents SymPy's experimental LaTeX parser from silently accepting a
    prefix of an unsupported expression.
    """
    compact = latex.strip()
    if (
        not compact
        or len(compact) > 240
        or any(marker in compact for marker in _FORBIDDEN_LATEX)
    ):
        return None
    try:
        expression = parse_latex(compact, strict=True, backend="antlr")
        if isinstance(expression, Relational) or expression.is_Atom:
            return None
        return srepr(expand(expression))
    except Exception:
        return None


def sympy_ast_token_proposals(
    source_nodes,
    target_nodes,
    source_token_spans,
    source_token_texts,
    target_token_spans,
    target_token_texts,
) -> list[SympyTokenProposal]:
    """Match unchanged algebraic atoms inside SymPy-equivalent expressions.

    The result is only a low-priority supplement to Lean's proof edges. It is
    useful when factoring, expansion, associativity, or commutativity changes
    every surrounding Expr path. Repeated atoms are left unmapped unless their
    occurrence is unique on both sides of the candidate expression.
    """

    def token_indices(node, spans):
        if not node.latex_spans or any(not span.valid for span in node.latex_spans):
            return []
        return [
            index
            for index, (start, end) in enumerate(spans)
            if any(
                start < semantic_span.end and semantic_span.start < end
                for semantic_span in node.latex_spans
            )
        ]

    def records(nodes, spans, texts):
        result: dict[str, list[tuple[SemanticExpressionNode, list[int]]]] = {}
        for node in nodes:
            if node.kind != "app":
                continue
            indices = token_indices(node, spans)
            if not 3 <= len(indices) <= 80:
                continue
            # Token isolation removes geometry-free LaTeX spacing. Reinsert a
            # harmless space so `\cdot` followed by `f` does not become the
            # unrelated command `\cdotf`.
            latex = " ".join(texts[index] for index in indices)
            signature = canonical_ast_signature(latex)
            if signature is not None:
                result.setdefault(signature, []).append((node, indices))
        return result

    source_records = records(source_nodes, source_token_spans, source_token_texts)
    target_records = records(target_nodes, target_token_spans, target_token_texts)
    proposals: list[SympyTokenProposal] = []
    for signature in source_records.keys() & target_records.keys():
        for source_node, source_indices in source_records[signature]:
            for target_node, target_indices in target_records[signature]:
                # Identical token sequences are already represented more
                # precisely by Lean edges; SymPy is for structural rearrangement.
                if [source_token_texts[i] for i in source_indices] == [
                    target_token_texts[i] for i in target_indices
                ]:
                    continue
                for token_text in dict.fromkeys(
                    source_token_texts[index] for index in source_indices
                ):
                    old = [
                        index
                        for index in source_indices
                        if source_token_texts[index] == token_text
                    ]
                    new = [
                        index
                        for index in target_indices
                        if target_token_texts[index] == token_text
                    ]
                    # Function heads belong to an application occurrence,
                    # never to the pool of algebraically equal letters. Lean's
                    # composite/application edges move ``f(x)`` as a phrase;
                    # SymPy may not pull a bare ``f`` out of it.
                    if any(
                        index + 1 < len(source_token_texts)
                        and source_token_texts[index + 1] in {"(", r"\left(", r"\big("}
                        for index in old
                    ) or any(
                        index + 1 < len(target_token_texts)
                        and target_token_texts[index + 1] in {"(", r"\left(", r"\big("}
                        for index in new
                    ):
                        continue
                    if len(old) == len(new) == 1:
                        proposals.append(
                            SympyTokenProposal(
                                old[0],
                                new[0],
                                source_node,
                                target_node,
                                len(source_indices) + len(target_indices),
                            )
                        )
    return proposals
