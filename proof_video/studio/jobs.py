from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from typing import Any, Callable

from proof_video.studio.sources import SourceManager
from proof_video.studio.store import StudioStore, utc_now


FINAL_STATUSES = {"succeeded", "failed", "cancelled"}


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            return result.returncode == 0 and f'"{pid}"' in result.stdout
        os.kill(pid, 0)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


class JobRunner:
    def __init__(
        self,
        project_root: Path,
        store: StudioStore,
        sources: SourceManager,
        worker_command: Callable[[Path], list[str]] | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.store = store
        self.sources = sources
        self.worker_command = worker_command or (
            lambda request: [
                sys.executable,
                "-m",
                "proof_video.studio.worker",
                str(request),
            ]
        )
        self._wake = asyncio.Event()
        self._stopping = False
        self._scheduler: asyncio.Task | None = None
        self._job_task: asyncio.Task | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._active_job_id: str | None = None
        self._detached_offsets: dict[str, int] = {}

    async def start(self) -> None:
        for job in self.store.jobs():
            if job["status"] not in {"running", "cancelling"}:
                continue
            if _pid_alive(job["pid"]):
                self._active_job_id = job["id"]
                asyncio.create_task(self._monitor_detached(job))
                break
            self.store.update_job(
                job["id"],
                status="interrupted",
                phase="interrupted",
                pid=None,
                message="Worker stopped; resume from verified checkpoints.",
            )
        self._scheduler = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._scheduler is not None:
            self._scheduler.cancel()
            try:
                await self._scheduler
            except asyncio.CancelledError:
                pass

    def wake(self) -> None:
        self._wake.set()

    async def _run_loop(self) -> None:
        while not self._stopping:
            if self._job_task is not None and self._job_task.done():
                failed_job_id = self._active_job_id
                try:
                    self._job_task.result()
                except Exception as error:
                    if failed_job_id is not None:
                        current = self.store.job(failed_job_id)
                        if current["status"] not in FINAL_STATUSES:
                            self.store.update_job(
                                failed_job_id,
                                status="failed",
                                phase="failed",
                                pid=None,
                                finished_at=utc_now(),
                                return_code=1,
                                error=str(error),
                                message=str(error),
                            )
                    self._active_job_id = None
                self._job_task = None
            if self._active_job_id is None:
                job = self.store.next_queued_job()
                if job is not None:
                    self._active_job_id = job["id"]
                    self._job_task = asyncio.create_task(self._run_job(job))
                    continue
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=1.0)
            except TimeoutError:
                pass

    def _request_payload(self, job: dict[str, Any], snapshot: Path) -> dict[str, Any]:
        project = self.store.project(job["project_id"])
        options = job["options"]
        return {
            "leanFile": str(snapshot),
            "theorem": project["theorem"],
            "output": job["output_path"],
            "kind": job["kind"],
            "quality": options.get("quality", "high"),
            "fps": options.get("fps"),
            "writeSpeed": options.get("writeSpeed"),
            "audio": options.get("audio"),
            "noAudio": options.get("noAudio", False),
            "renderHardware": options.get("renderHardware", "auto"),
            "renderConcurrency": options.get("renderConcurrency", "auto"),
            "renderChunking": options.get("renderChunking", "auto"),
            "toolchainBackend": options.get("toolchainBackend", "auto"),
            "traceBackend": options.get("traceBackend"),
            "traceMode": options.get("traceMode", "auto"),
            "resume": options.get("resume", True),
            "useCache": options.get("useCache", True),
            "rebuildTrace": options.get("rebuildTrace", False),
            "recalibrateRenderer": options.get("recalibrateRenderer", False),
        }

    async def _run_job(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        self._active_job_id = job_id
        job_root = self.store.jobs_root / job_id
        snapshot = self.sources.snapshot_for_job(job_id, job["revision_id"])
        request_path = job_root / "request.json"
        request_path.write_text(
            json.dumps(self._request_payload(job, snapshot), indent=2),
            encoding="utf-8",
        )
        log_path = job_root / "worker.log"
        log_handle = log_path.open("ab")
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self._process = await asyncio.create_subprocess_exec(
            *self.worker_command(request_path),
            cwd=self.project_root,
            stdout=log_handle,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=creationflags,
        )
        self.store.update_job(
            job_id,
            status="running",
            phase="starting",
            progress=0.0,
            message="Worker started.",
            pid=self._process.pid,
            attempts=int(job["attempts"]) + 1,
            started_at=utc_now(),
            finished_at=None,
            return_code=None,
            error=None,
        )
        event_task = asyncio.create_task(self._ingest_events(job_id))
        try:
            return_code = await self._process.wait()
            await event_task
            self._finish_from_worker_status(job_id, return_code)
        except asyncio.CancelledError:
            # The worker is deliberately not terminated: a restarted studio can
            # reconnect to its PID and durable event journal.
            event_task.cancel()
            raise
        except Exception as error:
            self.store.update_job(
                job_id,
                status="failed",
                phase="failed",
                pid=None,
                finished_at=utc_now(),
                return_code=1,
                error=str(error),
                message=str(error),
            )
            raise
        finally:
            log_handle.close()
            self._process = None
            if not _pid_alive(self.store.job(job_id).get("pid")):
                self._active_job_id = None
                self._wake.set()

    async def _ingest_events(self, job_id: str) -> None:
        path = self.store.jobs_root / job_id / "events.ndjson"
        offset = 0
        idle_after_exit = 0
        while True:
            if path.exists():
                with path.open("r", encoding="utf-8") as handle:
                    handle.seek(offset)
                    for line in handle:
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        self.store.update_job(
                            job_id,
                            phase=str(event.get("phase", "pipeline")),
                            progress=event.get("progress"),
                            message=str(event.get("message", "")),
                        )
                    offset = handle.tell()
            if self._process is not None and self._process.returncode is None:
                await asyncio.sleep(0.25)
                continue
            idle_after_exit += 1
            if idle_after_exit >= 2:
                return
            await asyncio.sleep(0.1)

    def _finish_from_worker_status(self, job_id: str, return_code: int) -> None:
        path = self.store.jobs_root / job_id / "worker-status.json"
        payload: dict[str, Any] = {}
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
        current = self.store.job(job_id)
        status = str(
            payload.get("status") or ("succeeded" if return_code == 0 else "failed")
        )
        if current["status"] == "cancelling":
            status = "cancelled"
        self.store.update_job(
            job_id,
            status=status,
            phase="complete" if status == "succeeded" else status,
            progress=1.0 if status == "succeeded" else current["progress"],
            pid=None,
            finished_at=str(payload.get("finishedAt") or utc_now()),
            return_code=int(payload.get("returnCode", return_code)),
            error=payload.get("error"),
            message=(
                "Job completed successfully."
                if status == "succeeded"
                else str(payload.get("error") or f"Job {status}.")
            ),
        )
        self.store.sync_artifacts(job_id)

    async def _monitor_detached(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        while _pid_alive(job["pid"]):
            await self._ingest_detached_once(job_id)
            await asyncio.sleep(0.5)
        await self._ingest_detached_once(job_id)
        self._finish_from_worker_status(job_id, 1)
        self._active_job_id = None
        self._wake.set()

    async def _ingest_detached_once(self, job_id: str) -> None:
        path = self.store.jobs_root / job_id / "events.ndjson"
        if not path.exists():
            return
        last = None
        with path.open("r", encoding="utf-8") as handle:
            handle.seek(self._detached_offsets.get(job_id, 0))
            for line in handle:
                try:
                    last = json.loads(line)
                except json.JSONDecodeError:
                    continue
            self._detached_offsets[job_id] = handle.tell()
        if last is not None:
            self.store.update_job(
                job_id,
                phase=str(last.get("phase", "pipeline")),
                progress=last.get("progress"),
                message=str(last.get("message", "")),
            )

    async def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.store.job(job_id)
        if job["status"] == "queued":
            self.store.update_job(
                job_id,
                status="cancelled",
                phase="cancelled",
                finished_at=utc_now(),
                message="Queued job cancelled.",
            )
            return self.store.job(job_id)
        if job["status"] not in {"running", "cancelling"}:
            raise ValueError("only queued or running jobs can be cancelled")
        self.store.update_job(
            job_id,
            status="cancelling",
            phase="cancelling",
            message="Stopping worker and preserving checkpoints…",
        )
        pid = int(job["pid"])
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T"],
                capture_output=True,
                check=False,
            )
        else:
            os.killpg(pid, signal.SIGTERM)
        return self.store.job(job_id)

    def resume(self, job_id: str) -> dict[str, Any]:
        job = self.store.requeue_job(job_id)
        self.wake()
        return job
