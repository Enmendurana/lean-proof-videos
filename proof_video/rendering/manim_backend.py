"""Manim worker processes and low-level full/segmented render execution."""

from __future__ import annotations

import multiprocessing
import os
import shutil
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from proof_video.models import Frame, Movie
from proof_video.scene import ProofChunkScene, ProofScene, ProofSegmentScene
from proof_video.tex_precompile import precompile_movie_tex
from proof_video.rendering.ffmpeg import _assemble_segments
from proof_video.rendering.planning import _chunk_key, _segment_key
from proof_video.rendering.types import RenderStats


TRANSITION_SECONDS = 0.65
FULL_RENDER_TIMEOUT_SECONDS = 3600


_SEGMENT_WORKER_OPTIONS: tuple[Any, ...] | None = None
_CHUNK_WORKER_OPTIONS: tuple[Any, ...] | None = None


def _segment_worker_init(movie: Movie, options: tuple[Any, ...]) -> None:
    global _SEGMENT_WORKER_OPTIONS
    _SEGMENT_WORKER_OPTIONS = (movie, *options)


def _segment_worker_render(index: int, destination: Path) -> int:
    assert _SEGMENT_WORKER_OPTIONS is not None
    movie, chars_per_second, width, height, fps, cache_root, use_cache = (
        _SEGMENT_WORKER_OPTIONS
    )
    _render_segment_guarded(
        movie,
        index,
        chars_per_second,
        TRANSITION_SECONDS,
        destination,
        width,
        height,
        fps,
        "cairo",
        cache_root,
        use_cache,
    )
    return index


def _chunk_worker_init(movie: Movie, options: tuple[Any, ...]) -> None:
    global _CHUNK_WORKER_OPTIONS
    _CHUNK_WORKER_OPTIONS = (movie, *options)


def _chunk_worker_render(start: int, end: int, destination: Path) -> tuple[int, int]:
    assert _CHUNK_WORKER_OPTIONS is not None
    movie, chars_per_second, width, height, fps, cache_root, use_cache = (
        _CHUNK_WORKER_OPTIONS
    )
    _render_chunk(
        movie,
        start,
        end,
        chars_per_second,
        TRANSITION_SECONDS,
        destination,
        width,
        height,
        fps,
        cache_root,
        use_cache,
    )
    return start, end


def _render_cairo_chunks_parallel(
    movie: Movie,
    frames: tuple[Frame, ...],
    output: Path,
    *,
    width: int,
    height: int,
    fps: int,
    chars_per_second: float,
    audio: Path | None,
    cache_root: Path,
    use_cache: bool,
    chunk_size: int,
) -> RenderStats:
    """Render groups of logical transitions in persistent Manim scenes."""

    ranges = tuple(
        (start, min(start + chunk_size, len(frames)))
        for start in range(0, len(frames), chunk_size)
    )
    chunks: list[Path] = []
    pending: list[tuple[int, int, Path]] = []
    cached_count = 0
    for start, end in ranges:
        key = _chunk_key(
            frames,
            start,
            end,
            chars_per_second,
            TRANSITION_SECONDS,
            width,
            height,
            fps,
        )
        chunk = cache_root / "chunks" / f"{key}.mp4"
        chunks.append(chunk)
        if use_cache and chunk.exists() and chunk.stat().st_size > 0:
            cached_count += 1
        else:
            pending.append((start, end, chunk))

    _precompile_tex_cache(movie, cache_root)
    configured = os.environ.get("LEAN_PROOF_RENDER_WORKERS")
    try:
        requested_workers = int(configured) if configured else 4
    except ValueError:
        requested_workers = 4
    workers = max(1, min(requested_workers, os.cpu_count() or 1, 8))
    if pending:
        options = (
            chars_per_second,
            width,
            height,
            fps,
            cache_root,
            use_cache,
        )
        print(
            f"Parallel chunks: {len(pending)} chunk(s) of at most {chunk_size} "
            f"transitions, {workers} workers, {cached_count} cached"
        )
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_chunk_worker_init,
            initargs=(movie, options),
        ) as executor:
            futures = {
                executor.submit(_chunk_worker_render, start, end, path): (start, end)
                for start, end, path in pending
            }
            completed = 0
            for future in as_completed(futures):
                future.result()
                completed += 1
                print(f"Parallel chunk progress: {completed}/{len(pending)}")

    _assemble_segments(chunks, output, audio, cache_root)
    return RenderStats(
        rendered_segments=len(pending),
        cached_segments=cached_count,
        renderer="cairo",
        chars_per_second=chars_per_second,
    )


