from dataclasses import replace
import json

from proof_video.diagnostics import build_transition_map, write_transition_debug
from proof_video.models import Movie
from proof_video.presentation.debug import build_canonical_transition_debug
from proof_video.proof.correspondence import (
    Correspondence,
    CorrespondenceEdge,
    EntityKind,
    EntityRef,
    ExplicitOccurrenceEdge,
    MatchProvenance,
    RelationKind,
)
from proof_video.proof.diff import diff_proof_states
from proof_video.proof.effects import TransitionMetadata
from proof_video.proof.state import (
    ExprOccurrence,
    Expression,
    GoalState,
    LocalDecl,
    ProofState,
)


def _expression(
    expression_id: str,
    fingerprint: str,
    *occurrences: ExprOccurrence,
    latex: str = "",
) -> Expression:
    return Expression(
        expression_id=expression_id,
        fingerprint=fingerprint,
        latex=latex,
        occurrences=occurrences,
    )


def _occurrence(
    occurrence_id: str,
    path: tuple[int, ...],
    *,
    identity: str = "",
    fingerprint: str = "atom",
) -> ExprOccurrence:
    return ExprOccurrence(
        occurrence_id=occurrence_id,
        kind="fvar" if identity else "term",
        path=path,
        fingerprint=fingerprint,
        lean_identity=identity,
        type_fingerprint="Real",
    )


def _state(target: Expression, *, local_name: str = "x") -> ProofState:
    local_type = _expression("type:R", "Real", latex=r"\mathbb R")
    return ProofState(
        goals=(
            GoalState(
                goal_id="g",
                lineage_id="lineage:g",
                locals=(
                    LocalDecl(
                        decl_id="fvar:x",
                        user_name=local_name,
                        type_expr=local_type,
                    ),
                ),
                target=target,
            ),
        ),
        focus=("g",),
    )


def test_canonical_debug_contains_the_complete_semantic_boundary() -> None:
    before = _state(
        _expression(
            "target:before",
            "before",
            _occurrence("old-x", (0,), identity="fvar:x"),
            _occurrence("old-zero", (1,), fingerprint="zero"),
            latex="x < 0",
        )
    )
    after = _state(
        _expression(
            "target:after",
            "after",
            _occurrence("new-x", (0,), identity="fvar:x"),
            _occurrence("new-zero", (1,), fingerprint="zero"),
            latex=r"x \leq 0",
        )
    )
    transition = diff_proof_states(
        before,
        after,
        explicit_occurrence_edges=(
            ExplicitOccurrenceEdge(
                "old-x",
                "new-x",
                "Lean expression occurrence survived the rewrite",
            ),
        ),
        metadata=TransitionMetadata(tactic_text="exact h"),
    )

    debug = build_canonical_transition_debug(before, after, transition)

    assert debug["before"]["fingerprint"] == before.fingerprint
    assert debug["after"]["fingerprint"] == after.fingerprint
    assert debug["before"]["goals"][0]["locals"][0]["declarationId"] == "fvar:x"
    assert (
        debug["before"]["goals"][0]["target"]["occurrences"][0]["occurrenceId"]
        == "old-x"
    )
    assert debug["validation"] == {
        "valid": True,
        "replayMatches": True,
        "errors": [],
    }
    assert debug["interpretation"]["primary"] == "rewriting"
    assert debug["transition"]["effects"]
    assert debug["presentation"]["anchors"]
    assert debug["presentation"]["primitives"]
    assert json.dumps(debug, sort_keys=True) == json.dumps(
        build_canonical_transition_debug(before, after, transition),
        sort_keys=True,
    )

    identity = next(
        edge
        for edge in debug["transition"]["hyperedges"]
        if edge["provenance"] == "lean-identity"
        and edge["sources"][0].get("occurrenceId") == "old-x"
    )
    assert identity["arity"] == "1->1"
    assert identity["relation"] == "preserve"
    assert identity["confidence"] == 1.0


