from types import SimpleNamespace

import pytest

import proof_video.cli as cli
import proof_video.evidence as evidence
import proof_video.lean_export as lean_export
import proof_video.render as render
from proof_video.cli import QUALITY, build_parser
from proof_video.commands.render_proof import build_parser as build_render_proof_parser
from proof_video.evidence import EvidenceResult


def test_default_is_standard_youtube_landscape() -> None:
    args = build_parser().parse_args(["Proof.lean", "demo"])
    assert args.quality == "high"
    assert QUALITY[args.quality] == (1920, 1080, 30)
    assert args.renderer == "auto"
    assert args.engine == "remotion"
    assert args.chars_per_second == 48.0
    assert args.render_mode == "full"
    assert args.cache is False
    assert args.resume is False
    assert args.remotion_concurrency == "auto"
    assert args.remotion_chunk_workers == 1
    assert args.render_hardware == "auto"
    assert args.render_chunking == "auto"
    assert args.checkpoint_seconds is None
    assert args.max_duration is None
    assert args.trace_mode == "hybrid"
    assert args.rebuild_trace is False
    assert args.rebuild_chapter is None
    assert args.toolchain_backend == "auto"
    assert args.trace_backend is None
    assert args.force_lean_export is False
    assert not hasattr(args, "step_seconds")


def test_path_fragments_are_deduplicated_without_reordering() -> None:
    separator = cli.os.pathsep
    assert cli._deduplicated_path(
        separator.join(("first", "second", "first")),
        separator.join(("second", "third")),
    ) == separator.join(("first", "second", "third"))


@pytest.mark.skipif(cli.os.name != "nt", reason="Windows PATH restoration")
def test_restore_windows_path_is_idempotent(monkeypatch) -> None:
    python_dir = str(cli.Path(cli.sys.executable).parent)
    monkeypatch.setenv(
        "PATH",
        cli.os.pathsep.join((python_dir, python_dir.upper(), r"C:\Tools")),
    )

    cli._restore_windows_path()
    restored = cli.os.environ["PATH"]
    cli._restore_windows_path()

    assert cli.os.environ["PATH"] == restored


def test_60_fps_is_an_explicit_profile() -> None:
    args = build_parser().parse_args(["Proof.lean", "demo", "--quality", "high60"])
    assert QUALITY[args.quality] == (1920, 1080, 60)


def test_cache_is_explicitly_opt_in() -> None:
    cached = build_parser().parse_args(["Proof.lean", "demo", "--cache"])
    uncached = build_parser().parse_args(["Proof.lean", "demo", "--no-cache"])
    assert cached.cache is True
    assert uncached.cache is False


def test_resume_profile_has_configurable_checkpoints() -> None:
    args = build_parser().parse_args(
        ["Proof.lean", "demo", "--resume", "--checkpoint-seconds", "12.5"]
    )
    assert args.resume is True
    assert args.checkpoint_seconds == 12.5


def test_advanced_renderer_controls_are_parsed() -> None:
    args = build_parser().parse_args(
        [
            "Proof.lean",
            "demo",
            "--render-hardware",
            "gpu-required",
            "--render-concurrency",
            "6",
            "--render-chunking",
            "8.5",
            "--recalibrate-renderer",
            "--render-profile-report",
            "profile.json",
        ]
    )

    assert args.render_hardware == "gpu-required"
    assert args.remotion_concurrency == "6"
    assert args.render_chunking == "8.5"
    assert args.recalibrate_renderer is True
    assert args.render_profile_report.name == "profile.json"


def test_rebuild_trace_is_an_explicit_escape_hatch() -> None:
    args = build_parser().parse_args(["Proof.lean", "demo", "--rebuild-trace"])
    assert args.rebuild_trace is True
    wrapper_args = build_render_proof_parser().parse_args(
        ["Proof.lean", "proof.mp4", "--rebuild-trace"]
    )
    assert wrapper_args.rebuild_trace is True


def test_incremental_backend_and_targeted_chapter_are_explicit() -> None:
    args = build_parser().parse_args(
        [
            "Proof.lean",
            "demo",
            "--toolchain-backend",
            "lean-4.32",
            "--trace-backend",
            "snapshot",
            "--rebuild-chapter",
            "Demo.helper",
        ]
    )
    assert args.toolchain_backend == "lean-4.32"
    assert args.trace_backend == "snapshot"
    assert args.rebuild_chapter == "Demo.helper"


def test_transition_map_path_is_parsed() -> None:
    args = build_parser().parse_args(
        ["Proof.lean", "demo", "--dump-transition-map", "transitions.json"]
    )
    assert args.dump_transition_map.name == "transitions.json"


def _raw_trace() -> dict:
    return {
        "theoremName": "demo",
        "startGoal": {
            "goalId": "g0",
            "state": "A",
            "latexTarget": "A",
        },
        "actions": [],
    }


