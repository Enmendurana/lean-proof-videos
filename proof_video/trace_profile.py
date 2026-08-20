"""Choose the canonical trace profile independently from renderer settings.

Native ABI 5 source actions expose complete ordered goal frontiers and are the
owned semantic boundary of the project. The proof-term extractor remains an
explicit fine-grained compatibility/debug option, not a competing default.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


_RESUMABLE_MARKER = re.compile(
    r"^\s*--\s*proof-video\s*:\s*resumable\s*$",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True)
class TraceProfile:
    trace_mode: str
    toolchain_backend: str
    trace_backend: str | None
    resumable: bool
    description: str


def source_is_resumable(lean_file: Path) -> bool:
    return bool(_RESUMABLE_MARKER.search(lean_file.read_text(encoding="utf-8")))


def resolve_trace_profile(
    lean_file: Path,
    *,
    requested_mode: str = "auto",
    requested_toolchain: str = "auto",
    requested_trace_backend: str | None = None,
    resume: bool = False,
) -> TraceProfile:
    """Resolve a user-facing trace choice to a compatible implementation."""

    if requested_mode not in {"auto", "hybrid", "proof-term", "tactic"}:
        raise ValueError(f"unsupported trace mode: {requested_mode}")

    marked_resumable = source_is_resumable(lean_file)
    mode = requested_mode
    if mode == "auto":
        mode = "hybrid"

    if mode == "proof-term":
        return TraceProfile(
            trace_mode="proof-term",
            toolchain_backend=requested_toolchain,
            trace_backend=requested_trace_backend,
            resumable=resume,
            description=(
                "Strict fine-grained proof-term profile enabled "
                "(Lean 4.32 snapshot with automatic 4.28 fallback)."
                if requested_toolchain == "auto"
                else f"Strict fine-grained proof-term profile enabled "
                f"({requested_toolchain})."
            ),
        )

    if mode == "hybrid":
        return TraceProfile(
            trace_mode="hybrid",
            toolchain_backend=requested_toolchain,
            trace_backend=requested_trace_backend,
            resumable=resume or marked_resumable,
            description=(
                "Canonical ABI 5 resumable snapshot profile enabled."
                if marked_resumable
                else "Canonical ABI 5 source-action profile enabled."
            ),
        )

    if requested_trace_backend == "snapshot":
        raise ValueError("the snapshot trace backend requires hybrid trace mode")
    if requested_toolchain == "lean-4.32":
        raise ValueError("the tactic trace currently requires Lean 4.28")
    return TraceProfile(
        trace_mode="tactic",
        toolchain_backend="lean-4.28",
        trace_backend="legacy",
        resumable=resume,
        description="Legacy source-tactic profile enabled (Lean 4.28).",
    )
