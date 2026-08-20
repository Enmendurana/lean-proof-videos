from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from proof_video import render_review
from proof_video.render_review import (
    build_review_plan,
    extract_selected_frames,
    review_render,
    write_contact_sheets,
)


def _timeline() -> dict[str, object]:
    return {
        "fps": 30,
        "durationInFrames": 100,
        "states": [
            {"tactic": "initial"},
            {"tactic": "intro"},
            {"tactic": "rw"},
        ],
        "transitions": [
            {
                "fromState": 0,
                "toState": 1,
                "startFrame": 10,
                "durationFrames": 9,
            },
            {
                "fromState": 1,
                "toState": 2,
                "startFrame": 19,
                "durationFrames": 81,
            },
        ],
    }


def test_review_plan_uses_exact_transition_boundaries() -> None:
    plan = build_review_plan(_timeline())

    assert plan.fps == 30
    assert [sample.frame for sample in plan.transitions[0].samples] == [10, 14, 19]
    assert [sample.frame for sample in plan.transitions[1].samples] == [19, 59, 99]
    assert plan.selected_frames == (10, 14, 19, 59, 99)
    assert plan.transitions[0].tactic == "intro"
    assert plan.transitions[1].samples[-1].time_seconds == pytest.approx(3.3)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("fps", 0, "fps must be positive"),
        ("durationInFrames", True, "durationInFrames must be an integer"),
    ],
)
def test_review_plan_rejects_invalid_timeline_roots(
    field: str, value: object, message: str
) -> None:
    timeline = _timeline()
    timeline[field] = value
    with pytest.raises(ValueError, match=message):
        build_review_plan(timeline)


def test_review_plan_rejects_unordered_transitions() -> None:
    timeline = _timeline()
    transitions = timeline["transitions"]
    assert isinstance(transitions, list)
    transitions[1]["startFrame"] = 4

    with pytest.raises(ValueError, match="ordered by startFrame"):
        build_review_plan(timeline)


def test_review_plan_rejects_a_skipped_proof_state() -> None:
    timeline = _timeline()
    transitions = timeline["transitions"]
    assert isinstance(transitions, list)
    transitions.pop(0)

    with pytest.raises(ValueError, match="each pair of adjacent proof states"):
        build_review_plan(timeline)


def test_contact_sheets_include_every_transition(tmp_path: Path) -> None:
    plan = build_review_plan(_timeline())
    frame_paths: dict[int, Path] = {}
    for frame in plan.selected_frames:
        path = tmp_path / f"source-{frame}.png"
        Image.new("RGB", (320, 180), (frame, 20, 40)).save(path)
        frame_paths[frame] = path

    sheets = write_contact_sheets(
        plan,
        tmp_path,
        frame_paths,
        transitions_per_sheet=1,
        thumbnail_width=160,
    )

    assert [path.name for path in sheets] == [
        "contact-sheet-001.png",
        "contact-sheet-002.png",
    ]
    for path in sheets:
        with Image.open(path) as image:
            assert image.width > 3 * 160
            assert image.height > 90


def test_frame_extraction_batches_exact_frame_numbers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    video = tmp_path / "proof.mp4"
    video.write_bytes(b"video")
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_options: object) -> SimpleNamespace:
        commands.append(command)
        pattern = Path(command[-1])
        selector = command[command.index("-vf") + 1]
        count = selector.count("eq(n\\,")
        for index in range(count):
            path = Path(str(pattern).replace("%06d", f"{index:06d}"))
            Image.new("RGB", (32, 18), "black").save(path)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(render_review.subprocess, "run", fake_run)
    extracted = extract_selected_frames(
        video,
        tmp_path / "review",
        [13, 2, 13, 8],
        ffmpeg="ffmpeg-test",
        batch_size=2,
    )

    assert tuple(extracted) == (2, 8, 13)
    assert all(path.is_file() for path in extracted.values())
    assert len(commands) == 2
    assert commands[0][commands[0].index("-fps_mode") + 1] == "passthrough"


def test_review_render_writes_json_and_csv_manifests(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    video = tmp_path / "proof.mp4"
    video.write_bytes(b"video")
    timeline = tmp_path / "proof.timeline.json"
    timeline.write_text(json.dumps(_timeline()), encoding="utf-8")
    output = tmp_path / "review"

    monkeypatch.setattr(
        render_review,
        "probe_video",
        lambda _path, ffprobe=None: {
            "width": 320,
            "height": 180,
            "fps": 30.0,
            "frameCount": 100,
            "durationSeconds": 100 / 30,
        },
    )

    def fake_extract(
        _video: Path,
        output_dir: Path,
        frames: tuple[int, ...],
        **_options: object,
    ) -> dict[int, Path]:
        frame_dir = output_dir / "frames"
        frame_dir.mkdir(parents=True)
        result: dict[int, Path] = {}
        for frame in frames:
            path = frame_dir / f"frame-{frame:09d}.png"
            Image.new("RGB", (320, 180), "black").save(path)
            result[frame] = path
        return result

    monkeypatch.setattr(render_review, "extract_selected_frames", fake_extract)

    manifest = review_render(
        video,
        timeline,
        output,
        transitions_per_sheet=2,
        thumbnail_width=160,
    )

    assert manifest["transitionCount"] == 2
    assert manifest["selectedFrameCount"] == 5
    persisted = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert persisted["selectionContract"].startswith("before=startFrame")
    assert persisted["contactSheets"] == ["contact-sheet-001.png"]
    with (output / "manifest.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 6
    assert [row["role"] for row in rows[:3]] == ["before", "mid", "after"]


def test_video_validation_detects_fps_and_length_mismatches() -> None:
    plan = build_review_plan(_timeline())
    with pytest.raises(ValueError, match="timeline is 30 fps"):
        render_review._validate_video(plan, {"fps": 24.0, "frameCount": 100})
    with pytest.raises(ValueError, match="video has 50 frames"):
        render_review._validate_video(plan, {"fps": 30.0, "frameCount": 50})