def _stats():
    return SimpleNamespace(
        cached_segments=0,
        rendered_segments=1,
        renderer="cairo",
        chars_per_second=24.0,
    )


def _stub_trace_io(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_restore_windows_path", lambda: None)
    monkeypatch.setattr(cli, "read_json", lambda _path: _raw_trace())
    monkeypatch.setattr(cli, "write_json", lambda _path, _raw: None)


def _stub_evidence_identity(monkeypatch, tmp_path) -> None:
    identity = {
        "schemaVersion": 1,
        "key": "stable-evidence",
        "theorem": "demo",
        "traceMode": "hybrid",
        "sourceDigest": "source",
        "toolchainDigest": "toolchain",
    }
    monkeypatch.setattr(evidence, "lean_evidence_identity", lambda *_args: identity)
    monkeypatch.setattr(
        evidence,
        "evidence_trace_path",
        lambda _root, _key: tmp_path / "evidence" / "stable-evidence.json",
    )


def test_full_render_is_routed_without_preview_argument(monkeypatch, tmp_path) -> None:
    _stub_trace_io(monkeypatch)
    calls = []
    monkeypatch.setattr(
        render,
        "render_full",
        lambda *args, **kwargs: calls.append((args, kwargs)) or _stats(),
        raising=False,
    )
    monkeypatch.setattr(
        render,
        "render_segmented",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("segmented render called")
        ),
    )

    result = cli.main(
        [
            "Proof.lean",
            "demo",
            "--trace",
            str(tmp_path / "trace.json"),
            "--output",
            str(tmp_path / "proof.mp4"),
            "--engine",
            "manim",
        ]
    )

    assert result == 0
    assert len(calls) == 1
    assert "preview" not in calls[0][1]
    assert calls[0][1]["use_cache"] is False


def test_persistent_lean_evidence_is_reused_by_default(
    monkeypatch, tmp_path, capsys
) -> None:
    proof = tmp_path / "Proof.lean"
    proof.write_text("theorem demo : True := by trivial", encoding="utf-8")
    _stub_evidence_identity(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_restore_windows_path", lambda: None)
    monkeypatch.setattr(
        evidence, "read_trace_evidence", lambda _path, _identity: _raw_trace()
    )
    monkeypatch.setattr(
        evidence,
        "export_trace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Lean export should not run")
        ),
    )
    monkeypatch.setattr(cli, "write_json", lambda _path, _value: None)

    result = cli.main(
        [
            str(proof),
            "demo",
            "--json-only",
            "--toolchain-backend",
            "lean-4.28",
            "--output",
            str(tmp_path / "proof.mp4"),
        ]
    )

    assert result == 0
    assert "Persistent Lean evidence hit" in capsys.readouterr().out


