import json
from pathlib import Path

import pytest

from proof_video.snapshot_runtime import _validate_kernel_certificate


def _certificate(path: Path, **validation: bool) -> None:
    path.write_text(
        json.dumps(
            {
                "selectedTheorem": "Demo.main",
                "sourceSha256": "source-hash",
                "rows": [
                    {
                        "theoremName": "Demo.main",
                        "validation": {
                            "valid": True,
                            "kernelChecked": True,
                            "noSorry": True,
                            **validation,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_kernel_certificate_accepts_exact_checked_no_sorry_bundle(
    tmp_path: Path,
) -> None:
    path = tmp_path / "certificate.json"
    _certificate(path)
    _validate_kernel_certificate(
        path,
        theorem="Demo.main",
        source_sha="source-hash",
    )


@pytest.mark.parametrize("field", ["valid", "kernelChecked", "noSorry"])
def test_kernel_certificate_rejects_any_failed_validation_flag(
    tmp_path: Path,
    field: str,
) -> None:
    path = tmp_path / "certificate.json"
    _certificate(path, **{field: False})
    with pytest.raises(RuntimeError, match="stale or invalid"):
        _validate_kernel_certificate(
            path,
            theorem="Demo.main",
            source_sha="source-hash",
        )
