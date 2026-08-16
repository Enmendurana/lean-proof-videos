"""Export a short prefix of the real strict IMO timeline for renderer QA."""

from __future__ import annotations

import json
from pathlib import Path

from proof_video.cache import write_json
from proof_video.models import Movie
from proof_video.remotion_export import build_remotion_timeline


def main() -> None:
    raw = json.loads(
        Path("output/imo-2011-p3-prooftrace-v2.json").read_text(encoding="utf-8")
    )
    source = Movie.from_json(raw)
    smoke = Movie(theorem_name=source.theorem_name, frames=source.frames[:24])
    write_json(
        Path("output/remotion-smoke.json"),
        build_remotion_timeline(smoke, width=1280, height=720, fps=30),
    )


if __name__ == "__main__":
    main()
