"""Fifteen-minute local supervisor for the Lean 4.32 snapshot reader.

The daemon is a performance layer only. Snapshot metadata and the kernel
certificate are validated by the caller before any request reaches it. The
Lean child retains the deserialized command tree and imported environment; a
failed lease transparently falls back to the one-shot official reader.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from typing import Any
import uuid

from proof_video.cache import lean_snapshot_extractor_identity, stable_hash, write_json
from proof_video.lean_runner import lean_runtime_environment


WORKER_IDLE_SECONDS = 15 * 60
_MAX_REQUEST_BYTES = 4 * 1024 * 1024
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024 * 1024


class SnapshotWorkerError(RuntimeError):
    pass


def _read_message(connection: socket.socket, limit: int) -> dict[str, Any]:
    chunks: list[bytes] = []
    size = 0
    while True:
        block = connection.recv(1024 * 1024)
        if not block:
            raise SnapshotWorkerError("snapshot worker closed the connection")
        newline = block.find(b"\n")
        if newline >= 0:
            chunks.append(block[:newline])
            size += newline
            if size > limit:
                raise SnapshotWorkerError("snapshot worker message exceeds safety limit")
            break
        chunks.append(block)
        size += len(block)
        if size > limit:
            raise SnapshotWorkerError("snapshot worker message exceeds safety limit")
    try:
        value = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise SnapshotWorkerError("snapshot worker returned invalid JSON") from error
    if not isinstance(value, dict):
        raise SnapshotWorkerError("snapshot worker response is not an object")
    return value


def _send(port: int, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    with socket.create_connection(("127.0.0.1", port), timeout=5.0) as connection:
        connection.settimeout(None)
        request = json.dumps(
            {"token": token, **payload},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        if len(request) > _MAX_REQUEST_BYTES:
            raise SnapshotWorkerError("snapshot worker request is unexpectedly large")
        connection.sendall(request)
        return _read_message(connection, _MAX_RESPONSE_BYTES)


def _ready_document(
    *, port: int, token: str, identity: str, idle_seconds: int
) -> dict[str, Any]:
    now = time.time()
    return {
        "schemaVersion": 1,
        "pid": os.getpid(),
        "port": port,
        "token": token,
        "identity": identity,
        "lastUsedAt": now,
        "expiresAt": now + idle_seconds,
    }


def _serve(
    *,
    workspace: Path,
    toolchain: str,
    ready_path: Path,
    token: str,
    identity: str,
    idle_seconds: int,
) -> int:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    server.settimeout(1.0)
    port = int(server.getsockname()[1])
    ready_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(
        ready_path,
        _ready_document(
            port=port,
            token=token,
            identity=identity,
            idle_seconds=idle_seconds,
        ),
    )
    log_path = ready_path.with_suffix(".log")
    with log_path.open("a", encoding="utf-8") as log:
        lean = subprocess.Popen(
            [
                "elan",
                "run",
                toolchain,
                "lean",
                "--run",
                str((workspace / "SnapshotAnimate432.lean").resolve()),
                "--worker",
            ],
            cwd=workspace,
            env=dict(lean_runtime_environment(workspace)),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        assert lean.stdin is not None and lean.stdout is not None
        last_used = time.monotonic()
        try:
            while time.monotonic() - last_used < idle_seconds:
                if lean.poll() is not None:
                    return int(lean.returncode or 1)
                try:
                    connection, _address = server.accept()
                except TimeoutError:
                    continue
                with connection:
                    try:
                        request = _read_message(connection, _MAX_REQUEST_BYTES)
                        if not secrets.compare_digest(str(request.get("token", "")), token):
                            raise SnapshotWorkerError("snapshot worker authentication failed")
                        operation = request.get("op")
                        if operation == "ping":
                            response: dict[str, Any] = {"ok": True, "identity": identity}
                        elif operation == "trace":
                            lean_request = request.get("request")
                            if not isinstance(lean_request, dict):
                                raise SnapshotWorkerError("missing Lean worker request")
                            lean.stdin.write(
                                json.dumps(
                                    lean_request,
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                )
                                + "\n"
                            )
                            lean.stdin.flush()
                            line = lean.stdout.readline()
                            if not line:
                                raise SnapshotWorkerError(
                                    "Lean snapshot worker exited before responding"
                                )
                            parsed = json.loads(line)
                            if not isinstance(parsed, dict):
                                raise SnapshotWorkerError(
                                    "Lean snapshot worker returned a non-object"
                                )
                            response = parsed
                        else:
                            raise SnapshotWorkerError(f"unknown worker operation: {operation}")
                    except Exception as error:  # supervisor protocol boundary
                        response = {"ok": False, "error": str(error)}
                    connection.sendall(
                        json.dumps(
                            response,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ).encode("utf-8")
                        + b"\n"
                    )
                    last_used = time.monotonic()
                    write_json(
                        ready_path,
                        _ready_document(
                            port=port,
                            token=token,
                            identity=identity,
                            idle_seconds=idle_seconds,
                        ),
                    )
        finally:
            if lean.poll() is None:
                lean.terminate()
                try:
                    lean.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    lean.kill()
            server.close()
            try:
                current = json.loads(ready_path.read_text(encoding="utf-8"))
                if current.get("pid") == os.getpid():
                    ready_path.unlink(missing_ok=True)
            except (OSError, json.JSONDecodeError, AttributeError):
                pass
    return 0


def _worker_identity(
    workspace: Path,
    *,
    toolchain: str,
    snapshot_metadata: dict[str, Any],
) -> str:
    identity = snapshot_metadata.get("identity", {})
    return stable_hash(
        "lean-4.32-snapshot-worker-v1",
        toolchain,
        identity.get("lakeManifestSha256"),
        identity.get("headerSha256"),
        identity.get("extractorAbi"),
        lean_snapshot_extractor_identity(workspace).get("key"),
    )


def _read_ready(path: Path, identity: str) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if (
            value.get("identity") == identity
            and float(value.get("expiresAt", 0)) > time.time()
        ):
            response = _send(int(value["port"]), str(value["token"]), {"op": "ping"})
            if response.get("ok") is True and response.get("identity") == identity:
                return value
    except (OSError, ValueError, TypeError, KeyError, SnapshotWorkerError):
        return None
    return None


def _start_worker(
    workspace: Path,
    *,
    toolchain: str,
    ready_path: Path,
    identity: str,
) -> dict[str, Any]:
    token = secrets.token_hex(32)
    command = [
        sys.executable,
        "-m",
        "proof_video.snapshot_worker",
        "serve",
        "--workspace",
        str(workspace),
        "--toolchain",
        toolchain,
        "--ready-path",
        str(ready_path),
        "--token",
        token,
        "--identity",
        identity,
        "--idle-seconds",
        str(WORKER_IDLE_SECONDS),
    ]
    kwargs: dict[str, Any] = {
        "cwd": Path(__file__).resolve().parents[1],
        "env": os.environ.copy(),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        ready = _read_ready(ready_path, identity)
        if ready is not None:
            return ready
        time.sleep(0.1)
    raise SnapshotWorkerError("Lean snapshot worker did not become ready")


def request_snapshot_trace(
    workspace: Path,
    *,
    toolchain: str,
    snapshot: Path,
    snapshot_metadata: dict[str, Any],
    certificate: Path,
    animate_args: list[str],
) -> dict[str, Any]:
    """Use or start the compatible source-local Lean worker lease."""

    identity = _worker_identity(
        workspace,
        toolchain=toolchain,
        snapshot_metadata=snapshot_metadata,
    )
    ready_path = snapshot.parent / "worker.json"
    ready = _read_ready(ready_path, identity)
    if ready is None:
        ready = _start_worker(
            workspace,
            toolchain=toolchain,
            ready_path=ready_path,
            identity=identity,
        )
        print("Lean 4.32 worker: started a 15-minute snapshot lease.", flush=True)
    else:
        print("Lean 4.32 worker: reusing the in-memory snapshot tree.", flush=True)
    request_id = uuid.uuid4().hex
    response = _send(
        int(ready["port"]),
        str(ready["token"]),
        {
            "op": "trace",
            "request": {
                "requestId": request_id,
                "snapshotPath": str(snapshot.resolve()),
                "snapshotKey": str(snapshot_metadata.get("snapshotSha256", "")),
                "certificatePath": str(certificate.resolve()),
                "animateArgs": animate_args,
            },
        },
    )
    if response.get("requestId") != request_id:
        raise SnapshotWorkerError("Lean snapshot worker response ID mismatch")
    if response.get("ok") is not True:
        raise SnapshotWorkerError(str(response.get("error", "unknown Lean worker error")))
    if response.get("comparedCommandTrees") is True:
        reused = max(0, int(response.get("reusedCommands", 0)))
        total = max(reused, int(response.get("totalCommands", 0)))
        print(
            "Lean 4.32 commands: "
            f"reused {reused}/{total} | re-elaborated {total - reused}/{total}",
            flush=True,
        )
    document = response.get("document")
    if not isinstance(document, dict):
        raise SnapshotWorkerError("Lean snapshot worker omitted the trace document")
    return document


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proof-video-snapshot-worker")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve")
    serve.add_argument("--workspace", type=Path, required=True)
    serve.add_argument("--toolchain", required=True)
    serve.add_argument("--ready-path", type=Path, required=True)
    serve.add_argument("--token", required=True)
    serve.add_argument("--identity", required=True)
    serve.add_argument("--idle-seconds", type=int, default=WORKER_IDLE_SECONDS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return _serve(
        workspace=args.workspace.resolve(),
        toolchain=args.toolchain,
        ready_path=args.ready_path.resolve(),
        token=args.token,
        identity=args.identity,
        idle_seconds=max(1, args.idle_seconds),
    )


if __name__ == "__main__":
    raise SystemExit(main())
