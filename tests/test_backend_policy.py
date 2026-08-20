from pathlib import Path

import pytest

from proof_video.backend_policy import (
    backend_attempts,
    run_with_backend_fallback,
)


def _project(root: Path) -> None:
    root.mkdir()
    (root / "lean-toolchain").write_text("leanprover/lean4:v4.28.0\n", encoding="utf-8")


def test_auto_orders_432_snapshot_before_428_legacy(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _project(project)

    attempts = backend_attempts(project, tmp_path / "cache", "auto", None)

    assert [(item.backend.name, item.trace_backend) for item in attempts] == [
        ("lean-4.32", "snapshot"),
        ("lean-4.28", "legacy"),
    ]


def test_auto_retries_the_whole_operation_on_428(tmp_path: Path, capsys) -> None:
    project = tmp_path / "project"
    _project(project)
    calls: list[tuple[str, str]] = []

    def operation(backend, trace_backend):
        calls.append((backend.name, trace_backend))
        if backend.name == "lean-4.32":
            raise RuntimeError("snapshot setup failed")
        return "verified trace"

    result = run_with_backend_fallback(
        project,
        tmp_path / "cache",
        "auto",
        None,
        operation,
        phase="test acquisition",
    )

    assert calls == [("lean-4.32", "snapshot"), ("lean-4.28", "legacy")]
    assert result.backend.name == "lean-4.28"
    assert result.value == "verified trace"
    assert "Automatically falling back" in capsys.readouterr().out


def test_explicit_432_is_fail_fast(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _project(project)
    calls: list[str] = []

    def operation(backend, _trace_backend):
        calls.append(backend.name)
        raise RuntimeError("4.32 failed")

    with pytest.raises(RuntimeError, match="4.32 failed"):
        run_with_backend_fallback(
            project,
            tmp_path / "cache",
            "lean-4.32",
            None,
            operation,
            phase="test acquisition",
        )
    assert calls == ["lean-4.32"]


def test_explicit_snapshot_disables_legacy_fallback(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _project(project)

    attempts = backend_attempts(project, tmp_path / "cache", "auto", "snapshot")

    assert [(item.backend.name, item.trace_backend) for item in attempts] == [
        ("lean-4.32", "snapshot")
    ]
