"""Lean extractor subprocess, durable progress, and ETA reporting."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import tempfile
import time

from proof_video.lean_profile import (
    format_profile_summary,
    read_command_profile,
    source_location,
)
from proof_video.lean_runner import (
    ensure_extractor_executable,
    ensure_snapshot_reader_modules,
    lean_runtime_environment,
)
from proof_video.toolchains import ToolchainBackend


def export_trace(
    root: Path,
    lean_file: Path,
    theorem: str,
    trace_mode: str = "hybrid",
    *,
    checkpoint_dir: Path | None = None,
    trace_output_dir: Path | None = None,
    postprocess_workers: int = 4,
    module_output: Path | None = None,
    rebuild_chapter: str | None = None,
    toolchain_backend: ToolchainBackend | None = None,
    trace_backend: str = "legacy",
) -> dict:
    snapshot_path: Path | None = None
    snapshot_certificate: Path | None = None
    if trace_backend == "snapshot":
        if toolchain_backend is None:
            raise ValueError("snapshot trace backend needs an explicit toolchain backend")
        from proof_video.snapshot_runtime import refresh_incremental_snapshots

        # Build the reader and all imported support modules once, but execute
        # it inside the official dynamically-linked `lean` process.  Snapshot
        # closures are owned by that process's shared libraries and cannot be
        # relocated safely inside an independently linked executable.
        ensure_snapshot_reader_modules(root)
        snapshot_result = refresh_incremental_snapshots(
            toolchain_backend, lean_file, theorem, module_output
        )
        snapshot_path = snapshot_result.full_snapshot
        snapshot_certificate = snapshot_result.certificate
        extractor = None
    else:
        extractor = ensure_extractor_executable(root)
    if snapshot_path is not None:
        assert toolchain_backend is not None
        command = [
            "elan",
            "run",
            toolchain_backend.lean_toolchain,
            "lean",
            "--run",
            str((root / "SnapshotAnimate432.lean").resolve()),
            str(lean_file),
            theorem,
        ]
    else:
        assert extractor is not None
        command = [str(extractor), str(lean_file), theorem]
    if snapshot_path is not None:
        command.append(str(snapshot_path.resolve()))
        assert snapshot_certificate is not None
        command.append(str(snapshot_certificate.resolve()))
    command.extend([
        "--trace-mode", trace_mode,
        "--postprocess-workers", str(postprocess_workers),
    ])
    if checkpoint_dir is not None and trace_mode in {"hybrid", "proof-term"}:
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        command.extend(("--trace-checkpoint-dir", str(checkpoint_dir.resolve())))
    if trace_output_dir is not None and trace_mode == "hybrid":
        trace_output_dir.mkdir(parents=True, exist_ok=True)
        command.extend(("--trace-output-dir", str(trace_output_dir.resolve())))
    if module_output is not None and trace_backend != "snapshot":
        module_output.parent.mkdir(parents=True, exist_ok=True)
        command.extend(("--module-output", str(module_output.resolve())))
    if rebuild_chapter is not None:
        command.extend(("--rebuild-chapter", rebuild_chapter))
    progress_path = checkpoint_dir / "progress.json" if checkpoint_dir else None
    if progress_path is not None:
        progress_path.unlink(missing_ok=True)
    if snapshot_path is not None:
        from proof_video.cache import read_json
        from proof_video.incremental_snapshot import snapshot_metadata_path
        from proof_video.snapshot_worker import (
            SnapshotWorkerError,
            request_snapshot_trace,
        )

        assert toolchain_backend is not None and snapshot_certificate is not None
        # The worker receives Animate's public argument vector, not the
        # one-shot reader's two transport arguments.
        animate_args = [str(lean_file), theorem]
        certificate_index = command.index(str(snapshot_certificate.resolve()))
        animate_args.extend(command[certificate_index + 1 :])
        try:
            document = _request_snapshot_trace_with_progress(
                lambda: request_snapshot_trace(
                    root,
                    toolchain=toolchain_backend.lean_toolchain,
                    snapshot=snapshot_path,
                    snapshot_metadata=read_json(snapshot_metadata_path(snapshot_path)),
                    certificate=snapshot_certificate,
                    animate_args=animate_args,
                ),
                progress_path=progress_path,
                lean_file=lean_file,
            )
        except (OSError, ValueError, SnapshotWorkerError) as error:
            print(
                "Lean 4.32 worker unavailable; using the verified one-shot "
                f"snapshot reader ({error}).",
                flush=True,
            )
        else:
            if trace_mode == "hybrid":
                document["schemaVersion"] = "4.0"
                document["snapshotTransport"] = "lean-4.32-incremental-worker"
            return document
    started = time.monotonic()
    print(
        f"Lean trace: starting verified Animate extractor ({trace_backend})...",
        flush=True,
    )
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        try:
            process = subprocess.Popen(
                command,
                cwd=root,
                env=lean_runtime_environment(root),
                stdout=stdout_file,
                stderr=stderr_file,
            )
        except FileNotFoundError as error:
            raise SystemExit(
                f"The verified Lean extractor disappeared: {extractor}"
            ) from error

        next_report = 5.0
        next_progress_report = 0.0
        last_checkpoint_count = -1
        last_progress_signature: tuple[object, ...] | None = None
        progress_started: float | None = None
        progress_baseline = 0.0
        finalizing_reported = False
        active_command: tuple[int, int] | None = None
        active_command_started: float | None = None
        try:
            lean_source = lean_file.read_bytes()
        except OSError:
            lean_source = b""
        try:
            while process.poll() is None:
                time.sleep(0.25)
                if checkpoint_dir is not None:
                    checkpoint_count = sum(
                        1
                        for pattern in ("chapter-*.json", "source-chapter-*.json")
                        for _ in checkpoint_dir.glob(pattern)
                    )
                    if checkpoint_count != last_checkpoint_count:
                        if checkpoint_count:
                            print(
                                f"Lean trace: {checkpoint_count} theorem chapter "
                                f"checkpoint(s) available.",
                                flush=True,
                            )
                        last_checkpoint_count = checkpoint_count
                elapsed = time.monotonic() - started
                progress = _read_trace_progress(progress_path)
                if progress is not None:
                    fraction = _trace_progress_fraction(progress)
                    measurable_phase = str(progress.get("phase", "")) in {
                        "elaborating-source",
                        "elaborating-command",
                        "counting-proof-nodes",
                        "extracting",
                        "serializing",
                        "source-tactic-actions",
                    }
                    if progress_started is None and measurable_phase:
                        progress_started = time.monotonic()
                        progress_baseline = fraction
                    signature = (
                        progress.get("phase"),
                        progress.get("chapterIndex"),
                        progress.get("theoremName"),
                        progress.get("processedSteps"),
                        progress.get("commandStartByte"),
                    )
                    command_key = (
                        int(progress.get("commandIndex", 0)),
                        int(progress.get("commandStartByte", 0)),
                    )
                    if str(progress.get("phase", "")) == "elaborating-command":
                        if command_key != active_command:
                            active_command = command_key
                            active_command_started = time.monotonic()
                    else:
                        active_command = None
                        active_command_started = None
                    important_change = signature[:3] != (
                        last_progress_signature[:3]
                        if last_progress_signature is not None
                        else (None, None, None)
                    )
                    finalizing = fraction >= 1.0
                    if (
                        important_change
                        or (finalizing and not finalizing_reported)
                        or elapsed >= next_progress_report
                    ):
                        eta = (
                            _trace_progress_eta(
                                fraction,
                                progress_baseline,
                                time.monotonic() - progress_started,
                            )
                            if progress_started is not None
                            else None
                        )
                        progress_line = (
                                _format_trace_finalizing(progress, elapsed)
                                if finalizing
                                else _format_trace_progress(
                                    progress,
                                    fraction,
                                    elapsed,
                                    eta,
                                    lean_source=lean_source,
                                    command_elapsed=(
                                        time.monotonic() - active_command_started
                                        if active_command_started is not None
                                        else None
                                    ),
                                )
                        )
                        if toolchain_backend is not None:
                            progress_line = (
                                f"{toolchain_backend.name}: {lean_file.stem} | "
                                f"{progress_line}"
                            )
                        print(progress_line, flush=True)
                        finalizing_reported = finalizing_reported or finalizing
                        next_progress_report = elapsed + (15.0 if finalizing else 5.0)
                    last_progress_signature = signature
                    next_report = elapsed + 5.0
                elif elapsed >= next_report:
                    print(
                        f"Lean trace: elaborating/exporting... "
                        f"{_format_elapsed(elapsed)} elapsed",
                        flush=True,
                    )
                    next_report += 5.0
        except KeyboardInterrupt:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            raise

        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read().decode("utf-8", errors="replace")
        stderr = stderr_file.read().decode("utf-8", errors="replace")
        if process.returncode:
            details = "\n".join(
                output for output in (stdout.strip(), stderr.strip()) if output
            )
            if not details:
                details = f"extractor exited with code {process.returncode}"
            raise SystemExit(f"Lean could not elaborate/export the theorem:\n{details}")

    print(
        f"Lean trace: complete in {_format_elapsed(time.monotonic() - started)}.",
        flush=True,
    )
    if checkpoint_dir is not None:
        summary = format_profile_summary(
            read_command_profile(checkpoint_dir / "command-profile.json", lean_file)
        )
        if summary:
            print(summary, flush=True)
    document = json.loads(stdout)
    if trace_backend == "snapshot" and isinstance(document, dict):
        document["schemaVersion"] = "4.0"
        document["snapshotTransport"] = "lean-4.32-incremental"
    return document


def _format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, remaining_seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{remaining_seconds:02d}"
    return f"{minutes:02d}:{remaining_seconds:02d}"


def _format_eta_interval(eta: float, fraction: float) -> str:
    """Show an honest narrowing interval instead of false point precision."""

    uncertainty = 0.35 if fraction < 0.15 else 0.25 if fraction < 0.5 else 0.15
    low = max(0.0, eta * (1.0 - uncertainty))
    high = max(low, eta * (1.0 + uncertainty))
    return f"{_format_elapsed(low)}–{_format_elapsed(high)}"


def _read_trace_progress(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _request_snapshot_trace_with_progress(
    request,
    *,
    progress_path: Path | None,
    lean_file: Path,
) -> dict:
    """Poll the ordinary progress journal while a persistent worker replies."""

    started = time.monotonic()
    try:
        source = lean_file.read_bytes()
    except OSError:
        source = b""
    print("Lean trace: requesting the in-memory 4.32 snapshot tree...", flush=True)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="lean-snapshot-client") as pool:
        future = pool.submit(request)
        next_report = 5.0
        last_signature: tuple[object, ...] | None = None
        while not future.done():
            time.sleep(0.25)
            elapsed = time.monotonic() - started
            progress = _read_trace_progress(progress_path)
            if progress is not None:
                fraction = _trace_progress_fraction(progress)
                signature = (
                    progress.get("phase"),
                    progress.get("chapterIndex"),
                    progress.get("processedSteps"),
                )
                if signature != last_signature or elapsed >= next_report:
                    print(
                        _format_trace_progress(
                            progress,
                            fraction,
                            elapsed,
                            _trace_progress_eta(fraction, 0.0, elapsed),
                            lean_source=source,
                        ),
                        flush=True,
                    )
                    last_signature = signature
                    next_report = elapsed + 5.0
            elif elapsed >= next_report:
                print(
                    "Lean trace: loading the verified in-memory snapshot "
                    f"| elapsed {_format_elapsed(elapsed)}",
                    flush=True,
                )
                next_report = elapsed + 5.0
        document = future.result()
    print(
        f"Lean trace: worker complete in {_format_elapsed(time.monotonic() - started)}.",
        flush=True,
    )
    return document


def _trace_progress_fraction(progress: dict) -> float:
    total_weight = max(0, int(progress.get("totalWeight", 0)))
    if total_weight <= 0:
        chapter_count = max(1, int(progress.get("chapterCount", 0)))
        return min(
            1.0,
            max(0.0, int(progress.get("completedChapters", 0)) / chapter_count),
        )
    completed_weight = max(0, int(progress.get("completedWeight", 0)))
    current_weight = max(0, int(progress.get("proofObjects", 0)))
    total_steps = max(0, int(progress.get("totalSteps", 0)))
    processed_steps = max(0, int(progress.get("processedSteps", 0)))
    current_fraction = min(1.0, processed_steps / total_steps) if total_steps else 0.0
    completed = completed_weight + current_weight * current_fraction
    return min(1.0, max(0.0, completed / total_weight))


def _trace_progress_eta(
    fraction: float, baseline: float, elapsed: float
) -> float | None:
    progress = fraction - baseline
    if elapsed < 2.0 or progress <= 0.0001 or fraction >= 1.0:
        return None
    return max(0.0, elapsed * (1.0 - fraction) / progress)


def _format_trace_progress(
    progress: dict,
    fraction: float,
    elapsed: float,
    eta: float | None,
    *,
    lean_source: bytes = b"",
    command_elapsed: float | None = None,
) -> str:
    chapter_count = max(0, int(progress.get("chapterCount", 0)))
    chapter_index = max(0, int(progress.get("chapterIndex", 0)))
    theorem = str(progress.get("theoremName", "")) or "dependency discovery"
    tactic = " ".join(str(progress.get("currentTactic", "")).split())
    tactic_text = f" | tactic {tactic[:96]}" if tactic else ""
    processed = max(0, int(progress.get("processedSteps", 0)))
    total = max(0, int(progress.get("totalSteps", 0)))
    phase = str(progress.get("phase", "extracting"))
    command_text = ""
    if phase in {"elaborating-command", "elaborating-source"} and lean_source:
        command_start = max(0, int(progress.get("commandStartByte", 0)))
        location = source_location(lean_source, command_start)
        duration = (
            command_elapsed
            if command_elapsed is not None
            else max(0, int(progress.get("commandElapsedMs", 0))) / 1000
        )
        duration_text = (
            f" | current command {duration:.1f}s" if duration is not None else ""
        )
        command_text = (
            f" | line {location.line}: {location.label}{duration_text}"
        )
    chapter = (
        f"chapter {min(chapter_index + 1, chapter_count)}/{chapter_count}"
        if chapter_count
        else "discovering chapters"
    )
    step_progress = f" | proof nodes {processed}/{total}" if total else ""
    rate_text = ""
    if elapsed >= 1.0 and fraction > 0:
        rate_text = f" | rate {fraction * 100.0 * 60.0 / elapsed:.2f}%/min"
    if fraction >= 1.0:
        eta_text = " | ETA 00:00"
    elif eta is not None:
        eta_text = f" | ETA {_format_eta_interval(eta, fraction)}"
    else:
        eta_text = " | ETA calculating..."
    return (
        f"Lean trace: {fraction * 100:5.1f}% | {phase} | {chapter}: {theorem}"
        f"{step_progress}{tactic_text}{command_text} | elapsed {_format_elapsed(elapsed)}"
        f"{rate_text}{eta_text}"
    )


def _format_trace_finalizing(progress: dict, elapsed: float) -> str:
    """Describe the exporter flush/link phase without a false zero-second ETA."""

    chapter_count = max(0, int(progress.get("chapterCount", 0)))
    chapter_index = max(0, int(progress.get("chapterIndex", 0)))
    theorem = str(progress.get("theoremName", "")) or "dependency discovery"
    chapter = (
        f"chapter {min(chapter_index + 1, chapter_count)}/{chapter_count}"
        if chapter_count
        else "all chapters"
    )
    return (
        f"Lean trace: 100.0% | finalizing trace files | {chapter}: {theorem}"
        f" | elapsed {_format_elapsed(elapsed)} | ETA waiting for final file flush"
    )
