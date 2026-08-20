"""Deterministic visual review artifacts for an already rendered proof video.

This module deliberately sits downstream of the renderer.  It never changes a
timeline or a video; it only samples the source, middle, and target of every
declared proof transition and arranges those samples for human inspection.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
import csv
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Literal

from PIL import Image, ImageDraw, ImageFont, ImageOps

from proof_video.cache import write_json


REVIEW_SCHEMA_VERSION = 1
SampleRole = Literal["before", "mid", "after"]


@dataclass(frozen=True, slots=True)
class ReviewSample:
    role: SampleRole
    frame: int
    time_seconds: float
    file: str


@dataclass(frozen=True, slots=True)
class TransitionReview:
    index: int
    from_state: int
    to_state: int
    start_frame: int
    duration_frames: int
    tactic: str
    samples: tuple[ReviewSample, ReviewSample, ReviewSample]


@dataclass(frozen=True, slots=True)
class ReviewPlan:
    fps: int
    duration_in_frames: int
    transitions: tuple[TransitionReview, ...]

    @property
    def selected_frames(self) -> tuple[int, ...]:
        return tuple(
            sorted(
                {
                    sample.frame
                    for transition in self.transitions
                    for sample in transition.samples
                }
            )
        )


def build_review_plan(timeline: Mapping[str, Any]) -> ReviewPlan:
    """Return an exact before/middle/after sample plan for every transition.

    Remotion evaluates a transition at ``startFrame`` with progress zero and
    reaches progress one at ``startFrame + durationFrames``.  Sampling those
    endpoints, rather than nearby timestamps, therefore captures the exact
    source and target states.  Adjacent transitions intentionally share the
    same boundary frame.
    """

    fps = _positive_int(timeline.get("fps"), "fps")
    duration = _positive_int(timeline.get("durationInFrames"), "durationInFrames")
    raw_transitions = timeline.get("transitions")
    if not isinstance(raw_transitions, list):
        raise ValueError("timeline.transitions must be an array")

    states = timeline.get("states")
    state_items = states if isinstance(states, list) else []
    result: list[TransitionReview] = []
    previous_start = -1
    last_frame = duration - 1
    for offset, raw in enumerate(raw_transitions):
        if not isinstance(raw, Mapping):
            raise ValueError(f"timeline.transitions[{offset}] must be an object")
        start = _nonnegative_int(raw.get("startFrame"), "startFrame", offset)
        transition_duration = _positive_int(
            raw.get("durationFrames"), "durationFrames", offset
        )
        if start < previous_start:
            raise ValueError("timeline transitions must be ordered by startFrame")
        if start >= duration:
            raise ValueError(
                f"transition {offset + 1} starts outside the timeline: {start}"
            )
        previous_start = start
        from_state = _nonnegative_int(raw.get("fromState"), "fromState", offset)
        to_state = _nonnegative_int(raw.get("toState"), "toState", offset)

        before = start
        middle = min(start + transition_duration // 2, last_frame)
        after = min(start + transition_duration, last_frame)
        tactic = _transition_tactic(raw, state_items, to_state)

        def frame_file(frame: int) -> str:
            return f"frames/frame-{frame:09d}.png"

        samples = (
            ReviewSample("before", before, before / fps, frame_file(before)),
            ReviewSample("mid", middle, middle / fps, frame_file(middle)),
            ReviewSample("after", after, after / fps, frame_file(after)),
        )
        result.append(
            TransitionReview(
                index=offset + 1,
                from_state=from_state,
                to_state=to_state,
                start_frame=start,
                duration_frames=transition_duration,
                tactic=tactic,
                samples=samples,
            )
        )

    if state_items:
        expected = tuple((index, index + 1) for index in range(len(state_items) - 1))
        observed = tuple(
            (transition.from_state, transition.to_state) for transition in result
        )
        if observed != expected:
            raise ValueError(
                "timeline does not expose exactly one ordered transition between "
                "each pair of adjacent proof states"
            )

    return ReviewPlan(
        fps=fps,
        duration_in_frames=duration,
        transitions=tuple(result),
    )


def extract_selected_frames(
    video: Path,
    output_dir: Path,
    frames: Sequence[int],
    *,
    ffmpeg: str | None = None,
    batch_size: int = 80,
) -> dict[int, Path]:
    """Decode exact numbered video frames in bounded command-line batches."""

    executable = ffmpeg or shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError("ffmpeg was not found on PATH")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    ordered = tuple(sorted(set(frames)))
    if any(frame < 0 for frame in ordered):
        raise ValueError("frame numbers must be non-negative")

    frame_dir = output_dir / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    extracted: dict[int, Path] = {}
    with tempfile.TemporaryDirectory(prefix="proof-review-", dir=output_dir) as temp:
        temporary = Path(temp)
        for batch_index, start in enumerate(range(0, len(ordered), batch_size)):
            batch = ordered[start : start + batch_size]
            selector = "+".join(f"eq(n\\,{frame})" for frame in batch)
            pattern = temporary / f"batch-{batch_index:04d}-%06d.png"
            command = [
                executable,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(video),
                "-vf",
                f"select={selector}",
                "-fps_mode",
                "passthrough",
                "-start_number",
                "0",
                str(pattern),
            ]
            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
            if process.returncode:
                detail = process.stderr.strip() or process.stdout.strip()
                raise RuntimeError(f"ffmpeg frame extraction failed: {detail}")
            produced = sorted(temporary.glob(f"batch-{batch_index:04d}-*.png"))
            if len(produced) != len(batch):
                raise RuntimeError(
                    "ffmpeg returned "
                    f"{len(produced)} frames for a requested batch of {len(batch)}"
                )
            for frame, source in zip(batch, produced, strict=True):
                destination = frame_dir / f"frame-{frame:09d}.png"
                source.replace(destination)
                extracted[frame] = destination
    return extracted


def write_contact_sheets(
    plan: ReviewPlan,
    output_dir: Path,
    frame_paths: Mapping[int, Path],
    *,
    transitions_per_sheet: int = 6,
    thumbnail_width: int = 480,
) -> tuple[Path, ...]:
    """Write numbered, legible sheets with one transition per row."""

    if transitions_per_sheet < 1:
        raise ValueError("transitions_per_sheet must be positive")
    if thumbnail_width < 120:
        raise ValueError("thumbnail_width must be at least 120 pixels")
    if not plan.transitions:
        return ()

    first_path = frame_paths[plan.transitions[0].samples[0].frame]
    with Image.open(first_path) as first:
        ratio = first.height / first.width
    thumbnail_height = max(68, round(thumbnail_width * ratio))
    gutter = 12
    page_header = 42
    row_header = 24
    cell_header = 20
    row_height = row_header + cell_header + thumbnail_height + gutter
    page_width = gutter + 3 * (thumbnail_width + gutter)
    font = _review_font(16)
    small_font = _review_font(13)

    sheets: list[Path] = []
    for page_index, start in enumerate(
        range(0, len(plan.transitions), transitions_per_sheet), start=1
    ):
        batch = plan.transitions[start : start + transitions_per_sheet]
        canvas = Image.new(
            "RGB",
            (page_width, page_header + len(batch) * row_height + gutter),
            "#07090e",
        )
        draw = ImageDraw.Draw(canvas)
        draw.text(
            (gutter, 10),
            f"Proof transitions {batch[0].index}-{batch[-1].index}",
            fill="#f3f5fa",
            font=font,
        )
        for row, transition in enumerate(batch):
            top = page_header + row * row_height
            tactic = transition.tactic or "unlabelled action"
            label = (
                f"{transition.index:04d}  state {transition.from_state} -> "
                f"{transition.to_state}  {tactic}"
            )
            draw.text((gutter, top), label, fill="#dce3f5", font=small_font)
            image_top = top + row_header + cell_header
            for column, sample in enumerate(transition.samples):
                left = gutter + column * (thumbnail_width + gutter)
                draw.text(
                    (left, top + row_header),
                    f"{sample.role} | frame {sample.frame} | {sample.time_seconds:.3f}s",
                    fill="#98a7c4",
                    font=small_font,
                )
                with Image.open(frame_paths[sample.frame]) as source:
                    thumbnail = ImageOps.contain(
                        source.convert("RGB"),
                        (thumbnail_width, thumbnail_height),
                        Image.Resampling.LANCZOS,
                    )
                x = left + (thumbnail_width - thumbnail.width) // 2
                y = image_top + (thumbnail_height - thumbnail.height) // 2
                canvas.paste(thumbnail, (x, y))
        destination = output_dir / f"contact-sheet-{page_index:03d}.png"
        canvas.save(destination, optimize=True)
        sheets.append(destination)
    return tuple(sheets)


def review_render(
    video: Path,
    timeline_path: Path,
    output_dir: Path,
    *,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
    transitions_per_sheet: int = 6,
    thumbnail_width: int = 480,
) -> dict[str, Any]:
    """Create the complete machine- and human-readable review package."""

    video = video.resolve()
    timeline_path = timeline_path.resolve()
    output_dir = output_dir.resolve()
    if not video.is_file():
        raise FileNotFoundError(video)
    if not timeline_path.is_file():
        raise FileNotFoundError(timeline_path)
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    if not isinstance(timeline, Mapping):
        raise ValueError("timeline root must be an object")
    plan = build_review_plan(timeline)
    media = probe_video(video, ffprobe=ffprobe)
    _validate_video(plan, media)

    output_dir.mkdir(parents=True, exist_ok=True)
    frame_paths = extract_selected_frames(
        video,
        output_dir,
        plan.selected_frames,
        ffmpeg=ffmpeg,
    )
    sheets = write_contact_sheets(
        plan,
        output_dir,
        frame_paths,
        transitions_per_sheet=transitions_per_sheet,
        thumbnail_width=thumbnail_width,
    )
    manifest = _manifest(
        video,
        timeline_path,
        output_dir,
        plan,
        media,
        sheets,
    )
    write_json(output_dir / "manifest.json", manifest)
    _write_manifest_csv(output_dir / "manifest.csv", plan)
    return manifest


def probe_video(path: Path, *, ffprobe: str | None = None) -> dict[str, Any]:
    executable = ffprobe or shutil.which("ffprobe")
    if not executable:
        raise RuntimeError("ffprobe was not found on PATH")
    process = subprocess.run(
        [
            executable,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,avg_frame_rate,nb_frames,duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode:
        detail = process.stderr.strip() or process.stdout.strip()
        raise RuntimeError(f"ffprobe failed: {detail}")
    payload = json.loads(process.stdout)
    streams = payload.get("streams", [])
    if not streams:
        raise RuntimeError("ffprobe found no video stream")
    stream = streams[0]
    rate = Fraction(stream.get("avg_frame_rate", "0/1"))
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "fps": float(rate),
        "frameCount": _optional_int(stream.get("nb_frames")),
        "durationSeconds": _optional_float(stream.get("duration")),
    }


def _manifest(
    video: Path,
    timeline: Path,
    output_dir: Path,
    plan: ReviewPlan,
    media: Mapping[str, Any],
    sheets: Sequence[Path],
) -> dict[str, Any]:
    return {
        "schemaVersion": REVIEW_SCHEMA_VERSION,
        "selectionContract": (
            "before=startFrame; mid=startFrame+floor(durationFrames/2); "
            "after=min(startFrame+durationFrames,lastFrame)"
        ),
        "video": {
            "path": str(video),
            "sha256": _sha256(video),
            **media,
        },
        "timeline": {
            "path": str(timeline),
            "sha256": _sha256(timeline),
            "fps": plan.fps,
            "durationInFrames": plan.duration_in_frames,
        },
        "transitionCount": len(plan.transitions),
        "selectedFrameCount": len(plan.selected_frames),
        "transitions": [
            {
                **asdict(transition),
                "samples": [asdict(sample) for sample in transition.samples],
            }
            for transition in plan.transitions
        ],
        "contactSheets": [str(path.relative_to(output_dir)) for path in sheets],
    }


def _write_manifest_csv(path: Path, plan: ReviewPlan) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "transition",
                "from_state",
                "to_state",
                "tactic",
                "start_frame",
                "duration_frames",
                "role",
                "frame",
                "time_seconds",
                "file",
            ),
        )
        writer.writeheader()
        for transition in plan.transitions:
            for sample in transition.samples:
                writer.writerow(
                    {
                        "transition": transition.index,
                        "from_state": transition.from_state,
                        "to_state": transition.to_state,
                        "tactic": transition.tactic,
                        "start_frame": transition.start_frame,
                        "duration_frames": transition.duration_frames,
                        "role": sample.role,
                        "frame": sample.frame,
                        "time_seconds": f"{sample.time_seconds:.9f}",
                        "file": sample.file,
                    }
                )
    temporary.replace(path)


def _validate_video(plan: ReviewPlan, media: Mapping[str, Any]) -> None:
    actual_fps = media.get("fps")
    if isinstance(actual_fps, (int, float)) and abs(actual_fps - plan.fps) > 0.01:
        raise ValueError(
            f"timeline is {plan.fps} fps but the video is {actual_fps:.6g} fps"
        )
    frame_count = media.get("frameCount")
    if isinstance(frame_count, int) and plan.selected_frames:
        if plan.selected_frames[-1] >= frame_count:
            raise ValueError(
                "timeline requests frame "
                f"{plan.selected_frames[-1]}, but the video has {frame_count} frames"
            )


def _transition_tactic(
    transition: Mapping[str, Any], states: Sequence[Any], to_state: int
) -> str:
    own = transition.get("tactic")
    if isinstance(own, str):
        return own
    if to_state < len(states) and isinstance(states[to_state], Mapping):
        tactic = states[to_state].get("tactic")
        if isinstance(tactic, str):
            return tactic
    return ""


def _positive_int(value: Any, field: str, index: int | None = None) -> int:
    parsed = _strict_int(value, field, index)
    if parsed < 1:
        where = f" in transition {index + 1}" if index is not None else ""
        raise ValueError(f"{field}{where} must be positive")
    return parsed


def _nonnegative_int(value: Any, field: str, index: int | None = None) -> int:
    parsed = _strict_int(value, field, index)
    if parsed < 0:
        where = f" in transition {index + 1}" if index is not None else ""
        raise ValueError(f"{field}{where} must be non-negative")
    return parsed


def _strict_int(value: Any, field: str, index: int | None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        where = f" in transition {index + 1}" if index is not None else ""
        raise ValueError(f"{field}{where} must be an integer")
    return value


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "N/A") else None
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "N/A") else None
    except (TypeError, ValueError):
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _review_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()
