"""Explicit Lean toolchain backends and their isolated cache namespaces.

The repository remains pinned to Lean 4.28 for a stable rollback environment.
Lean 4.32 is prepared in an isolated workspace and is the optimistic ``auto``
backend.  Its qualification record is diagnostic; operation-level fallback is
implemented separately so evidence from different toolchains never mixes.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import shutil
from typing import Any, Iterable

from proof_video.artifact_integrity import file_sha256
from proof_video.cache import read_json, write_json
from proof_video.lean_sources import WORKSPACE_SOURCE_PATHS


TOOLCHAIN_CHOICES = ("auto", "lean-4.32", "lean-4.28")
LEAN_428_TOOLCHAIN = "leanprover/lean4:v4.28.0"
LEAN_432_TOOLCHAIN = "leanprover/lean4:v4.32.1"
MATHLIB_428 = "v4.28.0"
MATHLIB_432 = "v4.32.1"
QUALIFICATION_SCHEMA_VERSION = 1
EXTRACTOR_ABI_VERSION = 5

_WORKSPACE_SOURCES = WORKSPACE_SOURCE_PATHS


@dataclass(frozen=True)
class ToolchainBackend:
    """Resolved backend.  Cache roots from different Lean versions never mix."""

    name: str
    lean_toolchain: str
    mathlib_version: str
    project_root: Path
    execution_root: Path
    cache_root: Path
    experimental: bool
    qualified: bool

    @property
    def identity(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "leanToolchain": self.lean_toolchain,
            "mathlibVersion": self.mathlib_version,
            "extractorAbi": EXTRACTOR_ABI_VERSION,
        }

    def evidence_cache_root(self, shared_cache_root: Path) -> Path:
        if self.name == "lean-4.28":
            # Preserve every existing 4.28 evidence key and path.
            return shared_cache_root
        return self.cache_root / "cache"


def qualification_path(cache_root: Path) -> Path:
    return cache_root / "toolchains" / "lean-4.32.1" / "qualification.json"


def _extractor_digest(project_root: Path) -> str:
    digest = hashlib.sha256()
    for name in _WORKSPACE_SOURCES:
        path = project_root / name
        if not path.is_file():
            continue
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def qualification_identity(project_root: Path) -> dict[str, Any]:
    return {
        "schemaVersion": QUALIFICATION_SCHEMA_VERSION,
        "leanToolchain": LEAN_432_TOOLCHAIN,
        "mathlibVersion": MATHLIB_432,
        "extractorAbi": EXTRACTOR_ABI_VERSION,
        "extractorDigest": _extractor_digest(project_root.resolve()),
    }


def lean_432_is_qualified(project_root: Path, cache_root: Path) -> bool:
    path = qualification_path(cache_root)
    try:
        value = read_json(path)
    except (FileNotFoundError, OSError, UnicodeError, ValueError):
        return False
    return (
        value.get("status") == "qualified"
        and value.get("identity") == qualification_identity(project_root)
        and bool(value.get("gates", {}).get("allPassed", False))
    )


def record_lean_432_qualification(
    project_root: Path,
    cache_root: Path,
    *,
    gates: dict[str, Any],
) -> Path:
    """Commit diagnostic qualification evidence when every gate passed."""

    required = (
        "tests",
        "lakeBuild",
        "noSorry",
        "typesAndAxiomsEquivalent",
        "strictAuditEquivalent",
        "coldNotSlower",
        "warmAtLeastTwoTimesFaster",
        "lateEditAtLeastTwoTimesFaster",
        "peakMemoryUnder8GiB",
    )
    all_passed = all(gates.get(name) is True for name in required)
    if not all_passed:
        missing = ", ".join(name for name in required if gates.get(name) is not True)
        raise ValueError(f"Lean 4.32 qualification gates did not pass: {missing}")
    path = qualification_path(cache_root)
    write_json(
        path,
        {
            "schemaVersion": QUALIFICATION_SCHEMA_VERSION,
            "status": "qualified",
            "identity": qualification_identity(project_root),
            "gates": {**gates, "allPassed": True},
        },
    )
    return path


def resolve_toolchain_backend(
    project_root: Path,
    cache_root: Path,
    requested: str = "auto",
) -> ToolchainBackend:
    project_root = project_root.resolve()
    cache_root = cache_root.resolve()
    if requested not in TOOLCHAIN_CHOICES:
        raise ValueError(
            f"unknown toolchain backend {requested!r}; expected "
            + ", ".join(TOOLCHAIN_CHOICES)
        )
    qualified = lean_432_is_qualified(project_root, cache_root)
    # ``auto`` is an optimistic preference, not a qualification gate.  The
    # caller owns the operation-level retry to 4.28 so preparation, extraction
    # and their cache namespaces can be retried as one atomic phase.
    selected = "lean-4.32" if requested == "auto" else requested
    if selected == "lean-4.28":
        return ToolchainBackend(
            name=selected,
            lean_toolchain=LEAN_428_TOOLCHAIN,
            mathlib_version=MATHLIB_428,
            project_root=project_root,
            execution_root=project_root,
            cache_root=cache_root,
            experimental=False,
            qualified=True,
        )
    backend_root = cache_root / "toolchains" / "lean-4.32.1"
    return ToolchainBackend(
        name=selected,
        lean_toolchain=LEAN_432_TOOLCHAIN,
        mathlib_version=MATHLIB_432,
        project_root=project_root,
        execution_root=backend_root / "workspace",
        cache_root=backend_root,
        experimental=True,
        qualified=qualified,
    )


def _atomic_copy(source: Path, target: Path) -> None:
    if target.is_file() and file_sha256(source) == file_sha256(target):
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".syncing")
    shutil.copy2(source, temporary)
    temporary.replace(target)


def _atomic_text(path: Path, value: str) -> None:
    if path.is_file() and path.read_text(encoding="utf-8") == value:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".syncing")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _patch_lean_tex_432(workspace: Path) -> None:
    """Apply the minimal upstream compatibility shim in the isolated clone.

    LeanTeX currently follows an older Lean API.  Keeping the edits here makes
    them reproducible after ``lake update`` while leaving the production 4.28
    package untouched.  Already-patched and not-yet-downloaded trees are both
    harmless.
    """

    replacements = {
        Path("LeanTeX/Builtins.lean"): (
            ("s.split ('_' == ·)", "s.splitToList ('_' == ·)"),
        ),
        Path("LeanTeX/LatexCmd.lean"): (
            (
                "res |>.split Char.isWhitespace |>.filter",
                "res |>.splitToList Char.isWhitespace |>.filter",
            ),
        ),
        Path("LeanTeX/RuleSyntax.lean"): (
            ("aux_def latex_pp_rule", "private def latex_pp_rule"),
        ),
    }
    package_root = workspace / ".lake" / "packages" / "LeanTeX"
    for relative, edits in replacements.items():
        path = package_root / relative
        if not path.is_file():
            continue
        source = path.read_text(encoding="utf-8")
        patched = source
        for old, new in edits:
            patched = patched.replace(old, new)
        if patched != source:
            _atomic_text(path, patched)


def prepare_lean_432_workspace(
    backend: ToolchainBackend,
    sources: Iterable[Path] = (),
    *,
    entry_sources: Iterable[Path] = (),
) -> dict[Path, Path]:
    """Synchronize extractor/input sources into the isolated 4.32 workspace.

    The function performs no network access and does not run Lake.  A caller
    may bootstrap dependencies explicitly after inspecting the workspace.
    """

    if backend.name != "lean-4.32":
        return {path.resolve(): path.resolve() for path in sources}
    workspace = backend.execution_root
    workspace.mkdir(parents=True, exist_ok=True)
    _atomic_text(workspace / "lean-toolchain", backend.lean_toolchain + "\n")
    _atomic_text(
        workspace / "lakefile.lean",
        """import Lake
