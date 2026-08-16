from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

from proof_video.lean_sources import EXTRACTOR_SOURCE_PATHS


CACHE_FORMAT_VERSION = 1
LEAN_EVIDENCE_KEY_VERSION = 2
LEAN_EXTRACTOR_KEY_VERSION = 1
LEAN_SNAPSHOT_EXTRACTOR_KEY_VERSION = 1
_IMPORT = re.compile(r"^\s*import\s+(.+?)\s*$")
_EXTRACTOR_SOURCES = EXTRACTOR_SOURCE_PATHS


def stable_hash(*values: Any) -> str:
    payload = json.dumps(
        [CACHE_FORMAT_VERSION, *values],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _contract_hash(contract: str, *values: Any) -> str:
    """Hash a durable contract without depending on general cache versions."""

    payload = json.dumps(
        [contract, *values],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_digest(paths: Iterable[Path], root: Path | None = None) -> str:
    digest = hashlib.sha256()
    for path in sorted({path.resolve() for path in paths}, key=str):
        if root:
            try:
                label = str(path.relative_to(root.resolve()))
            except ValueError:
                label = str(path)
        else:
            label = str(path)
        digest.update(label.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _without_lean_comments(source: str) -> str:
    """Remove nested Lean comments while preserving strings and line layout."""

    output: list[str] = []
    index = 0
    block_depth = 0
    in_string = False
    in_character = False
    while index < len(source):
        pair = source[index : index + 2]
        character = source[index]
        if block_depth:
            if pair == "/-":
                block_depth += 1
                index += 2
            elif pair == "-/":
                block_depth -= 1
                index += 2
            else:
                if character == "\n":
                    output.append("\n")
                index += 1
            continue
        if in_string or in_character:
            output.append(character)
            if character == "\\" and index + 1 < len(source):
                output.append(source[index + 1])
                index += 2
                continue
            if in_string and character == '"':
                in_string = False
            elif in_character and character == "'":
                in_character = False
            index += 1
            continue
        if pair == "/-":
            block_depth = 1
            index += 2
        elif pair == "--":
            while index < len(source) and source[index] != "\n":
                index += 1
        elif character == '"':
            in_string = True
            output.append(character)
            index += 1
        elif character == "'":
            # Lean identifiers may end in apostrophes. Treat a quote as a
            # character literal only when a closing quote is present nearby.
            closing = source.find("'", index + 1, min(len(source), index + 8))
            in_character = closing >= 0
            output.append(character)
            index += 1
        else:
            output.append(character)
            index += 1
    return "".join(output)


def _canonical_lean_source(source: str) -> bytes:
    """Stable cache form: comments/trailing blanks do not invalidate evidence.

    Leading indentation of every non-empty line is retained because Lean's
    layout parser observes it. Internal whitespace outside literals is
    normalized, so changing only a comment does not force a kernel re-export.
    """

    lines: list[str] = []
    for raw_line in _without_lean_comments(source).splitlines():
        expanded = raw_line.expandtabs(2).rstrip()
        if not expanded.strip():
            continue
        indentation = len(expanded) - len(expanded.lstrip(" "))
        body = expanded[indentation:]
        normalized: list[str] = []
        pending_space = False
        in_string = False
        in_character = False
        index = 0
        while index < len(body):
            character = body[index]
            if in_string or in_character:
                normalized.append(character)
                if character == "\\" and index + 1 < len(body):
                    normalized.append(body[index + 1])
                    index += 2
                    continue
                if in_string and character == '"':
                    in_string = False
                elif in_character and character == "'":
                    in_character = False
                index += 1
                continue
            if character.isspace():
                pending_space = True
                index += 1
                continue
            if pending_space and normalized:
                normalized.append(" ")
            pending_space = False
            if character == '"':
                in_string = True
            elif character == "'":
                closing = body.find("'", index + 1, min(len(body), index + 8))
                in_character = closing >= 0
            normalized.append(character)
            index += 1
        lines.append(" " * indentation + "".join(normalized))
    return ("\n".join(lines) + "\n").encode("utf-8")


def lean_source_digest(paths: Iterable[Path], root: Path | None = None) -> str:
    """Digest Lean syntax conservatively while ignoring comment-only edits."""

    digest = hashlib.sha256()
    for path in sorted({path.resolve() for path in paths}, key=str):
        if root:
            try:
                label = str(path.relative_to(root.resolve()))
            except ValueError:
                label = str(path)
        else:
            label = str(path)
        digest.update(label.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_canonical_lean_source(path.read_text(encoding="utf-8")))
        digest.update(b"\0")
    return digest.hexdigest()


def _local_imports(root: Path, path: Path) -> tuple[Path, ...]:
    result: set[Path] = set()
    pending = [path.resolve()]
    while pending:
        current = pending.pop()
        if current in result or not current.exists():
            continue
        result.add(current)
        for line in current.read_text(encoding="utf-8").splitlines():
            match = _IMPORT.match(line)
            if not match:
                continue
            for module in match.group(1).split():
                candidate = root / (module.replace(".", "/") + ".lean")
                if candidate.exists():
                    pending.append(candidate.resolve())
    return tuple(sorted(result, key=str))


def local_source_closure(root: Path, path: Path) -> tuple[Path, ...]:
    """Return the local import closure used by evidence and workspace sync."""

    return _local_imports(root.resolve(), path.resolve())


def _extractor_sources(root: Path) -> tuple[Path, ...]:
    return tuple(root / name for name in _EXTRACTOR_SOURCES if (root / name).exists())


def _control_files(root: Path) -> list[Path]:
    return [
        root / name
        for name in ("lean-toolchain", "lakefile.lean", "lake-manifest.json")
        if (root / name).exists()
    ]


def lean_trace_key(root: Path, lean_file: Path, theorem: str) -> str:
    """Legacy derived-trace key retained for one-time cache migration."""
    local_sources = [*_local_imports(root, lean_file), *_extractor_sources(root)]
    control_files = [
        *_control_files(root),
    ]
    if lean_file.resolve() not in {path.resolve() for path in local_sources}:
        local_sources.append(lean_file)
    return stable_hash(
        "lean-trace",
        theorem,
        file_digest([*local_sources, *control_files], root),
    )


def lean_evidence_identity(
    root: Path,
    lean_file: Path,
    theorem: str,
    trace_mode: str,
    backend_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Identity of kernel evidence, independent of presentation architecture.

    Local Lean source, the selected declaration, trace granularity, and the
    pinned Lean/Mathlib environment determine proof evidence. Python, Remotion,
    LaTeX cleanup, animation planning, and extractor implementation files do
    not. Old evidence therefore remains reusable after those layers change.
    """

    local_sources = list(_local_imports(root, lean_file))
    if lean_file.resolve() not in {path.resolve() for path in local_sources}:
        local_sources.append(lean_file.resolve())
    control_files = _control_files(root)
    source_digest = lean_source_digest(local_sources, root)
    toolchain_digest = file_digest(control_files, root)
    contract_values: list[Any] = [
        theorem,
        trace_mode,
        source_digest,
        toolchain_digest,
    ]
    if backend_identity is not None:
        contract_values.append(backend_identity)
    key = _contract_hash(
        f"lean-evidence-v{LEAN_EVIDENCE_KEY_VERSION}",
        *contract_values,
    )
    return {
        "schemaVersion": LEAN_EVIDENCE_KEY_VERSION,
        "key": key,
        "theorem": theorem,
        "traceMode": trace_mode,
        "sourceDigest": source_digest,
        "toolchainDigest": toolchain_digest,
        **({"toolchainBackend": backend_identity} if backend_identity is not None else {}),
    }


def legacy_lean_evidence_identity(
    root: Path,
    lean_file: Path,
    theorem: str,
    trace_mode: str,
    backend_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Locate schema-v1 evidence so upgrades never discard checked traces."""

    local_sources = list(_local_imports(root, lean_file))
    if lean_file.resolve() not in {path.resolve() for path in local_sources}:
        local_sources.append(lean_file.resolve())
    source_digest = file_digest(local_sources, root)
    toolchain_digest = file_digest(_control_files(root), root)
    values: list[Any] = [theorem, trace_mode, source_digest, toolchain_digest]
    if backend_identity is not None:
        values.append(backend_identity)
    return {
        "schemaVersion": 1,
        "key": _contract_hash("lean-evidence-v1", *values),
        "theorem": theorem,
        "traceMode": trace_mode,
        "sourceDigest": source_digest,
        "toolchainDigest": toolchain_digest,
        **({"toolchainBackend": backend_identity} if backend_identity is not None else {}),
    }


def lean_extractor_identity(root: Path) -> dict[str, Any]:
    """Identity of the native extractor, independent of input proof files."""

    source_digest = file_digest(_extractor_sources(root), root)
    toolchain_digest = file_digest(_control_files(root), root)
    key = _contract_hash(
        f"lean-extractor-v{LEAN_EXTRACTOR_KEY_VERSION}",
        source_digest,
        toolchain_digest,
    )
    return {
        "schemaVersion": LEAN_EXTRACTOR_KEY_VERSION,
        "key": key,
        "sourceDigest": source_digest,
        "toolchainDigest": toolchain_digest,
    }


def lean_snapshot_extractor_identity(root: Path) -> dict[str, Any]:
    """Identity of the Lean 4.32-only serialized-snapshot reader."""

    base = lean_extractor_identity(root)
    snapshot_sources = [
        path
        for path in (
            root / "SnapshotAnimate432.lean",
            root / "SnapshotCertificate432.lean",
        )
        if path.is_file()
    ]
    source_digest = (
        file_digest(snapshot_sources, root) if snapshot_sources else "missing"
    )
    key = _contract_hash(
        f"lean-snapshot-extractor-v{LEAN_SNAPSHOT_EXTRACTOR_KEY_VERSION}",
        base,
        source_digest,
    )
    return {
        "schemaVersion": LEAN_SNAPSHOT_EXTRACTOR_KEY_VERSION,
        "key": key,
        "legacyExtractor": base,
        "snapshotSourceDigest": source_digest,
    }


def lean_checkpoint_key(
    root: Path,
    lean_file: Path,
    theorem: str,
    backend_identity: dict[str, Any] | None = None,
) -> str:
    """Stable namespace for proof-fingerprint-validated partial chapters.

    The input contents are intentionally absent: unchanged theorem objects can
    survive edits elsewhere in the file. Toolchain and extractor changes still
    select a fresh namespace, while Lean validates every reused proof fingerprint.
    """

    extractor_sources = [*_extractor_sources(root), *_control_files(root)]
    if backend_identity is not None and backend_identity.get("name") == "lean-4.32":
        extractor_sources.extend(
            path
            for path in (
                root / "SnapshotAnimate432.lean",
                root / "SnapshotCertificate432.lean",
            )
            if path.is_file()
        )
    values: list[Any] = [
        "lean-checkpoints-v2",
        str(lean_file.resolve()),
        theorem,
        file_digest(extractor_sources, root),
    ]
    if backend_identity is not None:
        values.append(backend_identity)
    return stable_hash(*values)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
