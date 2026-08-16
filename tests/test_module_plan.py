from __future__ import annotations

import json
from pathlib import Path

import pytest

from proof_video.cache import write_json
from proof_video.module_plan import (
    generate_module_plan,
    load_module_plan,
    materialize_module_plan,
)
from proof_video.trace_store import iter_hybrid_chapters


def _trace(theorem: str) -> dict:
    return {
        "schemaVersion": "3.0",
        "theoremName": theorem,
        "chapters": [
            {
                "id": 0,
                "theoremName": theorem,
                "dependencies": [],
                "movie": {"theoremName": theorem, "startGoal": {}, "actions": []},
                "proofFingerprint": f"proof-{theorem}",
                "axioms": [],
                "validation": {
                    "valid": True,
                    "kernelChecked": True,
                    "noSorry": True,
                    "errors": [],
                },
                "isMain": True,
            }
        ],
        "validation": {
            "valid": True,
            "dependencyOrderValid": True,
            "allChaptersKernelChecked": True,
            "noSorry": True,
            "errors": [],
        },
    }


def test_module_units_resume_independently_and_merge_in_order(
    tmp_path: Path,
) -> None:
    first = tmp_path / "First.lean"
    final = tmp_path / "Final.lean"
    first.write_text("theorem first : True := by trivial\n", encoding="utf-8")
    final.write_text("import First\ntheorem final : True := by trivial\n", encoding="utf-8")
    write_json(
        final.with_suffix(".proof-video.json"),
        {
            "schemaVersion": 1,
            "units": [
                {"leanFile": "First.lean", "theorem": "first"},
                {"leanFile": "Final.lean", "theorem": "final"},
            ],
        },
    )
    plan = load_module_plan(final)
    assert plan is not None
    exports: list[str] = []

    def export(unit, output, _rebuild):
        exports.append(unit.theorem)
        write_json(output.with_suffix(".json"), _trace(unit.theorem))

    manifest = materialize_module_plan(
        plan,
        cache_root=tmp_path / "cache",
        rebuild_trace=False,
        export_unit=export,
    )
    # A second call reuses both unit traces.
    materialize_module_plan(
        plan,
        cache_root=tmp_path / "cache",
        rebuild_trace=False,
        export_unit=export,
    )

    merged = json.loads(manifest.read_text(encoding="utf-8"))
    chapters = list(iter_hybrid_chapters(merged, base_dir=manifest.parent))
    assert exports == ["first", "final"]
    assert [chapter["theoremName"] for chapter in chapters] == ["first", "final"]
    assert [chapter["isMain"] for chapter in chapters] == [False, True]


def test_module_plan_requires_requested_file_last(tmp_path: Path) -> None:
    first = tmp_path / "First.lean"
    final = tmp_path / "Final.lean"
    first.write_text("theorem first : True := by trivial\n", encoding="utf-8")
    final.write_text("theorem final : True := by trivial\n", encoding="utf-8")
    write_json(
        final.with_suffix(".proof-video.json"),
        {
            "schemaVersion": 1,
            "units": [{"leanFile": "First.lean", "theorem": "first"}],
        },
    )

    with pytest.raises(ValueError, match="final module plan unit"):
        load_module_plan(final)


def test_explicit_source_boundaries_generate_import_chain(tmp_path: Path) -> None:
    source = tmp_path / "Long.lean"
    source.write_text(
        """import Mathlib
-- proof-video: theorem final
-- proof-video: shared-preamble-begin
open Classical
set_option maxHeartbeats 0
-- proof-video: shared-preamble-end
theorem first : True := by trivial
-- proof-video: module-end first
theorem final : True := by exact first
""",
        encoding="utf-8",
    )

    plan = generate_module_plan(source)

    assert plan is not None
    assert [unit.theorem for unit in plan.units] == ["first", "final"]
    second = plan.units[1].lean_file.read_text(encoding="utf-8")
    assert second.startswith("import GeneratedProofs.External.G")
    assert "open Classical" in second
    assert "theorem final" in second

    first_path = plan.units[0].lean_file
    first_content = first_path.read_bytes()
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "theorem final : True := by exact first",
            "theorem final : True := by trivial",
        ),
        encoding="utf-8",
    )
    changed = generate_module_plan(source)
    assert changed is not None
    assert changed.units[0].lean_file == first_path
    assert changed.units[0].lean_file.read_bytes() == first_content
