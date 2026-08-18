"""FastAPI application for the localhost Lean Proof Studio."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
import mimetypes
import os
from pathlib import Path
import subprocess
from typing import Any, AsyncIterator

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from proof_video.studio.jobs import FINAL_STATUSES, JobRunner
from proof_video.studio.security import (
    COOKIE_NAME,
    StudioSecurity,
    valid_local_host,
    valid_same_origin,
)
from proof_video.studio.sources import SourceConflictError, SourceManager
from proof_video.studio.store import StudioStore


class ProjectCreate(BaseModel):
    path: str


class SourceSave(BaseModel):
    content: str
    base_sha256: str = Field(alias="baseSha256")


class RestoreRequest(BaseModel):
    base_sha256: str = Field(alias="baseSha256")


class JobCreate(BaseModel):
    project_id: str = Field(alias="projectId")
    kind: str
    options: dict[str, Any] = Field(default_factory=dict)


class BootstrapRequest(BaseModel):
    token: str


@dataclass
class StudioContext:
    root: Path
    state_root: Path
    store: StudioStore
    sources: SourceManager
    runner: JobRunner
    security: StudioSecurity


def _project_response(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": project["id"],
        "name": project["name"],
        "entryPath": project["entry_path"],
        "theorem": project["theorem"],
        "createdAt": project["created_at"],
        "updatedAt": project["updated_at"],
    }


def _job_response(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job["id"],
        "projectId": job["project_id"],
        "revisionId": job["revision_id"],
        "kind": job["kind"],
        "status": job["status"],
        "options": job["options"],
        "phase": job["phase"],
        "progress": job["progress"],
        "message": job["message"],
        "attempts": job["attempts"],
        "createdAt": job["created_at"],
        "startedAt": job["started_at"],
        "finishedAt": job["finished_at"],
        "returnCode": job["return_code"],
        "error": job["error"],
    }


def create_app(
    project_root: Path | None = None,
    state_root: Path | None = None,
    *,
    static_root: Path | None = None,
    run_jobs: bool = True,
) -> FastAPI:
    root = (project_root or Path.cwd()).resolve()
    state = (state_root or root / ".lean-proof-video-web").resolve()
    store = StudioStore(state)
    sources = SourceManager(root, store)
    security = StudioSecurity(state)
    runner = JobRunner(root, store, sources)
    context = StudioContext(root, state, store, sources, runner, security)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if run_jobs:
            await runner.start()
        try:
            yield
        finally:
            if run_jobs:
                await runner.stop()

    app = FastAPI(title="Lean Proof Studio", version="1", lifespan=lifespan)
    app.state.studio = context

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        host = request.headers.get("host", "")
        if not valid_local_host(host):
            return JSONResponse({"detail": "invalid host"}, status_code=400)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not valid_same_origin(
            request.headers.get("origin"), host
        ):
            return JSONResponse({"detail": "invalid origin"}, status_code=403)
        return await call_next(request)

    def require_session(
        proof_studio_session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    ) -> None:
        if not security.valid_session(proof_studio_session):
            raise HTTPException(status_code=401, detail="studio session required")

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "version": 1, "root": str(root)}

    @app.post("/api/session")
    async def exchange_session(payload: BootstrapRequest, response: Response) -> dict[str, bool]:
        try:
            session = security.exchange_bootstrap_token(payload.token)
        except ValueError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        response.set_cookie(
            COOKIE_NAME,
            session,
            httponly=True,
            samesite="strict",
            secure=False,
            path="/",
        )
        return {"authenticated": True}

    auth = [Depends(require_session)]

    @app.get("/api/files", dependencies=auth)
    async def files() -> dict[str, list[str]]:
        return {"files": sources.lean_files()}

    @app.get("/api/projects", dependencies=auth)
    async def projects() -> list[dict[str, Any]]:
        return [_project_response(project) for project in store.projects()]

    @app.post("/api/projects", dependencies=auth)
    async def create_project(payload: ProjectCreate) -> dict[str, Any]:
        try:
            return _project_response(sources.create_project(payload.path))
        except (ValueError, FileNotFoundError, RuntimeError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/projects/{project_id}/source", dependencies=auth)
    async def source(project_id: str) -> dict[str, Any]:
        try:
            return sources.read_project_source(project_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="project not found") from error

    @app.put("/api/projects/{project_id}/source", dependencies=auth)
    async def save_source(project_id: str, payload: SourceSave) -> dict[str, Any]:
        try:
            return sources.save_source(project_id, payload.content, payload.base_sha256)
        except SourceConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/projects/{project_id}/revisions", dependencies=auth)
    async def revisions(project_id: str) -> list[dict[str, Any]]:
        try:
            store.project(project_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="project not found") from error
        return [
            {
                "id": revision["id"],
                "sha256": revision["sha256"],
                "createdAt": revision["created_at"],
            }
            for revision in store.revisions(project_id)
        ]

    @app.post(
        "/api/projects/{project_id}/revisions/{revision_id}/restore",
        dependencies=auth,
    )
    async def restore(project_id: str, revision_id: str, payload: RestoreRequest):
        try:
            return sources.restore_revision(project_id, revision_id, payload.base_sha256)
        except SourceConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/jobs", dependencies=auth)
    async def jobs(project_id: str | None = None) -> list[dict[str, Any]]:
        return [_job_response(job) for job in store.jobs(project_id)]

    @app.get("/api/jobs/{job_id}", dependencies=auth)
    async def job(job_id: str) -> dict[str, Any]:
        try:
            return _job_response(store.job(job_id))
        except KeyError as error:
            raise HTTPException(status_code=404, detail="job not found") from error

    @app.post("/api/jobs", dependencies=auth)
    async def create_job(payload: JobCreate) -> dict[str, Any]:
        if payload.kind not in {"validate", "preview-head", "preview-tail", "render-full"}:
            raise HTTPException(status_code=400, detail="invalid job kind")
        options = dict(payload.options)
        if options.get("audio"):
            audio = Path(str(options["audio"])).resolve()
            allowed_audio_roots = (root, state / "audio")
            if not any(
                _is_within(audio, allowed_root.resolve())
                for allowed_root in allowed_audio_roots
            ) or not audio.is_file():
                raise HTTPException(status_code=400, detail="invalid audio path")
            options["audio"] = str(audio)
        try:
            current = sources.read_project_source(payload.project_id)
            revision = sources.capture_revision(payload.project_id, current["content"])
        except KeyError as error:
            raise HTTPException(status_code=404, detail="project not found") from error
        job_id_hint = "pending"
        output = state / "jobs" / job_id_hint / "result.mp4"
        created = store.create_job(
            payload.project_id,
            revision["id"],
            payload.kind,
            options,
            output,
        )
        output = store.jobs_root / created["id"] / "result.mp4"
        with store.connect() as connection:
            connection.execute(
                "UPDATE jobs SET output_path = ? WHERE id = ?", (str(output), created["id"])
            )
        runner.wake()
        return _job_response(store.job(created["id"]))

    @app.post("/api/jobs/{job_id}/cancel", dependencies=auth)
    async def cancel(job_id: str) -> dict[str, Any]:
        try:
            return _job_response(await runner.cancel(job_id))
        except KeyError as error:
            raise HTTPException(status_code=404, detail="job not found") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/api/jobs/{job_id}/resume", dependencies=auth)
    async def resume(job_id: str) -> dict[str, Any]:
        try:
            return _job_response(runner.resume(job_id))
        except KeyError as error:
            raise HTTPException(status_code=404, detail="job not found") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/api/jobs/{job_id}/retry", dependencies=auth)
    async def retry(job_id: str) -> dict[str, Any]:
        try:
            previous = store.job(job_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="job not found") from error
        created = store.create_job(
            previous["project_id"],
            previous["revision_id"],
            previous["kind"],
            previous["options"],
            state / "jobs" / "pending" / "result.mp4",
        )
        output = store.jobs_root / created["id"] / "result.mp4"
        with store.connect() as connection:
            connection.execute(
                "UPDATE jobs SET output_path = ? WHERE id = ?", (str(output), created["id"])
            )
        runner.wake()
        return _job_response(store.job(created["id"]))

    @app.get("/api/jobs/{job_id}/events", dependencies=auth)
    async def events(
        job_id: str,
        request: Request,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        try:
            store.job(job_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="job not found") from error
        start_sequence = int(last_event_id or 0)
        event_path = store.jobs_root / job_id / "events.ndjson"

        async def stream() -> AsyncIterator[str]:
            sent = start_sequence
            terminal_idle = 0
            offset = 0
            while not await request.is_disconnected():
                emitted = False
                if event_path.exists():
                    with event_path.open("r", encoding="utf-8") as handle:
                        handle.seek(offset)
                        for line in handle:
                            try:
                                item = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            sequence = int(item.get("sequence", 0))
                            if sequence <= sent:
                                continue
                            sent = sequence
                            emitted = True
                            yield (
                                f"id: {sequence}\nevent: progress\n"
                                f"data: {json.dumps(item)}\n\n"
                            )
                        offset = handle.tell()
                current = store.job(job_id)
                if current["status"] in FINAL_STATUSES or current["status"] == "interrupted":
                    terminal_idle = terminal_idle + 1 if not emitted else 0
                    if terminal_idle >= 2:
                        return
                if not emitted:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream")

    def artifact_rows(job_id: str) -> list[dict[str, Any]]:
        try:
            rows = store.sync_artifacts(job_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="job not found") from error
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "size": row["size"],
                "mediaType": row["media_type"],
                "url": f"/api/jobs/{job_id}/artifacts/{row['id']}",
            }
            for row in rows
        ]

    @app.get("/api/jobs/{job_id}/artifacts", dependencies=auth)
    async def artifacts(job_id: str) -> list[dict[str, Any]]:
        return artifact_rows(job_id)

    @app.get("/api/jobs/{job_id}/artifacts/{artifact_id}", dependencies=auth)
    async def artifact(job_id: str, artifact_id: str) -> FileResponse:
        try:
            row = store.artifact(job_id, artifact_id)
            path = sources.safe_artifact(job_id, row["relative_path"])
        except (KeyError, ValueError, FileNotFoundError) as error:
            raise HTTPException(status_code=404, detail="artifact not found") from error
        return FileResponse(path, filename=None, media_type=mimetypes.guess_type(path.name)[0])

    @app.post("/api/audio", dependencies=auth)
    async def upload_audio(
        request: Request,
        filename: str = Header(default="background.mp3", alias="X-Filename"),
    ) -> dict[str, str]:
        suffix = Path(filename).suffix.lower()
        if suffix not in {".mp3", ".wav", ".m4a", ".ogg"}:
            raise HTTPException(status_code=400, detail="unsupported audio format")
        body = await request.body()
        if len(body) > 150 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="audio file is too large")
        import hashlib

        digest = hashlib.sha256(body).hexdigest()
        target = state / "audio" / f"{digest}{suffix}"
        target.parent.mkdir(exist_ok=True)
        if not target.exists():
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_bytes(body)
            temporary.replace(target)
        return {"path": str(target), "name": filename}

    @app.post("/api/jobs/{job_id}/open-folder", dependencies=auth)
    async def open_folder(job_id: str) -> dict[str, bool]:
        try:
            store.job(job_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="job not found") from error
        folder = store.jobs_root / job_id
        if os.name == "nt":
            subprocess.Popen(["explorer.exe", str(folder)])
        return {"opened": True}

    @app.post("/api/studio/stop", dependencies=auth)
    async def stop_studio() -> dict[str, bool]:
        callback = getattr(app.state, "shutdown_callback", None)
        if callback is None:
            raise HTTPException(status_code=503, detail="shutdown is unavailable")
        asyncio.get_running_loop().call_later(0.2, callback)
        return {"stopping": True}

    resolved_static = static_root or root / "studio" / "dist"
    if resolved_static.is_dir():
        app.mount("/assets", StaticFiles(directory=resolved_static / "assets"), name="assets")

        @app.get("/{path:path}")
        async def frontend(path: str) -> FileResponse:
            candidate = (resolved_static / path).resolve()
            try:
                candidate.relative_to(resolved_static.resolve())
            except ValueError:
                candidate = resolved_static / "index.html"
            if not candidate.is_file():
                candidate = resolved_static / "index.html"
            return FileResponse(candidate)

    return app


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
