from __future__ import annotations

from copy import deepcopy
import pytest

from proof_video.models import Movie, ProofStep, ProofTrace
from proof_video.proof.frontier import (
    temporal_frontier_issues,
)
from proof_video.prooftrace import ProofTraceValidationError, validate_trace
from proof_video.strict_audit import build_strict_audit


def _trace_json() -> dict:
    return {
        "schemaVersion": "2.0",
        "theoremName": "Demo.identity",
        "theoremLatex": "P \\implies P",
        "theoremLean": "P → P",
        "axioms": [],
        "finalStepId": 2,
        "validation": {"valid": True},
        "steps": [
            {
                "id": 0,
                "scopeId": "root/body",
                "parentScopeId": "root",
                "depth": 1,
                "kind": "assumption",
                "rule": "assume",
                "premises": [],
                "propositionLatex": "P",
                "propositionLean": "P",
                "displayLatex": "h : P",
                "proofFingerprint": "a",
                "propositionFingerprint": "p",
                "proofPath": "root.binder",
                "opensScope": "root/body",
                "kernelChecked": True,
                "usesLocalContext": True,
            },
            {
                "id": 1,
                "scopeId": "root/body",
                "parentScopeId": "root",
                "depth": 1,
                "kind": "reference",
                "rule": "assumption",
                "premises": [0],
                "propositionLatex": "P",
                "propositionLean": "P",
                "proofFingerprint": "b",
                "propositionFingerprint": "p",
                "proofPath": "root.body",
                "kernelChecked": True,
                "usesLocalContext": True,
            },
            {
                "id": 2,
                "scopeId": "root",
                "parentScopeId": None,
                "depth": 0,
                "kind": "introduction",
                "rule": "implies-introduction",
                "premises": [0, 1],
                "propositionLatex": "P \\implies P",
                "propositionLean": "P → P",
                "proofFingerprint": "c",
                "propositionFingerprint": "pp",
                "proofPath": "root",
                "closesScope": "root/body",
                "kernelChecked": True,
                "usesLocalContext": True,
            },
        ],
    }


def test_valid_fitch_discharge_becomes_movie() -> None:
    movie = Movie.from_json(_trace_json())
    assert movie.proof_trace is not None
    assert movie.proof_trace.valid
    # Strict mode retains both the assumption reference and the certified
    # implication-introduction packaging instead of hiding the final rule.
    assert [frame.tactic for frame in movie.frames] == [
        "assumption",
        "implies-introduction",
    ]
    audit = build_strict_audit(movie)
    assert audit["valid"]
    assert audit["summary"]["renderedInferenceSteps"] == 2
    assert audit["summary"]["premiseCoverageFailures"] == 0


def test_forall_instantiation_metadata_round_trips_from_checked_trace() -> None:
    raw_step = deepcopy(_trace_json()["steps"][1])
    raw_step.update(
        {
            "rule": "forall-elimination",
            "instantiationBinderName": "x",
            "instantiationValueLatex": r"2 \cdot f(x)",
            "instantiationValueLean": "2 * f x",
        }
    )
    step = ProofStep.from_json(raw_step)
    assert step.instantiation_binder_name == "x"
    assert step.instantiation_value_latex == r"2 \cdot f(x)"
    assert step.instantiation_value_lean == "2 * f x"


def test_trace_22_requires_certified_forall_instantiation_argument() -> None:
    raw = _trace_json()
    raw["schemaVersion"] = "2.2"
    raw["steps"][1]["rule"] = "forall-elimination"
    missing = validate_trace(ProofTrace.from_json(raw))
    assert not missing.valid
    assert any("no certified instantiation" in error for error in missing.errors)

    raw["steps"][1].update(
        {
            "instantiationBinderName": "x",
            "instantiationValueLatex": "a",
            "instantiationValueLean": "a",
        }
    )
    assert validate_trace(ProofTrace.from_json(raw)).valid


def test_rigorous_timeline_stages_every_direct_premise_before_its_rule() -> None:
    trace = ProofTrace.from_json(_trace_json())
    states = trace.rigorous_states()

    for (source, source_context), (target, _target_context) in zip(
        states, states[1:], strict=False
    ):
        visible = {source.id, *(item.id for item in source_context)}
        assert set(target.premises) <= visible


