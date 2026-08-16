from __future__ import annotations

import json
from pathlib import Path

from proof_video.lean_profile import (
    format_profile_summary,
    read_command_profile,
    source_location,
)


def test_source_location_uses_utf8_bytes_and_skips_doc_comments() -> None:
    source = "-- α\n/-- razlaga -/\nlemma pomembna : True := by trivial\n".encode()
    start = len("-- α\n".encode())

    location = source_location(source, start)

    assert location.line == 3
    assert location.label == "lemma pomembna : True := by trivial"


def test_profile_summary_orders_slowest_commands(tmp_path: Path) -> None:
    lean_file = tmp_path / "Proof.lean"
    lean_file.write_text(
        "lemma first : True := by trivial\nlemma second : True := by trivial\n",
        encoding="utf-8",
    )
    second = len("lemma first : True := by trivial\n".encode())
    profile = tmp_path / "command-profile.json"
    profile.write_text(
        json.dumps(
            {
                "commands": [
                    {
                        "index": 0,
                        "startByte": 0,
                        "endByte": second,
                        "elapsedMs": 200,
                        "declarations": ["first"],
                    },
                    {
                        "index": 1,
                        "startByte": second,
                        "endByte": lean_file.stat().st_size,
                        "elapsedMs": 2500,
                        "declarations": ["second"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    summary = format_profile_summary(read_command_profile(profile, lean_file))

    assert summary.index("second") < summary.index("first")
    assert "2.50s | line 2" in summary
