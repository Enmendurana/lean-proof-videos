"""Source-aware diagnostics for long Lean elaboration runs.

The Lean worker owns proof checking.  This module only turns byte spans and
durations from its diagnostic sidecar into useful, stable terminal output.
Keeping that distinction explicit prevents profiling data from becoming part
of the trusted proof contract.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Iterable


_DECLARATION = re.compile(
    r"^(?:private\s+|protected\s+|noncomputable\s+)*"
    r"(?:theorem|lemma|def|abbrev|structure|class|instance|inductive)\b"
)
_COMMAND = re.compile(
    r"^(?:namespace|section|end|open|set_option|variable|include|omit|attribute)\b"
)


@dataclass(frozen=True)
class SourceLocation:
    line: int
    label: str


@dataclass(frozen=True)
class CommandTiming:
    index: int
    start_byte: int
    end_byte: int
    elapsed_ms: int
    declarations: tuple[str, ...]
    location: SourceLocation


def source_location(source: bytes, start_byte: int) -> SourceLocation:
    """Resolve a Lean UTF-8 byte position without confusing bytes and chars."""

    offset = min(max(0, int(start_byte)), len(source))
    line = source.count(b"\n", 0, offset) + 1
    tail = source[offset:].decode("utf-8", errors="replace")
    candidates: list[tuple[int, str]] = []
    in_block_comment = False
    for relative_line, raw in enumerate(tail.splitlines()[:80]):
        stripped = raw.strip()
        if not stripped:
            continue
        if in_block_comment:
            if "-/" in stripped:
                in_block_comment = False
            continue
        if stripped.startswith("/-"):
            in_block_comment = "-/" not in stripped[2:]
            continue
        if stripped.startswith(("--", "*", "-/")):
            continue
        candidates.append((relative_line, stripped))
        if _DECLARATION.match(stripped):
            break
        if _COMMAND.match(stripped):
            break
    if not candidates:
        return SourceLocation(line, "end of file")
    relative_line, text = candidates[-1]
    compact = " ".join(text.split())
    if len(compact) > 112:
        compact = compact[:109] + "..."
    return SourceLocation(line + relative_line, compact)


def read_command_profile(path: Path, lean_file: Path) -> list[CommandTiming]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return []
    try:
        source = lean_file.read_bytes()
    except OSError:
        return []
    result: list[CommandTiming] = []
    for item in payload.get("commands", []):
        if not isinstance(item, dict):
            continue
        start = max(0, int(item.get("startByte", 0)))
        declarations = item.get("declarations", [])
        result.append(
            CommandTiming(
                index=max(0, int(item.get("index", 0))),
                start_byte=start,
                end_byte=max(start, int(item.get("endByte", start))),
                elapsed_ms=max(0, int(item.get("elapsedMs", 0))),
                declarations=tuple(str(value) for value in declarations),
                location=source_location(source, start),
            )
        )
    return result


def slowest_commands(
    commands: Iterable[CommandTiming], limit: int = 5
) -> list[CommandTiming]:
    return sorted(commands, key=lambda item: (-item.elapsed_ms, item.index))[:limit]


def format_profile_summary(commands: Iterable[CommandTiming], limit: int = 5) -> str:
    slowest = slowest_commands(commands, limit)
    if not slowest:
        return ""
    lines = ["Lean trace: slowest source commands:"]
    for command in slowest:
        declaration = (
            f" [{', '.join(command.declarations)}]" if command.declarations else ""
        )
        lines.append(
            f"  {command.elapsed_ms / 1000:8.2f}s | line "
            f"{command.location.line}: {command.location.label}{declaration}"
        )
    return "\n".join(lines)
