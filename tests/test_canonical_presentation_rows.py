from __future__ import annotations

from unittest.mock import patch

from manim import VGroup

from proof_video.models import Frame, Goal, Movie
from proof_video.presentation.rows import context_presentation_rows
from proof_video.proof.state import (
    CharacterSpan,
    ExprOccurrence,
    Expression,
    LocalDecl,
)
from proof_video.remotion_export import build_remotion_timeline
from proof_video.scene import ProofScene


def _expression(identity: str, latex: str) -> Expression:
    return Expression(
        expression_id=f"expression:{identity}",
        fingerprint=f"fingerprint:{identity}",
        lean=identity,
        latex=latex,
        occurrences=(
            ExprOccurrence(
                occurrence_id=f"occurrence:{identity}",
                kind="fvar" if identity in {"u", "v"} else "const",
                path=(),
                fingerprint=f"node:{identity}",
                lean_identity=f"fvar:{identity}",
                latex_spans=(CharacterSpan(0, len(latex)),),
            ),
        ),
    )


_REAL = _expression("Real", r"\mathbb{R}")
_TARGET = _expression("P", "P")


def _local(value: Expression | None, *, presentation_visible: bool = True) -> LocalDecl:
    return LocalDecl(
        decl_id="s-id",
        user_name="s",
        type_expr=_REAL,
        value_expr=value,
        presentation_visible=presentation_visible,
    )


def _goal(value: Expression | None, *, include_local: bool = True) -> Goal:
    return Goal(
        goal_id="g",
        state="P",
        latex_target="P",
        lineage_id="lineage:g",
        canonical_locals=(_local(value),) if include_local else (),
        canonical_target=_TARGET,
    )


def _movie() -> Movie:
    empty = _goal(None, include_local=False)
    added = _goal(_expression("u", "u"))
    changed = _goal(_expression("v", "v"))
    cleared = _goal(None)
    return Movie(
        "definitions",
        (
            Frame(0, "", (empty,), (empty,)),
            Frame(1, "add", (added,), (added,)),
            Frame(2, "change", (changed,), (changed,)),
            Frame(3, "clear", (cleared,), (cleared,)),
        ),
    ).with_canonical_timeline()


def test_canonical_context_rows_show_add_change_and_clear_definition() -> None:
    _empty, added, changed, cleared = _movie().frames

    assert context_presentation_rows(added.goals[0])[0].latex == (
        r"s \;:\; \mathbb{R} \;\coloneqq\; u"
    )
    assert context_presentation_rows(changed.goals[0])[0].latex == (
        r"s \;:\; \mathbb{R} \;\coloneqq\; v"
    )
    assert context_presentation_rows(cleared.goals[0])[0].latex == (
        r"s \;:\; \mathbb{R}"
    )


def test_hidden_local_remains_canonical_but_has_no_presentation_row() -> None:
    hidden = _local(None, presentation_visible=False)
    visible = LocalDecl("x-id", "x", _REAL)
    goal = Goal(
        goal_id="g",
        state="P",
        latex_target="P",
        canonical_locals=(hidden, visible),
        canonical_target=_TARGET,
    )

    assert tuple(local.decl_id for local in goal.canonical_locals) == (
        "s-id",
        "x-id",
    )
    rows = context_presentation_rows(goal)
    assert [row.declaration_id for row in rows] == ["x-id"]
    assert [row.latex for row in rows] == [r"x \;:\; \mathbb{R}"]


def test_local_visibility_is_parsed_without_name_heuristics() -> None:
    payload = {
        "id": "_implementation-id",
        "userName": "perfectly_readable_name",
        "type": {
            "id": "type:_implementation-id",
            "fingerprint": "fp:type",
            "lean": "Real",
            "latex": r"\mathbb{R}",
        },
        "presentationVisible": False,
    }

    assert LocalDecl.from_json(payload).presentation_visible is False
    payload.pop("presentationVisible")
    assert LocalDecl.from_json(payload).presentation_visible is True


def test_hidden_local_is_filtered_by_both_renderer_routes() -> None:
    hidden = _local(None, presentation_visible=False)
    goal = Goal(
        goal_id="g",
        state="P",
        latex_target="P",
        lineage_id="lineage:g",
        canonical_locals=(hidden,),
        canonical_target=_TARGET,
    )
    movie = Movie("hidden", (Frame(0, "", (goal,), (goal,)),))

    timeline = build_remotion_timeline(movie, fps=30)
    assert [
        row for row in timeline["states"][0]["rows"] if row["kind"] == "context"
    ] == []

    scene = object.__new__(ProofScene)
    scene._goal_forest_layouts = {}
    scene._prepare_goal_forest(movie.semantic_frames())

    def fake_rows(source: str, **_kwargs):
        row = VGroup()
        row.proof_latex_source = source
        return [(row, 0, len(source))]

    with patch(
        "proof_video.scene._wrapped_math_rows_with_spans", side_effect=fake_rows
    ):
        block = scene._step_block(movie.semantic_frames()[0])
    assert [row.proof_latex_source for row in block[0]][:-1] == []


def test_definition_value_spans_follow_the_shared_rendered_row() -> None:
    _empty, added, changed, cleared = _movie().frames
    added_transition = added.goals[0].semantic_transition
    changed_transition = changed.goals[0].semantic_transition
    cleared_transition = cleared.goals[0].semantic_transition
    assert added_transition is not None
    assert changed_transition is not None
    assert cleared_transition is not None

    added_latex = context_presentation_rows(added.goals[0])[0].latex + "\n"
    changed_latex = context_presentation_rows(changed.goals[0])[0].latex + "\n"
    added_value = next(
        node for node in added_transition.target.nodes if node.node_id == "occurrence:u"
    )
    changed_value = next(
        node
        for node in changed_transition.target.nodes
        if node.node_id == "occurrence:v"
    )
    assert (
        added_latex[added_value.latex_spans[0].start : added_value.latex_spans[0].end]
        == "u"
    )
    assert (
        changed_latex[
            changed_value.latex_spans[0].start : changed_value.latex_spans[0].end
        ]
        == "v"
    )
    assert not any(
        node.node_id.startswith("local/s-id/value")
        for node in cleared_transition.target.nodes
    )


def test_remotion_consumes_the_shared_definition_rows() -> None:
    timeline = build_remotion_timeline(_movie(), fps=30)
    rows_by_state = [
        [row["latex"] for row in state["rows"] if row["kind"] == "context"]
        for state in timeline["states"]
    ]

    assert rows_by_state == [
        [],
        [r"s \;:\; \mathbb{R} \;\coloneqq\; u"],
        [r"s \;:\; \mathbb{R} \;\coloneqq\; v"],
        [r"s \;:\; \mathbb{R}"],
    ]


def test_manim_consumes_the_shared_definition_rows() -> None:
    frames = _movie().semantic_frames()
    scene = object.__new__(ProofScene)
    scene._goal_forest_layouts = {}
    scene._prepare_goal_forest(frames)

    def fake_rows(source: str, **_kwargs):
        row = VGroup()
        row.proof_latex_source = source
        return [(row, 0, len(source))]

    with patch(
        "proof_video.scene._wrapped_math_rows_with_spans", side_effect=fake_rows
    ):
        rendered = [scene._step_block(frame) for frame in frames]

    assert [
        [row.proof_latex_source for row in block[0]][:-1] for block in rendered
    ] == [
        [],
        [r"s \;:\; \mathbb{R} \;\coloneqq\; u"],
        [r"s \;:\; \mathbb{R} \;\coloneqq\; v"],
        [r"s \;:\; \mathbb{R}"],
    ]
