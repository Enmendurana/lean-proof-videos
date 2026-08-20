"""Inspect and manually prune the unbounded proof-video cache."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import time

from proof_video.toolchains import lean_432_is_qualified, qualification_path


@dataclass(frozen=True)
class CacheRow:
    name: str
    files: int
    bytes: int


_RENDER_DIRECTORIES = (
    "assembly",
    "chunks",
    "full",
    "manim",
    "remotion",
    "remotion-bundles",
    "remotion-checkpoints",
    "remotion-layouts",
    "remotion-segments",
    "remotion-tmp",
    "render-profiles",
    "renderer-fallback",
    "segments",
)
_TEMP_SUFFIXES = (".tmp", ".writing", ".syncing", ".rendering.mp4", ".assembling.mp4")


def cache_root() -> Path:
    return Path(__file__).resolve().parents[2] / ".lean-proof-video-cache"


def _tree_stats(path: Path) -> CacheRow:
    files = 0
    size = 0
    if path.exists():
        for candidate in path.rglob("*"):
            if not candidate.is_file():
                continue
            files += 1
            try:
                size += candidate.stat().st_size
            except OSError:
                pass
    return CacheRow(path.name, files, size)


def _format_bytes(value: int) -> str:
    size = float(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{value} B"


def status(root: Path) -> list[CacheRow]:
    if not root.exists():
        print(f"Cache does not exist yet: {root}")
        return []
    rows = [_tree_stats(path) for path in sorted(root.iterdir(), key=lambda p: p.name)]
    print(f"Proof-video cache: {root}")
    for row in rows:
        print(f"  {row.name:28} {row.files:7d} files  {_format_bytes(row.bytes):>12}")
    print(
        f"  {'TOTAL':28} {sum(row.files for row in rows):7d} files  "
        f"{_format_bytes(sum(row.bytes for row in rows)):>12}"
    )
    print("Automatic size eviction: disabled")
    project_root = Path(__file__).resolve().parents[2]
    qualification = qualification_path(root)
    if lean_432_is_qualified(project_root, root):
        print(f"Lean 4.32 backend: qualified ({qualification})")
    elif qualification.exists():
        print(
            "Lean 4.32 backend: qualification record is stale or incomplete; "
            "auto still tries 4.32 first and falls back to 4.28 on failure"
        )
    else:
        print(
            "Lean 4.32 backend: unqualified experimental profile; auto tries "
            "it first and falls back to 4.28 on failure"
        )
    active_workers = 0
    for worker in root.rglob("worker.json"):
        try:
            value = json.loads(worker.read_text(encoding="utf-8"))
            if float(value.get("expiresAt", 0)) > time.time():
                active_workers += 1
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    print(f"Lean 4.32 in-memory worker leases: {active_workers} active")
    return rows


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _remove_tree(root: Path, target: Path) -> tuple[int, int]:
    if not _inside(root, target) or target.resolve() == root.resolve():
        raise ValueError(f"refusing to prune unsafe path: {target}")
    row = _tree_stats(target)
    if target.exists():
        shutil.rmtree(target)
    return row.files, row.bytes


def prune(root: Path, scope: str) -> tuple[int, int]:
    if not root.exists():
        print(f"Cache does not exist yet: {root}")
        return (0, 0)
    removed_files = 0
    removed_bytes = 0
    if scope == "temporary":
        targets = [
            path
            for path in root.rglob("*")
            if path.is_file()
            and any(path.name.endswith(suffix) for suffix in _TEMP_SUFFIXES)
        ]
        for path in targets:
            if not _inside(root, path):
                continue
            try:
                removed_bytes += path.stat().st_size
                path.unlink()
                removed_files += 1
            except FileNotFoundError:
                pass
    elif scope == "render":
        for name in _RENDER_DIRECTORIES:
            files, size = _remove_tree(root, root / name)
            removed_files += files
            removed_bytes += size
    elif scope == "all":
        # The explicit all scope includes durable evidence and snapshots.
        for target in list(root.iterdir()):
            if target.is_dir():
                files, size = _remove_tree(root, target)
                removed_files += files
                removed_bytes += size
            elif _inside(root, target):
                try:
                    removed_bytes += target.stat().st_size
                    target.unlink()
                    removed_files += 1
                except FileNotFoundError:
                    pass
    else:
        raise ValueError(f"unknown prune scope: {scope}")
    print(
        f"Pruned {removed_files} file(s), {_format_bytes(removed_bytes)} "
        f"(scope: {scope})."
    )
    if scope != "all":
        print("Durable Lean evidence and incremental snapshots were preserved.")
    return removed_files, removed_bytes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="render-proof-cache",
        description="Inspect or manually prune the unbounded proof-video cache.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="Show cache categories and disk use")
    prune_parser = subparsers.add_parser("prune", help="Remove selected cache data")
    prune_parser.add_argument(
        "--scope",
        choices=("temporary", "render", "all"),
        default="temporary",
        help=(
            "temporary (default), render-only, or all including durable proof "
            "evidence and snapshots"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = cache_root().resolve()
    if args.command == "status":
        status(root)
    else:
        prune(root, args.scope)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