def _render_cairo_segments_parallel(
    movie: Movie,
    frames: tuple[Frame, ...],
    indices: tuple[int, ...],
    output: Path,
    *,
    width: int,
    height: int,
    fps: int,
    chars_per_second: float,
    audio: Path | None,
    cache_root: Path,
    use_cache: bool,
) -> RenderStats:
    """Render independent verified transitions concurrently with Cairo."""

    ordered_segments: dict[int, Path] = {}
    pending: list[tuple[int, Path]] = []
    cached_count = 0
    for index in indices:
        key = _segment_key(
            frames,
            index,
            chars_per_second,
            TRANSITION_SECONDS,
            width,
            height,
            fps,
            "cairo",
        )
        segment = cache_root / "segments" / f"{key}.mp4"
        ordered_segments[index] = segment
        if use_cache and segment.exists() and segment.stat().st_size > 0:
            cached_count += 1
        else:
            pending.append((index, segment))

    _precompile_tex_cache(movie, cache_root)
    configured = os.environ.get("LEAN_PROOF_RENDER_WORKERS")
    try:
        requested_workers = int(configured) if configured else 4
    except ValueError:
        requested_workers = 4
    workers = max(1, min(requested_workers, os.cpu_count() or 1, 8))
    if pending:
        options = (
            chars_per_second,
            width,
            height,
            fps,
            cache_root,
            use_cache,
        )
        print(
            f"Parallel Cairo: {len(pending)} transition(s), {workers} workers, "
            f"{cached_count} cached"
        )
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_segment_worker_init,
            initargs=(movie, options),
        ) as executor:
            futures = {
                executor.submit(_segment_worker_render, index, segment): index
                for index, segment in pending
            }
            completed = 0
            for future in as_completed(futures):
                future.result()
                completed += 1
                if completed % 25 == 0 or completed == len(pending):
                    print(f"Parallel Cairo progress: {completed}/{len(pending)}")

    segments = [ordered_segments[index] for index in indices]
    _assemble_segments(segments, output, audio, cache_root)
    return RenderStats(
        rendered_segments=len(pending),
        cached_segments=cached_count,
        renderer="cairo",
        chars_per_second=chars_per_second,
    )


def _precompile_tex_cache(movie: Movie, cache_root: Path) -> None:
    from manim import tempconfig

    tex_dir = cache_root / "manim" / "Tex"
    tex_dir.mkdir(parents=True, exist_ok=True)
    with tempconfig(
        {
            "tex_dir": str(tex_dir),
            "no_latex_cleanup": True,
        }
    ):
        stats = precompile_movie_tex(movie)
    print(
        "TeX precompile: "
        f"{stats.expressions} expressions, {stats.workers} workers, "
        f"{stats.elapsed_seconds:.1f}s"
    )
    if stats.failures:
        print(
            f"TeX precompile deferred {len(stats.failures)} expression(s) "
            "to the scene fallback."
        )


