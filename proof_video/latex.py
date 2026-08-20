from __future__ import annotations

from functools import lru_cache
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class MathState:
    context: tuple[tuple[str, str], ...]
    target: str
    case_name: str | None = None


_SYMBOLS = {
    "⇔": r"\Longleftrightarrow ",
    "↔": r"\Longleftrightarrow ",
    "→": r"\to ",
    "←": r"\leftarrow ",
    "⇒": r"\Longrightarrow ",
    "↑": "",
    "∀": r"\forall ",
    "∃": r"\exists ",
    "∧": r"\land ",
    "∨": r"\lor ",
    "¬": r"\neg ",
    "≤": r"\le ",
    "≥": r"\ge ",
    "≠": r"\ne ",
    "≡": r"\equiv ",
    "∈": r"\in ",
    "∉": r"\notin ",
    "⊂": r"\subset ",
    "⊆": r"\subseteq ",
    "×": r"\times ",
    "·": r"\cdot ",
    "∣": r"\mid ",
    "∞": r"\infty ",
    "ℕ": r"\mathbb{N}",
    "ℤ": r"\mathbb{Z}",
    "ℚ": r"\mathbb{Q}",
    "ℝ": r"\mathbb{R}",
    "ℂ": r"\mathbb{C}",
    "⊤": r"\top ",
    "⊥": r"\bot ",
    "φ": r"\varphi ",
    "ϕ": r"\varphi ",
}

_KNOWN_TYPES = {
    "Nat": r"\mathbb{N}",
    "Int": r"\mathbb{Z}",
    "Rat": r"\mathbb{Q}",
    "Real": r"\mathbb{R}",
    "Complex": r"\mathbb{C}",
    "Prop": r"\mathsf{Prop}",
    "True": r"\top",
    "False": r"\bot",
}


@lru_cache(maxsize=8192)
def parse_goal_state(state: str) -> MathState:
    """Split Lean's pretty-printed goal into assumptions and conclusion."""
    case_name: str | None = None
    context: list[tuple[str, str]] = []
    target_lines: list[str] = []
    in_target = False

    for raw_line in state.replace("\r\n", "\n").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("case ") and not context and not target_lines:
            case_name = line[5:].strip()
            continue
        if line.startswith("⊢"):
            in_target = True
            target_lines.append(line[1:].strip())
            continue
        if in_target:
            target_lines.append(line)
            continue
        if " : " in line:
            names, expression = line.split(" : ", 1)
            context.append((names.strip(), expression.strip()))
        else:
            context.append(("", line))

    target = " ".join(target_lines) or state.strip()
    return MathState(context=tuple(context), target=target, case_name=case_name)


@lru_cache(maxsize=16384)
def lean_to_latex(expression: str) -> str:
    """Best-effort conversion of Lean pretty-printing to readable MathTex.

    It deliberately preserves unknown identifiers as mathematical operators, so
    arbitrary Lean declarations remain renderable without falling back to code text.
    """
    text = " ".join(expression.replace("\n", " ").split())
    text = re.sub(r"\bfun\s+(.+?)\s*↦", r"λ \1 .", text)
    text = text.replace("λ", r"\lambda ")

    for source, target in _SYMBOLS.items():
        text = text.replace(source, target)
    # Lean's pretty-printer uses an ASCII asterisk for multiplication. This
    # path also handles expressions that LeanTeX marks as unsupported, so the
    # operator must be normalized here rather than only in semantic rules.
    text = text.replace("*", r"\cdot ")
    for source, target in _KNOWN_TYPES.items():
        text = re.sub(rf"\b{source}\b", lambda _m, t=target: t, text)

    text = re.sub(r"\b([A-Z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)\b", _operator, text)
    text = re.sub(
        r"\b([A-Za-z][A-Za-z0-9_']*)\.([A-Za-z][A-Za-z0-9_']*)\b", _qualified, text
    )
    text = re.sub(r"(?<!\\)\^\s*([A-Za-z0-9]+)", r"^{\1}", text)
    text = re.sub(
        r"(?<!\\)\b([A-Za-z][A-Za-z0-9_']*)\s+([A-Za-z0-9][A-Za-z0-9_']*)",
        _application,
        text,
    )
    text = text.replace("%", r"\bmod ")
    text = text.replace(":=", r"\coloneqq ")
    text = text.replace("_", r"\_")
    text = text.replace("'", r"^{\prime}")
    return text


def _operator(match: re.Match[str]) -> str:
    name = match.group(1).split(".")[-1]
    return rf"\operatorname{{{name.replace('_', ' ')}}}"


def _qualified(match: re.Match[str]) -> str:
    return rf"\operatorname{{{match.group(2).replace('_', ' ')}}}"


def _application(match: re.Match[str]) -> str:
    function, argument = match.groups()
    if function in {"if", "then", "else", "let", "in"}:
        return match.group(0)
    if len(function) == 1:
        return rf"{function}\!\left({argument}\right)"
    return rf"\operatorname{{{function.replace('_', ' ')}}}\!\left({argument}\right)"


def context_line_to_latex(names: str, expression: str) -> str:
    rendered = lean_to_latex(expression)
    if not names:
        return rendered
    safe_names = ", ".join(part.replace("_", r"\_") for part in names.split())
    return rf"{safe_names} \;:\; {rendered}"