def test_proved_fact_stays_visible_while_sibling_premise_is_built() -> None:
    def step(step_id: int, proposition: str, premises: list[int]) -> dict:
        return {
            "id": step_id,
            "scopeId": "root",
            "parentScopeId": None,
            "depth": 0,
            "kind": "theorem-application",
            "rule": "theorem-application",
            "premises": premises,
            "propositionLatex": proposition,
            "propositionLean": proposition,
            "displayLatex": proposition,
            "proofFingerprint": f"proof-{step_id}",
            "propositionFingerprint": f"prop-{step_id}",
            "proofPath": f"root.{step_id}",
            "kernelChecked": True,
            "usesLocalContext": True,
        }

    trace = ProofTrace.from_json(
        {
            "schemaVersion": "2.0",
            "theoremName": "Demo.live_frontier",
            "theoremLatex": "D",
            "theoremLean": "D",
            "axioms": [],
            "finalStepId": 3,
            "validation": {"valid": True},
            "steps": [
                step(0, "A", []),
                step(1, "B", []),
                step(2, "C", [1]),
                step(3, "D", [0, 2]),
            ],
        }
    )
    states = trace.rigorous_states()

    # A is already proved. It remains visible throughout the construction of
    # the sibling premise B -> C and disappears only after D consumes it.
    assert [item.id for item in states[1][1]] == [0]
    assert [item.id for item in states[2][1]] == [0]
    assert 0 not in {item.id for item in states[3][1]}


def test_promoted_conclusion_stays_above_new_branch_assumption() -> None:
    def step(
        step_id: int,
        proposition: str,
        kind: str,
        premises: list[int],
        scope: str,
        *,
        opens_scope: str | None = None,
    ) -> dict:
        return {
            "id": step_id,
            "scopeId": scope,
            "parentScopeId": scope.rpartition("/")[0] or None,
            "depth": scope.count("/"),
            "kind": kind,
            "rule": "assume" if kind == "assumption" else "theorem-application",
            "premises": premises,
            "propositionLatex": proposition,
            "propositionLean": proposition,
            "displayLatex": proposition,
            "binderName": f"h{step_id}" if kind == "assumption" else None,
            "proofFingerprint": f"proof-{step_id}",
            "propositionFingerprint": f"prop-{step_id}",
            "proofPath": f"root.{step_id}",
            "opensScope": opens_scope,
            "kernelChecked": True,
            "usesLocalContext": True,
        }

    trace = ProofTrace.from_json(
        {
            "schemaVersion": "2.0",
            "theoremName": "Demo.branch_order",
            "theoremLatex": "R",
            "theoremLean": "R",
            "axioms": [],
            "finalStepId": 4,
            "validation": {"valid": True},
            "steps": [
                step(0, "X", "assumption", [], "root/body", opens_scope="root/body"),
                step(1, "P \\lor Q", "theorem-application", [0], "root/body"),
                step(
                    2,
                    "Q",
                    "assumption",
                    [],
                    "root/body/branch",
                    opens_scope="root/body/branch",
                ),
                step(3, "S", "theorem-application", [2], "root/body/branch"),
                step(4, "R", "theorem-application", [1, 3], "root/body"),
            ],
        }
    )

    states = trace.rigorous_states()
    branch_context = states[1][1]
    assert [item.id for item in branch_context] == [0, 1, 2]


def test_live_frontier_deduplicates_equal_certified_propositions() -> None:
    def step(
        step_id: int, proposition: str, fingerprint: str, premises: list[int]
    ) -> dict:
        return {
            "id": step_id,
            "scopeId": "root",
            "parentScopeId": None,
            "depth": 0,
            "kind": "theorem-application",
            "rule": "theorem-application",
            "premises": premises,
            "propositionLatex": proposition,
            "propositionLean": proposition,
            "displayLatex": proposition,
            "proofFingerprint": f"proof-{step_id}",
            "propositionFingerprint": fingerprint,
            "proofPath": f"root.{step_id}",
            "kernelChecked": True,
            "usesLocalContext": True,
        }

    trace = ProofTrace.from_json(
        {
            "schemaVersion": "2.0",
            "theoremName": "Demo.deduplicate",
            "theoremLatex": "C",
            "theoremLean": "C",
            "axioms": [],
            "finalStepId": 3,
            "validation": {"valid": True},
            "steps": [
                step(0, "A", "same-a", []),
                step(1, "A", "same-a", []),
                step(2, "B", "b", []),
                step(3, "C", "c", [0, 1, 2]),
            ],
        }
    )
    states = trace.rigorous_states()

    assert [item.proposition_latex for item in states[2][1]] == ["A"]
    movie = Movie.from_json(
        {
            **{
                "schemaVersion": trace.schema_version,
                "theoremName": trace.theorem_name,
                "theoremLatex": trace.theorem_latex,
                "theoremLean": trace.theorem_lean,
                "axioms": list(trace.axioms),
                "finalStepId": trace.final_step_id,
                "validation": {"valid": True},
            },
            "steps": [
                step(0, "A", "same-a", []),
                step(1, "A", "same-a", []),
                step(2, "B", "b", []),
                step(3, "C", "c", [0, 1, 2]),
            ],
        }
    )
    assert build_strict_audit(movie)["summary"]["premiseCoverageFailures"] == 0


