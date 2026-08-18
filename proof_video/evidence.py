"""Acquisition and durable storage of kernel-checked Lean evidence.

This layer deliberately knows nothing about LaTeX, animation or video engines.
Keeping extraction here makes renderer refactors unable to invalidate a proof
trace and gives modular proof plans one reusable evidence service.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from proof_video.cache import (
    lean_checkpoint_key,
    lean_evidence_identity,
    legacy_lean_evidence_identity,
    lean_trace_key,
    read_json,
)
from proof_video.evidence_cache import (
    evidence_trace_path,
    read_trace_evidence,
    write_trace_evidence,
)
from proof_video.lean_export import export_trace
from proof_video.trace_store import ingest_hybrid_manifest
from proof_video.toolchains import ToolchainBackend
from proof_video.checkpoint_migration import seed_compatible_command_captures


@dataclass
class EvidenceResult:
    document: dict[str, Any]
    base_dir: Path
    source: str
    cache_hit: bool
    pending_commit: tuple[Path, dict[str, Any], dict[str, Any]] | None = None

    def commit(self) -> Path | None:
        """Publish evidence only after the caller's strict audit succeeds."""

        if self.pending_commit is None:
            return None
        path, document, identity = self.pending_commit
        write_trace_evidence(path, document, identity)
        self.pending_commit = None
        return path


