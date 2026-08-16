"""Bounded, deterministic parallel work used after Lean elaboration.

Proof checking stays inside Lean.  These helpers are only for independent
serialization, hashing and validation jobs.  Results always retain input order
so parallel execution cannot alter chapter dependencies or the video timeline.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from typing import Callable, Iterable, TypeVar


_T = TypeVar("_T")
_R = TypeVar("_R")


def bounded_worker_count(total: int, *, environment: str, cap: int = 8) -> int:
    if total <= 1:
        return 1
    configured = os.environ.get(environment)
    if configured is not None:
        try:
            requested = int(configured)
        except ValueError as error:
            raise ValueError(f"{environment} must be a positive integer") from error
        if requested <= 0:
            raise ValueError(f"{environment} must be a positive integer")
    else:
        requested = os.cpu_count() or 1
    return max(1, min(total, requested, cap))


def ordered_parallel_map(
    function: Callable[[_T], _R],
    values: Iterable[_T],
    *,
    environment: str = "LEAN_PROOF_POSTPROCESS_WORKERS",
    cap: int = 8,
) -> list[_R]:
    items = list(values)
    workers = bounded_worker_count(len(items), environment=environment, cap=cap)
    if workers == 1:
        return [function(item) for item in items]
    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="proof-chapter",
    ) as executor:
        return list(executor.map(function, items))