def test_explicit_old_proof_term_sidecar_is_rejected(monkeypatch, tmp_path) -> None:
    proof = tmp_path / "Proof.lean"
    proof.write_text("theorem demo : True := by trivial", encoding="utf-8")
    sidecar = tmp_path / "old.json"
    sidecar.write_text(
        '{"schemaVersion":"2.1","theoremName":"demo",'
        '"startGoal":{"goalId":"g0","state":"A","latexTarget":"A"},'
        '"actions":[]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "_restore_windows_path", lambda: None)

    with pytest.raises(SystemExit, match="predates schema 2.2"):
        cli.main(
            [
                str(proof),
                "demo",
                "--trace",
                str(sidecar),
                "--trace-mode",
                "proof-term",
                "--json-only",
                "--toolchain-backend",
                "lean-4.28",
                "--output",
                str(tmp_path / "proof.mp4"),
            ]
        )


def test_rebuild_trace_bypasses_evidence_and_commits_new_artifact(
    monkeypatch, tmp_path
) -> None:
    proof = tmp_path / "Proof.lean"
    proof.write_text("theorem demo : True := by trivial", encoding="utf-8")
    _stub_evidence_identity(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_restore_windows_path", lambda: None)
    monkeypatch.setattr(
        evidence,
        "read_trace_evidence",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("persistent evidence should be bypassed")
        ),
    )
    export_calls = []
    monkeypatch.setattr(
        evidence,
        "export_trace",
        lambda *_args, **kwargs: export_calls.append(kwargs) or _raw_trace(),
    )
    commits = []
    monkeypatch.setattr(
        evidence,
        "write_trace_evidence",
        lambda *args: commits.append(args),
    )
    monkeypatch.setattr(cli, "write_json", lambda _path, _value: None)

    result = cli.main(
        [
            str(proof),
            "demo",
            "--rebuild-trace",
            "--json-only",
            "--toolchain-backend",
            "lean-4.28",
            "--output",
            str(tmp_path / "proof.mp4"),
        ]
    )

    assert result == 0
    assert export_calls[0]["checkpoint_dir"] is None
    assert len(commits) == 1


def test_force_export_bypasses_evidence_but_keeps_checkpoints(
    monkeypatch, tmp_path
) -> None:
    proof = tmp_path / "Proof.lean"
    proof.write_text("theorem demo : True := by trivial", encoding="utf-8")
    _stub_evidence_identity(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_restore_windows_path", lambda: None)
    monkeypatch.setattr(
        evidence,
        "read_trace_evidence",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("persistent evidence should be bypassed")
        ),
    )
    export_calls = []
    monkeypatch.setattr(
        evidence,
        "export_trace",
        lambda *_args, **kwargs: export_calls.append(kwargs) or _raw_trace(),
    )
    monkeypatch.setattr(
        evidence,
        "write_trace_evidence",
        lambda *_args: None,
    )
    monkeypatch.setattr(cli, "write_json", lambda _path, _value: None)

    result = cli.main(
        [
            str(proof),
            "demo",
            "--force-lean-export",
            "--json-only",
            "--toolchain-backend",
            "lean-4.28",
            "--output",
            str(tmp_path / "proof.mp4"),
        ]
    )

    assert result == 0
    assert export_calls[0]["checkpoint_dir"] is not None


def test_auto_falls_back_to_428_before_audit_and_render(
    monkeypatch, tmp_path, capsys
) -> None:
    proof = tmp_path / "Proof.lean"
    proof.write_text("theorem demo : True := by trivial", encoding="utf-8")
    monkeypatch.setattr(cli, "_restore_windows_path", lambda: None)
    monkeypatch.setattr(cli, "local_source_closure", lambda *_args: {proof.resolve()})
    monkeypatch.setattr(
        cli,
        "prepare_lean_432_workspace",
        lambda _backend, _sources, entry_sources: {
            source.resolve(): source.resolve() for source in entry_sources
        },
    )
    calls: list[tuple[str, str]] = []

    def acquire(**kwargs):
        backend = kwargs["toolchain_backend"]
        calls.append((backend.name, kwargs["trace_backend"]))
        if backend.name == "lean-4.32":
            raise RuntimeError("incremental frontend unavailable")
        return EvidenceResult(_raw_trace(), tmp_path, "test-fallback", False)

    monkeypatch.setattr(cli, "acquire_lean_evidence", acquire)
    monkeypatch.setattr(cli, "write_json", lambda _path, _value: None)

    result = cli.main(
        [
            str(proof),
            "demo",
            "--json-only",
            "--output",
            str(tmp_path / "proof.mp4"),
        ]
    )

    assert result == 0
    assert calls == [("lean-4.32", "snapshot"), ("lean-4.28", "legacy")]
    output = capsys.readouterr().out
    assert "Automatically falling back to lean-4.28" in output
    assert "Lean backend: lean-4.28 | trace backend: legacy" in output


def test_preview_uses_segmented_rendering(monkeypatch, tmp_path, capsys) -> None:
    _stub_trace_io(monkeypatch)
    calls = []
    monkeypatch.setattr(
        render,
        "render_full",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("full render called")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        render,
        "render_segmented",
        lambda *args, **kwargs: calls.append((args, kwargs)) or _stats(),
    )

    result = cli.main(
        [
            "Proof.lean",
            "demo",
            "--trace",
            str(tmp_path / "trace.json"),
            "--output",
            str(tmp_path / "proof.mp4"),
            "--preview",
            "--engine",
            "manim",
        ]
    )

    assert result == 0
    assert calls[0][1]["preview"] is True
    output = capsys.readouterr().out
    assert "Preview uses segmented rendering" in output
    assert "Segments:" in output


def test_json_only_writes_transition_map_without_rendering(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(cli, "_restore_windows_path", lambda: None)
    monkeypatch.setattr(cli, "read_json", lambda _path: _raw_trace())
    writes = []
    monkeypatch.setattr(
        cli, "write_json", lambda path, value: writes.append((path, value))
    )

    destination = tmp_path / "transitions.json"
    result = cli.main(
        [
            "Proof.lean",
            "demo",
            "--trace",
            str(tmp_path / "trace.json"),
            "--json-only",
            "--dump-transition-map",
            str(destination),
            "--output",
            str(tmp_path / "proof.mp4"),
        ]
    )

    assert result == 0
    transition_write = next(value for path, value in writes if path == destination)
    assert transition_write["schemaVersion"] == 2
    assert transition_write["theorem"] == "demo"


def test_remotion_render_is_routed_to_remotion_engine(monkeypatch, tmp_path) -> None:
    _stub_trace_io(monkeypatch)
    calls = []
    import proof_video.remotion_render as remotion_render

    monkeypatch.setattr(
        remotion_render,
        "render_remotion",
        lambda *args, **kwargs: (
            calls.append((args, kwargs))
            or SimpleNamespace(
                states=2,
                duration_seconds=3.0,
                cached_segments=0,
                rendered_segments=1,
                chars_per_second=24.0,
            )
        ),
    )

    result = cli.main(
        [
            "Proof.lean",
            "demo",
            "--trace",
            str(tmp_path / "trace.json"),
            "--output",
            str(tmp_path / "proof.mp4"),
            "--engine",
            "remotion",
        ]
    )

    assert result == 0
    assert len(calls) == 1
    assert calls[0][1]["concurrency"] == "auto"
    assert calls[0][1]["chars_per_second"] == 48.0
    assert calls[0][1]["use_cache"] is False
    assert calls[0][1]["checkpoint_seconds"] is None
    assert calls[0][1]["render_hardware"] == "auto"
    assert calls[0][1]["render_chunking"] == "auto"


def test_remotion_preview_is_routed_as_twenty_seconds(monkeypatch, tmp_path) -> None:
    _stub_trace_io(monkeypatch)
    calls = []
    import proof_video.remotion_render as remotion_render

    monkeypatch.setattr(
        remotion_render,
        "render_remotion",
        lambda *args, **kwargs: (
            calls.append(kwargs)
            or SimpleNamespace(
                states=2,
                duration_seconds=20.0,
                cached_segments=0,
                rendered_segments=1,
                chars_per_second=24.0,
            )
        ),
    )
    result = cli.main(
        [
            "Proof.lean",
            "demo",
            "--trace",
            str(tmp_path / "trace.json"),
            "--output",
            str(tmp_path / "proof.mp4"),
            "--preview",
        ]
    )

    assert result == 0
    assert calls[0]["preview_seconds"] == 20.0


def test_remotion_preview_duration_is_configurable(monkeypatch, tmp_path) -> None:
    _stub_trace_io(monkeypatch)
    calls = []
    import proof_video.remotion_render as remotion_render

    monkeypatch.setattr(
        remotion_render,
        "render_remotion",
        lambda *args, **kwargs: (
            calls.append(kwargs)
            or SimpleNamespace(
                states=2,
                duration_seconds=10.0,
                cached_segments=0,
                rendered_segments=1,
                chars_per_second=24.0,
            )
        ),
    )
    result = cli.main(
        [
            "Proof.lean",
            "demo",
            "--trace",
            str(tmp_path / "trace.json"),
            "--output",
            str(tmp_path / "proof.mp4"),
            "--preview-seconds",
            "10",
        ]
    )

    assert result == 0
    assert calls[0]["preview_seconds"] == 10.0


def test_trace_progress_uses_weighted_current_chapter() -> None:
    progress = {
        "totalWeight": 1000,
        "completedWeight": 200,
        "proofObjects": 400,
        "processedSteps": 25,
        "totalSteps": 100,
    }
    assert lean_export._trace_progress_fraction(progress) == 0.3


def test_trace_progress_reports_theorem_steps_elapsed_and_eta() -> None:
    progress = {
        "phase": "extracting",
        "chapterIndex": 7,
        "chapterCount": 20,
        "theoremName": "large_lemma",
        "currentTactic": "rw [h]",
        "processedSteps": 250,
        "totalSteps": 1000,
        "detailMode": "source-tactics",
    }
    line = lean_export._format_trace_progress(progress, 0.4, 125.0, 300.0)
    assert "40.0%" in line
    assert "chapter 8/20: large_lemma" in line
    assert "proof nodes 250/1000" in line
    assert "tactic rw [h]" in line
    assert "elapsed 02:05" in line
    assert "rate 19.20%/min" in line
    assert "ETA 03:45–06:15" in line


def test_trace_progress_names_the_active_source_command() -> None:
    source = b"lemma quick : True := by trivial\nlemma slow : True := by trivial\n"
    start = len(b"lemma quick : True := by trivial\n")
    progress = {
        "phase": "elaborating-command",
        "theoremName": "slow",
        "commandStartByte": start,
        "commandIndex": 1,
        "completedWeight": start,
        "totalWeight": len(source),
    }

    line = lean_export._format_trace_progress(
        progress,
        start / len(source),
        12.0,
        14.0,
        lean_source=source,
        command_elapsed=7.25,
    )

    assert "line 2: lemma slow" in line
    assert "current command 7.2s" in line
    assert "compressed certificate" not in line


def test_trace_finalizing_does_not_claim_a_zero_second_eta() -> None:
    line = lean_export._format_trace_finalizing(
        {
            "chapterIndex": 19,
            "chapterCount": 20,
            "theoremName": "large_lemma",
        },
        3600.0,
    )

    assert "100.0% | finalizing trace files" in line
    assert "elapsed 1:00:00" in line
    assert "ETA 00:00" not in line
