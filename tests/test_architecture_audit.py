from proof_video.models import Frame, Goal, Movie
from proof_video.remotion_export import build_remotion_timeline
from proof_video.remotion_render import _renderer_fingerprint


def _fake_remotion_project(root) -> None:
    remotion = root / "remotion"
    (remotion / "src").mkdir(parents=True)
    for relative in (
        "package-lock.json",
        "render-semantic.mjs",
        "src/index-semantic.ts",
        "src/root-semantic.tsx",
        "src/types-semantic.ts",
        "src/video-semantic.tsx",
    ):
        (remotion / relative).write_text(relative, encoding="utf-8")


def test_renderer_fingerprint_tracks_transitive_typescript_and_mjs_sources(
    tmp_path,
) -> None:
    project = tmp_path / "project"
    _fake_remotion_project(project)
    remotion = project / "remotion"
    baseline = _renderer_fingerprint(remotion)

    (remotion / "src" / "latex-display.ts").write_text(
        "export const x = 1;", encoding="utf-8"
    )
    with_latex_helper = _renderer_fingerprint(remotion)
    (remotion / "concurrency.mjs").write_text("export const x = 2;", encoding="utf-8")

    assert baseline != with_latex_helper
    assert with_latex_helper != _renderer_fingerprint(remotion)


def test_browser_timeline_contains_only_the_validated_executable_plan() -> None:
    movie = Movie(
        "demo",
        (
            Frame(0, "rfl", (Goal("g0", "", latex_target="A"),)),
            Frame(1, "rfl", (Goal("g1", "", latex_target="B"),)),
        ),
    )

    timeline = build_remotion_timeline(movie, fps=30)

    assert timeline["transitions"][0]["semantic"] is None
    assert timeline["edgeReasons"] == []