def test_copy_hyperedge_remains_one_to_many_in_debug_output() -> None:
    source = _occurrence("source-x", (0,), identity="fvar:x")
    before = _state(_expression("before", "before", source, latex="x"))
    after = _state(
        _expression(
            "after",
            "after",
            _occurrence("left-x", (0,), identity="fvar:x"),
            _occurrence("right-x", (1,), identity="fvar:x"),
            latex="x+x",
        )
    )
    transition = diff_proof_states(
        before,
        after,
        explicit_occurrence_edges=(
            ExplicitOccurrenceEdge(
                "source-x", "left-x", "copy", relation=RelationKind.COPY
            ),
            ExplicitOccurrenceEdge(
                "source-x", "right-x", "copy", relation=RelationKind.COPY
            ),
        ),
    )

    debug = build_canonical_transition_debug(before, after, transition)
    copies = [
        edge for edge in debug["transition"]["hyperedges"] if edge["relation"] == "copy"
    ]

    assert len(copies) == 1
    assert copies[0]["arity"] == "1->2"
    assert [item["occurrenceId"] for item in copies[0]["targets"]] == [
        "left-x",
        "right-x",
    ]


def test_uncertified_text_continuity_is_reported_as_safe_discontinuity() -> None:
    before = _state(
        _expression(
            "before",
            "before",
            _occurrence("old-a", (0,), fingerprint="a"),
            latex="a",
        )
    )
    after = _state(
        _expression(
            "after",
            "after",
            _occurrence("new-a", (0,), fingerprint="a"),
            latex="a",
        )
    )
    transition = diff_proof_states(before, after)
    source = EntityRef(
        EntityKind.OCCURRENCE, "g", expression_role="target", occurrence_id="old-a"
    )
    target = EntityRef(
        EntityKind.OCCURRENCE, "g", expression_role="target", occurrence_id="new-a"
    )
    transition = replace(
        transition,
        correspondence=Correspondence(
            (
                CorrespondenceEdge(
                    (source,),
                    (target,),
                    RelationKind.PRESERVE,
                    MatchProvenance.TEXT_FALLBACK,
                    ("same rendered token only",),
                    0.25,
                ),
            )
        ),
    )

    debug = build_canonical_transition_debug(before, after, transition)

    assert debug["validation"]["valid"] is True
    text_primitives = [
        item
        for item in debug["presentation"]["primitives"]
        if "text-fallback" in item["provenance"]
    ]
    assert {item["kind"] for item in text_primitives} == {"create", "remove"}
    assert all(item["usedFallback"] is False for item in text_primitives)
    assert any(
        item["code"] == "uncertified-text-continuity-rejected"
        for item in debug["presentation"]["diagnostics"]
    )


def test_transition_map_embeds_renderer_independent_canonical_debug() -> None:
    movie = Movie.from_json(
        {
            "theoremName": "Demo.canonicalDiagnostic",
            "startGoal": {
                "goalId": "g1",
                "state": "x : R\n\u22a2 x = x",
                "latexTarget": "x=x",
                "latexContext": [{"name": "x", "latex": "x : R"}],
            },
            "actions": [
                {
                    "tacticText": "rfl",
                    "goalActions": [
                        {
                            "startGoalId": "g1",
                            "results": [
                                {
                                    "goal": {
                                        "goalId": "g2",
                                        "state": "x : R\n\u22a2 True",
                                        "latexTarget": r"\mathrm{True}",
                                        "latexContext": [
                                            {"name": "x", "latex": "x : R"}
                                        ],
                                    }
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )

    result = build_transition_map(movie)
    canonical = result["transitions"][0]["canonical"]

    assert result["schemaVersion"] == 2
    assert canonical["available"] is True
    assert canonical["before"]["fingerprint"]
    assert canonical["after"]["fingerprint"]
    assert canonical["transition"]["hyperedges"]
    assert "fallback" in canonical["presentation"]


def test_transition_debug_writes_json_and_human_html(tmp_path) -> None:
    movie = Movie.from_json(
        {
            "theoremName": "Demo.htmlDiagnostic",
            "startGoal": {"goalId": "g1", "state": "⊢ True"},
            "actions": [
                {
                    "tacticText": "exact True.intro",
                    "goalActions": [
                        {
                            "startGoalId": "g1",
                            "results": [{"goal": {"goalId": "g2", "state": "⊢ True"}}],
                        }
                    ],
                }
            ],
        }
    )

    json_path, html_path = write_transition_debug(
        tmp_path / "transition-map.anything", movie
    )

    assert json_path.name == "transition-map.json"
    assert html_path.name == "transition-map.html"
    assert json.loads(json_path.read_text(encoding="utf-8"))["schemaVersion"] == 2
    html = html_path.read_text(encoding="utf-8")
    assert "Canonical ABI transition audit" in html
    assert "hyperedges" in html
    assert "presentation" in html
