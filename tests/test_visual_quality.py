from __future__ import annotations

from proof_video import visual_quality


def test_visual_quality_accepts_expected_media(monkeypatch, tmp_path) -> None:
    video = tmp_path / "proof.mp4"
    video.write_bytes(b"test")
    monkeypatch.setattr(
        visual_quality,
        "_probe",
        lambda _path: {
            "streams": [{"width": 1920, "height": 1080, "duration": "20.0"}],
            "format": {"duration": "20.0"},
        },
    )
    monkeypatch.setattr(visual_quality, "_black_intervals", lambda _path: [])
    report = visual_quality.build_visual_quality_report(
        video,
        expected_width=1920,
        expected_height=1080,
        expected_duration=20.0,
    )
    assert report["valid"]


def test_visual_quality_rejects_prolonged_empty_board(monkeypatch, tmp_path) -> None:
    video = tmp_path / "proof.mp4"
    video.write_bytes(b"test")
    monkeypatch.setattr(
        visual_quality,
        "_probe",
        lambda _path: {
            "streams": [{"width": 1920, "height": 1080, "duration": "20.0"}],
            "format": {"duration": "20.0"},
        },
    )
    monkeypatch.setattr(visual_quality, "_black_intervals", lambda _path: [(4.0, 6.5)])
    report = visual_quality.build_visual_quality_report(
        video,
        expected_width=1920,
        expected_height=1080,
        expected_duration=20.0,
    )
    assert not report["valid"]
    assert "effectively empty" in report["errors"][0]


def test_visual_quality_accepts_progressively_written_sparse_intro(
    monkeypatch, tmp_path
) -> None:
    video = tmp_path / "proof.mp4"
    video.write_bytes(b"test")
    monkeypatch.setattr(
        visual_quality,
        "_probe",
        lambda _path: {
            "streams": [{"width": 1920, "height": 1080, "duration": "20.0"}],
            "format": {"duration": "20.0"},
        },
    )
    monkeypatch.setattr(visual_quality, "_black_intervals", lambda _path: [(0.0, 2.2)])
    monkeypatch.setattr(
        visual_quality,
        "_content_bbox_at",
        lambda _path, timestamp: (130, 70) if timestamp < 1 else (1580, 430),
    )

    report = visual_quality.build_visual_quality_report(
        video,
        expected_width=1920,
        expected_height=1080,
        expected_duration=20.0,
    )

    assert report["valid"]
    assert "active chalk writing" in report["warnings"][0]


def test_visual_quality_accepts_sparse_final_qed_with_wide_content(
    monkeypatch, tmp_path
) -> None:
    video = tmp_path / "proof.mp4"
    video.write_bytes(b"test")
    monkeypatch.setattr(
        visual_quality,
        "_probe",
        lambda _path: {
            "streams": [{"width": 1920, "height": 1080, "duration": "20.0"}],
            "format": {"duration": "20.0"},
        },
    )
    monkeypatch.setattr(
        visual_quality, "_black_intervals", lambda _path: [(14.0, 20.0)]
    )
    monkeypatch.setattr(visual_quality, "_last_content_bbox", lambda _path: (1650, 130))
    report = visual_quality.build_visual_quality_report(
        video,
        expected_width=1920,
        expected_height=1080,
        expected_duration=20.0,
    )
    assert report["valid"]
    assert "final QED hold" in report["warnings"][0]