def _schema_at_least(value: Any, major: int, minor: int) -> bool:
    try:
        parts = str(value).split(".", 2)
        actual = (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except (TypeError, ValueError):
        return False
    return actual >= (major, minor)


def _satisfies_trace_contract(document: dict[str, Any], trace_mode: str) -> bool:
    """Whether cached evidence contains every fact required by this ABI.

    Renderer-only changes intentionally do not invalidate Lean evidence.  The
    proof-term 2.2 change is different: exact checked forall arguments are new
    kernel evidence and cannot safely be guessed from a 2.1 document.
    """

    return trace_mode != "proof-term" or _schema_at_least(
        document.get("schemaVersion"), 2, 2
    )


def acquire_lean_evidence(
    *,
    root: Path,
    cache_root: Path,
    output: Path,
    lean_file: Path,
    theorem: str,
    trace_mode: str,
    rebuild_trace: bool,
    postprocess_workers: int,
    force_export: bool = False,
    module_output: Path | None = None,
    toolchain_backend: ToolchainBackend | None = None,
    trace_backend: str = "legacy",
    rebuild_chapter: str | None = None,
) -> EvidenceResult:
    """Return compatible evidence or run exactly one verified Lean export."""

    backend_identity = (
        toolchain_backend.identity
        if toolchain_backend is not None and toolchain_backend.name != "lean-4.28"
        else None
    )
    identity = lean_evidence_identity(
        root,
        lean_file,
        theorem,
        trace_mode,
        backend_identity,
    )
    durable_trace = evidence_trace_path(cache_root, identity["key"])
    legacy_identity = legacy_lean_evidence_identity(
        root,
        lean_file,
        theorem,
        trace_mode,
        backend_identity,
    )
    legacy_evidence = evidence_trace_path(cache_root, legacy_identity["key"])
    legacy_key = lean_trace_key(root, lean_file, f"{theorem}:{trace_mode}")
    legacy_trace = cache_root / "traces" / f"{legacy_key}.json"

    use_persistent_evidence = (
        not rebuild_trace and not force_export and rebuild_chapter is None
    )
    if use_persistent_evidence:
        try:
            cached = read_trace_evidence(durable_trace, identity)
        except ValueError as error:
            raise ValueError(
                f"{error}\nUse --rebuild-trace to replace this artifact."
            ) from error
        if cached is not None:
            if _satisfies_trace_contract(cached, trace_mode):
                print(f"Persistent Lean evidence hit: {durable_trace.name}", flush=True)
                return EvidenceResult(
                    cached,
                    durable_trace.parent,
                    "persistent-lean-evidence",
                    True,
                )
            print(
                "Persistent proof-term evidence predates the certified "
                "instantiation contract; rebuilding it once...",
                flush=True,
            )
        if legacy_evidence != durable_trace:
            try:
                cached = read_trace_evidence(legacy_evidence, legacy_identity)
            except ValueError as error:
                raise ValueError(
                    f"{error}\nUse --rebuild-trace to replace this artifact."
                ) from error
            if cached is not None:
                if _satisfies_trace_contract(cached, trace_mode):
                    print(
                        "Persistent Lean evidence hit (schema v1); migrating its "
                        "validated trace to the comment-stable v2 key...",
                        flush=True,
                    )
                    return EvidenceResult(
                        cached,
                        legacy_evidence.parent,
                        "persistent-lean-evidence-v1",
                        True,
                        (durable_trace, cached, identity),
                    )

    if use_persistent_evidence and legacy_trace.exists():
        print(
            "Migrating the compatible legacy Lean trace into durable evidence "
            "storage...",
            flush=True,
        )
        document = ingest_hybrid_manifest(
            read_json(legacy_trace),
            cache_root / "trace-objects",
            source_base=legacy_trace.parent,
        )
        if _satisfies_trace_contract(document, trace_mode):
            return EvidenceResult(
                document,
                durable_trace.parent,
                "legacy-trace-migrated",
                True,
                (durable_trace, document, identity),
            )
        print(
            "Legacy proof-term trace predates schema 2.2; retaining it on "
            "disk and exporting current certified evidence...",
            flush=True,
        )

    if rebuild_trace:
        print("Rebuilding Lean evidence from source as requested...", flush=True)
    elif rebuild_chapter is not None:
        print(
            f"Rebuilding theorem chapter {rebuild_chapter}; compatible sibling "
            "chapters remain reusable...",
            flush=True,
        )
    elif force_export:
        print(
            "Re-exporting the Lean environment while retaining compatible "
            "in-progress checkpoints...",
            flush=True,
        )
    print(
        "Exporting and checking the Lean proof trace "
        "(the first run may take a while)...",
        flush=True,
    )
    chapter_checkpoint_dir = (
        cache_root
        / "trace-chapters"
        / lean_checkpoint_key(
            root,
            lean_file,
            f"{theorem}:{trace_mode}",
            backend_identity,
        )
        if not rebuild_trace
        else None
    )
    streaming_dir = (
        chapter_checkpoint_dir / "stream"
        if chapter_checkpoint_dir is not None
        else output.with_suffix(".trace") / "raw"
    )
    if chapter_checkpoint_dir is not None:
        search_roots = [cache_root]
        if toolchain_backend is not None and toolchain_backend.name != "lean-4.28":
            shared_root = toolchain_backend.project_root / ".lean-proof-video-cache"
            if shared_root.resolve() != cache_root.resolve():
                search_roots.append(shared_root)
        migrated = seed_compatible_command_captures(
            source_file=lean_file,
            target_checkpoint_dir=chapter_checkpoint_dir,
            search_cache_roots=search_roots,
        )
        if migrated:
            print(
                f"Seeded {migrated} compatible command-capture candidate(s) "
                "from an older extractor namespace; Lean will revalidate every "
                "proof fingerprint.",
                flush=True,
            )
    document = export_trace(
        root,
        lean_file,
        theorem,
        trace_mode,
        checkpoint_dir=chapter_checkpoint_dir,
        trace_output_dir=streaming_dir if trace_mode == "hybrid" else None,
        postprocess_workers=postprocess_workers,
        module_output=module_output,
        rebuild_chapter=rebuild_chapter,
        toolchain_backend=toolchain_backend,
        trace_backend=trace_backend,
    )
    document = ingest_hybrid_manifest(
        document,
        cache_root / "trace-objects",
        source_base=streaming_dir,
    )
    return EvidenceResult(
        document,
        durable_trace.parent,
        "lean-export",
        False,
        (durable_trace, document, identity),
    )
