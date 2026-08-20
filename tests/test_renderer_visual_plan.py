from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from proof_video.animation.semantic import (
    compile_renderer_transition_plan,
    visual_primitive_payload,
)
from proof_video.animation.scene_helpers import _mapped_rows_animations
from proof_video.models import (
    Frame,
    Goal,
    Movie,
    SemanticExpression,
    SemanticExpressionNode,
    SemanticSpan,
    SemanticTransition,
    SemanticTransitionEdge,
)
from proof_video.presentation.model import (
    AnchorSide,
    LayoutAnchor,
    LayoutRowKind,
    SemanticVisualPlan,
    VisualPrimitive,
    VisualPrimitiveKind,
)
from proof_video.proof.correspondence import EntityKind, EntityRef
from proof_video.proof.state import CharacterSpan, ExprOccurrence, Expression
from proof_video.remotion_export import build_remotion_timeline


def _anchor(
    anchor_id: str,
    side: AnchorSide,
    entity: EntityRef,
    *,
    row_kind: LayoutRowKind = LayoutRowKind.TARGET,
    row_index: int = 0,
) -> LayoutAnchor:
    return LayoutAnchor(
        anchor_id=anchor_id,
        persistent_id=f"persistent:{entity.key}",
        side=side,
        entity=entity,
        goal_index=0,
        row_kind=row_kind,
        row_index=row_index,
    )


def _plan(
    anchors: tuple[LayoutAnchor, ...],
    *primitives: VisualPrimitive,
) -> SemanticVisualPlan:
    return SemanticVisualPlan("before", "after", anchors, primitives)


def _primitive(
    kind: VisualPrimitiveKind,
    sources: tuple[str, ...],
    targets: tuple[str, ...],
    *,
    scope: str = "target",
) -> VisualPrimitive:
    return VisualPrimitive(
        primitive_id=f"{kind.value}:{scope}",
        kind=kind,
        source_anchor_ids=sources,
        target_anchor_ids=targets,
        persistent_ids=(f"persistent:{kind.value}:{scope}",),
        scope=scope,
        evidence=(f"verified-{kind.value}",),
    )


def _node(node_id: str, start: int, end: int, *, kind: str = "const"):
    return SemanticExpressionNode(
        node_id,
        kind=kind,
        latex_spans=(SemanticSpan(start, end),),
    )


def _transition(
    source_nodes: tuple[SemanticExpressionNode, ...],
    target_nodes: tuple[SemanticExpressionNode, ...],
    *legacy_edges: SemanticTransitionEdge,
) -> SemanticTransition:
    return SemanticTransition(
        source=SemanticExpression(source_nodes),
        target=SemanticExpression(target_nodes),
        edges=legacy_edges,
        proof_kind="kernel-certified",
        adapter="intentionally-conflicting-legacy-fixture",
    )


@pytest.mark.parametrize(
    ("label", "kinds"),
    (
        ("intro", (VisualPrimitiveKind.MOVE,)),
        ("revert", (VisualPrimitiveKind.MOVE,)),
        ("replace", (VisualPrimitiveKind.REWRITE,)),
        (
            "subst",
            (VisualPrimitiveKind.REWRITE, VisualPrimitiveKind.REMOVE),
        ),
        ("split", (VisualPrimitiveKind.SPLIT, VisualPrimitiveKind.FOCUS)),
        ("close", (VisualPrimitiveKind.CLOSE,)),
        ("reorder", (VisualPrimitiveKind.REORDER,)),
    ),
)
def test_renderer_control_plan_preserves_canonical_operation_vocabulary(
    label: str,
    kinds: tuple[VisualPrimitiveKind, ...],
) -> None:
    source = _anchor(
        f"{label}:source",
        AnchorSide.BEFORE,
        EntityRef(EntityKind.GOAL, "before"),
        row_kind=LayoutRowKind.GOAL,
    )
    target = _anchor(
        f"{label}:target",
        AnchorSide.AFTER,
        EntityRef(EntityKind.GOAL, "after"),
        row_kind=LayoutRowKind.GOAL,
    )
    primitives = tuple(
        _primitive(
            kind,
            (source.anchor_id,) if kind not in {VisualPrimitiveKind.CREATE} else (),
            (target.anchor_id,)
            if kind not in {VisualPrimitiveKind.REMOVE, VisualPrimitiveKind.CLOSE}
            else (),
            scope=label,
        )
        for kind in kinds
    )

    payload = visual_primitive_payload(_plan((source, target), *primitives))

    assert [item["kind"] for item in payload] == [item.value for item in kinds]
    assert all(item["scope"] == label for item in payload)
    assert all(item["fallback"] == "" for item in payload)


