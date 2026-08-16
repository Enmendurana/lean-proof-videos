"""Integrity envelopes for generated Lean module snapshots.

An ``.olean`` is a performance artifact, not the durable proof trace itself.
It may be reused only when both its bytes and the Lean evidence identity of the
source unit still match.  Keeping this contract separate from rendering makes
module reuse safe across Python, Remotion and presentation refactors.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from proof_video.artifact_integrity import (
    artifact_is_current,
    artifact_metadata_path,
    record_artifact,
)


MODULE_ARTIFACT_KIND = "lean-module-snapshot"


def module_artifact_metadata_path(module_path: Path) -> Path:
    return artifact_metadata_path(module_path)


def module_artifact_is_current(
    module_path: Path,
    expected_identity: dict[str, Any],
) -> bool:
    """Return whether an intact module belongs to this exact Lean unit."""

    return artifact_is_current(
        module_path,
        kind=MODULE_ARTIFACT_KIND,
        expected_identity=expected_identity,
    )


def record_module_artifact(
    module_path: Path,
    identity: dict[str, Any],
) -> Path:
    """Commit module metadata last, after Lean has written the complete file."""

    return record_artifact(
        module_path,
        kind=MODULE_ARTIFACT_KIND,
        identity=identity,
    )
