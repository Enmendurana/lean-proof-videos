"""Validated envelopes for Lean 4.32 experimental incremental snapshots."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Any, Iterable

from proof_video.artifact_integrity import file_sha256
from proof_video.cache import read_json, write_json


SNAPSHOT_METADATA_VERSION = 1
_IMPORT_LINE = re.compile(r"^\s*import\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class SnapshotValidation:
    valid: bool
    status: str
    metadata: dict[str, Any] | None = None


def snapshot_metadata_path(snapshot: Path) -> Path:
    return snapshot.with_suffix(snapshot.suffix + ".proof-video.json")


def snapshot_deps_path(snapshot: Path) -> Path:
    return snapshot.with_suffix(snapshot.suffix + ".deps")


def _source_header(source: Path) -> str:
    text = source.read_text(encoding="utf-8")
    return "\n".join(match.group(0).strip() for match in _IMPORT_LINE.finditer(text))


def _sha_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _dependency_paths(deps_path: Path) -> tuple[Path, ...]:
    """Accept Lean's line format and a JSON list/object for forward compatibility."""

    text = deps_path.read_text(encoding="utf-8").strip()
    if not text:
        return ()
    raw_values: list[str]
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        raw_values = [line.strip() for line in text.splitlines() if line.strip()]
    else:
        raw_values = []

        def collect(item: Any) -> None:
            if isinstance(item, str):
                raw_values.append(item)
            elif isinstance(item, list):
                for child in item:
                    collect(child)
            elif isinstance(item, dict):
                for child in item.values():
                    collect(child)

        collect(value)
    result: list[Path] = []
    for raw in raw_values:
        cleaned = raw.strip().strip('"')
        if not cleaned:
            continue
        path = Path(cleaned)
        if not path.is_absolute():
            path = deps_path.parent / path
        resolved = path.resolve()
        if resolved.exists() or resolved.suffix.lower() in {
            ".olean",
            ".ir",
            ".private",
            ".server",
        }:
            result.append(resolved)
    return tuple(dict.fromkeys(result))