def test_intro_and_revert_resolve_local_declarations_without_glyph_matching() -> None:
    binder = EntityRef(
        EntityKind.OCCURRENCE,
        "g",
        expression_role="target",
        occurrence_id="forall-binder",
    )
    local = EntityRef(EntityKind.LOCAL, "g", local_id="x")
    before_binder = _anchor("before:binder", AnchorSide.BEFORE, binder)
    after_local = _anchor(
        "after:local",
        AnchorSide.AFTER,
        local,
        row_kind=LayoutRowKind.CONTEXT,
    )
    intro_plan = _plan(
        (before_binder, after_local),
        _primitive(
            VisualPrimitiveKind.MOVE,
            (before_binder.anchor_id,),
            (after_local.anchor_id,),
            scope="intro",
        ),
    )
    intro_transition = _transition(
        (_node("forall-binder", 8, 9, kind="bvar"),),
        (
            SemanticExpressionNode(
                "context/x/name",
                kind="declaration",
                identity="fvar:x",
                latex_spans=(SemanticSpan(0, 1),),
            ),
        ),
    )

    intro = compile_renderer_transition_plan(
        ((8, 9),),
        ("x",),
        ((0, 1),),
        ("x",),
        intro_transition,
        intro_plan,
    )
    assert intro.pairs == ((0, 0),)

    before_local = _anchor(
        "before:local",
        AnchorSide.BEFORE,
        local,
        row_kind=LayoutRowKind.CONTEXT,
    )
    after_binder = _anchor("after:binder", AnchorSide.AFTER, binder)
    revert_plan = _plan(
        (before_local, after_binder),
        _primitive(
            VisualPrimitiveKind.MOVE,
            (before_local.anchor_id,),
            (after_binder.anchor_id,),
            scope="revert",
        ),
    )
    revert_transition = _transition(
        (
            SemanticExpressionNode(
                "context/x/name",
                kind="declaration",
                identity="fvar:x",
                latex_spans=(SemanticSpan(0, 1),),
            ),
        ),
        (_node("forall-binder", 8, 9, kind="bvar"),),
    )
    revert = compile_renderer_transition_plan(
        ((0, 1),),
        ("x",),
        ((8, 9),),
        ("x",),
        revert_transition,
        revert_plan,
    )
    assert revert.pairs == ((0, 0),)


def test_canonical_plan_overrides_conflicting_legacy_edges_for_repeated_symbols() -> (
    None
):
    source_left = EntityRef(
        EntityKind.OCCURRENCE,
        "g",
        expression_role="target",
        occurrence_id="source-left-app",
    )
    target_left = EntityRef(
        EntityKind.OCCURRENCE,
        "g",
        expression_role="target",
        occurrence_id="target-left-app",
    )
    source_anchor = _anchor("source:left", AnchorSide.BEFORE, source_left)
    target_anchor = _anchor("target:left", AnchorSide.AFTER, target_left)
    visual = _plan(
        (source_anchor, target_anchor),
        _primitive(
            VisualPrimitiveKind.KEEP,
            (source_anchor.anchor_id,),
            (target_anchor.anchor_id,),
        ),
    )
    tokens = ("f", "(", "x", ")", "f", "(", "x", ")")
    spans = tuple((index, index + 1) for index in range(len(tokens)))
    transition = _transition(
        (
            _node("source-left-app", 0, 4, kind="app"),
            _node("source-right-app", 4, 8, kind="app"),
        ),
        (
            _node("target-left-app", 0, 4, kind="app"),
            _node("target-right-app", 4, 8, kind="app"),
        ),
        # Deliberately wrong: old code would fly the left f(x) to the right.
        SemanticTransitionEdge(
            "source-left-app",
            "target-right-app",
            "verified-but-stale-legacy-edge",
            1.0,
        ),
    )

    compiled = compile_renderer_transition_plan(
        spans,
        tokens,
        spans,
        tokens,
        transition,
        visual,
    )

    assert compiled.pairs == ((0, 0), (1, 1), (2, 2), (3, 3))
    assert not any(target >= 4 for _source, target in compiled.pairs)
    assert all(item.reason == "verified-keep" for item in compiled.selected)