def _render_full(
    movie: Movie,
    chars_per_second: float,
    transition_seconds: float,
    destination: Path,
    width: int,
    height: int,
    fps: int,
    renderer: str,
    cache_root: Path,
    use_cache: bool,
) -> None:
    from manim import tempconfig

    destination.parent.mkdir(parents=True, exist_ok=True)
    media_dir = cache_root / "manim" / renderer
    media_dir.mkdir(parents=True, exist_ok=True)
    tex_dir = cache_root / "manim" / "Tex"
    tex_dir.mkdir(parents=True, exist_ok=True)
    with tempconfig(
        {
            "renderer": renderer,
            "media_dir": str(media_dir),
            "tex_dir": str(tex_dir),
            "output_file": destination.stem,
            "pixel_width": width,
            "pixel_height": height,
            "frame_rate": fps,
            "format": "mp4",
            "disable_caching": not use_cache,
            # Parallel TeX workers share this content-addressed directory.
            # Cleaning another worker's DVI/log files mid-compile is unsafe.
            "no_latex_cleanup": True,
            # A proof commonly has more than Manim's default 100 play calls.
            # Retaining them makes interrupted/repeated full renders useful.
            "max_files_cached": 1000,
            "write_to_movie": True,
            "preview": False,
        }
    ):
        tex_stats = precompile_movie_tex(movie)
        print(
            "TeX precompile: "
            f"{tex_stats.expressions} expressions, {tex_stats.workers} workers, "
            f"{tex_stats.elapsed_seconds:.1f}s"
        )
        if tex_stats.failures:
            print(
                f"TeX precompile deferred {len(tex_stats.failures)} expression(s) "
                "to the scene fallback."
            )
        scene = ProofScene(
            movie=movie,
            chars_per_second=chars_per_second,
            transition_seconds=transition_seconds,
            audio=None,
        )
        scene.render()
        rendered = Path(scene.renderer.file_writer.movie_file_path)
        temporary = destination.with_suffix(".rendering.mp4")
        if temporary.exists():
            temporary.unlink()
        shutil.copy2(rendered, temporary)
        temporary.replace(destination)


def _render_full_guarded(
    movie: Movie,
    chars_per_second: float,
    transition_seconds: float,
    destination: Path,
    width: int,
    height: int,
    fps: int,
    renderer: str,
    cache_root: Path,
    use_cache: bool,
) -> None:
    """Render OpenGL out of process so a driver stall can fall back as a unit."""
    args = (
        movie,
        chars_per_second,
        transition_seconds,
        destination,
        width,
        height,
        fps,
        renderer,
        cache_root,
        use_cache,
    )
    if renderer != "opengl":
        _render_full(*args)
        return

    context = multiprocessing.get_context("spawn")
    errors = context.Queue()
    process = context.Process(target=_render_full_worker, args=(errors, *args))
    process.start()
    process.join(FULL_RENDER_TIMEOUT_SECONDS)
    if process.is_alive():
        process.terminate()
        process.join(10)
        raise TimeoutError(
            "OpenGL full scene did not finish within "
            f"{FULL_RENDER_TIMEOUT_SECONDS} seconds"
        )
    if process.exitcode != 0:
        details = (
            errors.get() if not errors.empty() else f"exit code {process.exitcode}"
        )
        raise RuntimeError(f"OpenGL full scene failed:\n{details}")


def _render_full_worker(errors, *args) -> None:
    try:
        _render_full(*args)
    except BaseException:
        errors.put(traceback.format_exc())
        raise


