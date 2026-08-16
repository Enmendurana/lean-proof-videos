"""Small renderer result types shared by orchestration and worker backends."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RenderStats:
    rendered_segments: int
    cached_segments: int
    renderer: str
    chars_per_second: float
