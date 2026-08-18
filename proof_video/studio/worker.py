"""Isolated render worker used by the local studio job queue."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import traceback

from proof_video.render_service import (
    JsonLinesProgressSink,
    ProgressEvent,
    RenderRequest,
    RenderService,
)


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    args = parser.parse_args(argv)
    payload = json.loads(args.request.read_text(encoding="utf-8"))
    job_root = args.request.parent
    event_path = job_root / "events.ndjson"
    status_path = job_root / "worker-status.json"
    journal = JsonLinesProgressSink(event_path)
    sequence = sum(1 for _ in event_path.open(encoding="utf-8")) if event_path.exists() else 0

    def sink(event: ProgressEvent) -> None:
        """Give every manual and captured stdout event one durable sequence."""
        nonlocal sequence
        sequence += 1
        journal(replace(event, sequence=sequence))

    def emit(kind: str, phase: str, message: str, progress: float | None = None) -> None:
        nonlocal sequence
        sink(
            ProgressEvent(
                sequence=0,
                timestamp=datetime.now(UTC).isoformat(),
                kind=kind,
                phase=phase,
                message=message,
                progress=progress,
            )
        )

    request = RenderRequest(
        lean_file=Path(payload["leanFile"]),
        theorem=str(payload["theorem"]),
        output=Path(payload["output"]),
        kind=payload["kind"],
        quality=payload.get("quality", "high"),
        fps=payload.get("fps"),
        write_speed=payload.get("writeSpeed"),
        audio=Path(payload["audio"]) if payload.get("audio") else None,
        no_audio=bool(payload.get("noAudio", False)),
        render_hardware=payload.get("renderHardware", "auto"),
        render_concurrency=str(payload.get("renderConcurrency", "auto")),
        render_chunking=str(payload.get("renderChunking", "auto")),
        toolchain_backend=payload.get("toolchainBackend", "auto"),
        trace_backend=payload.get("traceBackend"),
        trace_mode=payload.get("traceMode", "auto"),
        resume=bool(payload.get("resume", True)),
        use_cache=bool(payload.get("useCache", True)),
        rebuild_trace=bool(payload.get("rebuildTrace", False)),
        recalibrate_renderer=bool(payload.get("recalibrateRenderer", False)),
    )
    emit("started", "starting", f"Starting {request.kind}.", 0.0)
    try:
        result = RenderService(sink=sink, mirror_output=True).run(request)
        if result:
            raise RuntimeError(f"render pipeline returned exit code {result}")
    except KeyboardInterrupt:
        emit("cancelled", "cancelled", "Job cancelled; checkpoints were preserved.")
        _write_json(
            status_path,
            {"status": "cancelled", "returnCode": 130, "finishedAt": datetime.now(UTC).isoformat()},
        )
        return 130
    except BaseException as error:
        message = str(error) or error.__class__.__name__
        raw_code = getattr(error, "code", 1) or 1
        return_code = raw_code if isinstance(raw_code, int) else 1
        emit("failed", "failed", message)
        _write_json(
            status_path,
            {
                "status": "failed",
                "returnCode": return_code,
                "error": message,
                "traceback": traceback.format_exc(),
                "finishedAt": datetime.now(UTC).isoformat(),
            },
        )
        return return_code
    emit("completed", "complete", "Job completed successfully.", 1.0)
    _write_json(
        status_path,
        {"status": "succeeded", "returnCode": 0, "finishedAt": datetime.now(UTC).isoformat()},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
