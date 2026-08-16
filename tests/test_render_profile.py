from pathlib import Path

from proof_video.rendering.profile import (
    HardwareProfile,
    RenderPlan,
    calibration_candidates,
    semantic_chunk_ranges,
    semantic_chunks,
)


def _timeline() -> dict:
    return {
        "rendererContract": "strict-proof-transition-v1",
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "durationInFrames": 1000,
        "states": [
            {"id": "s0", "rows": []},
            {"id": "s1", "rows": []},
            {"id": "s2", "rows": []},
        ],
        "transitions": [
            {"fromState": 0, "toState": 1, "startFrame": 260, "durationFrames": 30},
            {"fromState": 1, "toState": 2, "startFrame": 570, "durationFrames": 30},
        ],
        "showQed": True,
        "celebrationFrames": 60,
        "completionHoldFrames": 90,
    }


def _plan(tmp_path: Path) -> RenderPlan:
    return RenderPlan(
        schema_version=1,
        hardware=HardwareProfile("machine", "windows", 12, None, False),
        hardware_policy="auto",
        requested_concurrency="auto",
        calibration_candidates=(3, 4, 6, 8),
        calibration_frames=120,
        profile_store=str(tmp_path / "profile.json"),
        profile_report=str(tmp_path / "report.json"),
        renderer_fingerprint="renderer",
        width=1920,
        height=1080,
        fps=30,
        gpu_compositing="benchmark",
        nvenc_bitrates=("8M", "12M", "16M"),
        minimum_ssim=0.995,
    )


def test_semantic_chunks_prefer_transition_boundaries_and_cover_every_frame() -> None:
    ranges = semantic_chunk_ranges(_timeline())

    assert ranges[0] == (0, 289)
    assert ranges[1] == (290, 599)
    assert ranges[-1][-1] == 999
    assert [start for start, _end in ranges[1:]] == [end + 1 for _start, end in ranges[:-1]]


def test_local_chunk_identity_ignores_unrelated_later_state(tmp_path: Path) -> None:
    timeline = _timeline()
    before = semantic_chunks(
        timeline,
        cache_root=tmp_path,
        renderer_fingerprint="renderer",
        render_plan=_plan(tmp_path),
    )
    timeline["states"][2]["rows"] = [{"tokens": [["B", 0, 1]]}]
    after = semantic_chunks(
        timeline,
        cache_root=tmp_path,
        renderer_fingerprint="renderer",
        render_plan=_plan(tmp_path),
    )

    assert before[0].key == after[0].key
    assert before[-1].key != after[-1].key


def test_calibration_candidates_never_exceed_available_threads() -> None:
    assert calibration_candidates(12) == (3, 4, 6, 8)
    assert calibration_candidates(2) == (2,)