def _render_segment(
    movie: Movie,
    index: int,
    chars_per_second: float,
    transition_seconds: float,
    destination: Path,
    width: int,
    height: int,
    fps: int,
    renderer: str,
    cache_root: Path,
    use_cache: bool,
) -> None:
    from manim import tempconfig

    destination.parent.mkdir(parents=True, exist_ok=True)
    # Segment workers use independent Manim media trees.  Their final segment
    # paths are already content-addressed, while sharing partial-movie folders
    # between processes would create filename races.
    media_dir = cache_root / "manim" / "segments" / str(os.getpid()) / renderer
    media_dir.mkdir(parents=True, exist_ok=True)
    tex_dir = cache_root / "manim" / "Tex"
    tex_dir.mkdir(parents=True, exist_ok=True)
    with tempconfig(
        {
            "renderer": renderer,
            "media_dir": str(media_dir),
            "tex_dir": str(tex_dir),
            "output_file": destination.stem,
            "pixel_width": width,
            "pixel_height": height,
            "frame_rate": fps,
            "format": "mp4",
            "disable_caching": not use_cache,
            "no_latex_cleanup": True,
            "write_to_movie": True,
            "preview": False,
        }
    ):
        scene = ProofSegmentScene(
            movie=movie,
            segment_index=index,
            chars_per_second=chars_per_second,
            transition_seconds=transition_seconds,
        )
        scene.render()
        rendered = Path(scene.renderer.file_writer.movie_file_path)
        temporary = destination.with_suffix(".tmp.mp4")
        shutil.copy2(rendered, temporary)
        temporary.replace(destination)


def _render_chunk(
    movie: Movie,
    start: int,
    end: int,
    chars_per_second: float,
    transition_seconds: float,
    destination: Path,
    width: int,
    height: int,
    fps: int,
    cache_root: Path,
    use_cache: bool,
) -> None:
    from manim import tempconfig

    destination.parent.mkdir(parents=True, exist_ok=True)
    media_dir = cache_root / "manim" / "chunks" / str(os.getpid()) / "cairo"
    media_dir.mkdir(parents=True, exist_ok=True)
    tex_dir = cache_root / "manim" / "Tex"
    tex_dir.mkdir(parents=True, exist_ok=True)
    with tempconfig(
        {
            "renderer": "cairo",
            "media_dir": str(media_dir),
            "tex_dir": str(tex_dir),
            "output_file": destination.stem,
            "pixel_width": width,
            "pixel_height": height,
            "frame_rate": fps,
            "format": "mp4",
            "disable_caching": not use_cache,
            "no_latex_cleanup": True,
            "max_files_cached": max(1000, (end - start) * 8),
            "write_to_movie": True,
            "preview": False,
        }
    ):
        scene = ProofChunkScene(
            movie=movie,
            start_index=start,
            end_index=end,
            chars_per_second=chars_per_second,
            transition_seconds=transition_seconds,
        )
        scene.render()
        rendered = Path(scene.renderer.file_writer.movie_file_path)
        temporary = destination.with_suffix(".tmp.mp4")
        shutil.copy2(rendered, temporary)
        temporary.replace(destination)


def _render_segment_guarded(
    movie: Movie,
    index: int,
    chars_per_second: float,
    transition_seconds: float,
    destination: Path,
    width: int,
    height: int,
    fps: int,
    renderer: str,
    cache_root: Path,
    use_cache: bool,
) -> None:
    """Isolate GPU rendering so a driver stall can fall back cleanly."""
    if renderer != "opengl":
        _render_segment(
            movie,
            index,
            chars_per_second,
            transition_seconds,
            destination,
            width,
            height,
            fps,
            renderer,
            cache_root,
            use_cache,
        )
        return

    context = multiprocessing.get_context("spawn")
    errors = context.Queue()
    process = context.Process(
        target=_render_segment_worker,
        args=(
            errors,
            movie,
            index,
            chars_per_second,
            transition_seconds,
            destination,
            width,
            height,
            fps,
            renderer,
            cache_root,
            use_cache,
        ),
    )
    process.start()
    process.join(180)
    if process.is_alive():
        process.terminate()
        process.join(10)
        raise TimeoutError(f"OpenGL segment {index} did not finish within 180 seconds")
    if process.exitcode != 0:
        details = (
            errors.get() if not errors.empty() else f"exit code {process.exitcode}"
        )
        raise RuntimeError(f"OpenGL segment {index} failed:\n{details}")


def _render_segment_worker(errors, *args) -> None:
    try:
        _render_segment(*args)
    except BaseException:
        errors.put(traceback.format_exc())
        raise
