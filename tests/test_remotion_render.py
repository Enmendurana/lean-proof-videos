from pathlib import Path

from proof_video.models import Frame, Goal, Movie
from proof_video.remotion_render import render_remotion


def _fake_project(root: Path) -> None:
    remotion = root / "remotion"
    (remotion / "src").mkdir(parents=True)
    (remotion / "node_modules" / "remotion").mkdir(parents=True)
    for relative in (
        "package-lock.json",
        "render.mjs",
        "src/types.ts",
        "src/root.tsx",
        "src/video.tsx",
    ):
        (remotion / relative).write_text(relative, encoding="utf-8")


def test_remotion_render_routes_timeline_and_atomically_finishes(
    monkeypatch, tmp_path
) -> None:
    project = tmp_path / "project"
    _fake_project(project)
    monkeypatch.setattr(
        "proof_video.remotion_render.shutil.which", lambda _name: "node"
    )

    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        output = Path(command[command.index("--output") + 1])
        output.write_bytes(b"strict-mp4")

    monkeypatch.setattr("proof_video.remotion_render.subprocess.run", fake_run)
    movie = Movie("demo", (Frame(0, "rfl", (Goal("g", "", latex_target="A"),)),))
    output = tmp_path / "proof.mp4"

    stats = render_remotion(
        movie,
        output,
        width=854,
        height=480,
        fps=15,
        max_duration=60,
        cache_root=tmp_path / "cache",
        use_cache=False,
        project_root=project,
    )

    assert output.read_bytes() == b"strict-mp4"
    assert stats.renderer == "remotion"
    assert stats.states == 1
    assert stats.rendered_segments == 1
    assert calls[0][1]["check"] is True
    assert not output.with_name(".proof.remotion-rendering.mp4").exists()
