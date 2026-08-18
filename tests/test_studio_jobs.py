import asyncio
from pathlib import Path
import sys

from proof_video.studio.jobs import JobRunner
from proof_video.studio.sources import SourceManager
from proof_video.studio.store import StudioStore


LEAN = "theorem demo : True := by\n  trivial\n"


async def _run_fake_worker(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "Demo.lean").write_text(LEAN, encoding="utf-8")
    store = StudioStore(root / ".state")
    sources = SourceManager(root, store)
    project = sources.create_project("Demo.lean")
    revision = store.revisions(project["id"])[0]
    job = store.create_job(
        project["id"], revision["id"], "validate", {}, root / "unused.mp4"
    )
    fake = root / "fake_worker.py"
    fake.write_text(
        """import json, pathlib, sys, time
root = pathlib.Path(sys.argv[1]).parent
(root / 'events.ndjson').write_text(json.dumps({'sequence': 1, 'phase': 'lean', 'progress': 0.5, 'message': 'half'}) + '\\n')
time.sleep(0.05)
(root / 'worker-status.json').write_text(json.dumps({'status': 'succeeded', 'returnCode': 0}))
""",
        encoding="utf-8",
    )
    runner = JobRunner(
        root,
        store,
        sources,
        worker_command=lambda request: [sys.executable, str(fake), str(request)],
    )
    await runner.start()
    try:
        for _ in range(100):
            if store.job(job["id"])["status"] == "succeeded":
                break
            await asyncio.sleep(0.03)
        assert store.job(job["id"])["status"] == "succeeded"
        assert store.job(job["id"])["attempts"] == 1
    finally:
        await runner.stop()


def test_runner_executes_persistent_queue_with_fake_worker(tmp_path: Path) -> None:
    asyncio.run(_run_fake_worker(tmp_path))


def test_cancelled_job_can_resume_without_losing_identity(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "Demo.lean").write_text(LEAN, encoding="utf-8")
    store = StudioStore(root / ".state")
    sources = SourceManager(root, store)
    project = sources.create_project("Demo.lean")
    revision = store.revisions(project["id"])[0]
    job = store.create_job(project["id"], revision["id"], "validate", {}, root / "x")
    store.update_job(job["id"], status="cancelled", phase="cancelled")
    resumed = store.requeue_job(job["id"])
    assert resumed["id"] == job["id"]
    assert resumed["status"] == "queued"
