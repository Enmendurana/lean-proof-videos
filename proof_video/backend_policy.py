"""Shared Lean backend selection and automatic rollback policy.

The renderer has two entry points: the low-level theorem CLI and the convenient
two-path ``render-proof`` command.  Keeping the retry policy here prevents a
modular proof from accidentally mixing artifacts produced by different Lean
toolchains.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Generic, TypeVar

from proof_video.toolchains import ToolchainBackend, resolve_toolchain_backend

T = TypeVar("T")


@dataclass(frozen=True)
class BackendAttempt:
    backend: ToolchainBackend
    trace_backend: str


@dataclass(frozen=True)
class BackendResult(Generic[T]):
    backend: ToolchainBackend
    trace_backend: str
    value: T


def backend_attempts(
    project_root: Path,
    cache_root: Path,
    requested: str,
    requested_trace_backend: str | None,
) -> tuple[BackendAttempt, ...]:
    """Return the ordered, isolated backend attempts for one Lean operation."""

    primary = resolve_toolchain_backend(project_root, cache_root, requested)
    primary_trace = requested_trace_backend or (
        "snapshot" if primary.name == "lean-4.32" else "legacy"
    )
    if primary_trace == "snapshot" and primary.name != "lean-4.32":
        raise ValueError(
            "--trace-backend snapshot requires --toolchain-backend lean-4.32"
        )

    attempts = [BackendAttempt(primary, primary_trace)]
    # An explicitly requested snapshot is intentionally fail-fast: legacy 4.28
    # cannot honor that API contract.  With the default or explicit legacy
    # frontend, auto may safely retry the complete Lean operation on 4.28.
    if requested == "auto" and requested_trace_backend != "snapshot":
        fallback = resolve_toolchain_backend(project_root, cache_root, "lean-4.28")
        attempts.append(BackendAttempt(fallback, "legacy"))
    return tuple(attempts)


def describe_attempt(attempt: BackendAttempt) -> str:
    backend = attempt.backend
    status = (
        "qualified"
        if backend.name == "lean-4.32" and backend.qualified
        else "experimental profile"
        if backend.name == "lean-4.32"
        else "rollback profile"
    )
    return (
        f"Lean backend: {backend.name} | trace backend: "
        f"{attempt.trace_backend} | {status}"
    )


def run_with_backend_fallback(
    project_root: Path,
    cache_root: Path,
    requested: str,
    requested_trace_backend: str | None,
    operation: Callable[[ToolchainBackend, str], T],
    *,
    phase: str,
) -> BackendResult[T]:
    """Run one complete Lean phase, rolling auto back from 4.32 to 4.28.

    Only exceptions raised inside ``operation`` are eligible for fallback.
    Callers therefore keep strict audit, post-processing and rendering outside
    this function so a failure in those phases is never hidden by a retry.
    """

    attempts = backend_attempts(
        project_root,
        cache_root,
        requested,
        requested_trace_backend,
    )
    for index, attempt in enumerate(attempts):
        print(describe_attempt(attempt), flush=True)
        try:
            value = operation(attempt.backend, attempt.trace_backend)
        except KeyboardInterrupt:
            raise
        except (Exception, SystemExit) as error:
            if index + 1 >= len(attempts):
                raise
            detail = str(error).strip() or type(error).__name__
            print(
                f"Lean 4.32 {phase} failed: {detail}\n"
                "Automatically falling back to lean-4.28 with the legacy "
                "trace backend.",
                flush=True,
            )
            continue
        return BackendResult(attempt.backend, attempt.trace_backend, value)
    raise AssertionError("backend attempt list cannot be empty")