open Lake DSL

package «animate» where
  leanOptions := #[
    ⟨`pp.unicode.fun, true⟩,
    ⟨`autoImplicit, false⟩,
    ⟨`relaxedAutoImplicit, false⟩
  ]

@[default_target] lean_lib Annotations
@[default_target] lean_lib Input
@[default_target] lean_lib StringMatching
@[default_target] lean_lib HighlightSyntax
@[default_target] lean_lib MathlibLatex
@[default_target] lean_lib ProofLatex
@[default_target] lean_lib SemanticTransitions
@[default_target] lean_lib ProofTrace
lean_lib ProofVideoExtractor where
  roots := #[`Animate]
lean_lib SnapshotCertificate where
  roots := #[`SnapshotCertificate432]
lean_lib SnapshotReader where
  roots := #[`SnapshotAnimate432]
@[default_target] lean_exe «Animate» where
  root := `AnimateMain
  supportInterpreter := true

require LeanTeX from git "https://github.com/kmill/LeanTeX" @ "main"
-- Keep Mathlib last so its exact 4.32 transitive revisions override LeanTeX's
-- older pins and match the official binary cache hashes.
require mathlib from git "https://github.com/leanprover-community/mathlib4" @ "v4.32.1"
""",
    )
    mapping: dict[Path, Path] = {}
    entries = {path.resolve() for path in entry_sources}
    for name in _WORKSPACE_SOURCES:
        source = backend.project_root / name
        if source.is_file():
            _atomic_copy(source, workspace / name)
    for source in sources:
        resolved = source.resolve()
        try:
            relative = resolved.relative_to(backend.project_root)
        except ValueError:
            digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:16]
            relative = Path("External") / digest / resolved.name
        target = workspace / relative
        if resolved in entries:
            content = resolved.read_text(encoding="utf-8")
            imports = {line.strip() for line in content.splitlines()}
            injected = []
            if "import SnapshotCertificate432" not in imports:
                # Snapshot restoration must register the extractor's environment
                # extensions in exactly the same module order as its reader.
                injected.append("import SnapshotCertificate432")
            if "import ProofLatex" not in imports:
                injected.append("import ProofLatex")
            if injected:
                content = "\n".join(injected) + "\n" + content
            _atomic_text(target, content)
        else:
            _atomic_copy(resolved, target)
        mapping[resolved] = target.resolve()
    _patch_lean_tex_432(workspace)
    return mapping
