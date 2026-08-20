import json
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


def test_resumable_render_preserves_and_reuses_checkpoint_chunks(
    monkeypatch, tmp_path
) -> None:
    project = tmp_path / "project"
    _fake_project(project)
    monkeypatch.setattr(
        "proof_video.remotion_render.shutil.which", lambda _name: "node"
    )

    def fake_run(command, **_kwargs):
        manifest_path = Path(command[command.index("--chunk-manifest") + 1])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        render_plan_path = Path(command[command.index("--render-plan") + 1])
        render_plan = json.loads(render_plan_path.read_text(encoding="utf-8"))
        profile = {
            "schemaVersion": 1,
            "rendererFingerprint": render_plan["rendererFingerprint"],
            "hardwareFingerprint": render_plan["hardware"]["fingerprint"],
            "width": render_plan["width"],
            "height": render_plan["height"],
            "fps": render_plan["fps"],
            "encoding": {"hardwareAcceleration": "disable", "bitrate": None},
        }
        profile_path = Path(render_plan["profileStore"])
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        for specification in manifest["chunks"]:
            chunk = Path(specification["output"])
            chunk.parent.mkdir(parents=True, exist_ok=True)
            if not chunk.exists():
                chunk.write_bytes(
                    f"{specification['start']}-{specification['end']}".encode()
                )
            Path(f"{chunk}.render.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "key": specification["key"],
                        "rendererFingerprint": render_plan["rendererFingerprint"],
                        "encoding": {
                            "hardwareAcceleration": "disable",
                            "bitrate": None,
                        },
                    }
                ),
                encoding="utf-8",
            )

    monkeypatch.setattr("proof_video.remotion_render.subprocess.run", fake_run)

    def fake_assemble(segments, output, *_args, **_kwargs):
        output.write_bytes(b"".join(path.read_bytes() for path in segments))

    monkeypatch.setattr("proof_video.remotion_render._assemble_segments", fake_assemble)
    movie = Movie("demo", (Frame(0, "rfl", (Goal("g", "", latex_target="A"),)),))
    output = tmp_path / "proof.mp4"
    options = dict(
        width=854,
        height=480,
        fps=15,
        max_duration=60,
        cache_root=tmp_path / "cache",
        use_cache=False,
        project_root=project,
        checkpoint_seconds=1,
    )

    first = render_remotion(movie, output, **options)
    second = render_remotion(movie, output, **options)

    assert first.rendered_segments >= 1
    assert first.cached_segments == 0
    assert second.rendered_segments == 0
    assert second.cached_segments == first.rendered_segments
    assert output.stat().st_size > 0
