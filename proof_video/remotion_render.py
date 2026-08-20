from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from proof_video.models import Movie
from proof_video.remotion_export import build_remotion_timeline
from proof_video.rendering.ffmpeg import _assemble_master, _assemble_segments
from proof_video.rendering.pacing import DEFAULT_VISIBLE_GLYPHS_PER_SECOND
from proof_video.rendering.profile import (
    RenderPlan,
    make_render_plan,
    semantic_chunks,
)


DEFAULT_BACKGROUND_VOLUME = 0.22


@dataclass(frozen=True)
class RemotionRenderStats:
    cached_segments: int
    rendered_segments: int
    renderer: str
    chars_per_second: float
    states: int
    duration_seconds: float
    profile_report: Path | None = None


def _update_profile_report(path: Path, **measurements: object) -> None:
    """Add Python-owned assembly/audio measurements to the Node report."""

    try:
        report = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, UnicodeError, ValueError):
        report = {}
    report.setdefault("schemaVersion", 1)
    report.setdefault("pythonStages", {}).update(measurements)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)


def _cached_chunk_matches_profile(chunk, render_plan: RenderPlan) -> bool:
    """Validate MP4, renderer and calibrated encoder before skipping Node."""

    try:
        profile = json.loads(
            Path(render_plan.profile_store).read_text(encoding="utf-8")
        )
        metadata = json.loads(
            Path(f"{chunk.output}.render.json").read_text(encoding="utf-8")
        )
        expected_encoding = {
            "hardwareAcceleration": profile["encoding"]["hardwareAcceleration"],
            "bitrate": profile["encoding"].get("bitrate"),
        }
        return bool(
            chunk.output.is_file()
            and chunk.output.stat().st_size > 0
            and profile.get("schemaVersion") == 1
            and profile.get("rendererFingerprint") == render_plan.renderer_fingerprint
            and profile.get("hardwareFingerprint") == render_plan.hardware.fingerprint
            and profile.get("width") == render_plan.width
            and profile.get("height") == render_plan.height
            and profile.get("fps") == render_plan.fps
            and metadata.get("schemaVersion") == 1
            and metadata.get("key") == chunk.key
            and metadata.get("rendererFingerprint") == render_plan.renderer_fingerprint
            and metadata.get("encoding") == expected_encoding
        )
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        return False


