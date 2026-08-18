from pathlib import Path
import json

from fastapi.testclient import TestClient

from proof_video.studio.app import create_app


LEAN = "theorem demo : True := by\n  trivial\n"


def authenticated_client(tmp_path: Path) -> tuple[TestClient, object]:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "Demo.lean").write_text(LEAN, encoding="utf-8")
    app = create_app(root, root / ".state", run_jobs=False)
    client = TestClient(app, base_url="http://127.0.0.1")
    token = app.state.studio.security.issue_bootstrap_token()
    response = client.post("/api/session", json={"token": token})
    assert response.status_code == 200
    return client, app


def test_api_versions_source_creates_job_and_lists_range_artifact(tmp_path: Path) -> None:
    client, app = authenticated_client(tmp_path)
    project = client.post("/api/projects", json={"path": "Demo.lean"}).json()
    source = client.get(f"/api/projects/{project['id']}/source").json()
    saved = client.put(
        f"/api/projects/{project['id']}/source",
        json={"content": LEAN + "\n-- saved\n", "baseSha256": source["sha256"]},
    )
    assert saved.status_code == 200
    assert len(client.get(f"/api/projects/{project['id']}/revisions").json()) == 2

    job_response = client.post(
        "/api/jobs",
        json={"projectId": project["id"], "kind": "preview-head", "options": {}},
    )
    assert job_response.status_code == 200
    job = job_response.json()
    assert job["status"] == "queued"
    result = app.state.studio.store.jobs_root / job["id"] / "result.mp4"
    result.write_bytes(b"0123456789")
    artifacts = client.get(f"/api/jobs/{job['id']}/artifacts").json()
    assert len(artifacts) == 1
    ranged = client.get(artifacts[0]["url"], headers={"Range": "bytes=2-5"})
    assert ranged.status_code == 206
    assert ranged.content == b"2345"


def test_api_rejects_unauthenticated_origin_traversal_and_replayed_token(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "Demo.lean").write_text(LEAN, encoding="utf-8")
    app = create_app(root, root / ".state", run_jobs=False)
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.get("/api/projects").status_code == 401
    token = app.state.studio.security.issue_bootstrap_token()
    assert client.post("/api/session", json={"token": token}).status_code == 200
    assert client.post("/api/session", json={"token": token}).status_code == 401
    assert client.post("/api/projects", json={"path": "../Secret.lean"}).status_code == 400
    assert client.post(
        "/api/projects",
        json={"path": "Demo.lean"},
        headers={"Origin": "https://evil.invalid"},
    ).status_code == 403


def test_sse_reconnect_resumes_after_last_event_id(tmp_path: Path) -> None:
    client, app = authenticated_client(tmp_path)
    project = client.post("/api/projects", json={"path": "Demo.lean"}).json()
    job = client.post(
        "/api/jobs",
        json={"projectId": project["id"], "kind": "validate", "options": {}},
    ).json()
    app.state.studio.store.update_job(
        job["id"], status="succeeded", phase="complete", progress=1.0
    )
    events = app.state.studio.store.jobs_root / job["id"] / "events.ndjson"
    events.write_text(
        "\n".join(
            json.dumps(
                {
                    "sequence": sequence,
                    "kind": "progress",
                    "phase": "lean",
                    "message": f"event {sequence}",
                }
            )
            for sequence in (1, 2)
        )
        + "\n",
        encoding="utf-8",
    )

    with client.stream(
        "GET",
        f"/api/jobs/{job['id']}/events",
        headers={"Last-Event-ID": "1"},
    ) as response:
        body = "\n".join(response.iter_lines())

    assert response.status_code == 200
    assert "id: 1" not in body
    assert "id: 2" in body
    assert "event 2" in body
