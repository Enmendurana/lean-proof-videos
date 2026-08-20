"""Orchestrate validated Lean 4.32 full/header snapshots for one source."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from proof_video.incremental_snapshot import (
    commit_snapshot,
    run_incremental_lean,
    validate_snapshot,
)
from proof_video.lean_runner import lean_runtime_environment
from proof_video.toolchains import EXTRACTOR_ABI_VERSION, ToolchainBackend


@dataclass(frozen=True)
class SnapshotResult:
    status: str
    full_snapshot: Path
    header_snapshot: Path
    certificate: Path


def snapshot_paths(backend: ToolchainBackend, source: Path) -> tuple[Path, Path]:
    path_key = hashlib.sha256(str(source.resolve()).encode("utf-8")).hexdigest()
    directory = backend.cache_root / "snapshots" / path_key
    return directory / "full.incr", directory / "header.incr"


def _validate_kernel_certificate(
    certificate: Path,
    *,
    theorem: str,
    source_sha: str,
) -> None:
    """Reject stale proof evidence before publishing snapshot metadata.

    A snapshot is only a performance artifact.  The sidecar is emitted by a
    command executed in the same official Lean process that elaborated the
    source, and is therefore the boundary that certifies which declarations
    the snapshot actually contains.
    """

    if not certificate.is_file():
        raise RuntimeError(
            "Lean 4.32 completed without publishing the kernel certificate sidecar"
        )
    try:
        document = json.loads(certificate.read_text(encoding="utf-8"))
        rows = document["rows"]
        valid = (
            document.get("selectedTheorem") == theorem
            and document.get("sourceSha256") == source_sha
            and bool(rows)
            and rows[-1].get("theoremName") == theorem
            and all(row.get("validation", {}).get("valid") is True for row in rows)
            and all(
                row.get("validation", {}).get("kernelChecked") is True for row in rows
            )
            and all(row.get("validation", {}).get("noSorry") is True for row in rows)
        )
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError):
        valid = False
    if not valid:
        raise RuntimeError(
            "Lean 4.32 produced a stale or invalid kernel certificate sidecar"
        )


def refresh_incremental_snapshots(
    backend: ToolchainBackend,
    source: Path,
    theorem: str,
    module_output: Path | None = None,
) -> SnapshotResult:
    if backend.name != "lean-4.32":
        raise ValueError("incremental snapshots require the lean-4.32 backend")
    manifest = backend.execution_root / "lake-manifest.json"
    if not manifest.is_file():
        raise RuntimeError(
            "The isolated Lean 4.32 workspace has no lake-manifest.json yet. "
            "Run Lake setup for that backend before snapshot extraction."
        )
    full, header = snapshot_paths(backend, source)
    certificate = full.parent / "kernel-certificates.json"
    module_temporary: Path | None = None
    if module_output is not None:
        module_output.parent.mkdir(parents=True, exist_ok=True)
        module_temporary = module_output.with_suffix(module_output.suffix + ".writing")
        module_temporary.unlink(missing_ok=True)
    marker = "\n-- proof-video: generated snapshot certificate command\n"
    source_text = source.read_text(encoding="utf-8")
    source_text = source_text.split(marker, 1)[0].rstrip() + "\n"
    source_sha = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    certificate_command = (
        marker
        + f"#proof_video_certificate {theorem} "
        + json.dumps(str(certificate.resolve()))
        + " "
        + json.dumps(source_sha)
        + "\n"
    )
    prepared_text = source_text + certificate_command
    if source.read_text(encoding="utf-8") != prepared_text:
        temporary_source = source.with_suffix(source.suffix + ".snapshot-preparing")
        temporary_source.write_text(prepared_text, encoding="utf-8")
        temporary_source.replace(source)
    validation = validate_snapshot(
        full,
        source=source,
        lean_toolchain=backend.lean_toolchain,
        mathlib_version=backend.mathlib_version,
        lake_manifest=manifest,
        extractor_abi=EXTRACTOR_ABI_VERSION,
    )
    # Validate the import-only snapshot as an independent artifact as well.
    # It is not loaded for full-command reuse, but a damaged header snapshot
    # must be diagnosed instead of silently being treated as trusted cache.
    header_validation = validate_snapshot(
        header,
        source=source,
        lean_toolchain=backend.lean_toolchain,
        mathlib_version=backend.mathlib_version,
        lake_manifest=manifest,
        extractor_abi=EXTRACTOR_ABI_VERSION,
    )
    load = validation.valid
    print(
        f"Lean 4.32: {source.stem} | theorem {theorem}",
        flush=True,
    )
    print(
        "Lean 4.32 snapshot: "
        f"full={validation.status if load else 'cold-elaboration'} | "
        f"header={header_validation.status}",
        flush=True,
    )
    result = run_incremental_lean(
        toolchain=backend.lean_toolchain,
        workspace=backend.execution_root,
        source=source,
        snapshot=full,
        header_snapshot=header,
        load_snapshot=load,
        reuse_status=validation.status if load else "cold-elaboration",
        extra_args=(
            ("-o", str(module_temporary.resolve()))
            if module_temporary is not None
            else ()
        ),
        environment=dict(lean_runtime_environment(backend.execution_root)),
    )
    if result.returncode:
        details = "\n".join(
            value.strip() for value in (result.stdout, result.stderr) if value.strip()
        )
        raise RuntimeError(f"Lean 4.32 incremental frontend failed:\n{details}")
    if module_output is not None and module_temporary is not None:
        if not module_temporary.is_file():
            raise RuntimeError("Lean 4.32 did not publish the requested module output")
        module_temporary.replace(module_output)

    # Certificate validation deliberately precedes both metadata commits.  A
    # failed or interrupted proof command can leave raw `.incr` files behind,
    # but without a committed envelope they can never become load candidates.
    _validate_kernel_certificate(
        certificate,
        theorem=theorem,
        source_sha=source_sha,
    )
    validated_rows = (
        validation.metadata.get("identity", {}).get("dependencies", [])
        if validation.valid and validation.metadata is not None
        else []
    )
    validated_deps_sha = (
        str(validation.metadata.get("depsSha256", ""))
        if validation.valid and validation.metadata is not None
        else None
    )
    full_metadata = commit_snapshot(
        full,
        source=source,
        lean_toolchain=backend.lean_toolchain,
        mathlib_version=backend.mathlib_version,
        lake_manifest=manifest,
        extractor_abi=EXTRACTOR_ABI_VERSION,
        validated_dependencies=validated_rows,
        validated_deps_sha256=validated_deps_sha,
    )
    full_envelope = json.loads(full_metadata.read_text(encoding="utf-8"))
    commit_snapshot(
        header,
        source=source,
        lean_toolchain=backend.lean_toolchain,
        mathlib_version=backend.mathlib_version,
        lake_manifest=manifest,
        extractor_abi=EXTRACTOR_ABI_VERSION,
        validated_dependencies=full_envelope["identity"]["dependencies"],
        validated_deps_sha256=full_envelope["depsSha256"],
    )
    return SnapshotResult(
        validation.status if load else "cold-elaboration",
        full,
        header,
        certificate,
    )
