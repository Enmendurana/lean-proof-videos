"""Render a Lean file with only input and output paths on the command line."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

from proof_video.backend_policy import backend_attempts, run_with_backend_fallback
from proof_video.cache import lean_evidence_identity, local_source_closure
from proof_video.cli import main as render_main
from proof_video.render_service import RenderService
from proof_video.module_artifact import (
    module_artifact_is_current,
    record_module_artifact,
)
from proof_video.module_plan import (
    ModuleUnit,
    load_module_plan,
    materialize_module_plan,
)
from proof_video.toolchains import (
    TOOLCHAIN_CHOICES,
    prepare_lean_432_workspace,
)
from proof_video.trace_profile import resolve_trace_profile

_MARKER = re.compile(
    r"^\s*--\s*proof-video\s*:\s*theorem\s+([^\s]+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_COMMAND = re.compile(
    r"^\s*(?P<modifiers>(?:(?:private|protected|noncomputable)\s+)*)"
    r"(?P<kind>namespace|section|end|theorem|lemma)"
    r"(?:\s+(?P<name>[^\s(:={]+))?"
)


@dataclass(frozen=True)
class _Block:
    namespace_depth: int


def _without_comments_and_strings(source: str) -> str:
    """Mask nested Lean comments and strings while preserving line breaks."""

    result: list[str] = []
    index = 0
    block_depth = 0
    in_string = False
    while index < len(source):
        pair = source[index : index + 2]
        character = source[index]
        if block_depth:
            if pair == "/-":
                block_depth += 1
                result.extend("  ")
                index += 2
            elif pair == "-/":
                block_depth -= 1
                result.extend("  ")
                index += 2
            else:
                result.append("\n" if character == "\n" else " ")
                index += 1
            continue
        if in_string:
            if character == "\\" and index + 1 < len(source):
                result.extend("  ")
                index += 2
            else:
                if character == '"':
                    in_string = False
                result.append("\n" if character == "\n" else " ")
                index += 1
            continue
        if pair == "/-":
            block_depth = 1
            result.extend("  ")
            index += 2
        elif pair == "--":
            while index < len(source) and source[index] != "\n":
                result.append(" ")
                index += 1
        elif character == '"':
            in_string = True
            result.append(" ")
            index += 1
        else:
            result.append(character)
            index += 1
    return "".join(result)


def discover_theorem(lean_file: Path) -> str:
    """Return the marked theorem, or the last public theorem in the file.

    Lean files commonly introduce helper lemmas before one final theorem, so
    the final declaration is the useful deterministic default.  A source can
    override it without adding a CLI parameter using::

        -- proof-video: theorem MyNamespace.main_theorem
    """

    source = lean_file.read_text(encoding="utf-8")
    markers = _MARKER.findall(source)
    if len(markers) > 1:
        raise ValueError("The Lean file contains more than one proof-video marker.")
    if markers:
        return markers[0]

    namespace: list[str] = []
    blocks: list[_Block] = []
    declarations: list[str] = []
    cleaned = _without_comments_and_strings(source)
    for line in cleaned.splitlines():
        match = _COMMAND.match(line)
        if match is None:
            continue
        kind = match.group("kind")
        name = match.group("name")
        modifiers = frozenset(match.group("modifiers").split())
        if kind == "namespace" and name:
            blocks.append(_Block(len(namespace)))
            namespace.extend(part for part in name.split(".") if part)
        elif kind == "section":
            blocks.append(_Block(len(namespace)))
        elif kind == "end":
            if blocks:
                namespace[:] = namespace[: blocks.pop().namespace_depth]
        elif kind in {"theorem", "lemma"} and name and "private" not in modifiers:
            declarations.append(".".join((*namespace, name)))

    if not declarations:
        raise ValueError(f"No theorem or lemma declaration found in {lean_file}.")
    return declarations[-1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="render-proof",
        description="Render the main theorem in a Lean file to an MP4.",
    )
    parser.add_argument("lean_file", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--rebuild-trace",
        action="store_true",
        help="Ignore durable Lean evidence and elaborate the selected theorem again",
    )
    parser.add_argument(
        "--rebuild-chapter",
        metavar="THEOREM",
        help="Recompute only one theorem chapter and reuse compatible siblings",
    )
    parser.add_argument(
        "--toolchain-backend",
        choices=TOOLCHAIN_CHOICES,
        default="auto",
        help="auto (try 4.32, then fall back to 4.28), lean-4.32, or lean-4.28",
    )
    parser.add_argument(
        "--trace-backend",
        choices=("snapshot", "legacy"),
        default=None,
        help="Incremental snapshot or legacy Lean frontend",
    )
    parser.add_argument(
        "--trace-granularity",
        choices=("auto", "fine", "scalable"),
        default="auto",
        help=(
            "auto uses canonical ABI 5 source actions; fine selects the "
            "proof-term compatibility extractor and scalable explicitly "
            "selects the same resumable ABI 5 chapter format"
        ),
    )
    parser.add_argument(
        "--render-hardware",
        choices=("auto", "cpu", "gpu-required"),
        default="auto",
    )
    parser.add_argument("--render-concurrency", default="auto")
    parser.add_argument("--render-chunking", default="auto")
    parser.add_argument("--recalibrate-renderer", action="store_true")
    parser.add_argument("--render-profile-report", type=Path)
    return parser


def _materialize_module_trace(
    *,
    module_plan,
    backend,
    trace_backend: str,
    project_root: Path,
    shared_cache_root: Path,
    rebuild_trace: bool,
    rebuild_chapter: str | None,
) -> Path:
    """Materialize every unit with one backend, never a mixed toolchain set."""

    workspace_mapping = (
        prepare_lean_432_workspace(
            backend,
            {
                source
                for unit in module_plan.units
                for source in local_source_closure(project_root, unit.lean_file)
            },
            entry_sources=[unit.lean_file for unit in module_plan.units],
        )
        if backend.name == "lean-4.32"
        else {}
    )

    def module_output_path(source: Path) -> Path:
        try:
            relative = source.relative_to(project_root).with_suffix(".olean")
        except ValueError as error:
            raise ValueError(
                f"modular Lean source must be inside {project_root}: {source}"
            ) from error
        build_root = (
            backend.execution_root if backend.name == "lean-4.32" else project_root
        )
        return build_root / ".lake" / "build" / "lib" / "lean" / relative

    def export_unit(unit: ModuleUnit, unit_output: Path, rebuild: bool) -> None:
        olean_output = module_output_path(unit.lean_file)
        identity = lean_evidence_identity(
            backend.execution_root,
            workspace_mapping.get(unit.lean_file.resolve(), unit.lean_file),
            unit.theorem,
            "hybrid",
            backend.identity if backend.name != "lean-4.28" else None,
        )
        must_rebuild = rebuild or not module_artifact_is_current(
            olean_output,
            identity,
        )
        unit_arguments = [
            str(unit.lean_file),
            unit.theorem,
            "--trace-mode",
            "hybrid",
            "--json-only",
            "--output",
            str(unit_output),
            "--lean-module-output",
            str(olean_output),
            "--toolchain-backend",
            backend.name,
            "--trace-backend",
            trace_backend,
        ]
        if rebuild:
            unit_arguments.append("--rebuild-trace")
        elif must_rebuild:
            unit_arguments.append("--force-lean-export")
        if rebuild_chapter is not None:
            unit_arguments.extend(("--rebuild-chapter", rebuild_chapter))
        result = render_main(unit_arguments)
        if result:
            raise RuntimeError(f"module trace export failed for {unit.lean_file}")
        if not olean_output.exists():
            raise RuntimeError(f"module trace did not publish {olean_output}")
        record_module_artifact(olean_output, identity)

    return materialize_module_plan(
        module_plan,
        cache_root=backend.evidence_cache_root(shared_cache_root),
        rebuild_trace=rebuild_trace,
        export_unit=export_unit,
        rebuild_chapter=rebuild_chapter,
        identity_root=backend.execution_root,
        backend_identity=(backend.identity if backend.name != "lean-4.28" else None),
        identity_sources=workspace_mapping,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    lean_file = args.lean_file.resolve()
    if not lean_file.is_file():
        raise SystemExit(f"Lean file does not exist: {lean_file}")
    if args.output.suffix.lower() != ".mp4":
        raise SystemExit("Output path must end in .mp4")
    output = args.output.resolve()
    project_root = Path(__file__).resolve().parents[2]
    shared_cache_root = project_root / ".lean-proof-video-cache"
    try:
        primary_attempt = backend_attempts(
            project_root,
            shared_cache_root,
            args.toolchain_backend,
            args.trace_backend,
        )[0]
    except ValueError as error:
        raise SystemExit(str(error)) from error
    backend = primary_attempt.backend
    trace_backend = primary_attempt.trace_backend
    try:
        theorem = discover_theorem(lean_file)
    except (OSError, UnicodeError, ValueError) as error:
        raise SystemExit(str(error)) from error
    print(f"Detected theorem: {theorem}", flush=True)
    if output.exists():
        print(
            "An existing MP4 will remain unchanged until the new render "
            f"finishes successfully: {output}",
            flush=True,
        )
    try:
        module_plan = load_module_plan(lean_file)
    except (OSError, UnicodeError, ValueError) as error:
        raise SystemExit(str(error)) from error
    if module_plan is not None:
        print(
            f"Modular proof plan: {len(module_plan.units)} independently cached "
            "Lean units.",
            flush=True,
        )

        try:
            module_result = run_with_backend_fallback(
                project_root,
                shared_cache_root,
                args.toolchain_backend,
                args.trace_backend,
                lambda candidate, candidate_trace: _materialize_module_trace(
                    module_plan=module_plan,
                    backend=candidate,
                    trace_backend=candidate_trace,
                    project_root=project_root,
                    shared_cache_root=shared_cache_root,
                    rebuild_trace=args.rebuild_trace,
                    rebuild_chapter=args.rebuild_chapter,
                ),
                phase="modular trace acquisition",
            )
        except (OSError, UnicodeError, ValueError, RuntimeError) as error:
            raise SystemExit(str(error)) from error
        backend = module_result.backend
        trace_backend = module_result.trace_backend
        module_trace = module_result.value

    requested_mode = {
        "auto": "auto",
        "fine": "proof-term",
        "scalable": "hybrid",
    }[args.trace_granularity]
    try:
        profile = resolve_trace_profile(
            lean_file,
            requested_mode=requested_mode,
            requested_toolchain=(
                backend.name if module_plan is not None else args.toolchain_backend
            ),
            requested_trace_backend=(
                trace_backend if module_plan is not None else args.trace_backend
            ),
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise SystemExit(str(error)) from error

    render_args = [
        str(lean_file),
        theorem,
        "--engine",
        "remotion",
        "--output",
        str(output),
        "--toolchain-backend",
        profile.toolchain_backend,
        "--render-hardware",
        args.render_hardware,
        "--render-concurrency",
        args.render_concurrency,
        "--render-chunking",
        args.render_chunking,
    ]
    if args.recalibrate_renderer:
        render_args.append("--recalibrate-renderer")
    if args.render_profile_report is not None:
        render_args.extend(("--render-profile-report", str(args.render_profile_report)))
    if module_plan is not None:
        render_args.extend(
            ("--trace-backend", trace_backend, "--trace", str(module_trace))
        )
    elif profile.trace_backend is not None:
        render_args.extend(("--trace-backend", profile.trace_backend))
    render_args.extend(("--trace-mode", profile.trace_mode))
    if profile.resumable:
        render_args.append("--resume")
    print(profile.description, flush=True)
    if args.rebuild_trace and module_plan is None:
        render_args.append("--rebuild-trace")
    if args.rebuild_chapter is not None and module_plan is None:
        render_args.extend(("--rebuild-chapter", args.rebuild_chapter))
    return RenderService(runner=render_main).run_arguments(render_args)


if __name__ == "__main__":
    raise SystemExit(main())