def _renderer_fingerprint(remotion_root: Path) -> str:
    """Hash every local source that can change rendered pixels or encoding.

    The old hand-maintained list omitted transitive imports such as
    ``latex-display.ts`` and the concurrency parser.  Resume mode could then
    reuse stale MP4 chunks after a renderer fix.  The Remotion project is
    small, so hashing all local entry/helper/source files is both cheaper and
    safer than duplicating its import graph here.
    """
    digest = hashlib.sha256()
    paths = [
        path
        for path in (
            *(remotion_root.glob("*.mjs")),
            *((remotion_root / "src").rglob("*")),
            remotion_root / "package.json",
            remotion_root / "package-lock.json",
        )
        if path.is_file()
        and path.suffix.lower()
        in {".mjs", ".js", ".jsx", ".ts", ".tsx", ".json", ".css"}
    ]
    for path in sorted(
        paths, key=lambda item: item.relative_to(remotion_root).as_posix()
    ):
        relative = path.relative_to(remotion_root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _timeline_key(
    timeline: dict, remotion_root: Path, render_plan: RenderPlan | None = None
) -> str:
    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            timeline, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )
    digest.update(_renderer_fingerprint(remotion_root).encode("ascii"))
    if render_plan is not None:
        digest.update(
            json.dumps(
                {
                    "hardware": render_plan.hardware.fingerprint,
                    "hardwarePolicy": render_plan.hardware_policy,
                    "renderer": render_plan.renderer_fingerprint,
                    "dimensions": [
                        render_plan.width,
                        render_plan.height,
                        render_plan.fps,
                    ],
                    "nvencBitrates": render_plan.nvenc_bitrates,
                    "minimumSsim": render_plan.minimum_ssim,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    return digest.hexdigest()


def render_remotion(
    movie: Movie,
    output: Path,
    *,
    width: int,
    height: int,
    fps: int,
    chars_per_second: float = DEFAULT_VISIBLE_GLYPHS_PER_SECOND,
    max_duration: float | None,
    cache_root: Path,
    use_cache: bool,
    concurrency: str = "auto",
    chunk_workers: int = 1,
    project_root: Path | None = None,
    preview_seconds: float | None = None,
    preview_tail_seconds: float | None = None,
    audio: Path | None = None,
    checkpoint_seconds: float | None = None,
    render_hardware: str = "auto",
    render_chunking: str = "auto",
    recalibrate_renderer: bool = False,
    profile_report: Path | None = None,
) -> RemotionRenderStats:
    """Render a strict proof timeline, optionally as resumable MP4 checkpoints."""

    project_root = project_root or Path(__file__).resolve().parents[1]
    remotion_root = project_root / "remotion"
    entrypoint = remotion_root / "render-semantic.mjs"
    if not entrypoint.exists():
        entrypoint = remotion_root / "render.mjs"
    if not entrypoint.exists():
        raise RuntimeError(f"Remotion entrypoint is missing: {entrypoint}")
    if not (remotion_root / "node_modules" / "remotion").exists():
        raise RuntimeError(
            "Remotion dependencies are not installed. Run `npm install` in "
            f"{remotion_root}."
        )
    node = shutil.which("node")
    if node is None:
        raise RuntimeError(
            "Node.js was not found on PATH; it is required for Remotion."
        )

    last_timeline_percent = -1
    last_timeline_report = 0.0

    def report_timeline(current: int, total: int) -> None:
        nonlocal last_timeline_percent, last_timeline_report
        percent = 100 if total == 0 else int(current * 100 / total)
        now = time.monotonic()
        if (
            percent < 100
            and percent <= last_timeline_percent
            and now - last_timeline_report < 5.0
        ):
            return
        if (
            percent < 100
            and percent < last_timeline_percent + 2
            and now - last_timeline_report < 2.0
        ):
            return
        print(
            f"Timeline: {percent:3d}% | {current}/{total} proof states",
            flush=True,
        )
        last_timeline_percent = max(last_timeline_percent, percent)
        last_timeline_report = now

    print("Preparing proof animation timeline...", flush=True)
    timeline = build_remotion_timeline(
        movie,
        width=width,
        height=height,
        fps=fps,
        chars_per_second=chars_per_second,
        max_duration=max_duration,
        preview_seconds=preview_seconds,
        preview_tail_seconds=preview_tail_seconds,
        on_progress=report_timeline,
    )
    duration_seconds = timeline["durationInFrames"] / fps
    renderer_fingerprint = _renderer_fingerprint(remotion_root)
    render_plan = make_render_plan(
        cache_root=cache_root,
        output=output,
        renderer_fingerprint=renderer_fingerprint,
        width=width,
        height=height,
        fps=fps,
        hardware_policy=render_hardware,
        concurrency=concurrency,
        profile_report=profile_report,
    )
    key = _timeline_key(timeline, remotion_root, render_plan)
    master = cache_root / "remotion" / f"{key}.mp4"
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    if use_cache and master.exists() and master.stat().st_size > 0:
        assembly_started = time.monotonic()
        _assemble_master(
            master,
            output,
            audio,
            audio_volume=DEFAULT_BACKGROUND_VOLUME,
        )
        _update_profile_report(
            Path(render_plan.profile_report),
            silentMasterCacheHit=True,
            assemblyAndAudioMuxMilliseconds=round(
                (time.monotonic() - assembly_started) * 1000, 3
            ),
        )
        return RemotionRenderStats(
            1,
            0,
            "remotion",
            timeline["writeSpeed"],
            len(timeline["states"]),
            duration_seconds,
            Path(render_plan.profile_report),
        )

    explicit_chunking = render_chunking not in {"auto", "off"}
    if (
        checkpoint_seconds is not None
        or (render_chunking != "off" and use_cache)
        or explicit_chunking
    ):
        if checkpoint_seconds is not None and checkpoint_seconds <= 0:
            raise ValueError("checkpoint_seconds must be greater than zero")
        checkpoint_dir = cache_root / "remotion-checkpoints" / key
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        timeline_path = checkpoint_dir / "timeline.json"
        timeline_temporary = timeline_path.with_suffix(".json.tmp")
        timeline_temporary.write_text(
            json.dumps(timeline, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(timeline_temporary, timeline_path)
        ranges = None
        fixed_chunk_seconds = None
        if checkpoint_seconds is not None:
            fixed_chunk_seconds = checkpoint_seconds
        elif render_chunking not in {"auto", "off"}:
            fixed_chunk_seconds = float(render_chunking)
        if fixed_chunk_seconds is not None:
            checkpoint_frames = max(1, round(fixed_chunk_seconds * fps))
            ranges = [
                (
                    start,
                    min(
                        timeline["durationInFrames"] - 1,
                        start + checkpoint_frames - 1,
                    ),
                )
                for start in range(0, timeline["durationInFrames"], checkpoint_frames)
            ]
        specifications = semantic_chunks(
            timeline,
            cache_root=cache_root,
            renderer_fingerprint=renderer_fingerprint,
            render_plan=render_plan,
            ranges=ranges,
        )
        for specification in specifications:
            specification.output.parent.mkdir(parents=True, exist_ok=True)
        chunks = [specification.output for specification in specifications]
        cached_chunks = (
            0
            if recalibrate_renderer
            else sum(
                _cached_chunk_matches_profile(specification, render_plan)
                for specification in specifications
            )
        )
        chunk_manifest = checkpoint_dir / "chunk-plan.json"
        chunk_manifest_temporary = chunk_manifest.with_suffix(".json.tmp")
        chunk_manifest_temporary.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "timelineKey": key,
                    "chunks": [item.to_json() for item in specifications],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        os.replace(chunk_manifest_temporary, chunk_manifest)
        render_plan_path = checkpoint_dir / "render-plan.json"
        render_plan_path.write_text(
            json.dumps(
                render_plan.to_json(), ensure_ascii=False, separators=(",", ":")
            ),
            encoding="utf-8",
        )
        command = [
            node,
            str(entrypoint),
            "--timeline",
            str(timeline_path),
            "--output",
            str(output.with_name(f".{output.stem}.unused-checkpoint-output.mp4")),
            "--render-plan",
            str(render_plan_path),
            "--chunk-manifest",
            str(chunk_manifest),
            "--layout-cache-dir",
            str((cache_root / "remotion-layouts").resolve()),
        ]
        if recalibrate_renderer:
            command.append("--recalibrate-renderer")
        if cached_chunks != len(chunks):
            try:
                subprocess.run(command, cwd=remotion_root, check=True)
            except subprocess.CalledProcessError as error:
                raise RuntimeError(
                    "Remotion checkpoint render failed; completed chunks were preserved in "
                    f"{checkpoint_dir}. Exit code: {error.returncode}."
                ) from error
        else:
            print(
                f"Remotion semantic: all {len(chunks)} checkpoints validated from cache.",
                flush=True,
            )
        missing = [
            path for path in chunks if not path.exists() or path.stat().st_size == 0
        ]
        if missing:
            raise RuntimeError(
                f"Remotion did not create {len(missing)} checkpoint chunk(s); "
                f"completed chunks remain in {checkpoint_dir}."
            )
        assembly_started = time.monotonic()
        _assemble_segments(
            chunks,
            output,
            audio,
            cache_root,
            audio_volume=DEFAULT_BACKGROUND_VOLUME,
        )
        _update_profile_report(
            Path(render_plan.profile_report),
            checkpointCount=len(chunks),
            assemblyAndAudioMuxMilliseconds=round(
                (time.monotonic() - assembly_started) * 1000, 3
            ),
        )
        return RemotionRenderStats(
            cached_chunks,
            len(chunks) - cached_chunks,
            "remotion-checkpoints",
            timeline["writeSpeed"],
            len(timeline["states"]),
            duration_seconds,
            Path(render_plan.profile_report),
        )

    master.parent.mkdir(parents=True, exist_ok=True)
    rendering = output.with_name(f".{output.stem}.remotion-rendering.mp4")
    if rendering.exists():
        rendering.unlink()

    temporary_root = cache_root / "remotion-tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="lean-proof-remotion-", dir=temporary_root
    ) as temporary:
        timeline_path = Path(temporary) / "timeline.json"
        timeline_path.write_text(
            json.dumps(timeline, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        render_plan_path = Path(temporary) / "render-plan.json"
        render_plan_path.write_text(
            json.dumps(
                render_plan.to_json(), ensure_ascii=False, separators=(",", ":")
            ),
            encoding="utf-8",
        )
        command = [
            node,
            str(entrypoint),
            "--timeline",
            str(timeline_path),
            "--output",
            str(rendering),
            "--render-plan",
            str(render_plan_path),
            "--layout-cache-dir",
            str((cache_root / "remotion-layouts").resolve()),
        ]
        if recalibrate_renderer:
            command.append("--recalibrate-renderer")
        try:
            subprocess.run(command, cwd=remotion_root, check=True)
        except subprocess.CalledProcessError as error:
            raise RuntimeError(
                f"Remotion render failed with exit code {error.returncode}."
            ) from error

    if not rendering.exists() or rendering.stat().st_size == 0:
        raise RuntimeError(
            "Remotion reported success but did not create a non-empty MP4."
        )
    if use_cache:
        cached_rendering = master.with_suffix(".rendering.mp4")
        shutil.copy2(rendering, cached_rendering)
        os.replace(cached_rendering, master)
        silent_master = master
    else:
        silent_master = rendering
    assembly_started = time.monotonic()
    _assemble_master(
        silent_master,
        output,
        audio,
        audio_volume=DEFAULT_BACKGROUND_VOLUME,
    )
    _update_profile_report(
        Path(render_plan.profile_report),
        silentMasterCacheHit=False,
        assemblyAndAudioMuxMilliseconds=round(
            (time.monotonic() - assembly_started) * 1000, 3
        ),
    )
    if rendering.exists():
        rendering.unlink()
    return RemotionRenderStats(
        0,
        1,
        "remotion",
        timeline["writeSpeed"],
        len(timeline["states"]),
        duration_seconds,
        Path(render_plan.profile_report),
    )
