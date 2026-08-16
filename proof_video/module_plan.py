"""Resumable multi-module proof developments.

A long proof can place stable helper results in ordinary Lean modules and list
those modules in ``Proof.proof-video.json``.  Each unit is elaborated and
cached independently.  Its kernel-certified chapters are then merged in the
declared topological order, so later edits do not invalidate earlier modules.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Any, Callable

from proof_video.cache import lean_evidence_identity, read_json, write_json
from proof_video.trace_store import (
    hydrate_hybrid_manifest,
    ingest_hybrid_manifest,
)


MODULE_PLAN_SCHEMA_VERSION = 1
_MODULE_END = re.compile(
    r"^\s*--\s*proof-video\s*:\s*module-end\s+([^\s]+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_THEOREM_MARKER = re.compile(
    r"^\s*--\s*proof-video\s*:\s*theorem\s+([^\s]+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_SHARED_PREAMBLE = re.compile(
    r"^\s*--\s*proof-video\s*:\s*shared-preamble-begin\s*$"
    r"(?P<body>.*?)"
    r"^\s*--\s*proof-video\s*:\s*shared-preamble-end\s*$",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)


@dataclass(frozen=True)
class ModuleUnit:
    lean_file: Path
    theorem: str


@dataclass(frozen=True)
class ModulePlan:
    path: Path
    units: tuple[ModuleUnit, ...]

    @property
    def final(self) -> ModuleUnit:
        return self.units[-1]


def companion_plan_path(lean_file: Path) -> Path:
    return lean_file.with_suffix(".proof-video.json")


def load_module_plan(lean_file: Path) -> ModulePlan | None:
    path = companion_plan_path(lean_file.resolve())
    if not path.exists():
        return generate_module_plan(lean_file.resolve())
    payload = read_json(path)
    if int(payload.get("schemaVersion", 0)) != MODULE_PLAN_SCHEMA_VERSION:
        raise ValueError(f"unsupported proof module plan schema: {path}")
    raw_units = payload.get("units")
    if not isinstance(raw_units, list) or not raw_units:
        raise ValueError(f"proof module plan has no units: {path}")
    units: list[ModuleUnit] = []
    for index, raw in enumerate(raw_units):
        if not isinstance(raw, dict):
            raise ValueError(f"module plan unit {index} is not an object")
        relative = str(raw.get("leanFile", "")).strip()
        theorem = str(raw.get("theorem", "")).strip()
        if not relative or not theorem:
            raise ValueError(f"module plan unit {index} needs leanFile and theorem")
        source = (path.parent / relative).resolve()
        if not source.is_file():
            raise ValueError(f"module plan source does not exist: {source}")
        units.append(ModuleUnit(source, theorem))
    if units[-1].lean_file != lean_file.resolve():
        raise ValueError("the final module plan unit must be the requested Lean file")
    return ModulePlan(path.resolve(), tuple(units))


def _atomic_write_text(path: Path, content: str) -> None:
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def generate_module_plan(lean_file: Path) -> ModulePlan | None:
    """Materialize explicit safe module boundaries from one readable source.

    Boundaries are source annotations, never heuristics. This prevents a
    generated module from accidentally cutting an open namespace, section or
    declaration. The original file remains the human-edited authority.
    """

    source = lean_file.read_text(encoding="utf-8")
    boundaries = list(_MODULE_END.finditer(source))
    if not boundaries:
        return None
    theorem_markers = _THEOREM_MARKER.findall(source)
    if len(theorem_markers) != 1:
        raise ValueError(
            "a generated modular proof needs exactly one proof-video theorem marker"
        )
    preamble_match = _SHARED_PREAMBLE.search(source)
    if preamble_match is None:
        raise ValueError(
            "module-end annotations require a shared-preamble-begin/end block"
        )
    shared_preamble = preamble_match.group("body").strip() + "\n"
    chunks: list[str] = []
    theorems: list[str] = []
    cursor = 0
    for boundary in boundaries:
        chunks.append(source[cursor : boundary.start()].rstrip() + "\n")
        theorems.append(boundary.group(1))
        cursor = boundary.end()
    chunks.append(source[cursor:].lstrip())
    theorems.append(theorem_markers[0])

    project_root = Path(__file__).resolve().parents[1]
    try:
        source_key = lean_file.relative_to(project_root).with_suffix("")
        generated_root = project_root / "GeneratedProofs" / source_key
    except ValueError:
        path_key = hashlib.sha256(str(lean_file).encode("utf-8")).hexdigest()[:16]
        generated_root = (
            project_root / "GeneratedProofs" / "External" / f"G{path_key}"
            / lean_file.stem
        )
    module_prefix = ".".join(generated_root.relative_to(project_root).parts)
    units: list[ModuleUnit] = []
    previous_module: str | None = None
    for index, (chunk, theorem) in enumerate(zip(chunks, theorems, strict=True), 1):
        part = generated_root / f"Part{index:02d}.lean"
        module_name = f"{module_prefix}.Part{index:02d}"
        if index == 1:
            content = chunk
        else:
            content = f"import {previous_module}\n\n{shared_preamble}\n{chunk}"
        content = re.sub(
            r"^\s*--\s*proof-video\s*:\s*shared-preamble-(?:begin|end)\s*$\n?",
            "",
            content,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        _atomic_write_text(part, content)
        units.append(ModuleUnit(part.resolve(), theorem))
        previous_module = module_name

    plan_path = generated_root / "plan.json"
    write_json(
        plan_path,
        {
            "schemaVersion": MODULE_PLAN_SCHEMA_VERSION,
            "generatedFrom": str(lean_file),
            "units": [
                {"leanFile": str(unit.lean_file), "theorem": unit.theorem}
                for unit in units
            ],
        },
    )
    return ModulePlan(plan_path.resolve(), tuple(units))


def module_plan_key(plan: ModulePlan) -> str:
    digest = hashlib.sha256(b"proof-video-module-plan-v1\0")
    digest.update(plan.path.read_bytes())
    for unit in plan.units:
        digest.update(str(unit.lean_file).encode())
        digest.update(b"\0")
        digest.update(unit.theorem.encode())
        digest.update(b"\0")
    return digest.hexdigest()


def merge_module_traces(
    traces: list[tuple[dict[str, Any], Path]],
    *,
    theorem: str,
    object_store: Path,
) -> dict[str, Any]:
    """Merge independently checked modules without weakening certificates."""

    chapters: list[dict[str, Any]] = []
    module_rows: list[dict[str, Any]] = []
    for module_index, (manifest, base_dir) in enumerate(traces):
        hydrated = hydrate_hybrid_manifest(manifest, base_dir=base_dir)
        unit_chapters = hydrated.get("chapters", [])
        if not hydrated.get("validation", {}).get("valid", False):
            raise ValueError(f"module trace {module_index} failed Lean validation")
        start = len(chapters)
        for chapter in unit_chapters:
            rewritten = {
                **chapter,
                "id": len(chapters),
                "isMain": False,
            }
            chapters.append(rewritten)
        module_rows.append(
            {
                "index": module_index,
                "theorem": hydrated.get("theoremName", ""),
                "firstChapter": start,
                "chapterCount": len(unit_chapters),
            }
        )
    if not chapters:
        raise ValueError("module plan produced no proof chapters")
    if str(chapters[-1].get("theoremName", "")) != theorem:
        raise ValueError("the final module chapter is not the requested theorem")
    chapters[-1] = {**chapters[-1], "isMain": True}
    embedded = {
        "schemaVersion": "3.0",
        "theoremName": theorem,
        "source": "Lean.InfoTree/modular-source-tactics+kernel-chapter-certificates",
        "granularity": "source-tactic/local-theorem-chapters",
        "chapters": chapters,
        "validation": {
            "valid": True,
            "dependencyOrderValid": True,
            "allChaptersKernelChecked": True,
            "noSorry": True,
            "errors": [],
        },
        "modulePlan": module_rows,
    }
    merged = ingest_hybrid_manifest(embedded, object_store)
    return {**merged, "modulePlan": module_rows}


def materialize_module_plan(
    plan: ModulePlan,
    *,
    cache_root: Path,
    rebuild_trace: bool,
    export_unit: Callable[[ModuleUnit, Path, bool], None],
    rebuild_chapter: str | None = None,
    identity_root: Path | None = None,
    backend_identity: dict[str, Any] | None = None,
    identity_sources: dict[Path, Path] | None = None,
) -> Path:
    """Export missing units, then atomically publish one combined manifest."""

    directory = cache_root / "module-plans" / module_plan_key(plan)
    directory.mkdir(parents=True, exist_ok=True)
    traces: list[tuple[dict[str, Any], Path]] = []
    for index, unit in enumerate(plan.units):
        unit_identity = lean_evidence_identity(
            identity_root or cache_root.parent,
            (identity_sources or {}).get(unit.lean_file.resolve(), unit.lean_file),
            unit.theorem,
            "hybrid",
            backend_identity,
        )
        unit_output = directory / f"unit-{index}-{unit_identity['key']}.mp4"
        unit_trace = unit_output.with_suffix(".json")
        rebuild_unit_chapter = False
        if rebuild_chapter is not None and unit_trace.exists():
            try:
                existing = hydrate_hybrid_manifest(read_json(unit_trace), base_dir=unit_trace.parent)
                rebuild_unit_chapter = any(
                    str(chapter.get("theoremName", "")) == rebuild_chapter
                    for chapter in existing.get("chapters", [])
                )
            except (OSError, UnicodeError, ValueError):
                rebuild_unit_chapter = True
        if rebuild_chapter == unit.theorem:
            rebuild_unit_chapter = True
        needs_export = rebuild_trace or rebuild_unit_chapter or not unit_trace.exists()
        print(
            f"Lean module: {unit.lean_file.name} | unit {index + 1}/{len(plan.units)} "
            f"| {unit.theorem} | {'cold/rebuild' if needs_export else 'trace reused'}",
            flush=True,
        )
        if needs_export:
            export_unit(unit, unit_output, rebuild_trace)
        if not unit_trace.exists():
            raise ValueError(f"module unit did not produce a trace: {unit_trace}")
        traces.append((read_json(unit_trace), unit_trace.parent))
    merged = merge_module_traces(
        traces,
        theorem=plan.final.theorem,
        object_store=directory / "objects",
    )
    manifest = directory / "combined.json"
    write_json(manifest, merged)
    return manifest
