"""Post-render media and blackboard-occupancy checks."""

from __future__ import annotations

from html import escape
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from proof_video.cache import write_json


_BLACK_INTERVAL = re.compile(
    r"black_start:(?P<start>[0-9.]+)\s+black_end:(?P<end>[0-9.]+)"
)
_BBOX = re.compile(
    r"x1:(?P<x1>\d+)\s+x2:(?P<x2>\d+)\s+y1:(?P<y1>\d+)\s+"
    r"y2:(?P<y2>\d+)\s+w:(?P<width>\d+)\s+h:(?P<height>\d+)"
)


def _probe(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {}
    process = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,nb_frames,duration",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode:
        return {}
    return json.loads(process.stdout)


def _black_intervals(path: Path) -> list[tuple[float, float]]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return []
    process = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-vf",
            "blackdetect=d=0.5:pic_th=0.985:pix_th=0.10",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return [
        (float(match.group("start")), float(match.group("end")))
        for match in _BLACK_INTERVAL.finditer(process.stderr)
    ]


def _last_content_bbox(path: Path) -> tuple[int, int] | None:
    """Return the real non-black extent of a frame near the video end."""

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    process = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-sseof",
            "-0.1",
            "-i",
            str(path),
            "-vf",
            "bbox=min_val=16",
            "-frames:v",
            "1",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    matches = list(_BBOX.finditer(process.stderr))
    if not matches:
        return None
    match = matches[-1]
    return int(match.group("width")), int(match.group("height"))


def _content_bbox_at(path: Path, timestamp: float) -> tuple[int, int] | None:
    """Return the non-black extent at one absolute video timestamp."""

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    process = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-ss",
            f"{max(0.0, timestamp):.6f}",
            "-i",
            str(path),
            "-vf",
            "bbox=min_val=16",
            "-frames:v",
            "1",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    matches = list(_BBOX.finditer(process.stderr))
    if not matches:
        return None
    match = matches[-1]
    return int(match.group("width")), int(match.group("height"))


def _progressive_initial_write(
    path: Path, start: float, end: float
) -> bool:
    """Distinguish sparse chalk writing from a genuinely blank intro.

    FFmpeg's black detector measures the percentage of bright pixels.  A
    perfectly valid first formula can therefore be classified as black while
    it is being written stroke by stroke.  A real write-in has a growing
    non-black bounding box; a static blank/title artifact does not.
    """

    if start > 0.25 or end - start <= 0 or end > 5.0:
        return False
    early = _content_bbox_at(path, start + min(0.15, (end - start) * 0.2))
    late = _content_bbox_at(path, max(start, end - 0.1))
    if late is None:
        return False
    if early is None:
        return late[0] > 0 and late[1] > 0
    early_area = early[0] * early[1]
    late_area = late[0] * late[1]
    return (
        late_area >= max(1, early_area) * 2
        and (late[0] >= early[0] * 1.25 or late[1] >= early[1] * 1.25)
    )


def _contact_sheet(path: Path, destination: Path, duration: float) -> Path | None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or duration <= 0:
        return None
    interval = max(0.25, duration / 8.0)
    process = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vf",
            f"fps=1/{interval:.6f},scale=480:-1,tile=4x2",
            "-frames:v",
            "1",
            str(destination),
        ],
        capture_output=True,
        check=False,
    )
    return destination if process.returncode == 0 and destination.exists() else None


def build_visual_quality_report(
    video: Path,
    *,
    expected_width: int,
    expected_height: int,
    expected_duration: float,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    probe = _probe(video)
    streams = probe.get("streams", ())
    stream = streams[0] if streams else {}
    width = int(stream.get("width", 0) or 0)
    height = int(stream.get("height", 0) or 0)
    duration = float(
        stream.get("duration")
        or probe.get("format", {}).get("duration")
        or 0.0
    )
    if not stream:
        errors.append("ffprobe could not read the rendered video stream")
    elif (width, height) != (expected_width, expected_height):
        errors.append(
            f"rendered resolution is {width}x{height}, expected "
            f"{expected_width}x{expected_height}"
        )
    if duration and abs(duration - expected_duration) > max(0.5, expected_duration * 0.03):
        errors.append(
            f"rendered duration is {duration:.2f}s, expected {expected_duration:.2f}s"
        )
    black = _black_intervals(video)
    prolonged = [(start, end) for start, end in black if end - start >= 2.0]
    final_bbox = _last_content_bbox(video) if prolonged else None
    for start, end in prolonged:
        if _progressive_initial_write(video, start, end):
            warnings.append(
                f"initial sparse interval is active chalk writing for "
                f"{end - start:.2f}s"
            )
            continue
        is_final_hold = (
            duration > 0
            and end >= duration - 0.25
            and final_bbox is not None
            and final_bbox[0] >= width * 0.5
            and final_bbox[1] >= height * 0.03
        )
        if is_final_hold:
            warnings.append(
                f"final QED hold has intentionally sparse content for "
                f"{end - start:.2f}s"
            )
            continue
        errors.append(
            f"blackboard is effectively empty for {end - start:.2f}s "
            f"({start:.2f}s–{end:.2f}s)"
        )
    if black and not prolonged:
        warnings.append(f"{len(black)} short near-empty blackboard interval(s) detected")
    return {
        "schemaVersion": 1,
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "video": str(video.resolve()),
        "media": {
            "width": width,
            "height": height,
            "duration": duration,
            "blackIntervals": [[start, end] for start, end in black],
            "finalContentBbox": list(final_bbox) if final_bbox else None,
        },
    }


def write_visual_quality_report(
    video: Path, report: dict[str, Any]
) -> tuple[Path, Path, Path | None]:
    json_path = video.with_suffix(".visual-qa.json")
    html_path = video.with_suffix(".visual-qa.html")
    sheet_path = video.with_suffix(".visual-qa.png")
    write_json(json_path, report)
    duration = float(report.get("media", {}).get("duration", 0.0))
    sheet = _contact_sheet(video, sheet_path, duration)
    items = "".join(
        f"<li class='{kind}'>{escape(message)}</li>"
        for kind in ("error", "warning")
        for message in report[f"{kind}s"]
    ) or "<li class='ok'>No media or occupancy violations.</li>"
    image = f"<img src='{sheet.name}' alt='sampled rendered frames'>" if sheet else ""
    html_path.write_text(
        "<!doctype html><meta charset='utf-8'><title>Rendered proof QA</title>"
        "<style>body{font:16px system-ui;max-width:1200px;margin:3rem auto;"
        "background:#111;color:#eee}.error{color:#ff7777}.warning{color:#ffd166}"
        ".ok{color:#7ee787}img{width:100%;border:1px solid #444}</style>"
        f"<h1>Rendered proof QA: {'PASS' if report['valid'] else 'FAIL'}</h1>"
        f"<ul>{items}</ul>{image}",
        encoding="utf-8",
    )
    return json_path, html_path, sheet


def require_visual_quality(
    video: Path,
    *,
    expected_width: int,
    expected_height: int,
    expected_duration: float,
) -> dict[str, Any]:
    report = build_visual_quality_report(
        video,
        expected_width=expected_width,
        expected_height=expected_height,
        expected_duration=expected_duration,
    )
    write_visual_quality_report(video, report)
    if not report["valid"]:
        raise ValueError("rendered proof QA failed: " + "; ".join(report["errors"][:6]))
    return report
