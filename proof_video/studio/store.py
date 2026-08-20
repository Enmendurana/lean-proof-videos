from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import json
import mimetypes
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from uuid import uuid4


SCHEMA_VERSION = 2


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class StudioStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database_path = self.root / "studio.db"
        self.revisions_root = self.root / "revisions"
        self.jobs_root = self.root / "jobs"
        self.revisions_root.mkdir(exist_ok=True)
        self.jobs_root.mkdir(exist_ok=True)
        with sqlite3.connect(self.database_path, timeout=30) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                entry_path TEXT NOT NULL UNIQUE,
                theorem TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS revisions (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                sha256 TEXT NOT NULL,
                source_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(project_id, sha256)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                revision_id TEXT NOT NULL REFERENCES revisions(id),
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                options_json TEXT NOT NULL,
                output_path TEXT NOT NULL,
                phase TEXT NOT NULL DEFAULT 'queued',
                progress REAL,
                message TEXT NOT NULL DEFAULT '',
                pid INTEGER,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                return_code INTEGER,
                error TEXT
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_jobs_status_created
            ON jobs(status, created_at)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_jobs_project_created
            ON jobs(project_id, created_at DESC)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_revisions_project_created
            ON revisions(project_id, created_at DESC)
            """,
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                media_type TEXT NOT NULL,
                size INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(job_id, relative_path)
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_artifacts_job
            ON artifacts(job_id, name)
            """,
        )
        with self.connect() as connection:
            for statement in statements:
                connection.execute(statement)
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            connection.execute("PRAGMA optimize")

    def create_project(
        self, name: str, entry_path: str, theorem: str
    ) -> dict[str, Any]:
        project_id = uuid4().hex
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO projects(id, name, entry_path, theorem, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (project_id, name, entry_path, theorem, now, now),
            )
        return self.project(project_id)

    def projects(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM projects ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def project(self, project_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        if row is None:
            raise KeyError(project_id)
        return dict(row)

    def touch_project(self, project_id: str, *, theorem: str | None = None) -> None:
        with self.connect() as connection:
            if theorem is None:
                connection.execute(
                    "UPDATE projects SET updated_at = ? WHERE id = ?",
                    (utc_now(), project_id),
                )
            else:
                connection.execute(
                    "UPDATE projects SET theorem = ?, updated_at = ? WHERE id = ?",
                    (theorem, utc_now(), project_id),
                )

    def add_revision(
        self,
        project_id: str,
        sha256: str,
        source_path: Path,
    ) -> dict[str, Any]:
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM revisions WHERE project_id = ? AND sha256 = ?",
                (project_id, sha256),
            ).fetchone()
            if existing is not None:
                return dict(existing)
            revision_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO revisions(id, project_id, sha256, source_path, created_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (revision_id, project_id, sha256, str(source_path), utc_now()),
            )
        return self.revision(revision_id)

    def revision(self, revision_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM revisions WHERE id = ?", (revision_id,)
            ).fetchone()
        if row is None:
            raise KeyError(revision_id)
        return dict(row)

    def revisions(self, project_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM revisions WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_job(
        self,
        project_id: str,
        revision_id: str,
        kind: str,
        options: dict[str, Any],
        output_path: Path,
    ) -> dict[str, Any]:
        job_id = uuid4().hex
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs(
                    id, project_id, revision_id, kind, status, options_json,
                    output_path, created_at
                ) VALUES(?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (
                    job_id,
                    project_id,
                    revision_id,
                    kind,
                    json.dumps(options, ensure_ascii=False, sort_keys=True),
                    str(output_path),
                    utc_now(),
                ),
            )
        (self.jobs_root / job_id).mkdir(parents=True, exist_ok=True)
        return self.job(job_id)

    def job(self, job_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        result = dict(row)
        result["options"] = json.loads(result.pop("options_json"))
        return result

    def jobs(self, project_id: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as connection:
            if project_id is None:
                rows = connection.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM jobs WHERE project_id = ? ORDER BY created_at DESC",
                    (project_id,),
                ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["options"] = json.loads(item.pop("options_json"))
            result.append(item)
        return result

    def next_queued_job(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["options"] = json.loads(result.pop("options_json"))
        return result

    def update_job(self, job_id: str, **changes: Any) -> None:
        allowed = {
            "status",
            "phase",
            "progress",
            "message",
            "pid",
            "attempts",
            "started_at",
            "finished_at",
            "return_code",
            "error",
        }
        invalid = set(changes) - allowed
        if invalid:
            raise ValueError(f"invalid job columns: {sorted(invalid)}")
        if not changes:
            return
        assignments = ", ".join(f"{name} = ?" for name in changes)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE jobs SET {assignments} WHERE id = ?",  # noqa: S608
                (*changes.values(), job_id),
            )

    def requeue_job(self, job_id: str) -> dict[str, Any]:
        job = self.job(job_id)
        if job["status"] not in {"cancelled", "failed", "interrupted"}:
            raise ValueError("only cancelled, failed, or interrupted jobs can resume")
        self.update_job(
            job_id,
            status="queued",
            phase="queued",
            progress=None,
            message="Queued to resume from verified checkpoints.",
            pid=None,
            started_at=None,
            finished_at=None,
            return_code=None,
            error=None,
        )
        return self.job(job_id)

    def reconcile_running_jobs(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = 'interrupted', phase = 'interrupted', pid = NULL,
                    message = 'Studio restarted; resume from verified checkpoints.'
                WHERE status IN ('running', 'cancelling')
                """
            )

    def sync_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        self.job(job_id)
        root = self.jobs_root / job_id
        internal = {"request.json", "events.ndjson", "worker-status.json"}
        discovered: set[str] = set()
        with self.connect() as connection:
            for path in root.rglob("*"):
                if not path.is_file() or path.name in internal:
                    continue
                relative = path.relative_to(root)
                if relative.parts and relative.parts[0] == "snapshot":
                    continue
                relative_text = relative.as_posix()
                discovered.add(relative_text)
                existing = connection.execute(
                    "SELECT id FROM artifacts WHERE job_id = ? AND relative_path = ?",
                    (job_id, relative_text),
                ).fetchone()
                artifact_id = existing["id"] if existing else uuid4().hex
                connection.execute(
                    """
                    INSERT INTO artifacts(
                        id, job_id, name, relative_path, media_type, size, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(job_id, relative_path) DO UPDATE SET
                        name = excluded.name,
                        media_type = excluded.media_type,
                        size = excluded.size
                    """,
                    (
                        artifact_id,
                        job_id,
                        path.name,
                        relative_text,
                        mimetypes.guess_type(path.name)[0]
                        or "application/octet-stream",
                        path.stat().st_size,
                        utc_now(),
                    ),
                )
            if discovered:
                placeholders = ",".join("?" for _ in discovered)
                connection.execute(
                    f"DELETE FROM artifacts WHERE job_id = ? AND relative_path NOT IN ({placeholders})",  # noqa: S608
                    (job_id, *sorted(discovered)),
                )
            else:
                connection.execute("DELETE FROM artifacts WHERE job_id = ?", (job_id,))
        return self.artifacts(job_id)

    def artifacts(self, job_id: str) -> list[dict[str, Any]]:
        self.job(job_id)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM artifacts WHERE job_id = ? ORDER BY name", (job_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def artifact(self, job_id: str, artifact_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE id = ? AND job_id = ?",
                (artifact_id, job_id),
            ).fetchone()
        if row is None:
            raise KeyError(artifact_id)
        return dict(row)
