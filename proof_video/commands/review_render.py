from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from proof_video.render_review import review_render


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="review-proof-render",
        description=(
            "Extract exact before/mid/after frames for every proof transition "
            "and create review manifests and contact sheets."
        ),
    )
    parser.add_argument("video", type=Path, help="rendered MP4")
    parser.add_argument("timeline", type=Path, help="renderer timeline JSON")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="review directory (default: <video-stem>-review)",
    )
    parser.add_argument("--transitions-per-sheet", type=int, default=6)
    parser.add_argument("--thumbnail-width", type=int, default=480)
    parser.add_argument("--ffmpeg", help="explicit ffmpeg executable")
    parser.add_argument("--ffprobe", help="explicit ffprobe executable")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = args.output_dir or args.video.with_name(f"{args.video.stem}-review")
    manifest = review_render(
        args.video,
        args.timeline,
        output_dir,
        ffmpeg=args.ffmpeg,
        ffprobe=args.ffprobe,
        transitions_per_sheet=args.transitions_per_sheet,
        thumbnail_width=args.thumbnail_width,
    )
    resolved = output_dir.resolve()
    print(
        f"Reviewed {manifest['transitionCount']} transitions; "
        f"{manifest['selectedFrameCount']} unique frames."
    )
    print(resolved / "manifest.json")
    print(resolved / "manifest.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
