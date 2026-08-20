"""Shared typed render orchestration and structured progress events.

The command line and local web studio intentionally call the same strict CLI
pipeline.  This module adds a stable request/event boundary without moving
kernel validation or renderer policy into the user interface.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import sys
from typing import Callable, Literal, Protocol, TextIO

from proof_video.trace_profile import resolve_trace_profile


JobKind = Literal["validate", "preview-head", "preview-tail", "render-full"]


@dataclass(frozen=True)
class RenderRequest:
    lean_file: Path
    theorem: str
    output: Path
    kind: JobKind = "render-full"
    quality: str = "high"
    fps: int | None = None
    write_speed: float | None = None
    audio: Path | None = None
    no_audio: bool = False
    render_hardware: str = "auto"
    render_concurrency: str = "auto"
    render_chunking: str = "auto"
    toolchain_backend: str = "auto"
    trace_backend: str | None = None
    trace_mode: str = "auto"
    resume: bool = True
    use_cache: bool = True
    rebuild_trace: bool = False
    recalibrate_renderer: bool = False

    def cli_arguments(self) -> list[str]:
        profile = resolve_trace_profile(
            self.lean_file,
            requested_mode=self.trace_mode,
            requested_toolchain=self.toolchain_backend,
            requested_trace_backend=self.trace_backend,
            resume=self.resume,
        )
        arguments = [
            str(self.lean_file),
            self.theorem,
            "--engine",
            "remotion",
            "--quality",
            self.quality,
            "--toolchain-backend",
            profile.toolchain_backend,
            "--trace-mode",
            profile.trace_mode,
            "--render-hardware",
            self.render_hardware,
            "--render-concurrency",
            self.render_concurrency,
            "--render-chunking",
            self.render_chunking,
            "--output",
            str(self.output),
        ]
        if profile.trace_backend is not None:
            arguments.extend(("--trace-backend", profile.trace_backend))
        if self.fps is not None:
            arguments.extend(("--fps", str(self.fps)))
        if self.write_speed is not None:
            arguments.extend(("--write-speed", str(self.write_speed)))
        if self.audio is not None:
            arguments.extend(("--audio", str(self.audio)))
        elif self.no_audio:
            arguments.append("--no-audio")
        if profile.resumable:
            arguments.append("--resume")
        elif self.use_cache:
            arguments.append("--cache")
        else:
            arguments.append("--no-cache")
        if self.rebuild_trace:
            arguments.append("--rebuild-trace")
        if self.recalibrate_renderer:
            arguments.append("--recalibrate-renderer")
        if self.kind == "validate":
            arguments.append("--json-only")
        elif self.kind == "preview-head":
            arguments.extend(("--preview-seconds", "20"))
        elif self.kind == "preview-tail":
            arguments.append("--preview-tail")
        return arguments


@dataclass(frozen=True)
class ProgressEvent:
    sequence: int
    timestamp: str
    kind: str
    phase: str
    message: str
    progress: float | None = None
    elapsed_seconds: float | None = None
    eta_low_seconds: float | None = None
    eta_high_seconds: float | None = None
    cached: bool | None = None

    def to_json(self) -> dict[str, object]:
        return asdict(self)


class ProgressSink(Protocol):
    def __call__(self, event: ProgressEvent) -> None: ...


_PERCENT = re.compile(r"(?<!\d)(100(?:\.0+)?|\d{1,2}(?:\.\d+)?)%")
_ELAPSED = re.compile(r"elapsed\s+(\d+):(\d{2})(?::(\d{2}))?", re.I)
_ETA_RANGE = re.compile(
    r"ETA\s+(\d+):(\d{2})(?::(\d{2}))?\s*[–-]\s*"
    r"(\d+):(\d{2})(?::(\d{2}))?",
    re.I,
)
_ETA_SINGLE = re.compile(r"ETA\s+(\d+):(\d{2})(?::(\d{2}))?", re.I)


def _clock_seconds(groups: tuple[str | None, ...]) -> float:
    values = [int(item or 0) for item in groups]
    if len(values) == 3 and groups[2] is not None:
        return float(values[0] * 3600 + values[1] * 60 + values[2])
    return float(values[0] * 60 + values[1])


def classify_progress_line(line: str) -> tuple[str, float | None]:
    lower = line.lower()
    phases = (
        ("lean", ("lean trace", "elaborat", "kernel", "snapshot")),
        ("audit", ("strict audit", "semantic and presentation qa")),
        ("timeline", ("timeline:", "preparing proof animation")),
        ("bundle", ("bundling renderer", "bundle")),
        ("render", ("checkpoint", "rendering", "rendered ", "frames at")),
        ("encode", ("encoded", "encoding", "nvenc", "x264")),
        ("audio", ("audio", "mux")),
        ("visual-qa", ("visual qa",)),
    )
    phase = "pipeline"
    for candidate, needles in phases:
        if any(needle in lower for needle in needles):
            phase = candidate
            break
    match = _PERCENT.search(line)
    progress = float(match.group(1)) / 100.0 if match else None
    return phase, progress


class _ProgressStream:
    def __init__(self, sink: ProgressSink, mirror: TextIO | None) -> None:
        self.sink = sink
        self.mirror = mirror
        self.buffer = ""
        self.sequence = 0

    def write(self, value: str) -> int:
        if self.mirror is not None:
            self.mirror.write(value)
            self.mirror.flush()
        self.buffer += value
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            self._emit(line.rstrip("\r"))
        return len(value)

    def flush(self) -> None:
        if self.mirror is not None:
            self.mirror.flush()

    def close(self) -> None:
        if self.buffer:
            self._emit(self.buffer.rstrip("\r"))
            self.buffer = ""

    def _emit(self, line: str) -> None:
        if not line:
            return
        self.sequence += 1
        phase, progress = classify_progress_line(line)
        elapsed_match = _ELAPSED.search(line)
        range_match = _ETA_RANGE.search(line)
        single_match = _ETA_SINGLE.search(line) if range_match is None else None
        eta_low = eta_high = None
        if range_match is not None:
            eta_low = _clock_seconds(range_match.groups()[:3])
            eta_high = _clock_seconds(range_match.groups()[3:])
        elif single_match is not None:
            eta_low = eta_high = _clock_seconds(single_match.groups())
        self.sink(
            ProgressEvent(
                sequence=self.sequence,
                timestamp=datetime.now(UTC).isoformat(),
                kind="progress",
                phase=phase,
                message=line,
                progress=progress,
                elapsed_seconds=(
                    _clock_seconds(elapsed_match.groups())
                    if elapsed_match is not None
                    else None
                ),
                eta_low_seconds=eta_low,
                eta_high_seconds=eta_high,
                cached=(True if "cache hit" in line.lower() else None),
            )
        )


class RenderService:
    """Run the strict CLI pipeline while exposing structured progress."""

    def __init__(
        self,
        sink: ProgressSink | None = None,
        *,
        mirror_output: bool = True,
        runner: Callable[[list[str]], int] | None = None,
    ) -> None:
        self.sink = sink
        self.mirror_output = mirror_output
        self.runner = runner

    def run(self, request: RenderRequest) -> int:
        return self.run_arguments(request.cli_arguments())

    def run_arguments(self, arguments: list[str]) -> int:
        from contextlib import redirect_stderr, redirect_stdout

        if self.runner is None:
            from proof_video.cli import main as cli_main
        else:
            cli_main = self.runner

        if self.sink is None:
            return cli_main(arguments)
        stream = _ProgressStream(
            self.sink,
            sys.__stdout__ if self.mirror_output else None,
        )
        try:
            with redirect_stdout(stream), redirect_stderr(stream):
                return cli_main(arguments)
        finally:
            stream.close()


class JsonLinesProgressSink:
    """Atomically append web-worker events to a durable journal."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, event: ProgressEvent) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_json(), ensure_ascii=False) + "\n")
            handle.flush()


def cli_entrypoint(argv: list[str] | None = None) -> int:
    """Installed CLI entrypoint using the same service boundary as the studio."""
    return RenderService().run_arguments(
        list(argv if argv is not None else sys.argv[1:])
    )