def test_manim_path_uses_the_same_canonical_compiler_result() -> None:
    source_ref = EntityRef(
        EntityKind.OCCURRENCE,
        "g",
        expression_role="target",
        occurrence_id="old-x",
    )
    target_ref = EntityRef(
        EntityKind.OCCURRENCE,
        "g",
        expression_role="target",
        occurrence_id="new-x",
    )
    source_anchor = _anchor("old", AnchorSide.BEFORE, source_ref)
    target_anchor = _anchor("new", AnchorSide.AFTER, target_ref)
    visual = _plan(
        (source_anchor, target_anchor),
        _primitive(
            VisualPrimitiveKind.MOVE,
            (source_anchor.anchor_id,),
            (target_anchor.anchor_id,),
        ),
    )
    transition = _transition(
        (_node("old-x", 0, 1, kind="fvar"),),
        (
            _node("wrong-x", 0, 1, kind="fvar"),
            _node("new-x", 2, 3, kind="fvar"),
        ),
        SemanticTransitionEdge("old-x", "wrong-x", "verified-stale-legacy", 1.0),
    )
    source_row = SimpleNamespace(
        proof_row_key="target:0",
        proof_tokens=("x",),
        proof_token_spans=((0, 1),),
        proof_token_mobjects=(object(),),
        proof_char_span=(0, 1),
    )
    target_row = SimpleNamespace(
        proof_row_key="target:0",
        proof_tokens=("x", "x"),
        proof_token_spans=((0, 1), (2, 3)),
        proof_token_mobjects=(object(), object()),
        proof_char_span=(0, 3),
    )

    with patch(
        "proof_video.animation.scene_helpers._phased_token_transition",
        return_value="canonical-animation",
    ) as animation:
        result = _mapped_rows_animations(
            [source_row],
            [target_row],
            None,
            transition,
            visual,
        )

    assert result == ["canonical-animation"]
    assert animation.call_args.args[2] == [(0, 1)]


def test_remotion_path_marks_canonical_plan_as_its_source() -> None:
    def canonical_expression(expression_id: str, occurrence_id: str) -> Expression:
        return Expression(
            expression_id=expression_id,
            fingerprint="same-x",
            lean="x",
            latex="x",
            occurrences=(
                ExprOccurrence(
                    occurrence_id=occurrence_id,
                    kind="fvar",
                    path=(0,),
                    fingerprint="same-x",
                    lean_identity="fvar:x",
                    latex_spans=(CharacterSpan(0, 1),),
                ),
            ),
        )

    first = Goal(
        "g1",
        "",
        latex_target="x",
        lineage_id="proof",
        semantic_nodes=(_node("old", 8, 9, kind="fvar"),),
        canonical_target=canonical_expression("target:g1", "old"),
    )
    transition = _transition(
        (_node("old", 8, 9),),
        (_node("new", 8, 9),),
        SemanticTransitionEdge("old", "new", "verified-legacy", 1.0),
    )
    second = Goal(
        "g2",
        "",
        latex_target="x + 0",
        lineage_id="proof",
        parent_goal_id="g1",
        semantic_transition=transition,
        semantic_nodes=(_node("new", 8, 9, kind="fvar"),),
        canonical_target=replace(
            canonical_expression("target:g2", "new"),
            lean="x + 0",
            latex="x + 0",
        ),
    )
    movie = Movie(
        "renderer-source",
        (
            Frame(0, "", (first,), canonical_abi=5),
            Frame(1, "", (second,), canonical_abi=5),
        ),
    )

    timeline = build_remotion_timeline(movie)

    assert timeline["transitions"][0]["plan"]["source"] == "canonical-visual-plan"
    assert timeline["transitions"][0]["plan"]["pairs"] == [[1, 1, 0]]
    assert any(
        item["kind"] in {"keep", "move"}
        for item in timeline["transitions"][0]["plan"]["primitives"]
    )


def test_long_wrapped_expression_keeps_every_certified_token_pair() -> None:
    token_count = 80
    tokens = tuple(f"x_{{{index}}}" for index in range(token_count))
    spans = tuple((index * 2, index * 2 + 1) for index in range(token_count))
    source_ref = EntityRef(
        EntityKind.OCCURRENCE,
        "g",
        expression_role="target",
        occurrence_id="long-before",
    )
    target_ref = EntityRef(
        EntityKind.OCCURRENCE,
        "g",
        expression_role="target",
        occurrence_id="long-after",
    )
    source_anchor = _anchor("long:before", AnchorSide.BEFORE, source_ref)
    target_anchor = _anchor("long:after", AnchorSide.AFTER, target_ref)
    visual = _plan(
        (source_anchor, target_anchor),
        _primitive(
            VisualPrimitiveKind.KEEP,
            (source_anchor.anchor_id,),
            (target_anchor.anchor_id,),
        ),
    )
    transition = _transition(
        (_node("long-before", 0, token_count * 2, kind="app"),),
        (_node("long-after", 0, token_count * 2, kind="app"),),
    )

    compiled = compile_renderer_transition_plan(
        spans,
        tokens,
        spans,
        tokens,
        transition,
        visual,
    )

    assert compiled.valid
    assert compiled.pairs == tuple((index, index) for index in range(token_count))
    assert compiled.created_targets == ()
    assert compiled.deleted_sources == ()
