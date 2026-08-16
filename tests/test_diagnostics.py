from proof_video.diagnostics import build_transition_map
from proof_video.models import Movie


def test_legacy_character_edges_are_not_reported_as_semantic() -> None:
    movie = Movie.from_json(
        {
            "theoremName": "Demo.maps",
            "startGoal": {"goalId": "g1", "state": "goal", "latexTarget": "AB"},
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
                                        "state": "goal",
                                        "latexTarget": "AC",
                                    },
                                    "latexIndexMaps": {
                                        "s1_to_s2": [0, None, 2],
                                        "s2_to_s1": [0, None, 2],
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )

    diagnostic = build_transition_map(movie)
    block = diagnostic["transitions"][0]["blocks"][0]

    assert block["reason"] == "same_lineage"
    assert block["mappingMode"] == "legacy_character_map"
    assert block["edges"] == [
        {
            "sourceNodeId": "char:0",
            "targetNodeId": "char:0",
            "reason": "legacy_character_map",
            "confidence": 1.0,
        },
        {
            "sourceNodeId": "char:2",
            "targetNodeId": "char:2",
            "reason": "legacy_character_map",
            "confidence": 1.0,
        },
    ]
    assert block["unmappedSourceIds"] == ["char:1"]
    assert block["unmappedTargetIds"] == ["char:1"]


def test_semantic_transition_edges_and_unmapped_ids_are_preserved() -> None:
    movie = Movie.from_json(
        {
            "theoremName": "Demo.semantic",
            "startGoal": {"goalId": "g1", "state": "old", "latexTarget": "a+b"},
            "actions": [{
                "tacticText": "ring",
                "goalActions": [{
                    "startGoalId": "g1",
                    "results": [{
                        "goal": {"goalId": "g2", "state": "new", "latexTarget": "b+a"},
                        "latexIndexMaps": {
                            "s1_to_s2": [0, 1, 2],
                            "s2_to_s1": [0, 1, 2],
                        },
                        "semanticTransition": {
                            "proofKind": "ring",
                            "adapter": "expr-tree-v1",
                            "sourceNodes": [
                                {"id": "s-add", "kind": "add"},
                                {"id": "s-unmapped", "kind": "term"},
                            ],
                            "targetNodes": [
                                {"id": "t-add", "kind": "add"},
                                {"id": "t-unmapped", "kind": "term"},
                            ],
                            "edges": [{
                                "sourceNodeId": "s-add",
                                "targetNodeId": "t-add",
                                "reason": "same_operator",
                                "confidence": 0.98,
                            }],
                        },
                    }],
                }],
            }],
        }
    )

    block = build_transition_map(movie)["transitions"][0]["blocks"][0]

    assert block["mappingMode"] == "semantic_transition"
    assert block["proofKind"] == "ring"
    assert block["adapter"] == "expr-tree-v1"
    assert block["edges"] == [{
        "sourceNodeId": "s-add",
        "targetNodeId": "t-add",
        "reason": "same_operator",
        "confidence": 0.98,
    }]
    assert block["unmappedSourceIds"] == ["s-unmapped"]
    assert block["unmappedTargetIds"] == ["t-unmapped"]


def test_returning_branch_reports_similarity_and_legacy_fallback() -> None:
    movie = Movie.from_json(
        {
            "theoremName": "Demo.branch",
            "startGoal": {
                "goalId": "g1",
                "state": "goal",
                "latexTarget": "A",
                "latexContext": [{"name": "x", "latex": "X"}],
            },
            "actions": [
                {
                    "tacticText": "switch",
                    "goalActions": [
                        {
                            "startGoalId": "missing",
                            "results": [
                                {
                                    "goal": {
                                        "goalId": "g2",
                                        "state": "goal",
                                        "latexTarget": "B",
                                        "latexContext": [{"name": "x", "latex": "X"}],
                                    },
                                    "latexIndexMaps": {
                                        "s1_to_s2": [0],
                                        "s2_to_s1": [0],
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )

    block = build_transition_map(movie)["transitions"][0]["blocks"][0]

    assert block["reason"] == "dormant_branch_similarity"
    assert block["confidence"] == 1.0
    assert block["mappingMode"] == "legacy_shape_fallback"
    assert block["edges"] == []
    assert "lineage_changed" in block["fallbackReason"]


def test_missing_latex_map_is_explicit_legacy_fallback() -> None:
    movie = Movie.from_json(
        {
            "theoremName": "Demo.legacy",
            "startGoal": {"goalId": "g1", "state": "goal A"},
            "actions": [
                {
                    "tacticText": "change",
                    "goalActions": [
                        {
                            "startGoalId": "g1",
                            "results": [
                                {"goal": {"goalId": "g2", "state": "goal B"}}
                            ],
                        }
                    ],
                }
            ],
        }
    )

    block = build_transition_map(movie)["transitions"][0]["blocks"][0]

    assert block["mappingMode"] == "legacy_shape_fallback"
    assert block["fallbackReason"] == "latex_index_maps_missing"
    assert block["source"]["notationSource"] == "legacy_lean_state"
