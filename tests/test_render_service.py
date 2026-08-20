from pathlib import Path

from proof_video.render_service import RenderRequest, classify_progress_line


def test_render_request_maps_preview_and_resume_to_shared_cli(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Demo.lean"
    source.write_text("theorem main : True := by trivial\n", encoding="utf-8")
    request = RenderRequest(
        source,
        "Demo.main",
        Path("output/demo.mp4"),
        kind="preview-tail",
        render_hardware="gpu-required",
        resume=True,
    )
    arguments = request.cli_arguments()
    assert Path(arguments[0]) == source
    assert arguments[1] == "Demo.main"
    assert "--preview-tail" in arguments
    assert "--resume" in arguments
    assert arguments[arguments.index("--render-hardware") + 1] == "gpu-required"
    # Every public entry point must select the same canonical ABI-5 action
    # frontier by default.  The old proof-term mode remains an explicit
    # compatibility option, never a silent web/CLI divergence.
    assert arguments[arguments.index("--trace-mode") + 1] == "hybrid"
    assert arguments[arguments.index("--toolchain-backend") + 1] == "auto"
    assert "--trace-backend" not in arguments


def test_progress_line_classification_is_structured() -> None:
    phase, progress = classify_progress_line(
        "Checkpoint 2/5: 64% | rendered 320/500 | elapsed 00:28 | ETA 00:16"
    )
    assert phase == "render"
    assert progress == 0.64
