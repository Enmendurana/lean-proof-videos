"""Atomic FFmpeg assembly and optional audio muxing."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from proof_video.cache import stable_hash

def _assemble_master(
    master: Path,
    output: Path,
    audio: Path | None,
    *,
    audio_volume: float = 1.0,
) -> None:
    """Atomically publish a silent master or mux a soundtrack onto it."""
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".assembling.mp4")
    if temporary.exists():
        temporary.unlink()
    if audio is None:
        shutil.copy2(master, temporary)
    else:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise SystemExit("FFmpeg was not found on PATH.")
        _run_ffmpeg(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(master),
                "-stream_loop",
                "-1",
                "-i",
                str(audio),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-filter:a",
                f"volume={audio_volume}",
                "-c:a",
                "aac",
                "-shortest",
                str(temporary),
            ]
        )
    temporary.replace(output)

def _assemble_segments(
    segments: list[Path],
    output: Path,
    audio: Path | None,
    cache_root: Path,
    *,
    audio_volume: float = 1.0,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("FFmpeg was not found on PATH.")
    output.parent.mkdir(parents=True, exist_ok=True)
    assembly_dir = cache_root / "assembly"
    assembly_dir.mkdir(parents=True, exist_ok=True)
    assembly_key = stable_hash(
        "assembly",
        [
            {
                "path": str(path),
                "size": path.stat().st_size,
                "mtime": path.stat().st_mtime_ns,
            }
            for path in segments
        ],
    )
    concat_file = assembly_dir / f"{assembly_key}.txt"
    concat_file.write_text(
        "".join(f"file '{_ffmpeg_path(path)}'\n" for path in segments),
        encoding="utf-8",
    )
    silent = assembly_dir / f"{assembly_key}.mp4"
    if not silent.exists() or silent.stat().st_size == 0:
        _run_ffmpeg(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-c",
                "copy",
                str(silent),
            ]
        )

    temporary = output.with_suffix(".assembling.mp4")
    if temporary.exists():
        temporary.unlink()
    if audio is None:
        shutil.copy2(silent, temporary)
    else:
        _run_ffmpeg(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(silent),
                "-stream_loop",
                "-1",
                "-i",
                str(audio),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-filter:a",
                f"volume={audio_volume}",
                "-c:a",
                "aac",
                "-shortest",
                str(temporary),
            ]
        )
    temporary.replace(output)

def _run_ffmpeg(command: list[str]) -> None:
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as error:
        raise SystemExit(f"FFmpeg could not assemble the cached segments: {error}") from error

def _ffmpeg_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "'\\''")
