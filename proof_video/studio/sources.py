from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
from typing import Any

from proof_video.commands.render_proof import discover_theorem
from proof_video.studio.store import StudioStore


class SourceConflictError(RuntimeError):
    pass


class SourceManager:
    def __init__(self, project_root: Path, store: StudioStore) -> None:
        self.project_root = project_root.resolve()
        self.store = store

    @staticmethod
    def digest(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def resolve_entry(self, relative_path: str) -> Path:
        candidate = (self.project_root / relative_path).resolve()
        try:
            candidate.relative_to(self.project_root)
        except ValueError as error:
            raise ValueError("Lean file must stay inside the project root") from error
        if candidate.suffix.lower() != ".lean":
            raise ValueError("Only .lean source files are supported")
        return candidate

    def lean_files(self) -> list[str]:
        excluded = {
            ".git",
            ".cache",
            ".lake",
            ".venv",
            ".lean-proof-video-cache",
            ".lean-proof-video-web",
            "output",
            "GeneratedProofs",
        }
        result: list[str] = []
        for path in self.project_root.rglob("*.lean"):
            relative = path.relative_to(self.project_root)
            if any(
                part in excluded or part.startswith(".test-") for part in relative.parts
            ):
                continue
            result.append(relative.as_posix())
        return sorted(result, key=str.casefold)

    def create_project(self, relative_path: str) -> dict[str, Any]:
        entry = self.resolve_entry(relative_path)
        if not entry.is_file():
            raise FileNotFoundError(entry)
        for project in self.store.projects():
            if project["entry_path"].casefold() == relative_path.casefold():
                return project
        theorem = discover_theorem(entry)
        project = self.store.create_project(entry.stem, relative_path, theorem)
        self.capture_revision(project["id"], entry.read_text(encoding="utf-8"))
        return project

    def read_project_source(self, project_id: str) -> dict[str, Any]:
        project = self.store.project(project_id)
        entry = self.resolve_entry(project["entry_path"])
        content = entry.read_text(encoding="utf-8")
        return {
            "projectId": project_id,
            "path": project["entry_path"],
            "content": content,
            "sha256": self.digest(content),
            "theorem": discover_theorem(entry),
        }

    def capture_revision(self, project_id: str, content: str) -> dict[str, Any]:
        digest = self.digest(content)
        directory = self.store.revisions_root / project_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{digest}.lean"
        if not path.exists():
            temporary = path.with_suffix(".lean.tmp")
            temporary.write_text(content, encoding="utf-8")
            os.replace(temporary, path)
        return self.store.add_revision(project_id, digest, path)

    def save_source(
        self,
        project_id: str,
        content: str,
        base_sha256: str,
    ) -> dict[str, Any]:
        project = self.store.project(project_id)
        entry = self.resolve_entry(project["entry_path"])
        current = entry.read_text(encoding="utf-8")
        current_digest = self.digest(current)
        if current_digest != base_sha256:
            raise SourceConflictError(
                "The Lean file changed outside the studio. Reload before saving."
            )
        self.capture_revision(project_id, current)
        new_revision = self.capture_revision(project_id, content)
        temporary = entry.with_name(f".{entry.name}.studio-{os.getpid()}.tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, entry)
        theorem = discover_theorem(entry)
        self.store.touch_project(project_id, theorem=theorem)
        return {
            "content": content,
            "sha256": new_revision["sha256"],
            "theorem": theorem,
            "revisionId": new_revision["id"],
        }

    def restore_revision(
        self,
        project_id: str,
        revision_id: str,
        base_sha256: str,
    ) -> dict[str, Any]:
        revision = self.store.revision(revision_id)
        if revision["project_id"] != project_id:
            raise ValueError("revision does not belong to this project")
        content = Path(revision["source_path"]).read_text(encoding="utf-8")
        return self.save_source(project_id, content, base_sha256)

    def snapshot_for_job(self, job_id: str, revision_id: str) -> Path:
        revision = self.store.revision(revision_id)
        project = self.store.project(revision["project_id"])
        source = Path(revision["source_path"])
        relative = Path(project["entry_path"])
        # The executable snapshot is content-addressed by revision rather than
        # job ID. Repeated previews of the same source therefore hit the same
        # Lean evidence and incremental-snapshot identities. A per-job copy is
        # still retained as provenance, but is never used as the cache key.
        destination = (
            self.store.root
            / "snapshots"
            / project["id"]
            / revision["sha256"]
            / relative
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if (
            not destination.exists()
            or self.digest(destination.read_text(encoding="utf-8"))
            != revision["sha256"]
        ):
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            shutil.copyfile(source, temporary)
            os.replace(temporary, destination)
        if self.digest(destination.read_text(encoding="utf-8")) != revision["sha256"]:
            raise RuntimeError("source snapshot hash mismatch")

        provenance = self.store.jobs_root / job_id / "snapshot" / relative
        provenance.parent.mkdir(parents=True, exist_ok=True)
        if not provenance.exists():
            shutil.copyfile(destination, provenance)
        return destination

    def safe_artifact(self, job_id: str, artifact_name: str) -> Path:
        job_root = (self.store.jobs_root / job_id).resolve()
        candidate = (job_root / artifact_name).resolve()
        try:
            candidate.relative_to(job_root)
        except ValueError as error:
            raise ValueError("artifact path escaped its job directory") from error
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        return candidate