def _dependency_rows(
    paths: Iterable[Path],
    prior_rows: Iterable[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    prior = {str(row.get("path", "")): row for row in prior_rows}
    rows = []
    for path in sorted({path.resolve() for path in paths}, key=str):
        if not path.is_file():
            raise FileNotFoundError(path)
        stat = path.stat()
        row: dict[str, Any] = {
            "path": str(path),
            "size": stat.st_size,
            "mtimeNs": stat.st_mtime_ns,
            "ctimeNs": stat.st_ctime_ns,
            "device": stat.st_dev,
            "inode": stat.st_ino,
        }
        previous = prior.get(str(path))
        stat_keys = ("size", "mtimeNs", "ctimeNs", "device", "inode")
        if previous is not None and all(previous.get(key) == row[key] for key in stat_keys):
            row["sha256"] = str(previous.get("sha256", ""))
        else:
            row["sha256"] = file_sha256(path)
        rows.append(row)
    return rows


def snapshot_identity(
    *,
    source: Path,
    lean_toolchain: str,
    mathlib_version: str,
    lake_manifest: Path,
    extractor_abi: int,
    dependency_paths: Iterable[Path],
    prior_dependencies: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    source = source.resolve()
    return {
        "leanToolchain": lean_toolchain,
        "mathlibVersion": mathlib_version,
        "lakeManifestSha256": file_sha256(lake_manifest),
        "sourcePath": str(source),
        # Kept for diagnostics. It is not required on load because Lean itself
        # reuses only the unchanged syntactic prefix.
        "sourceSha256": file_sha256(source),
        "headerSha256": _sha_text(_source_header(source)),
        "extractorAbi": extractor_abi,
        "dependencies": _dependency_rows(dependency_paths, prior_dependencies),
    }


def commit_snapshot(
    snapshot: Path,
    *,
    source: Path,
    lean_toolchain: str,
    mathlib_version: str,
    lake_manifest: Path,
    extractor_abi: int,
    validated_dependencies: Iterable[dict[str, Any]] | None = None,
    validated_deps_sha256: str | None = None,
) -> Path:
    """Commit metadata last; an interrupted snapshot is never considered valid."""

    deps = snapshot_deps_path(snapshot)
    if not snapshot.is_file() or not deps.is_file():
        raise FileNotFoundError(snapshot if not snapshot.is_file() else deps)
    prior_dependencies: Iterable[dict[str, Any]] = ()
    existing_metadata = snapshot_metadata_path(snapshot)
    if existing_metadata.is_file():
        try:
            prior = read_json(existing_metadata)
            prior_dependencies = prior.get("identity", {}).get("dependencies", [])
        except (OSError, UnicodeError, ValueError, AttributeError):
            # A damaged old envelope cannot make the new artifact valid; it
            # merely disables the stat-keyed hash reuse for this commit.
            prior_dependencies = ()
    deps_sha256 = file_sha256(deps)
    reusable_rows = list(validated_dependencies or ())
    if reusable_rows and validated_deps_sha256 == deps_sha256:
        identity = snapshot_identity(
            source=source,
            lean_toolchain=lean_toolchain,
            mathlib_version=mathlib_version,
            lake_manifest=lake_manifest,
            extractor_abi=extractor_abi,
            dependency_paths=(),
        )
        identity["dependencies"] = reusable_rows
    else:
        dependencies = _dependency_paths(deps)
        identity = snapshot_identity(
            source=source,
            lean_toolchain=lean_toolchain,
            mathlib_version=mathlib_version,
            lake_manifest=lake_manifest,
            extractor_abi=extractor_abi,
            dependency_paths=dependencies,
            prior_dependencies=prior_dependencies,
        )
    metadata = snapshot_metadata_path(snapshot)
    write_json(
        metadata,
        {
            "schemaVersion": SNAPSHOT_METADATA_VERSION,
            "kind": "lean-4.32-incremental-snapshot",
            "identity": identity,
            "snapshotSha256": file_sha256(snapshot),
            "depsSha256": deps_sha256,
        },
    )
    return metadata


def validate_snapshot(
    snapshot: Path,
    *,
    source: Path,
    lean_toolchain: str,
    mathlib_version: str,
    lake_manifest: Path,
    extractor_abi: int,
) -> SnapshotValidation:
    metadata_path = snapshot_metadata_path(snapshot)
    deps = snapshot_deps_path(snapshot)
    if not snapshot.is_file() or not deps.is_file() or not metadata_path.is_file():
        return SnapshotValidation(False, "missing")
    try:
        metadata = read_json(metadata_path)
        identity = metadata["identity"]
        dependencies = _dependency_paths(deps)
        current = snapshot_identity(
            source=source,
            lean_toolchain=lean_toolchain,
            mathlib_version=mathlib_version,
            lake_manifest=lake_manifest,
            extractor_abi=extractor_abi,
            dependency_paths=dependencies,
            prior_dependencies=identity.get("dependencies", []),
        )
    except (KeyError, OSError, UnicodeError, ValueError):
        return SnapshotValidation(False, "corrupt")
    if metadata.get("schemaVersion") != SNAPSHOT_METADATA_VERSION:
        return SnapshotValidation(False, "schema-mismatch", metadata)
    if metadata.get("kind") != "lean-4.32-incremental-snapshot":
        return SnapshotValidation(False, "kind-mismatch", metadata)
    # Full source contents may differ: --incr-load intentionally finds the
    # first changed syntax node. Header/import environment may not differ.
    immutable = (
        "leanToolchain",
        "mathlibVersion",
        "lakeManifestSha256",
        "sourcePath",
        "headerSha256",
        "extractorAbi",
        "dependencies",
    )
    if any(identity.get(key) != current.get(key) for key in immutable):
        return SnapshotValidation(False, "environment-mismatch", metadata)
    if file_sha256(snapshot) != metadata.get("snapshotSha256"):
        return SnapshotValidation(False, "snapshot-hash-mismatch", metadata)
    if file_sha256(deps) != metadata.get("depsSha256"):
        return SnapshotValidation(False, "deps-hash-mismatch", metadata)
    status = "snapshot-hit" if identity.get("sourceSha256") == current["sourceSha256"] else "partial-reuse"
    return SnapshotValidation(True, status, metadata)


def run_incremental_lean(
    *,
    toolchain: str,
    workspace: Path,
    source: Path,
    snapshot: Path,
    load_snapshot: bool,
    reuse_status: str = "cold-elaboration",
    extra_args: Iterable[str] = (),
    header_snapshot: Path | None = None,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run official Lean snapshot plumbing and atomically promote its output.

    Metadata validation must happen before passing ``load_snapshot=True``.
    The final validating envelope is intentionally committed by the caller,
    after checking the generated dependency list.
    """

    snapshot.parent.mkdir(parents=True, exist_ok=True)
    temporary = snapshot.with_suffix(snapshot.suffix + ".writing")
    temporary.unlink(missing_ok=True)
    snapshot_deps_path(temporary).unlink(missing_ok=True)
    command = ["elan", "run", toolchain, "lean"]
    if load_snapshot:
        command.extend(("--incr-load", str(snapshot.resolve())))
    command.extend(("--incr-save", str(temporary.resolve())))
    header_temporary = None
    if header_snapshot is not None:
        header_snapshot.parent.mkdir(parents=True, exist_ok=True)
        header_temporary = header_snapshot.with_suffix(header_snapshot.suffix + ".writing")
        header_temporary.unlink(missing_ok=True)
        snapshot_deps_path(header_temporary).unlink(missing_ok=True)
        command.extend(("--incr-header-save", str(header_temporary.resolve())))
    command.extend(extra_args)
    command.append(str(source.resolve()))
    started = time.monotonic()
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(
            command,
            cwd=workspace,
            stdout=stdout_file,
            stderr=stderr_file,
            env=environment,
        )
        next_report = 5.0
        while process.poll() is None:
            time.sleep(0.25)
            elapsed = time.monotonic() - started
            if elapsed >= next_report:
                total = max(0, int(elapsed))
                minutes, seconds = divmod(total, 60)
                hours, minutes = divmod(minutes, 60)
                elapsed_text = (
                    f"{hours:d}:{minutes:02d}:{seconds:02d}"
                    if hours
                    else f"{minutes:02d}:{seconds:02d}"
                )
                activity = {
                    "snapshot-hit": "restoring verified command snapshot",
                    "partial-reuse": (
                        "reusing unchanged command prefix; elaborating suffix"
                    ),
                }.get(reuse_status, "cold command elaboration")
                print(
                    f"Lean 4.32 snapshot: {activity} | elapsed {elapsed_text} "
                    "| ETA awaiting Lean command boundary",
                    flush=True,
                )
                next_report += 5.0
        stdout_file.seek(0)
        stderr_file.seek(0)
        result = subprocess.CompletedProcess(
            command,
            process.returncode,
            stdout_file.read().decode("utf-8", errors="replace"),
            stderr_file.read().decode("utf-8", errors="replace"),
        )
    if result.returncode == 0:
        temporary.replace(snapshot)
        temporary_deps = snapshot_deps_path(temporary)
        if temporary_deps.exists():
            temporary_deps.replace(snapshot_deps_path(snapshot))
        if header_snapshot is not None and header_temporary is not None:
            header_temporary.replace(header_snapshot)
            header_temporary_deps = snapshot_deps_path(header_temporary)
            if header_temporary_deps.exists():
                header_temporary_deps.replace(snapshot_deps_path(header_snapshot))
    return result
