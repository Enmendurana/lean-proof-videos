from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from proof_video.cli import _restore_windows_path
from proof_video.lean_export import export_trace


ROOT = Path(__file__).resolve().parents[1]
FOUNDATION = ROOT / "examples" / "modular" / "Foundation.lean"
MAIN = ROOT / "examples" / "modular" / "Main.lean"


def test_trace_and_olean_share_one_elaboration_then_import_cleanly(
    tmp_path: Path,
) -> None:
    if shutil.which("lake") is None:
        pytest.skip("Lean integration fixture requires lake")
    _restore_windows_path()
    foundation_olean = (
        ROOT
        / ".lake"
        / "build"
        / "lib"
        / "lean"
        / "examples"
        / "modular"
        / "Foundation.olean"
    )
    foundation = export_trace(
        ROOT,
        FOUNDATION,
        "ModularExample.foundation",
        "hybrid",
        checkpoint_dir=tmp_path / "foundation-checkpoints",
        module_output=foundation_olean,
        postprocess_workers=2,
    )
    main = export_trace(
        ROOT,
        MAIN,
        "ModularExample.main",
        "hybrid",
        checkpoint_dir=tmp_path / "main-checkpoints",
        postprocess_workers=2,
    )

    assert foundation["validation"]["valid"] is True
    assert main["validation"]["valid"] is True
    assert foundation_olean.stat().st_size > 0
    assert main["chapters"][-1]["theoremName"] == "ModularExample.main"
    command_profile = json.loads(
        (tmp_path / "foundation-checkpoints" / "command-profile.json").read_text(
            encoding="utf-8"
        )
    )
    assert command_profile["complete"] is True
    assert command_profile["commands"]
