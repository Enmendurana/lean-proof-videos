from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from proof_video.cli import _restore_windows_path
from proof_video.lean_export import export_trace


ROOT = Path(__file__).resolve().parents[1]
LEAN_FIXTURE = ROOT / "Input" / "ExtractorTacticFixtures.lean"
THEOREM = "ExtractorTacticFixtures.tacticAdapters"
TRACE_ARTIFACT = ROOT / ".pytest_cache" / "extractor-tactic-fixtures-trace.json"


@pytest.fixture(scope="module")
def tactic_trace() -> dict:
    supplied_trace = os.environ.get("EXTRACTOR_TACTIC_FIXTURE_TRACE")
    if supplied_trace:
        return json.loads(Path(supplied_trace).read_text(encoding="utf-8"))
    if shutil.which("lake") is None:
        pytest.skip("Lean integration fixture requires lake")
    _restore_windows_path()
    trace = export_trace(ROOT, LEAN_FIXTURE, THEOREM, "tactic")
    TRACE_ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    pending = TRACE_ARTIFACT.with_suffix(".tmp")
    pending.write_text(json.dumps(trace, ensure_ascii=False), encoding="utf-8")
    pending.replace(TRACE_ARTIFACT)
    return trace


def _action(trace: dict, exact_text: str) -> dict:
    return next(
        action for action in trace["actions"] if action["tacticText"] == exact_text
    )


def _action_containing(trace: dict, text: str) -> dict:
    return next(action for action in trace["actions"] if text in action["tacticText"])


def _single_result(action: dict) -> dict:
    assert len(action["goalActions"]) == 1
    results = action["goalActions"][0]["results"]
    assert len(results) == 1
    result = results[0]
    assert set(result["latexIndexMaps"]) == {"s1_to_s2", "s2_to_s1"}
    return result


def _semantic(result: dict) -> dict:
    transition = result["semanticTransition"]
    assert transition["sourceNodes"]
    assert transition["targetNodes"]
    assert transition["edges"]
    return transition


def _assert_goal_diff_matches_result(action: dict, result: dict) -> None:
    transition = result["semanticTransition"]
    evidence = transition.get("goalDiff")
    assert evidence is not None
    assert evidence["sourceGoalId"] == action["goalActions"][0]["startGoalId"]
    assert evidence["targetGoalId"] == result["goal"]["goalId"]
    assert isinstance(evidence["sourceChangedPaths"], list)
    assert isinstance(evidence["targetChangedPaths"], list)


def _latex_state(goal: dict) -> str:
    context = []
    for hypothesis in goal.get("latexContext", []):
        name = hypothesis["name"].replace("_", r"\_")
        context.append(f"{name} \\;:\\; {hypothesis['latex']}")
    return "\n".join(context + [rf"\vdash\;{goal.get('latexTarget', '')}"])


def _goals_by_id(trace: dict) -> dict[str, dict]:
    goals = {trace["startGoal"]["goalId"]: trace["startGoal"]}
    for action in trace["actions"]:
        # ABI 5 action frontiers are the authoritative observation timeline.
        # In particular a resumed continuation can be a GoalAction source
        # without first appearing as a legacy result of the preceding action.
        for goal in (*action.get("beforeState", ()), *action.get("afterState", ())):
            goals[goal["goalId"]] = goal
        for goal_action in action["goalActions"]:
            for result in goal_action["results"]:
                goals[result["goal"]["goalId"]] = result["goal"]
    return goals


