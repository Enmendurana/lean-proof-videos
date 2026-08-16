from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from proof_video.cli import _restore_windows_path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "lean" / "MathlibLatexFixtures.lean"


def test_central_mathlib_latex_dictionary() -> None:
    if shutil.which("lake") is None:
        pytest.skip("Lean integration fixture requires lake")
    _restore_windows_path()
    completed = subprocess.run(
        ["lake", "env", "lean", str(FIXTURE)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, (completed.stdout or "") + (completed.stderr or "")