def test_proof_definition_appears_only_after_its_value_is_completed() -> None:
    def step(
        step_id: int,
        proposition: str,
        kind: str,
        *,
        premises: list[int] | None = None,
        scope: str = "root",
        opens_scope: str | None = None,
    ) -> dict:
        return {
            "id": step_id,
            "scopeId": scope,
            "parentScopeId": "root" if scope != "root" else None,
            "depth": 1 if scope != "root" else 0,
            "kind": kind,
            "rule": "let-proof"
            if kind == "proof-definition"
            else "theorem-application",
            "premises": premises or [],
            "propositionLatex": proposition,
            "propositionLean": proposition,
            "displayLatex": proposition,
            "proofFingerprint": f"proof-{step_id}",
            "propositionFingerprint": f"prop-{proposition}",
            "proofPath": f"root.{step_id}",
            "opensScope": opens_scope,
            "kernelChecked": True,
            "usesLocalContext": True,
            "semanticNodes": [
                {
                    "id": f"proof-step-{step_id}/0",
                    "kind": "fvar",
                    "identity": f"prop:{proposition}",
                    "fingerprint": f"prop-{proposition}",
                    "path": "0",
                    "latexSpans": [{"start": 0, "end": len(proposition)}],
                }
            ],
        }

    raw = {
        "schemaVersion": "2.0",
        "theoremName": "Demo.temporal_let",
        "theoremLatex": "Q",
        "theoremLean": "Q",
        "axioms": [],
        "finalStepId": 2,
        "validation": {"valid": True},
        "steps": [
            step(0, "P", "theorem-application"),
            step(
                1,
                "P",
                "proof-definition",
                scope="root/let",
                opens_scope="root/let",
            ),
            step(2, "Q", "theorem-application", premises=[1], scope="root/let"),
        ],
    }

    trace = ProofTrace.from_json(raw)
    states = trace.rigorous_states(render_only=True)
    assert [item.id for item in states[0][1]] == []
    assert [item.id for item in states[1][1]] == [1]

    movie = Movie.from_json(raw)
    transition = movie.frames[1].display_goals[0].semantic_transition
    assert transition is not None
    assert any(
        edge.source_node_id == "proof-step-0/0"
        and edge.target_node_id == "proof-context-1/proof-step-1/0"
        and edge.reason == "verified-proof-definition-storage"
        for edge in transition.edges
    )
    audit = build_strict_audit(movie)
    assert audit["valid"]
    assert audit["summary"]["temporalFrontierFailures"] == 0


def test_named_proof_definition_shadows_the_older_local_declaration() -> None:
    def step(
        step_id: int,
        proposition: str,
        kind: str,
        *,
        scope: str,
        parent_scope: str | None,
        opens_scope: str | None = None,
        binder_name: str | None = None,
        premises: list[int] | None = None,
    ) -> dict:
        return {
            "id": step_id,
            "scopeId": scope,
            "parentScopeId": parent_scope,
            "depth": scope.count("/"),
            "kind": kind,
            "rule": "let-proof"
            if kind == "proof-definition"
            else "theorem-application",
            "premises": premises or [],
            "propositionLatex": proposition,
            "propositionLean": proposition,
            "displayLatex": proposition,
            "proofFingerprint": f"proof-{step_id}",
            "propositionFingerprint": f"prop-{step_id}",
            "proofPath": f"root.{step_id}",
            "opensScope": opens_scope,
            "binderName": binder_name,
            "kernelChecked": True,
            "usesLocalContext": True,
            "semanticNodes": [],
        }

    trace = ProofTrace.from_json(
        {
            "schemaVersion": "2.0",
            "theoremName": "Demo.replace",
            "theoremLatex": "R",
            "theoremLean": "R",
            "axioms": [],
            "finalStepId": 3,
            "validation": {"valid": True},
            "steps": [
                step(
                    0,
                    "P",
                    "assumption",
                    scope="root",
                    parent_scope=None,
                    opens_scope="root/body",
                    binder_name="h",
                ),
                step(
                    1,
                    "Q",
                    "theorem-application",
                    scope="root/body",
                    parent_scope="root",
                ),
                step(
                    2,
                    "Q",
                    "proof-definition",
                    scope="root/body",
                    parent_scope="root/body",
                    opens_scope="root/body/replaced",
                    binder_name="h",
                ),
                step(
                    3,
                    "R",
                    "theorem-application",
                    scope="root/body/replaced",
                    parent_scope="root/body",
                    premises=[2],
                ),
            ],
        }
    )

    states = trace.rigorous_states(render_only=True)
    assert [item.id for item in states[0][1]] == [0]
    assert [item.id for item in states[1][1]] == [2]