@pytest.mark.parametrize(
    ("tactic", "target_tail", "adapter", "proof_kind"),
    [
        ("rw [hab]", "⊢ b + 1 = c", "rewrite", "equality-transport"),
        ("simp only [Nat.add_zero]", "⊢ a = b", "simp", "goal-reduction"),
        ("subst b", "⊢ a + a = a + a", "subst", "goal-reduction"),
        ("change a + a = a + a", "⊢ a + a = a + a", "change", "goal-reduction"),
        ("show b + b = b + b", "⊢ b + b = b + b", "change", "goal-reduction"),
        (
            "ring_nf at hRingNormal ⊢",
            "⊢ 1 + x * 2 + x ^ 2 = 0",
            "ring",
            "equality-transport",
        ),
    ],
)
def test_in_place_tactics_emit_one_mapped_successor(
    tactic_trace: dict,
    tactic: str,
    target_tail: str,
    adapter: str,
    proof_kind: str,
) -> None:
    action = _action(tactic_trace, tactic)
    result = _single_result(action)
    assert result["goal"]["state"].splitlines()[-1] == target_tail
    transition = _semantic(result)
    assert transition["adapter"] == adapter
    assert transition["proofKind"] == proof_kind
    assert transition["proofFingerprint"]
    assert transition["proofTerm"]
    assert result["goal"]["goalId"] in transition["proofDescendants"]
    _assert_goal_diff_matches_result(action, result)
    assert {edge["reason"] for edge in transition["edges"]} <= {
        "same-fvar",
        "same-identity",
        "defeq-normal-form",
        "verified-rewrite-position",
        "verified-substitution-position",
        "verified-definitional-change",
        "verified-intro-body",
        "verified-intro-binder",
        "verified-intro-binder-punctuation",
        "verified-intro-binder-use",
    }


def test_linarith_is_a_closing_assignment(tactic_trace: dict) -> None:
    action = _action(tactic_trace, "linarith")
    assert [goal_action["results"] for goal_action in action["goalActions"]] == [[]]


def test_intro_preserves_the_body_and_moves_the_binder_into_context(
    tactic_trace: dict,
) -> None:
    result = _single_result(_action(tactic_trace, "intro z"))
    transition = _semantic(result)
    assert transition["adapter"] == "intro"
    source = {node["id"]: node for node in transition["sourceNodes"]}
    target = {node["id"]: node for node in transition["targetNodes"]}

    binder_edges = [
        edge
        for edge in transition["edges"]
        if edge["reason"] == "verified-intro-binder"
    ]
    assert len(binder_edges) == 1
    binder_source = source[binder_edges[0]["sourceNodeId"]]
    binder_target = target[binder_edges[0]["targetNodeId"]]
    assert binder_source["latexSpans"]
    assert binder_target["latexSpans"]
    assert binder_source["kind"] == binder_target["kind"] == "declaration"

    body_edges = [
        edge for edge in transition["edges"] if edge["reason"] == "verified-intro-body"
    ]
    assert body_edges
    assert any(
        len(source[edge["sourceNodeId"]]["latexSpans"]) == 1
        and len(target[edge["targetNodeId"]]["latexSpans"]) == 1
        for edge in body_edges
    )
    assert any(
        edge["reason"] == "verified-intro-binder-use" for edge in transition["edges"]
    )


def test_intro_maps_every_quantifier_colon_to_its_own_context_declaration(
    tactic_trace: dict,
) -> None:
    action = _action(tactic_trace, "intro u v")
    result = _single_result(action)
    transition = _semantic(result)
    source = {node["id"]: node for node in transition["sourceNodes"]}
    target = {node["id"]: node for node in transition["targetNodes"]}
    goals = _goals_by_id(tactic_trace)
    source_latex = _latex_state(goals[action["goalActions"][0]["startGoalId"]])
    target_latex = _latex_state(result["goal"])

    punctuation_edges = [
        edge
        for edge in transition["edges"]
        if edge["reason"] == "verified-intro-binder-punctuation"
    ]
    assert len(punctuation_edges) == 2
    assert len({edge["sourceNodeId"] for edge in punctuation_edges}) == 2
    assert len({edge["targetNodeId"] for edge in punctuation_edges}) == 2
    for edge in punctuation_edges:
        source_node = source[edge["sourceNodeId"]]
        target_node = target[edge["targetNodeId"]]
        assert source_node["path"].endswith(".binder.colon")
        assert target_node["path"].endswith(".colon")
        source_span = source_node["latexSpans"][0]
        target_span = target_node["latexSpans"][0]
        assert source_latex[source_span["start"] : source_span["end"]] == ":"
        assert target_latex[target_span["start"] : target_span["end"]] == ":"


