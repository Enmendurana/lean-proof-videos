"""Small Windows-safe smoke test for the parallel segmented renderer."""

from __future__ import annotations

import json
from pathlib import Path

from proof_video.models import Movie
from proof_video.render import render_segmented


def main() -> None:
    raw = json.loads(
        Path("output/imo-2011-p3-prooftrace-v2.json").read_text(encoding="utf-8")
    )
    source = Movie.from_json(raw)
    movie = Movie(theorem_name="parallel_smoke", frames=source.frames[:9])
    stats = render_segmented(
        movie,
        Path("output/qa-semantic-preservation/parallel-smoke.mp4"),
        width=426,
        height=240,
        fps=15,
        chars_per_second=80,
        max_duration=60,
        audio=None,
        cache_root=Path(".cache/chunk-smoke"),
        renderer="cairo",
        use_cache=True,
    )
    print(stats)


if __name__ == "__main__":
    main()
