from pathlib import Path

from proof_video.module_artifact import (
    module_artifact_is_current,
    module_artifact_metadata_path,
    record_module_artifact,
)


def test_module_artifact_requires_matching_identity_and_bytes(tmp_path: Path) -> None:
    module = tmp_path / "Part01.olean"
    module.write_bytes(b"first module")
    identity = {"key": "unit-a", "sourceDigest": "source-a"}

    assert not module_artifact_is_current(module, identity)
    metadata = record_module_artifact(module, identity)

    assert metadata == module_artifact_metadata_path(module)
    assert module_artifact_is_current(module, identity)
    assert not module_artifact_is_current(module, {**identity, "key": "unit-b"})

    module.write_bytes(b"tampered module")
    assert not module_artifact_is_current(module, identity)


def test_module_artifact_rejects_corrupt_metadata(tmp_path: Path) -> None:
    module = tmp_path / "Part01.olean"
    module.write_bytes(b"module")
    module_artifact_metadata_path(module).write_text("not json", encoding="utf-8")

    assert not module_artifact_is_current(module, {"key": "unit"})