def test_unchanged_quantifier_symbols_keep_their_semantic_identity(
    tactic_trace: dict,
) -> None:
    transition = _semantic(_single_result(_action(tactic_trace, "rw [hab]")))
    source = {node["id"]: node for node in transition["sourceNodes"]}
    target = {node["id"]: node for node in transition["targetNodes"]}
    edges = {
        (edge["sourceNodeId"], edge["targetNodeId"]): edge
        for edge in transition["edges"]
    }
    source_quantifiers = [
        node
        for node in source.values()
        if node["kind"] == "quantifier-symbol" and node["latexSpans"]
    ]

    assert {node["identity"].split(":", 1)[0] for node in source_quantifiers} == {
        "quantifier"
    }
    assert len(source_quantifiers) >= 2
    for source_node in source_quantifiers:
        matching_targets = [
            node
            for node in target.values()
            if node["identity"] == source_node["identity"]
        ]
        assert len(matching_targets) == 1
        target_node = matching_targets[0]
        edge = edges[(source_node["id"], target_node["id"])]
        assert edge["reason"] == "same-identity"


@pytest.mark.parametrize(
    "tactic_fragment",
    ["cases h with", "constructor", "induction n with"],
)
def test_branching_tactics_emit_two_distinct_successors(
    tactic_trace: dict, tactic_fragment: str
) -> None:
    action = _action_containing(tactic_trace, tactic_fragment)
    assert len(action["goalActions"]) == 1
    results = action["goalActions"][0]["results"]
    assert len(results) == 2
    assert len({result["goal"]["goalId"] for result in results}) == 2
    expected_adapter = tactic_fragment.split()[0]
    assert all(_semantic(result)["adapter"] == expected_adapter for result in results)
    # Nested case/induction branches can already be assigned by the enclosing
    # TacticInfo's mctxAfter. The extractor still records the actual descendant
    # set (possibly empty) instead of inferring it from goalsAfter.
    assert all(
        isinstance(_semantic(result)["proofDescendants"], list) for result in results
    )
    assert all(_semantic(result)["proofTerm"] for result in results)


def test_term_calc_has_no_separate_tactic_goal(tactic_trace: dict) -> None:
    action = _action_containing(tactic_trace, "calc a + b = b + a")
    assert action["tacticText"].startswith("have hCalc")
    result = _single_result(action)
    assert result["goal"]["state"].splitlines()[-1] == "⊢ True"
    transition = _semantic(result)
    # A term-mode calc is contained in the outer `have`, so its adapter is
    # intentionally generic even though its source text contains `calc`.
    assert transition["adapter"] == "generic"
    assert transition["proofKind"] == "goal-reduction"
    assert not any(
        action["tacticText"].lstrip().startswith("calc")
        for action in tactic_trace["actions"]
    )


def test_semantic_node_spans_are_inside_their_canonical_latex_states(
    tactic_trace: dict,
) -> None:
    goals = _goals_by_id(tactic_trace)
    for action in tactic_trace["actions"]:
        for goal_action in action["goalActions"]:
            source_latex = _latex_state(goals[goal_action["startGoalId"]])
            for result in goal_action["results"]:
                transition = _semantic(result)
                target_latex = _latex_state(result["goal"])
                for nodes, latex in (
                    (transition["sourceNodes"], source_latex),
                    (transition["targetNodes"], target_latex),
                ):
                    for node in nodes:
                        for span in node["latexSpans"]:
                            assert 0 <= span["start"] < span["end"] <= len(latex)
                            fragment = latex[span["start"] : span["end"]]
                            if (
                                node["kind"] == "fvar"
                                and len(fragment) == 1
                                and fragment.isalnum()
                            ):
                                previous = (
                                    latex[span["start"] - 1] if span["start"] else ""
                                )
                                following = (
                                    latex[span["end"]]
                                    if span["end"] < len(latex)
                                    else ""
                                )
                                assert previous != "\\"
                                assert not previous.isalnum()
                                assert not following.isalnum()


