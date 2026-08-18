"""Small, renderer-independent primitives for matching Lean expression nodes."""

from __future__ import annotations

from proof_video.proof.schema import SemanticExpressionNode


def node_latex(node: SemanticExpressionNode, sequent: str) -> str:
    if not node.latex_spans or any(not span.valid for span in node.latex_spans):
        return ""
    return "".join(sequent[span.start : span.end] for span in node.latex_spans)


def rendered_expression_key(source: str) -> str:
    """Normalize only closing delimiters omitted by LeanTeX spans."""

    result = source.strip()
    while True:
        previous = result
        for suffix in (r"\right)", r"\right]", r"\big)", r"\big]", ")", "]"):
            if result.endswith(suffix):
                result = result[: -len(suffix)].rstrip()
                break
        if result == previous:
            return result


def path_without_sequent_prefix(
    path: tuple[str | int, ...],
) -> tuple[str | int, ...]:
    if len(path) >= 2 and path[0] == "context":
        local_path = path[2:]
    elif path and path[0] == "target":
        local_path = path[1:]
    else:
        local_path = path

    # Hierarchical proof traces used to prepend ``chapter-N/`` to the first
    # AST path component while remapping globally unique node ids.  Paths are
    # expression-local coordinates, so that namespace made certified rule
    # adapters such as forall introduction miss otherwise identical body
    # nodes.  Accept those already-persisted traces while new exports retain
    # the canonical local path from Lean.
    if local_path and isinstance(local_path[0], str):
        head = local_path[0]
        namespace, separator, component = head.rpartition("/")
        if separator and namespace.startswith("chapter-") and component:
            return (component, *local_path[1:])
    return local_path


def adapted_expression_path(
    path: tuple[str | int, ...], rule: str
) -> tuple[str | int, ...] | None:
    """Map an expression path through one primitive natural-deduction rule.

    This function is intentionally renderer-independent.  It is used both by
    direct transitions and by a contracted derivation slice whose immediate
    premise is hidden from the blackboard.
    """

    if rule == "forall-elimination" and len(path) >= 2 and path[:2] == ("0", "1"):
        return ("0", *path[2:])
    if rule == "forall-introduction" and path and path[0] == "0":
        return ("0", "1", *path[1:])
    if rule == "certified-substitution":
        return path
    return None


def common_path_prefix(
    source: SemanticExpressionNode, target: SemanticExpressionNode
) -> int:
    source_path = path_without_sequent_prefix(source.path)
    target_path = path_without_sequent_prefix(target.path)
    return next(
        (
            index
            for index, (old, new) in enumerate(
                zip(source_path, target_path, strict=False)
            )
            if old != new
        ),
        min(len(source_path), len(target_path)),
    )


def span_extent(node: SemanticExpressionNode) -> int:
    return sum(span.end - span.start for span in node.latex_spans if span.valid)
