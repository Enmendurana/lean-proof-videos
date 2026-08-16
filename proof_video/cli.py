from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from proof_video.cache import local_source_closure, read_json, write_json
from proof_video.evidence import acquire_lean_evidence
from proof_video.backend_policy import (
    backend_attempts,
    describe_attempt,
    run_with_backend_fallback,
)
from proof_video.models import Movie
from proof_video.rendering.pacing import DEFAULT_VISIBLE_GLYPHS_PER_SECOND
from proof_video.toolchains import (
    TOOLCHAIN_CHOICES,
    prepare_lean_432_workspace,
)


QUALITY = {
    "low": (854, 480, 15),
    "medium": (1280, 720, 30),
    "high": (1920, 1080, 30),
    "high60": (1920, 1080, 60),
    "shorts": (1080, 1920, 30),
    "shorts60": (1080, 1920, 60),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lean-proof-video",
        description="Render a verified Lean theorem as a cinematic MP4.",
    )
    parser.add_argument("lean_file", type=Path, help="Lean source containing the theorem")
    parser.add_argument("theorem", help="Fully qualified theorem name")
    parser.add_argument("-o", "--output", type=Path, default=Path("proof.mp4"))
    parser.add_argument("--quality", choices=QUALITY, default="high")
    parser.add_argument(
        "--fps",
        type=int,
        choices=(15, 24, 30, 60),
        help="Override the selected quality profile's frame rate",
    )
    parser.add_argument(
        "--engine",
        choices=("manim", "remotion"),
        default="remotion",
        help="Use the faster Remotion renderer (default) or the legacy Manim renderer",
    )
    parser.add_argument(
        "--renderer",
        choices=("auto", "opengl", "cairo"),
        default="auto",
        help="Use GPU OpenGL when available, with automatic Cairo fallback",
    )
    parser.add_argument(
        "--render-concurrency",
        "--remotion-concurrency",
        dest="remotion_concurrency",
        default="auto",
        help=(
            "Global Chromium tab budget: auto (calibrated default), a positive "
            "integer, or a percentage such as 75%%"
        ),
    )
    parser.add_argument(
        "--remotion-chunk-workers",
        type=int,
        default=1,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--render-hardware",
        choices=("auto", "cpu", "gpu-required"),
        default="auto",
        help="Automatically use a validated GPU encoder, force CPU, or require GPU",
    )
    parser.add_argument(
        "--render-chunking",
        default="auto",
        metavar="auto|SECONDS|off",
        help="Semantic resumable chunks (default auto), fixed seconds, or off",
    )
    parser.add_argument(
        "--recalibrate-renderer",
        action="store_true",
        help="Rebenchmark Chromium concurrency, GPU composition, and NVENC quality",
    )
    parser.add_argument(
        "--render-profile-report",
        type=Path,
        help="Write detailed per-stage renderer measurements to this JSON file",
    )
    parser.add_argument(
        "--lean-workers",
        type=int,
        default=min(4, os.cpu_count() or 1),
        help=(
            "Concurrent workers for independent post-elaboration Lean chapter "
            "certificates (source commands themselves remain sequential)"
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Persist the Lean trace, timeline, and independently reusable Remotion "
            "MP4 checkpoints so an interrupted long render can continue"
        ),
    )
    parser.add_argument(
        "--checkpoint-seconds",
        type=float,
        default=None,
        help=(
            "Legacy fixed checkpoint length. By default --resume uses semantic "
            "5-15 second checkpoints; prefer --render-chunking for new scripts."
        ),
    )
    audio = parser.add_mutually_exclusive_group()
    audio.add_argument(
        "--audio",
        type=Path,
        help="Override the project's default background soundtrack",
    )
    audio.add_argument(
        "--no-audio",
        action="store_true",
        help="Render a silent MP4 instead of using the default soundtrack",
    )
    parser.add_argument(
        "--max-duration",
        type=float,
        default=None,
        help="Optional hard duration ceiling in seconds (default: unlimited)",
    )
    parser.add_argument(
        "--write-speed",
        "--chars-per-second",
        dest="chars_per_second",
        type=float,
        default=DEFAULT_VISIBLE_GLYPHS_PER_SECOND,
        help=(
            "Middle proof-animation pace. Movement and writing share each step; "
            "the first and final step keep a fixed absolute speed. Higher values "
            "make the middle faster. "
            "The --chars-per-second alias is retained for compatibility."
        ),
    )
    parser.add_argument("--json-only", action="store_true", help="Only export the Lean trace next to the output")
    parser.add_argument("--trace", type=Path, help="Use an existing Animate JSON trace instead of running Lean")
    parser.add_argument(
        "--rebuild-trace",
        action="store_true",
        help=(
            "Ignore durable Lean evidence and elaborate the proof again. "
            "Renderer and animation changes do not require this option."
        ),
    )
    parser.add_argument(
        "--rebuild-chapter",
        metavar="THEOREM",
        help=(
            "Recompute one theorem chapter while reusing all other compatible "
            "fingerprint-validated chapters"
        ),
    )
    parser.add_argument(
        "--toolchain-backend",
        choices=TOOLCHAIN_CHOICES,
        default="auto",
        help=(
            "auto (try isolated 4.32, then fall back to 4.28), explicit "
            "lean-4.32, or rollback lean-4.28"
        ),
    )
    parser.add_argument(
        "--trace-backend",
        choices=("snapshot", "legacy"),
        default=None,
        help="Lean 4.32 incremental snapshot frontend or legacy frontend",
    )
    parser.add_argument(
        "--trace-mode",
        choices=("hybrid", "proof-term", "tactic"),
        default="hybrid",
        help=(
            "Use kernel-certified source-tactic chapters (default), the unbounded "
            "proof-term extractor, or the legacy single-theorem tactic extractor"
        ),
    )
    parser.add_argument(
        "--lean-module-output",
        type=Path,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--force-lean-export",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--dump-transition-map",
        type=Path,
        help="Write semantic block/token mapping diagnostics as JSON",
    )
    parser.add_argument(
        "--export-remotion",
        type=Path,
        help="Write a strict renderer-neutral timeline for the Remotion frontend",
    )
    preview_group = parser.add_mutually_exclusive_group()
    preview_group.add_argument(
        "--preview",
        action="store_true",
        help="Render a 20-second opening demo (Remotion) or representative transitions (Manim)",
    )
    preview_group.add_argument(
        "--preview-seconds",
        type=float,
        metavar="SECONDS",
        help="Render only the first SECONDS of the proof with Remotion",
    )
    preview_group.add_argument(
        "--preview-tail",
        action="store_true",
        help="Render the final 20 seconds including the QED (Remotion)",
    )
    parser.add_argument(
        "--render-mode",
        choices=("full", "segmented"),
        default="full",
        help="Render one continuous Manim scene (default) or cached video segments",
    )
    cache_group = parser.add_mutually_exclusive_group()
    cache_group.add_argument(
        "--cache",
        dest="cache",
        action="store_true",
        help=(
            "Reuse Manim/Remotion rendering artifacts in addition to the "
            "always-on verified Lean evidence cache"
        ),
    )
    cache_group.add_argument(
        "--no-cache",
        dest="cache",
        action="store_false",
        help=(
            "Disable renderer cache reuse. Verified Lean evidence remains "
            "persistent unless --rebuild-trace is supplied."
        ),
    )
    parser.set_defaults(cache=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    pipeline_started = time.monotonic()
    args = build_parser().parse_args(argv)
    _restore_windows_path()
    if args.max_duration is not None and args.max_duration < 5:
        raise SystemExit("--max-duration must be at least 5 seconds")
    if args.chars_per_second <= 0:
        raise SystemExit("--write-speed must be greater than zero")
    if args.checkpoint_seconds is not None and args.checkpoint_seconds <= 0:
        raise SystemExit("--checkpoint-seconds must be greater than zero")
    if args.remotion_chunk_workers <= 0:
        raise SystemExit("--remotion-chunk-workers must be greater than zero")
    if args.render_chunking not in {"auto", "off"}:
        try:
            chunk_seconds = float(args.render_chunking)
        except ValueError as error:
            raise SystemExit("--render-chunking must be auto, off, or seconds") from error
        if chunk_seconds <= 0:
            raise SystemExit("--render-chunking seconds must be greater than zero")
    if args.lean_workers <= 0:
        raise SystemExit("--lean-workers must be greater than zero")
    if args.preview_seconds is not None and args.preview_seconds <= 0:
        raise SystemExit("--preview-seconds must be greater than zero")
    if args.trace and (
        args.rebuild_trace or args.force_lean_export or args.rebuild_chapter
    ):
        raise SystemExit(
            "--trace cannot be combined with a forced Lean export"
        )
    if args.rebuild_trace and args.force_lean_export:
        raise SystemExit(
            "--rebuild-trace and --force-lean-export cannot be used together"
        )
    if args.rebuild_trace and args.rebuild_chapter:
        raise SystemExit(
            "--rebuild-trace and --rebuild-chapter cannot be used together"
        )
    if args.resume and not args.cache:
        args.cache = True
        print("Resume mode: persistent trace and render checkpoints enabled.", flush=True)
    root = Path(__file__).resolve().parents[1]
    cache_root = root / ".lean-proof-video-cache"
    try:
        primary_attempt = backend_attempts(
            root,
            cache_root,
            args.toolchain_backend,
            args.trace_backend,
        )[0]
    except ValueError as error:
        raise SystemExit(str(error)) from error
    toolchain_backend = primary_attempt.backend
    trace_backend = primary_attempt.trace_backend
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    opening_preview_seconds = (
        args.preview_seconds
        if args.preview_seconds is not None
        else (20.0 if args.preview else None)
    )

    output_trace_store = output.with_suffix(".trace") / "objects"
    trace_document_base: Path | None = None
    trace_started = time.monotonic()
    trace_cache_hit = False
    trace_source = "lean-export"
    evidence_result = None
    if args.trace:
        if trace_backend == "snapshot" and args.trace_mode != "hybrid":
            raise SystemExit(
                "--trace-backend snapshot currently requires --trace-mode hybrid"
            )
        print(describe_attempt(primary_attempt), flush=True)
        trace_source = "existing-sidecar"
        print(f"Using existing Lean proof trace: {args.trace.resolve()}", flush=True)
        trace_document = read_json(args.trace.resolve())
        trace_document_base = args.trace.resolve().parent
    else:
        original_lean_file = args.lean_file.resolve()

        def acquire_for_backend(candidate, candidate_trace_backend):
            if candidate_trace_backend == "snapshot" and args.trace_mode != "hybrid":
                raise ValueError(
                    "snapshot trace backend currently requires --trace-mode hybrid"
                )
            extraction_root = root
            lean_file = original_lean_file
            if candidate.name == "lean-4.32":
                sources = local_source_closure(root, original_lean_file)
                mapping = prepare_lean_432_workspace(
                    candidate,
                    sources,
                    entry_sources=[original_lean_file],
                )
                extraction_root = candidate.execution_root
                lean_file = mapping[original_lean_file]
            return acquire_lean_evidence(
                root=extraction_root,
                cache_root=candidate.evidence_cache_root(cache_root),
                output=output,
                lean_file=lean_file,
                theorem=args.theorem,
                trace_mode=args.trace_mode,
                rebuild_trace=args.rebuild_trace,
                postprocess_workers=args.lean_workers,
                force_export=args.force_lean_export,
                module_output=(
                    args.lean_module_output.resolve()
                    if args.lean_module_output is not None
                    else None
                ),
                toolchain_backend=candidate,
                trace_backend=candidate_trace_backend,
                rebuild_chapter=args.rebuild_chapter,
            )

        try:
            backend_result = run_with_backend_fallback(
                root,
                cache_root,
                args.toolchain_backend,
                args.trace_backend,
                acquire_for_backend,
                phase="trace acquisition",
            )
        except ValueError as error:
            raise SystemExit(str(error)) from error
        toolchain_backend = backend_result.backend
        trace_backend = backend_result.trace_backend
        evidence_result = backend_result.value
        trace_document = evidence_result.document
        trace_document_base = evidence_result.base_dir
        trace_cache_hit = evidence_result.cache_hit
        trace_source = evidence_result.source
    trace_seconds = time.monotonic() - trace_started

    from proof_video.trace_store import (
        hydrate_hybrid_manifest,
        ingest_hybrid_manifest,
        is_hybrid_manifest,
        iter_hybrid_chapters,
        relativize_hybrid_manifest,
    )

    # Always publish a portable sidecar object store next to the requested
    # video. This also migrates a cached manifest away from cache-owned paths.
    trace_document = ingest_hybrid_manifest(
        trace_document,
        output_trace_store,
        source_base=trace_document_base,
    )
    print("Loading and validating Lean proof trace...", flush=True)
    streaming_manifest = is_hybrid_manifest(trace_document)
    raw = None
    if streaming_manifest:
        movie = Movie.from_hybrid_chapters(
            trace_document,
            iter_hybrid_chapters(trace_document, base_dir=output.parent),
        )
    else:
        raw = hydrate_hybrid_manifest(trace_document, base_dir=output.parent)
        movie = Movie.from_json(raw)
    trace_path = output.with_suffix(".json")
    write_json(
        trace_path,
        relativize_hybrid_manifest(trace_document, manifest_dir=trace_path.parent),
    )
    if movie.proof_trace is not None:
        from proof_video.strict_audit import require_strict_audit

        audit_path = output.with_suffix(".audit.json")
        print("Running strict proof audit...", flush=True)
        try:
            audit = require_strict_audit(movie)
        except ValueError as error:
            raise SystemExit(str(error)) from error
        write_json(audit_path, audit)
        print(f"Strict audit: {audit_path}")
    elif movie.hybrid_trace is not None:
        from proof_video.strict_audit import (
            require_hybrid_audit,
            require_hybrid_audit_chapters,
        )

        audit_path = output.with_suffix(".audit.json")
        print("Running strict source-tactic/kernel audit...", flush=True)
        try:
            audit = (
                require_hybrid_audit_chapters(
                    trace_document,
                    iter_hybrid_chapters(trace_document, base_dir=output.parent),
                )
                if streaming_manifest
                else require_hybrid_audit(movie.hybrid_trace)
            )
        except ValueError as error:
            raise SystemExit(str(error)) from error
        write_json(audit_path, audit)
        print(f"Strict audit: {audit_path}")
    if evidence_result is not None:
        evidence_path = evidence_result.commit()
        if evidence_path is not None:
            print(f"Persistent Lean evidence: {evidence_path}", flush=True)
    from proof_video.quality import (
        build_movie_quality_report,
        require_movie_quality_report,
        write_quality_report,
    )

    print("Running semantic and presentation QA...", flush=True)
    try:
        # Audit exactly what the renderer consumes. Hybrid evidence may be an
        # older, still kernel-valid trace whose presentation payload is
        # upgraded in ``Movie.from_json`` (fallback LaTeX span remapping and
        # unique Lean-identity edges). Auditing the immutable raw JSON here
        # would reject the old representation instead of checking the safe
        # renderer-facing compatibility layer.
        quality = require_movie_quality_report(movie)
    except ValueError as error:
        quality = build_movie_quality_report(movie)
        qa_json, qa_html = write_quality_report(output, quality)
        raise SystemExit(f"{error}\nQA reports: {qa_json}, {qa_html}") from error
    qa_json, qa_html = write_quality_report(output, quality)
    print(f"Quality audit: {qa_json} ({len(quality['warnings'])} warning(s)); {qa_html}")
    if args.dump_transition_map:
        from proof_video.diagnostics import build_transition_map

        transition_map_path = args.dump_transition_map.resolve()
        write_json(transition_map_path, build_transition_map(movie))
        print(f"Transition map: {transition_map_path}")
    if args.export_remotion:
        from proof_video.remotion_export import build_remotion_timeline

        width, height, profile_fps = QUALITY[args.quality]
        remotion_path = args.export_remotion.resolve()
        write_json(
            remotion_path,
            build_remotion_timeline(
                movie,
                width=width,
                height=height,
                fps=args.fps or profile_fps,
                chars_per_second=args.chars_per_second,
                max_duration=args.max_duration,
                preview_seconds=(
                    opening_preview_seconds if args.engine == "remotion" else None
                ),
                preview_tail_seconds=(
                    20.0 if args.preview_tail and args.engine == "remotion" else None
                ),
            ),
        )
        print(f"Remotion timeline: {remotion_path}")
    if args.json_only:
        write_json(
            output.with_suffix(".metrics.json"),
            {
                "schemaVersion": 1,
                "trace": {
                    "source": trace_source,
                    "cacheHit": trace_cache_hit,
                    "wallSeconds": trace_seconds,
                    "toolchainBackend": toolchain_backend.name,
                    "traceBackend": trace_backend,
                },
                "pipelineWallSeconds": time.monotonic() - pipeline_started,
            },
        )
        print(trace_path)
        return 0

    default_audio = root / "assets" / "background-music.mp3"
    selected_audio = None if args.no_audio else (args.audio or default_audio)
    if selected_audio and not selected_audio.exists():
        raise SystemExit(f"Audio file does not exist: {selected_audio}")
    args.audio = selected_audio
    if args.engine == "remotion":
        from proof_video.remotion_render import render_remotion

        width, height, profile_fps = QUALITY[args.quality]
        fps = args.fps or profile_fps
        render_started = time.monotonic()
        try:
            stats = render_remotion(
                movie,
                output,
                width=width,
                height=height,
                fps=fps,
                chars_per_second=args.chars_per_second,
                max_duration=args.max_duration,
                cache_root=cache_root,
                use_cache=args.cache,
                concurrency=args.remotion_concurrency,
                chunk_workers=args.remotion_chunk_workers,
                project_root=root,
                preview_seconds=opening_preview_seconds,
                preview_tail_seconds=20.0 if args.preview_tail else None,
                audio=args.audio.resolve() if args.audio else None,
                checkpoint_seconds=args.checkpoint_seconds if args.resume else None,
                render_hardware=args.render_hardware,
                render_chunking=args.render_chunking,
                recalibrate_renderer=args.recalibrate_renderer,
                profile_report=(
                    args.render_profile_report.resolve()
                    if args.render_profile_report is not None
                    else None
                ),
            )
        except (RuntimeError, ValueError) as error:
            raise SystemExit(str(error)) from error
        print(
            f"Remotion: {stats.states} states, {stats.duration_seconds:.2f}s; "
            f"{stats.cached_segments} cached, {stats.rendered_segments} rendered; "
            f"global animation pace {stats.chars_per_second:.2f}."
        )
        if output.exists():
            from proof_video.visual_quality import require_visual_quality

            try:
                require_visual_quality(
                    output,
                    expected_width=width,
                    expected_height=height,
                    expected_duration=stats.duration_seconds,
                )
            except ValueError as error:
                raise SystemExit(str(error)) from error
            print(f"Visual QA: {output.with_suffix('.visual-qa.html')}")
        write_json(
            output.with_suffix(".metrics.json"),
            {
                "schemaVersion": 1,
                "trace": {
                    "source": trace_source,
                    "cacheHit": trace_cache_hit,
                    "wallSeconds": trace_seconds,
                    "toolchainBackend": toolchain_backend.name,
                    "traceBackend": trace_backend,
                },
                "render": {
                    "engine": "remotion",
                    "wallSeconds": time.monotonic() - render_started,
                    "videoSeconds": stats.duration_seconds,
                    "charsPerSecond": stats.chars_per_second,
                    "states": stats.states,
                    "cachedCheckpoints": stats.cached_segments,
                    "renderedCheckpoints": stats.rendered_segments,
                    "profileReport": (
                        str(getattr(stats, "profile_report", None))
                        if getattr(stats, "profile_report", None) is not None
                        else None
                    ),
                },
                "pipelineWallSeconds": time.monotonic() - pipeline_started,
            },
        )
        print(output)
        return 0

    from proof_video.render import render_full, render_segmented

    width, height, profile_fps = QUALITY[args.quality]
    fps = args.fps or profile_fps
    render_mode = args.render_mode
    if (args.preview or args.preview_seconds is not None or args.preview_tail) and render_mode == "full":
        render_mode = "segmented"
        print("Preview uses segmented rendering for representative transitions.")

    render_options = {
        "width": width,
        "height": height,
        "fps": fps,
        "chars_per_second": args.chars_per_second,
        "max_duration": args.max_duration,
        "audio": args.audio.resolve() if args.audio else None,
        "cache_root": cache_root,
        "renderer": args.renderer,
        "use_cache": args.cache,
    }
    render_started = time.monotonic()
    if render_mode == "segmented":
        stats = render_segmented(
            movie,
            output,
            preview=args.preview,
            **render_options,
        )
        print(
            f"Segments: {stats.cached_segments} cached, "
            f"{stats.rendered_segments} rendered ({stats.renderer})."
        )
    else:
        stats = render_full(movie, output, **render_options)
        print(
            f"Full scene: {stats.cached_segments} cached, "
            f"{stats.rendered_segments} rendered ({stats.renderer})."
        )
    write_json(
        output.with_suffix(".metrics.json"),
        {
            "schemaVersion": 1,
            "trace": {
                "source": trace_source,
                "cacheHit": trace_cache_hit,
                "wallSeconds": trace_seconds,
                "toolchainBackend": toolchain_backend.name,
                "traceBackend": trace_backend,
            },
            "render": {
                "engine": "manim",
                "mode": render_mode,
                "wallSeconds": time.monotonic() - render_started,
                "cachedSegments": stats.cached_segments,
                "renderedSegments": stats.rendered_segments,
                "renderer": stats.renderer,
            },
            "pipelineWallSeconds": time.monotonic() - pipeline_started,
        },
    )
    print(f"Writing speed: {stats.chars_per_second:.2f} glyphs/second.")
    print(output)
    return 0


def _restore_windows_path() -> None:
    """Recover tools installed in user/system PATH without activating PowerShell."""
    if os.name != "nt":
        return
    paths = [str(Path(sys.executable).parent), os.environ.get("PATH", "")]
    try:
        import winreg

        locations = (
            (winreg.HKEY_CURRENT_USER, r"Environment"),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            ),
        )
        for hive, key_name in locations:
            with winreg.OpenKey(hive, key_name) as key:
                value, _kind = winreg.QueryValueEx(key, "Path")
                paths.append(os.path.expandvars(value))
    except OSError:
        pass
    os.environ["PATH"] = os.pathsep.join(path for path in paths if path)


if __name__ == "__main__":
    sys.exit(main())
