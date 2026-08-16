from pathlib import Path

from proof_video.commands.cache import prune, status


def test_status_reports_unbounded_categories(tmp_path: Path, capsys) -> None:
    (tmp_path / "lean-evidence").mkdir()
    (tmp_path / "lean-evidence" / "trace.json").write_bytes(b"trace")
    rows = status(tmp_path)
    assert rows[0].name == "lean-evidence"
    assert rows[0].files == 1
    output = capsys.readouterr().out
    assert "Automatic size eviction: disabled" in output
    assert "Lean 4.32 backend:" in output


def test_default_prune_removes_only_temporary_files(tmp_path: Path) -> None:
    evidence = tmp_path / "lean-evidence" / "trace.json"
    evidence.parent.mkdir()
    evidence.write_bytes(b"proof")
    temporary = tmp_path / "snapshots" / "full.incr.writing"
    temporary.parent.mkdir()
    temporary.write_bytes(b"partial")

    removed, _size = prune(tmp_path, "temporary")

    assert removed == 1
    assert not temporary.exists()
    assert evidence.exists()