def test_temporal_frontier_audit_rejects_a_future_context_row() -> None:
    trace = ProofTrace.from_json(_trace_json())
    issues = temporal_frontier_issues(((trace.steps[0], (trace.steps[1],)),))
    assert len(issues) == 1
    assert "before that row has been completed" in issues[0].message()


def test_administrative_certificate_is_contracted_without_losing_premise() -> None:
    def step(
        step_id: int,
        proposition: str,
        premises: list[int],
        *,
        theorem: str,
    ) -> dict:
        return {
            "id": step_id,
            "scopeId": "root/body",
            "parentScopeId": "root",
            "depth": 1,
            "kind": "theorem-application",
            "rule": "theorem-application",
            "premises": premises,
            "propositionLatex": proposition,
            "propositionLean": proposition,
            "displayLatex": proposition,
            "theoremName": theorem,
            "proofFingerprint": f"proof-{step_id}",
            "propositionFingerprint": f"prop-{step_id}",
            "proofPath": f"root.body.{step_id}",
            "kernelChecked": True,
            "usesLocalContext": True,
        }

    raw = {
        "schemaVersion": "2.0",
        "theoremName": "Demo.contracted",
        "theoremLatex": "R",
        "theoremLean": "R",
        "axioms": [],
        "finalStepId": 3,
        "validation": {"valid": True},
        "steps": [
            {
                **step(0, "A", [], theorem="Axiom.a"),
                "kind": "assumption",
                "rule": "assume",
                "binderName": "h",
                "opensScope": "root/body",
            },
            step(1, "P", [0], theorem="Demo.p"),
            step(2, "C", [1], theorem="Mathlib.Tactic.Certificate.internal"),
            step(3, "R", [2], theorem="Demo.result"),
        ],
    }

    trace = ProofTrace.from_json(raw)
    assert [item.id for item in trace.render_steps()] == [1, 3]
    assert trace.rendered_premise_map()[3] == (1,)

    movie = Movie.from_json(raw)
    audit = build_strict_audit(movie)
    assert [frame.index for frame in movie.frames] == [1, 3]
    assert audit["valid"]
    assert audit["summary"]["kernelInferenceSteps"] == 3
    assert audit["summary"]["hiddenAdministrativeSteps"] == 1
    assert audit["summary"]["renderedInferenceSteps"] == 2
    assert audit["summary"]["premiseCoverageFailures"] == 0


def test_future_dependency_is_rejected() -> None:
    raw = _trace_json()
    raw["steps"][1]["premises"] = [2]
    with pytest.raises(ProofTraceValidationError, match="non-earlier"):
        Movie.from_json(raw)


def test_sibling_scope_dependency_is_rejected() -> None:
    raw = _trace_json()
    sibling = deepcopy(raw["steps"][0])
    sibling.update(
        id=1,
        scopeId="root/sibling",
        opensScope="root/sibling",
        proofFingerprint="sibling",
    )
    raw["steps"].insert(1, sibling)
    raw["steps"][2]["id"] = 2
    raw["steps"][2]["premises"] = [1]
    raw["steps"][3]["id"] = 3
    raw["steps"][3]["premises"] = [0, 2]
    raw["finalStepId"] = 3
    trace = ProofTrace.from_json(raw)
    report = validate_trace(trace)
    assert not report.valid
    assert any("out-of-scope" in error for error in report.errors)
