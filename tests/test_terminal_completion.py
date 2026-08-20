from __future__ import annotations

from copy import deepcopy

from proof_video.models import Frame, Goal, Movie
from proof_video.proof.completion import CompletionStatus, TerminalCompletion
from proof_video.remotion_export import build_remotion_timeline
from proof_video.scene import _certified_terminal_frame


_CAPABILITIES = ["canonical-proof-state", "ordered-action-frontiers"]


def _goal(goal_id: str, proposition: str) -> dict:
    return {
        "goalId": goal_id,
        "state": f"⊢ {proposition}",
        "latexTarget": proposition,
        "latexContext": [],
    }


def _source_movie(*, after: list[dict] | None) -> dict:
    start = _goal("g0", "P")
    action = {
        "tacticText": "exact h",
        "beforeState": [start],
        "focusBefore": ["g0"],
        "focusAfter": [],
        "goalLineage": [
            {
                "sourceGoalIds": ["g0"],
                "targetGoalIds": [],
                "relation": "close",
            }
        ],
        "goalActions": [],
    }
    if after is not None:
        action["afterState"] = after
    return {
        "canonicalAbi": 5,
        "capabilities": _CAPABILITIES,
        "theoremName": "Demo.main",
        "startGoal": start,
        "actions": [action],
    }


def _hybrid(chapters: list[dict]) -> dict:
    return {
        "schemaVersion": "3.0",
        "theoremName": "Demo.main",
        "chapters": chapters,
        "validation": {
            "valid": True,
            "dependencyOrderValid": True,
            "allChaptersKernelChecked": True,
            "noSorry": True,
            "errors": [],
        },
    }


def _chapter(theorem: str, *, is_main: bool, after: list[dict] | None) -> dict:
    movie = _source_movie(after=after)
    movie["theoremName"] = theorem
    return {
        "id": 1 if is_main else 0,
        "theoremName": theorem,
        "dependencies": ["Demo.seed"] if is_main else [],
        "proofFingerprint": f"proof:{theorem}",
        "axioms": [],
        "isMain": is_main,
        "validation": {
            "valid": True,
            "kernelChecked": True,
            "noSorry": True,
            "errors": [],
        },
        "movie": movie,
    }


def test_empty_authoritative_after_frontier_certifies_qed() -> None:
    movie = Movie.from_json(_source_movie(after=[]))

    assert movie.certified_closed
    visible = tuple(frame for frame in movie.semantic_frames() if frame.display_goals)
    assert visible[-1].terminal_completion is not None
    assert visible[-1].terminal_completion.certified_closed
    assert _certified_terminal_frame(visible[-1])
    timeline = build_remotion_timeline(movie, fps=30)
    assert timeline["showQed"] is True
    assert timeline["terminalCompletion"]["status"] == "certified-closed"


def test_open_or_missing_terminal_frontier_never_certifies_qed() -> None:
    open_movie = Movie.from_json(_source_movie(after=[_goal("g1", "Q")]))
    truncated_movie = Movie.from_json(_source_movie(after=None))

    assert open_movie.terminal_completion.status is CompletionStatus.OPEN
    assert not open_movie.certified_closed
    assert not truncated_movie.certified_closed
    assert not _certified_terminal_frame(open_movie.semantic_frames()[-1])
    assert not _certified_terminal_frame(truncated_movie.semantic_frames()[-1])
    assert build_remotion_timeline(open_movie)["showQed"] is False
    assert build_remotion_timeline(truncated_movie)["showQed"] is False


def test_head_preview_of_certified_proof_does_not_inherit_terminal_qed() -> None:
    frames = tuple(
        Frame(index, "step", (Goal(f"g{index}", "", latex_target=str(index)),))
        for index in range(40)
    )
    movie = Movie(
        "Demo.preview",
        frames,
        terminal_completion=TerminalCompletion(
            status=CompletionStatus.CERTIFIED_CLOSED,
            source="test-certificate",
        ),
    )

    head = build_remotion_timeline(movie, fps=30, preview_seconds=20)
    tail = build_remotion_timeline(movie, fps=30, preview_tail_seconds=20)

    assert head["showQed"] is False
    assert head["terminalCompletion"]["status"] == "unknown"
    assert tail["showQed"] is True


def test_only_closed_kernel_certified_main_chapter_can_close_hybrid_movie() -> None:
    dependency = _chapter("Demo.seed", is_main=False, after=[])
    main = _chapter("Demo.main", is_main=True, after=[])
    manifest = _hybrid([dependency, main])

    movie = Movie.from_json(manifest)
    assert movie.certified_closed
    semantic = tuple(frame for frame in movie.semantic_frames() if frame.display_goals)
    assert semantic[0].terminal_completion is None
    assert semantic[-1].terminal_completion is not None
    assert semantic[-1].terminal_completion.certified_closed

    partial = Movie.from_hybrid_chapters(manifest, [deepcopy(dependency)])
    assert not partial.certified_closed
    assert build_remotion_timeline(partial)["showQed"] is False

    main_only = Movie.from_hybrid_chapters(manifest, [deepcopy(main)])
    assert not main_only.certified_closed
    assert main_only.terminal_completion.source == "hybrid-chapter-selection-is-partial"

    open_main = deepcopy(main)
    open_main["movie"] = _source_movie(after=[_goal("g1", "still open")])
    open_manifest = _hybrid([dependency, open_main])
    assert not Movie.from_json(open_manifest).certified_closed
