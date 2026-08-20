"""Build once and run a content-addressed native Lean extractor."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
import shutil
import subprocess
from typing import Mapping

from proof_video.artifact_integrity import artifact_is_current, record_artifact
from proof_video.cache import lean_extractor_identity, lean_snapshot_extractor_identity


EXTRACTOR_ARTIFACT_KIND = "lean-extractor-binary"
SNAPSHOT_READER_ARTIFACT_KIND = "lean-4.32-snapshot-reader-module"
SNAPSHOT_READER_BUILD_TIMEOUT_SECONDS = 600


def extractor_executable_path(root: Path) -> Path:
    identity = lean_extractor_identity(root.resolve())
    suffix = ".exe" if os.name == "nt" else ""
    return (
        root.resolve()
        / ".lean-proof-video-cache"
        / "extractors"
        / str(identity["key"])
        / f"Animate{suffix}"
    )


def snapshot_reader_module_path(root: Path) -> Path:
    return (
        root.resolve() / ".lake" / "build" / "lib" / "lean" / "SnapshotAnimate432.olean"
    )


def _lake_executable_path(root: Path, target: str = "Animate") -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    return root / ".lake" / "build" / "bin" / f"{target}{suffix}"


def _canonical_output_is_locked(details: str) -> bool:
    lowered = details.lower()
    return "animate.exe" in lowered and any(
        marker in lowered
        for marker in (
            "permission denied",
            "access is denied",
            "being used by another process",
        )
    )


def _link_versioned_executable(
    root: Path, target: Path, lake_target: str = "Animate"
) -> None:
    """Link current Lake objects elsewhere when the canonical EXE is locked."""

    response_file = root / ".lake" / "build" / "bin" / f"{lake_target}.exe.rsp"
    if not response_file.is_file():
        raise SystemExit(
            "Lake did not leave the extractor linker response file after the "
            "locked-output failure."
        )
    prefix = _lean_prefix(str(root))
    clang = prefix / "bin" / ("clang.exe" if os.name == "nt" else "clang")
    if not clang.is_file():
        raise SystemExit(f"Lean's bundled clang was not found: {clang}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    result = subprocess.run(
        [str(clang), "-o", str(target), f"@{response_file}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        details = "\n".join(
            value.strip() for value in (result.stdout, result.stderr) if value.strip()
        )
        raise SystemExit(f"Versioned Lean extractor link failed:\n{details}")


def ensure_extractor_executable(root: Path) -> Path:
    """Return a verified current executable, invoking Lake only when stale."""

    root = root.resolve()
    executable = extractor_executable_path(root)
    identity = lean_extractor_identity(root)
    if artifact_is_current(
        executable,
        kind=EXTRACTOR_ARTIFACT_KIND,
        expected_identity=identity,
    ):
        return executable

    print("Lean extractor: building changed Lean sources once...", flush=True)
    try:
        result = subprocess.run(
            ["lake", "build", "Animate"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as error:
        raise SystemExit(
            "Lean's `lake` command was not found. Install elan/Lean 4 first."
        ) from error
    details = "\n".join(
        value.strip() for value in (result.stdout, result.stderr) if value.strip()
    )
    canonical = _lake_executable_path(root)
    if result.returncode == 0:
        if not canonical.is_file():
            raise SystemExit(f"Lake did not produce the extractor: {canonical}")
        executable.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(canonical, executable)
    elif os.name == "nt" and _canonical_output_is_locked(details):
        print(
            "Lean extractor: canonical EXE is in use; linking a versioned copy...",
            flush=True,
        )
        _link_versioned_executable(root, executable)
    else:
        raise SystemExit(f"Lean extractor build failed:\n{details}")
    if not executable.is_file():
        raise SystemExit(f"Lean did not produce the versioned extractor: {executable}")
    record_artifact(
        executable,
        kind=EXTRACTOR_ARTIFACT_KIND,
        identity=identity,
    )
    print("Lean extractor: native executable is current.", flush=True)
    return executable


def ensure_snapshot_reader_modules(root: Path) -> Path:
    """Build the modules consumed by the official Lean snapshot process.

    The reader intentionally is not a standalone executable.  Serialized
    compacted regions retain their shared-library owners, so loading them in
    the exact `lean.exe` process used by `--incr-save` is both faster to build
    and more robust than linking a second native runtime on Windows.
    """

    root = root.resolve()
    reader = snapshot_reader_module_path(root)
    proof_latex = root / ".lake" / "build" / "lib" / "lean" / "ProofLatex.olean"
    identity = lean_snapshot_extractor_identity(root)
    if proof_latex.is_file() and artifact_is_current(
        reader,
        kind=SNAPSHOT_READER_ARTIFACT_KIND,
        expected_identity=identity,
    ):
        return reader
    print("Lean 4.32 snapshot reader: building changed modules once...", flush=True)
    try:
        result = subprocess.run(
            # The official incremental frontend imports ProofLatex in the mirrored
            # entry source.  Build that environment module together with the reader
            # so the subsequent plain `lean --incr-*` invocation can resolve it.
            ["lake", "build", "ProofLatex", "SnapshotCertificate", "SnapshotReader"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=SNAPSHOT_READER_BUILD_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise SystemExit(
            "Lean 4.32 snapshot reader cold build exceeded "
            f"{SNAPSHOT_READER_BUILD_TIMEOUT_SECONDS // 60} minutes. "
            "The auto backend may now retry the complete operation on Lean 4.28."
        ) from error
    details = "\n".join(
        value.strip() for value in (result.stdout, result.stderr) if value.strip()
    )
    if result.returncode:
        raise SystemExit(f"Lean snapshot reader build failed:\n{details}")
    if not reader.is_file():
        raise SystemExit(f"Lake did not produce the snapshot reader module: {reader}")
    record_artifact(
        reader,
        kind=SNAPSHOT_READER_ARTIFACT_KIND,
        identity=identity,
    )
    return reader


@lru_cache(maxsize=None)
def _lean_prefix(root_text: str) -> Path:
    root = Path(root_text)
    try:
        prefix_result = subprocess.run(
            ["lean", "--print-prefix"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as error:
        raise SystemExit(
            "Lean's `lean` command was not found. Install elan/Lean 4 first."
        ) from error
    if prefix_result.returncode:
        raise SystemExit(
            "Could not determine Lean's toolchain prefix:\n"
            f"{prefix_result.stderr.strip()}"
        )
    return Path(prefix_result.stdout.strip())


@lru_cache(maxsize=None)
def _lean_runtime_path(root_text: str, inherited: str) -> str:
    root = Path(root_text)
    search_paths: list[Path] = []
    local_library = root / ".lake" / "build" / "lib" / "lean"
    if local_library.is_dir():
        search_paths.append(local_library)
    packages = root / ".lake" / "packages"
    if packages.is_dir():
        for package in sorted(packages.iterdir(), key=lambda path: path.name.lower()):
            library = package / ".lake" / "build" / "lib" / "lean"
            if library.is_dir():
                search_paths.append(library)

    toolchain_library = _lean_prefix(str(root)) / "lib" / "lean"
    if toolchain_library.is_dir():
        search_paths.append(toolchain_library)

    if inherited:
        search_paths.extend(
            Path(value) for value in inherited.split(os.pathsep) if value
        )
    return os.pathsep.join(str(path.resolve()) for path in dict.fromkeys(search_paths))


def lean_runtime_environment(root: Path) -> Mapping[str, str]:
    """Recreate Lake's module search path for direct extractor launches."""

    environment = os.environ.copy()
    toolchain_bin = _lean_prefix(str(root.resolve())) / "bin"
    # Compacted snapshot closure owners are keyed by their canonical absolute
    # DLL path.  Windows path casing therefore has to match the official
    # `lean.exe` writer (`C:\\...`, not elans's lower-case `c:\\...`).
    environment["LEAN_SYSROOT"] = str(toolchain_bin.parent.resolve())
    environment["PATH"] = os.pathsep.join(
        (str(toolchain_bin), environment.get("PATH", ""))
    )
    environment["LEAN_PATH"] = _lean_runtime_path(
        str(root.resolve()),
        environment.get("LEAN_PATH", ""),
    )
    return environment
