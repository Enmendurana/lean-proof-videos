from __future__ import annotations

import os
from pathlib import Path

from proof_video.models import Movie
from proof_video.rendering.ffmpeg import (
    _assemble_master,
    _assemble_segments,
)
from proof_video.rendering.planning import (
    _full_key,
    _opengl_safe_for_frame,
    _opengl_safe_for_movie,
    _preview_indices,
    _resolve_renderer,
    _segment_key,
    effective_write_speed,
)
from proof_video.rendering.manim_backend import (
    FULL_RENDER_TIMEOUT_SECONDS as FULL_RENDER_TIMEOUT_SECONDS,
    TRANSITION_SECONDS,
    _render_cairo_chunks_parallel,
    _render_cairo_segments_parallel,
    _render_full_guarded,
    _render_segment_guarded,
)
from proof_video.rendering.types import RenderStats


def render_full(
    movie: Movie,
    output: Path,
    *,
    width: int,
    height: int,
    fps: int,
    chars_per_second: float,
    max_duration: float | None,
    audio: Path | None,
    cache_root: Path,
    renderer: str = "auto",
    preview: bool = False,
    use_cache: bool = False,
) -> RenderStats:
    """Render the entire proof in one Manim scene.

    The cached artifact is deliberately a silent, content-addressed master.
    Audio is muxed only when producing ``output``, so changing a soundtrack
    never invalidates the expensive Manim render.

    Preview remains segmented because it intentionally selects a few distant
    proof states instead of playing the complete scene.
    """
    if preview:
        return render_segmented(
            movie,
            output,
            width=width,
            height=height,
            fps=fps,
            chars_per_second=chars_per_second,
            max_duration=max_duration,
            audio=audio,
            cache_root=cache_root,
            renderer=renderer,
            preview=True,
            use_cache=use_cache,
        )

    frames = tuple(frame for frame in movie.semantic_frames() if frame.display_goals)
    if not frames:
        raise SystemExit("The Lean trace contains no visible proof states.")
    chars_per_second = effective_write_speed(
        frames,
        requested=chars_per_second,
        max_duration=max_duration,
        transition_seconds=TRANSITION_SECONDS,
        fps=fps,
    )

    preferred_renderer = _resolve_renderer(renderer)
    active_renderer = preferred_renderer
    if renderer == "auto" and active_renderer == "opengl":
        opengl_key = _full_key(
            frames,
            chars_per_second,
            TRANSITION_SECONDS,
            width,
            height,
            fps,
            "opengl",
        )
        fallback_marker = cache_root / "renderer-fallback" / f"full-{opengl_key}.txt"
        if (use_cache and fallback_marker.exists()) or not _opengl_safe_for_movie(frames):
            active_renderer = "cairo"
    else:
        fallback_marker = None

    key = _full_key(
        frames,
        chars_per_second,
        TRANSITION_SECONDS,
        width,
        height,
        fps,
        active_renderer,
    )
    silent_master = cache_root / "full" / f"{key}.mp4"
    cache_hit = use_cache and silent_master.exists() and silent_master.stat().st_size > 0

    if not cache_hit:
        try:
            _render_full_guarded(
                movie,
                chars_per_second,
                TRANSITION_SECONDS,
                silent_master,
                width,
                height,
                fps,
                active_renderer,
                cache_root,
                use_cache,
            )
        except Exception:
            if renderer != "auto" or active_renderer != "opengl":
                raise
            assert fallback_marker is not None
            if use_cache:
                fallback_marker.parent.mkdir(parents=True, exist_ok=True)
                fallback_marker.write_text(
                    "OpenGL failed or exceeded the full-scene deadline; use Cairo.\n",
                    encoding="utf-8",
                )
            active_renderer = "cairo"
            print("OpenGL is unavailable for the full scene; falling back to Cairo.")
            key = _full_key(
                frames,
                chars_per_second,
                TRANSITION_SECONDS,
                width,
                height,
                fps,
                active_renderer,
            )
            silent_master = cache_root / "full" / f"{key}.mp4"
            cache_hit = (
                use_cache
                and silent_master.exists()
                and silent_master.stat().st_size > 0
            )
            if not cache_hit:
                _render_full_guarded(
                    movie,
                    chars_per_second,
                    TRANSITION_SECONDS,
                    silent_master,
                    width,
                    height,
                    fps,
                    active_renderer,
                    cache_root,
                    use_cache,
                )

    _assemble_master(silent_master, output, audio)
    return RenderStats(
        rendered_segments=0 if cache_hit else 1,
        cached_segments=1 if cache_hit else 0,
        renderer=active_renderer,
        chars_per_second=chars_per_second,
    )


