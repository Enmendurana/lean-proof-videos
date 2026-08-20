from pathlib import Path

import pytest

from proof_video.toolchains import (
    prepare_lean_432_workspace,
    record_lean_432_qualification,
    resolve_toolchain_backend,
)


def _project(root: Path) -> None:
    (root / "Animate.lean").write_text("import Lean\n", encoding="utf-8")
    (root / "Animate").mkdir()
    (root / "Animate" / "Schema.lean").write_text(
        "import Lean\nnamespace Animate\nend Animate\n", encoding="utf-8"
    )
    (root / "lean-toolchain").write_text("leanprover/lean4:v4.28.0\n", encoding="utf-8")


def _passing_gates() -> dict[str, bool]:
    return {
        "tests": True,
        "lakeBuild": True,
        "noSorry": True,
        "typesAndAxiomsEquivalent": True,
        "strictAuditEquivalent": True,
        "coldNotSlower": True,
        "warmAtLeastTwoTimesFaster": True,
        "lateEditAtLeastTwoTimesFaster": True,
        "peakMemoryUnder8GiB": True,
    }


def test_auto_prefers_432_before_and_after_qualification(tmp_path: Path) -> None:
    project = tmp_path / "project"
    cache = tmp_path / "cache"
    project.mkdir()
    _project(project)

    initial = resolve_toolchain_backend(project, cache, "auto")
    assert initial.name == "lean-4.32"
    assert initial.qualified is False
    record_lean_432_qualification(project, cache, gates=_passing_gates())
    selected = resolve_toolchain_backend(project, cache, "auto")
    assert selected.name == "lean-4.32"
    assert selected.qualified is True


def test_qualification_rejects_a_missing_gate(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _project(project)
    gates = _passing_gates()
    gates["strictAuditEquivalent"] = False
    with pytest.raises(ValueError, match="strictAuditEquivalent"):
        record_lean_432_qualification(project, tmp_path / "cache", gates=gates)


def test_432_workspace_is_isolated_and_preserves_source_layout(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _project(project)
    source = project / "Input" / "Demo.lean"
    source.parent.mkdir()
    source.write_text("theorem demo : True := by trivial\n", encoding="utf-8")
    backend = resolve_toolchain_backend(project, tmp_path / "cache", "lean-4.32")

    mapping = prepare_lean_432_workspace(backend, [source], entry_sources=[source])

    mirrored = mapping[source.resolve()]
    assert mirrored == backend.execution_root / "Input" / "Demo.lean"
    mirrored_text = mirrored.read_text(encoding="utf-8")
    assert mirrored_text.startswith(
        "import SnapshotCertificate432\nimport ProofLatex\n"
    )
    assert mirrored.read_text(encoding="utf-8").endswith(
        source.read_text(encoding="utf-8")
    )
    assert (
        (backend.execution_root / "lean-toolchain")
        .read_text()
        .strip()
        .endswith("v4.32.1")
    )
    assert (backend.execution_root / "Animate" / "Schema.lean").is_file()
    assert "v4.32.1" in (backend.execution_root / "lakefile.lean").read_text()
    assert (project / "lean-toolchain").read_text().strip().endswith("v4.28.0")
