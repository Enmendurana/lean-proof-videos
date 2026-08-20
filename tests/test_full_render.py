from pathlib import Path
from unittest.mock import patch

from proof_video.models import Frame, Goal, Movie
from proof_video.render import render_full


def _movie() -> Movie:
    frame = Frame(index=0, tactic="", goals=(Goal("g", "A", latex_target="A"),))
    return Movie(theorem_name="demo", frames=(frame,))


def _options(tmp_path: Path) -> dict:
    return {
        "width": 854,
        "height": 480,
        "fps": 15,
        "chars_per_second": 24.0,
        "max_duration": 600.0,
        "audio": None,
        "cache_root": tmp_path / "cache",
        "renderer": "cairo",
    }


def test_full_render_invokes_one_guarded_scene_and_publishes_master(
    tmp_path: Path,
) -> None:
    output = tmp_path / "proof.mp4"

    def fake_render(*args) -> None:
        destination = args[3]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"silent master")

    with patch(
        "proof_video.render._render_full_guarded", side_effect=fake_render
    ) as render:
        stats = render_full(_movie(), output, **_options(tmp_path))

    render.assert_called_once()
    assert output.read_bytes() == b"silent master"
    assert stats.rendered_segments == 1
    assert stats.cached_segments == 0


def test_full_render_reuses_silent_master_without_rendering(tmp_path: Path) -> None:
    output = tmp_path / "first.mp4"
    options = _options(tmp_path)
    options["use_cache"] = True

    def fake_render(*args) -> None:
        destination = args[3]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"cached master")

    with patch("proof_video.render._render_full_guarded", side_effect=fake_render):
        render_full(_movie(), output, **options)

    second = tmp_path / "second.mp4"
    with patch("proof_video.render._render_full_guarded") as render:
        stats = render_full(_movie(), second, **options)

    render.assert_not_called()
    assert second.read_bytes() == b"cached master"
    assert stats.rendered_segments == 0
    assert stats.cached_segments == 1