def render_segmented(
    movie: Movie,
    output: Path,
    *,
    width: int,
    height: int,
    fps: int,
    chars_per_second: float,
    max_duration: float | None,
    audio: Path | None,
    cache_root: Path,
    renderer: str = "auto",
    preview: bool = False,
    use_cache: bool = False,
) -> RenderStats:
    frames = tuple(frame for frame in movie.semantic_frames() if frame.display_goals)
    if not frames:
        raise SystemExit("The Lean trace contains no visible proof states.")
    chars_per_second = effective_write_speed(
        frames,
        requested=chars_per_second,
        max_duration=max_duration,
        transition_seconds=TRANSITION_SECONDS,
        fps=fps,
    )
    indices = _preview_indices(len(frames)) if preview else tuple(range(len(frames)))
    preferred_renderer = _resolve_renderer(renderer)
    if preferred_renderer == "cairo" and not preview and len(indices) > 4:
        configured_chunk = os.environ.get("LEAN_PROOF_CHUNK_SIZE", "24")
        try:
            chunk_size = int(configured_chunk)
        except ValueError:
            chunk_size = 24
        if chunk_size > 1:
            return _render_cairo_chunks_parallel(
                movie,
                frames,
                output,
                width=width,
                height=height,
                fps=fps,
                chars_per_second=chars_per_second,
                audio=audio,
                cache_root=cache_root,
                use_cache=use_cache,
                chunk_size=max(2, min(chunk_size, 96)),
            )
        return _render_cairo_segments_parallel(
            movie,
            frames,
            indices,
            output,
            width=width,
            height=height,
            fps=fps,
            chars_per_second=chars_per_second,
            audio=audio,
            cache_root=cache_root,
            use_cache=use_cache,
        )
    used_renderers: set[str] = set()
    opengl_disabled = False
    segments: list[Path] = []
    rendered_count = 0
    cached_count = 0

    for index in indices:
        active_renderer = preferred_renderer
        if (
            renderer == "auto"
            and active_renderer == "opengl"
            and (opengl_disabled or not _opengl_safe_for_frame(frames[index]))
        ):
            active_renderer = "cairo"
        key = _segment_key(
            frames,
            index,
            chars_per_second,
            TRANSITION_SECONDS,
            width,
            height,
            fps,
            active_renderer,
        )
        fallback_marker = cache_root / "renderer-fallback" / f"{key}.txt"
        if (
            renderer == "auto"
            and active_renderer == "opengl"
            and use_cache
            and fallback_marker.exists()
        ):
            active_renderer = "cairo"
            key = _segment_key(
                frames,
                index,
                chars_per_second,
                TRANSITION_SECONDS,
                width,
                height,
                fps,
                active_renderer,
            )
        segment = cache_root / "segments" / f"{key}.mp4"
        if use_cache and segment.exists() and segment.stat().st_size > 0:
            cached_count += 1
            segments.append(segment)
            continue

        try:
            _render_segment_guarded(
                movie,
                index,
                chars_per_second,
                TRANSITION_SECONDS,
                segment,
                width,
                height,
                fps,
                active_renderer,
                cache_root,
                use_cache,
            )
        except Exception:
            if renderer != "auto" or active_renderer != "opengl":
                raise
            if use_cache:
                fallback_marker.parent.mkdir(parents=True, exist_ok=True)
                fallback_marker.write_text(
                    "OpenGL failed or exceeded the segment deadline; use Cairo.\n",
                    encoding="utf-8",
                )
            opengl_disabled = True
            active_renderer = "cairo"
            print("OpenGL is unavailable for this scene; falling back to Cairo.")
            key = _segment_key(
                frames,
                index,
                chars_per_second,
                TRANSITION_SECONDS,
                width,
                height,
                fps,
                active_renderer,
            )
            segment = cache_root / "segments" / f"{key}.mp4"
            if use_cache and segment.exists() and segment.stat().st_size > 0:
                cached_count += 1
                segments.append(segment)
                continue
            _render_segment_guarded(
                movie,
                index,
                chars_per_second,
                TRANSITION_SECONDS,
                segment,
                width,
                height,
                fps,
                active_renderer,
                cache_root,
                use_cache,
            )
        rendered_count += 1
        used_renderers.add(active_renderer)
        segments.append(segment)

    _assemble_segments(segments, output, audio, cache_root)
    renderer_label = (
        "+".join(sorted(used_renderers)) if used_renderers else preferred_renderer
    )
    return RenderStats(
        rendered_count,
        cached_count,
        renderer_label,
        chars_per_second,
    )
