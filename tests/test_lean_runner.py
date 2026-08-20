from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from proof_video.artifact_integrity import record_artifact
from proof_video.cache import lean_extractor_identity
from proof_video.lean_runner import (
    EXTRACTOR_ARTIFACT_KIND,
    SNAPSHOT_READER_BUILD_TIMEOUT_SECONDS,
    ensure_extractor_executable,
    ensure_snapshot_reader_modules,
    extractor_executable_path,
)


def _minimal_project(root: Path) -> None:
    (root / "Animate.lean").write_text("import Lean\n", encoding="utf-8")
    (root / "lean-toolchain").write_text("leanprover/lean4:v4.28.0\n", encoding="utf-8")
    (root / "lakefile.lean").write_text("import Lake\n", encoding="utf-8")


def test_current_extractor_skips_lake(tmp_path: Path, monkeypatch) -> None:
    _minimal_project(tmp_path)
    executable = extractor_executable_path(tmp_path)
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"extractor")
    record_artifact(
        executable,
        kind=EXTRACTOR_ARTIFACT_KIND,
        identity=lean_extractor_identity(tmp_path),
    )

    def unexpected_run(*_args, **_kwargs):
        raise AssertionError("Lake must not run for a current extractor")

    monkeypatch.setattr("proof_video.lean_runner.subprocess.run", unexpected_run)
    assert ensure_extractor_executable(tmp_path) == executable


def test_stale_extractor_builds_once_and_records_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _minimal_project(tmp_path)
    executable = extractor_executable_path(tmp_path)
    canonical = tmp_path / ".lake" / "build" / "bin" / executable.name
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        canonical.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_bytes(b"new extractor")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("proof_video.lean_runner.subprocess.run", fake_run)

    assert ensure_extractor_executable(tmp_path) == executable
    assert ensure_extractor_executable(tmp_path) == executable
    assert calls == [["lake", "build", "Animate"]]


def test_locked_canonical_executable_links_versioned_copy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _minimal_project(tmp_path)
    executable = extractor_executable_path(tmp_path)
    linked = []

    def locked_build(command, **_kwargs):
        assert command == ["lake", "build", "Animate"]
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="failed to write output 'Animate.exe': Permission denied",
        )

    def fake_link(root: Path, target: Path) -> None:
        linked.append((root, target))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"versioned extractor")

    monkeypatch.setattr("proof_video.lean_runner.os.name", "nt")
    monkeypatch.setattr("proof_video.lean_runner.subprocess.run", locked_build)
    monkeypatch.setattr(
        "proof_video.lean_runner._link_versioned_executable",
        fake_link,
    )

    assert ensure_extractor_executable(tmp_path) == executable
    assert linked == [(tmp_path.resolve(), executable)]


def test_snapshot_reader_timeout_is_a_backend_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _minimal_project(tmp_path)

    def timed_out(command, **kwargs):
        assert command == [
            "lake",
            "build",
            "ProofLatex",
            "SnapshotCertificate",
            "SnapshotReader",
        ]
        assert kwargs["timeout"] == SNAPSHOT_READER_BUILD_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr("proof_video.lean_runner.subprocess.run", timed_out)

    with pytest.raises(SystemExit, match="auto backend may now retry"):
        ensure_snapshot_reader_modules(tmp_path)
