from pathlib import Path

import pytest

from proof_video.commands import render_proof


def test_discovers_last_theorem_with_namespace_and_ignores_comments(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Proof.lean"
    source.write_text(
        """
/- theorem Fake.nope : True := by trivial -/
namespace Demo
lemma helper : True := by trivial
section
theorem inner : True := by trivial
end
theorem main_result : True := by trivial
end Demo
""",
        encoding="utf-8",
    )

    assert render_proof.discover_theorem(source) == "Demo.main_result"


def test_marker_overrides_the_default_declaration(tmp_path: Path) -> None:
    source = tmp_path / "Proof.lean"
    source.write_text(
        """
-- proof-video: theorem Demo.first
namespace Demo
theorem first : True := by trivial
theorem second : True := by trivial
end Demo
""",
        encoding="utf-8",
    )

    assert render_proof.discover_theorem(source) == "Demo.first"


def test_default_discovery_ignores_inaccessible_private_helpers(tmp_path: Path) -> None:
    source = tmp_path / "Proof.lean"
    source.write_text(
        """
namespace Demo
theorem public_result : True := by trivial
private theorem final_helper : True := by trivial
end Demo
""",
        encoding="utf-8",
    )

    assert render_proof.discover_theorem(source) == "Demo.public_result"


def test_two_path_command_passes_detected_theorem_to_general_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "Proof.lean"
    output = tmp_path / "movie.mp4"
    source.write_text("theorem demo : True := by trivial\n", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        render_proof,
        "render_main",
        lambda argv: calls.append(argv) or 0,
    )

    assert render_proof.main([str(source), str(output)]) == 0

    command = calls[0]
    assert command[:2] == [str(source.resolve()), "demo"]
    assert command[command.index("--output") + 1] == str(output.resolve())
    assert command[command.index("--toolchain-backend") + 1] == "auto"
    assert "--trace-backend" not in command
    assert command[command.index("--trace-mode") + 1] == "proof-term"
    assert "--resume" not in command
    assert "--preview" not in command


def test_resumable_marker_enables_long_proof_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "LongProof.lean"
    output = tmp_path / "long-proof.mp4"
    source.write_text(
        "-- proof-video: resumable\ntheorem long_proof : True := by trivial\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(render_proof, "render_main", lambda argv: calls.append(argv) or 0)

    assert render_proof.main([str(source), str(output)]) == 0
    assert "--resume" in calls[0]
    assert calls[0][calls[0].index("--trace-mode") + 1] == "hybrid"


def test_explicit_scalable_granularity_keeps_incremental_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "Proof.lean"
    output = tmp_path / "movie.mp4"
    source.write_text("theorem demo : True := by trivial\n", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(render_proof, "render_main", lambda argv: calls.append(argv) or 0)

    assert (
        render_proof.main(
            [str(source), str(output), "--trace-granularity", "scalable"]
        )
        == 0
    )
    command = calls[0]
    assert command[command.index("--toolchain-backend") + 1] == "auto"
    assert command[command.index("--trace-mode") + 1] == "hybrid"
