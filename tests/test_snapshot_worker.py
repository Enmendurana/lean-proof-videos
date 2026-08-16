import json
from pathlib import Path
import socket

from proof_video.snapshot_worker import _read_message, _worker_identity


def test_worker_protocol_reads_one_bounded_json_message() -> None:
    left, right = socket.socketpair()
    try:
        right.sendall(json.dumps({"ok": True, "value": 7}).encode() + b"\n")
        assert _read_message(left, 1024) == {"ok": True, "value": 7}
    finally:
        left.close()
        right.close()


def test_worker_identity_changes_with_import_header(tmp_path: Path) -> None:
    (tmp_path / "Animate.lean").write_text("import Lean\n", encoding="utf-8")
    (tmp_path / "SnapshotAnimate432.lean").write_text(
        "import Animate\n", encoding="utf-8"
    )
    (tmp_path / "lean-toolchain").write_text(
        "leanprover/lean4:v4.32.1\n", encoding="utf-8"
    )
    metadata = {
        "identity": {
            "lakeManifestSha256": "manifest",
            "headerSha256": "header-a",
            "extractorAbi": 4,
        }
    }
    first = _worker_identity(
        tmp_path,
        toolchain="leanprover/lean4:v4.32.1",
        snapshot_metadata=metadata,
    )
    metadata["identity"]["headerSha256"] = "header-b"
    second = _worker_identity(
        tmp_path,
        toolchain="leanprover/lean4:v4.32.1",
        snapshot_metadata=metadata,
    )
    assert first != second
