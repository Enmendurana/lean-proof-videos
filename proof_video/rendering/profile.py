"""Portable render planning, hardware discovery, and semantic chunk identities.

The Python side owns policy and cache validity.  The Node renderer owns the
actual Chromium measurements, but it receives a complete, serializable plan so
that local, resumed, and eventually remote renders use the same contract.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


RENDER_PLAN_SCHEMA = 1
DEFAULT_MIN_CHUNK_SECONDS = 5.0
DEFAULT_TARGET_CHUNK_SECONDS = 10.0
DEFAULT_MAX_CHUNK_SECONDS = 15.0


@dataclass(frozen=True)
class HardwareProfile:
    fingerprint: str
    platform: str
    logical_cpus: int
    ffmpeg_directory: str | None
    nvenc_available: bool


@dataclass(frozen=True)
class RenderPlan:
    schema_version: int
    hardware: HardwareProfile
    hardware_policy: str
    requested_concurrency: str
    calibration_candidates: tuple[int, ...]
    calibration_frames: int
    profile_store: str
    profile_report: str
    renderer_fingerprint: str
    width: int
    height: int
    fps: int
    gpu_compositing: str
    nvenc_bitrates: tuple[str, ...]
    minimum_ssim: float

    def to_json(self) -> dict[str, Any]:
        value = asdict(self)
        value["schemaVersion"] = value.pop("schema_version")
        value["hardwarePolicy"] = value.pop("hardware_policy")
        value["requestedConcurrency"] = value.pop("requested_concurrency")
        value["calibrationCandidates"] = value.pop("calibration_candidates")
        value["calibrationFrames"] = value.pop("calibration_frames")
        value["profileStore"] = value.pop("profile_store")
        value["profileReport"] = value.pop("profile_report")
        value["rendererFingerprint"] = value.pop("renderer_fingerprint")
        value["gpuCompositing"] = value.pop("gpu_compositing")
        value["nvencBitrates"] = value.pop("nvenc_bitrates")
        value["minimumSsim"] = value.pop("minimum_ssim")
        hardware = value["hardware"]
        hardware["logicalCpus"] = hardware.pop("logical_cpus")
        hardware["ffmpegDirectory"] = hardware.pop("ffmpeg_directory")
        hardware["nvencAvailable"] = hardware.pop("nvenc_available")
        return value


@dataclass(frozen=True)
class SemanticChunk:
    start: int
    end: int
    key: str
    output: Path

    def to_json(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "key": self.key,
            "output": str(self.output.resolve()),
        }


def _run_text(command: list[str], *, timeout: float = 8.0) -> str:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return f"{completed.stdout}\n{completed.stderr}"


def detect_hardware() -> HardwareProfile:
    """Return a conservative renderer capability profile.

    Encoder availability is proven from the FFmpeg binary we will hand to
    Remotion, not inferred merely from the presence of a GPU or driver.
    """

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg and "ffmpeg" not in Path(ffmpeg).name.lower():
        # Defensive against broken PATH shims (and keeps capability discovery
        # from invoking an unrelated executable).
        ffmpeg = None
    ffmpeg_directory = str(Path(ffmpeg).resolve().parent) if ffmpeg else None
    encoders = _run_text([ffmpeg, "-hide_banner", "-encoders"]) if ffmpeg else ""
    nvenc_available = "h264_nvenc" in encoders
    gpu_description = ""
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi and "nvidia-smi" not in Path(nvidia_smi).name.lower():
        nvidia_smi = None
    if nvidia_smi:
        gpu_description = _run_text(
            [
                nvidia_smi,
                "--query-gpu=name,driver_version,memory.total",
                "--format=csv,noheader",
            ]
        ).strip()
    identity = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "logicalCpus": os.cpu_count() or 1,
        "gpu": gpu_description,
        "ffmpeg": ffmpeg or "",
        "nvenc": nvenc_available,
    }
    fingerprint = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return HardwareProfile(
        fingerprint=fingerprint,
        platform=platform.system().lower(),
        logical_cpus=os.cpu_count() or 1,
        ffmpeg_directory=ffmpeg_directory,
        nvenc_available=nvenc_available,
    )


def calibration_candidates(logical_cpus: int) -> tuple[int, ...]:
    candidates = tuple(value for value in (3, 4, 6, 8) if value <= logical_cpus)
    if candidates:
        return candidates
    return (max(1, logical_cpus),)


def make_render_plan(
    *,
    cache_root: Path,
    output: Path,
    renderer_fingerprint: str,
    width: int,
    height: int,
    fps: int,
    hardware_policy: str,
    concurrency: str,
    profile_report: Path | None,
) -> RenderPlan:
    hardware = detect_hardware()
    if hardware_policy == "gpu-required" and not hardware.nvenc_available:
        raise ValueError(
            "--render-hardware gpu-required was selected, but the active FFmpeg "
            "does not expose h264_nvenc"
        )
    profile_identity = hashlib.sha256(
        json.dumps(
            {
                "hardware": hardware.fingerprint,
                "renderer": renderer_fingerprint,
                "width": width,
                "height": height,
                "fps": fps,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    store = cache_root / "render-profiles" / f"{profile_identity}.json"
    report = profile_report or output.with_suffix(".render-profile.json")
    return RenderPlan(
        schema_version=RENDER_PLAN_SCHEMA,
        hardware=hardware,
        hardware_policy=hardware_policy,
        requested_concurrency=concurrency,
        calibration_candidates=calibration_candidates(hardware.logical_cpus),
        calibration_frames=120,
        profile_store=str(store.resolve()),
        profile_report=str(report.resolve()),
        renderer_fingerprint=renderer_fingerprint,
        width=width,
        height=height,
        fps=fps,
        gpu_compositing="benchmark",
        nvenc_bitrates=("8M", "12M", "16M"),
        minimum_ssim=0.995,
    )


def semantic_chunk_ranges(
    timeline: dict[str, Any],
    *,
    min_seconds: float = DEFAULT_MIN_CHUNK_SECONDS,
    target_seconds: float = DEFAULT_TARGET_CHUNK_SECONDS,
    max_seconds: float = DEFAULT_MAX_CHUNK_SECONDS,
) -> list[tuple[int, int]]:
    """Split at proof-transition boundaries while bounding recovery cost."""

    total = int(timeline["durationInFrames"])
    fps = int(timeline["fps"])
    if total <= 0:
        return []
    min_frames = max(1, round(min_seconds * fps))
    target_frames = max(min_frames, round(target_seconds * fps))
    max_frames = max(target_frames, round(max_seconds * fps))
    boundaries = sorted(
        {
            min(
                total,
                int(transition["startFrame"]) + int(transition["durationFrames"]),
            )
            for transition in timeline.get("transitions", [])
        }
        | {total}
    )
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < total:
        minimum = min(total, start + min_frames)
        target = min(total, start + target_frames)
        maximum = min(total, start + max_frames)
        candidates = [value for value in boundaries if minimum <= value <= maximum]
        end_exclusive = (
            min(candidates, key=lambda value: (abs(value - target), value))
            if candidates
            else maximum
        )
        if end_exclusive <= start:
            end_exclusive = min(total, start + max_frames)
        ranges.append((start, end_exclusive - 1))
        start = end_exclusive
    return ranges


def _transitions_for_range(
    timeline: dict[str, Any], start: int, end: int
) -> list[dict[str, Any]]:
    transitions = timeline.get("transitions", [])
    selected: list[dict[str, Any]] = []
    for transition in transitions:
        transition_start = int(transition["startFrame"])
        transition_end = transition_start + int(transition["durationFrames"]) - 1
        if transition_start <= end and transition_end >= start:
            selected.append(transition)
    prior = [item for item in transitions if int(item["startFrame"]) < start]
    if prior and (not selected or selected[0] is not prior[-1]):
        selected.insert(0, prior[-1])
    return selected


def _stable_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def semantic_chunks(
    timeline: dict[str, Any],
    *,
    cache_root: Path,
    renderer_fingerprint: str,
    render_plan: RenderPlan,
    ranges: Iterable[tuple[int, int]] | None = None,
) -> list[SemanticChunk]:
    """Build independently reusable chunk identities from local dependencies."""

    states = timeline.get("states", [])
    layout_states = (timeline.get("layoutManifest") or {}).get("states", {})
    result: list[SemanticChunk] = []
    selected_ranges = list(ranges or semantic_chunk_ranges(timeline))
    for start, end in selected_ranges:
        transitions = _transitions_for_range(timeline, start, end)
        state_indexes = {0} if start == 0 else set()
        for transition in transitions:
            state_indexes.add(int(transition["fromState"]))
            state_indexes.add(int(transition["toState"]))
        local_states = [
            states[index] for index in sorted(state_indexes) if index < len(states)
        ]
        payload: dict[str, Any] = {
            "contract": timeline.get("rendererContract"),
            "renderer": renderer_fingerprint,
            "dimensions": [
                timeline.get("width"),
                timeline.get("height"),
                timeline.get("fps"),
            ],
            "range": [start, end],
            "states": local_states,
            "transitions": transitions,
            "layout": {
                state.get("id"): layout_states.get(state.get("id"))
                for state in local_states
                if state.get("id") in layout_states
            },
            "encoder": {
                "policy": render_plan.hardware_policy,
                "nvenc": render_plan.hardware.nvenc_available,
                "bitrates": render_plan.nvenc_bitrates,
            },
        }
        if end == int(timeline["durationInFrames"]) - 1:
            payload["completion"] = {
                "duration": timeline.get("durationInFrames"),
                "celebration": timeline.get("celebrationFrames"),
                "hold": timeline.get("completionHoldFrames"),
                "qed": timeline.get("showQed"),
            }
        key = hashlib.sha256(_stable_json(payload)).hexdigest()
        output = cache_root / "remotion-segments" / key[:2] / f"{key}.mp4"
        result.append(SemanticChunk(start=start, end=end, key=key, output=output))
    return result