def test_duplicate_occurrences_have_unambiguous_semantic_mappings(
    tactic_trace: dict,
) -> None:
    result = _single_result(_action(tactic_trace, "rw [hfg]"))
    assert result["goal"]["latexTarget"] == "g(a) + g(a) = 0"
    transition = _semantic(result)
    assert transition["adapter"] == "rewrite"
    assert transition["proofKind"] == "equality-transport"

    source_nodes = {node["id"]: node for node in transition["sourceNodes"]}
    target_nodes = {node["id"]: node for node in transition["targetNodes"]}
    source_latex = _latex_state(
        _goals_by_id(tactic_trace)[
            _action(tactic_trace, "rw [hfg]")["goalActions"][0]["startGoalId"]
        ]
    )
    target_latex = _latex_state(result["goal"])

    def occurrences(nodes: dict[str, dict], latex: str, glyph: str) -> list[dict]:
        return [
            node
            for node in nodes.values()
            if node["kind"] == "fvar"
            and node["latexSpans"]
            and any(
                latex[span["start"] : span["end"]] == glyph
                for span in node["latexSpans"]
            )
            and node["latexSpans"][0]["start"] >= latex.rfind(r"\vdash\;")
        ]

    source_f = occurrences(source_nodes, source_latex, "f")
    target_g = occurrences(target_nodes, target_latex, "g")
    assert len(source_f) == len(target_g) == 2
    assert len({node["id"] for node in source_f}) == 2
    assert len({node["latexSpans"][0]["start"] for node in source_f}) == 2
    assert {node["path"] for node in source_f} == {node["path"] for node in target_g}

    rewritten_functions = [
        edge
        for edge in transition["edges"]
        if edge["sourceNodeId"] in {node["id"] for node in source_f}
        and edge["targetNodeId"] in {node["id"] for node in target_g}
    ]
    assert len(rewritten_functions) == 2
    assert {edge["reason"] for edge in rewritten_functions} == {
        "verified-rewrite-position"
    }
    assert len({edge["sourceNodeId"] for edge in rewritten_functions}) == 2
    assert len({edge["targetNodeId"] for edge in rewritten_functions}) == 2

    source_a = {node["id"] for node in occurrences(source_nodes, source_latex, "a")}
    target_a = {node["id"] for node in occurrences(target_nodes, target_latex, "a")}
    duplicate_edges = [
        edge
        for edge in transition["edges"]
        if edge["sourceNodeId"] in source_a and edge["targetNodeId"] in target_a
    ]
    assert len(duplicate_edges) == 2
    assert {edge["reason"] for edge in duplicate_edges} == {"same-fvar"}
    assert len({edge["sourceNodeId"] for edge in duplicate_edges}) == 2
    assert len({edge["targetNodeId"] for edge in duplicate_edges}) == 2


def test_tactic_explanations_are_bound_to_the_elaborated_assignment(
    tactic_trace: dict,
) -> None:
    for action in tactic_trace["actions"]:
        for goal_action in action["goalActions"]:
            explanation = goal_action["explanation"]
            assert (
                explanation["certificateFingerprint"] == goal_action["proofFingerprint"]
            )
            assert explanation["certificateKind"] == goal_action["proofKind"]
            assert isinstance(explanation["premiseIds"], list)
            assert isinstance(explanation["supportingConstants"], list)
            for result in goal_action["results"]:
                transition = result["semanticTransition"]
                assert set(transition["proofPremises"]) == set(
                    explanation["premiseIds"]
                )
                assert set(transition["proofConstants"]) == set(
                    explanation["supportingConstants"]
                )
