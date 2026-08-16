from __future__ import annotations

import pytest

from proof_video.workers import bounded_worker_count, ordered_parallel_map


def test_parallel_map_keeps_dependency_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEAN_PROOF_POSTPROCESS_WORKERS", "4")

    result = ordered_parallel_map(lambda value: value * value, range(20))

    assert result == [value * value for value in range(20)]


def test_worker_count_is_bounded_and_validated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEAN_PROOF_POSTPROCESS_WORKERS", "99")
    assert bounded_worker_count(100, environment="LEAN_PROOF_POSTPROCESS_WORKERS") == 8

    monkeypatch.setenv("LEAN_PROOF_POSTPROCESS_WORKERS", "zero")
    with pytest.raises(ValueError, match="positive integer"):
        bounded_worker_count(2, environment="LEAN_PROOF_POSTPROCESS_WORKERS")
