"""LeanTeX cleanup and renderer-neutral LaTeX tokenization."""

from __future__ import annotations

from functools import lru_cache
import re

from proof_video.latex import lean_to_latex


def _normalize_unicode_math(source: str) -> str:
    replacements = {
        "⇔": r"\Longleftrightarrow ",
        "⇒": r"\Longrightarrow ",
        "↔": r"\Longleftrightarrow ",
        "→": r"\to ",
        "←": r"\leftarrow ",
        "∧": r"\land ",
        "∨": r"\lor ",
        "¬": r"\lnot ",
        "∀": r"\forall ",
        "∃": r"\exists ",
        "≤": r"\leq ",
        "≥": r"\geq ",
        "≠": r"\neq ",
        "∈": r"\in ",
        "∉": r"\notin ",
        "⊆": r"\subseteq ",
        "∪": r"\cup ",
        "∩": r"\cap ",
        "ℕ": r"\mathbb{N}",
        "ℤ": r"\mathbb{Z}",
        "ℚ": r"\mathbb{Q}",
        "ℝ": r"\mathbb{R}",
        "φ": r"\varphi ",
        "⊢": r"\vdash ",
    }
    for symbol, latex in replacements.items():
        source = source.replace(symbol, latex)
    source = re.sub(
        r"\\(?:iff|Leftrightarrow)\b",
        r"\\Longleftrightarrow",
        source,
    )
    source = re.sub(
        r"\\(?:implies|Rightarrow)\b",
        r"\\Longrightarrow",
        source,
    )
    return source


def _latex_matching_tokens(source: str) -> list[str]:
    """Tokenize LaTeX into independently renderable matching units."""
    return [token for token, _start, _end in _latex_matching_token_spans(source)]


def _latex_matching_token_spans(source: str) -> list[tuple[str, int, int]]:
    """Return renderable LaTeX tokens together with source character spans."""
    # Return a fresh list to preserve the historical caller contract while the
    # structural tokenization itself is interned across repeated goal states.
    return list(_cached_latex_matching_token_spans(source))


@lru_cache(maxsize=16384)
def _cached_latex_matching_token_spans(
    source: str,
) -> tuple[tuple[str, int, int], ...]:
    """Intern one canonical LaTeX row independent of renderer/timeline state."""
    # Every token must be valid when rendered independently by KaTeX/Manim.
    # Commands which consume braced arguments therefore stay attached to those
    # arguments; a bare ``\mathcal`` or ``\mathsf`` renders as a red error.
    grouped_command_arities = {
        "mathbb": 1,
        "mathbf": 1,
        "mathcal": 1,
        "mathfrak": 1,
        "mathit": 1,
        "mathrm": 1,
        "mathsf": 1,
        "mathtt": 1,
        "mathnormal": 1,
        "boldsymbol": 1,
        "text": 1,
        "operatorname": 1,
        "mathop": 1,
        "bar": 1,
        "vec": 1,
        "hat": 1,
        "widehat": 1,
        "tilde": 1,
        "widetilde": 1,
        "dot": 1,
        "ddot": 1,
        "overline": 1,
        "underline": 1,
        "overbrace": 1,
        "underbrace": 1,
        "sqrt": 1,
        "frac": 2,
        "dfrac": 2,
        "tfrac": 2,
        "binom": 2,
        "overset": 2,
        "underset": 2,
        "stackrel": 2,
    }
    tokens: list[tuple[str, int, int]] = []
    index = 0

    def balanced_group(start: int) -> tuple[str, int]:
        depth = 0
        cursor = start
        while cursor < len(source):
            if source[cursor] == "{" and (cursor == 0 or source[cursor - 1] != "\\"):
                depth += 1
            elif source[cursor] == "}" and (cursor == 0 or source[cursor - 1] != "\\"):
                depth -= 1
                if depth == 0:
                    return source[start : cursor + 1], cursor + 1
            cursor += 1
        return source[start:], len(source)

    while index < len(source):
        character = source[index]
        if character.isspace():
            index += 1
            continue
        if character == "\\":
            start = index
            end = index + 1
            if end < len(source) and source[end].isalpha():
                while end < len(source) and source[end].isalpha():
                    end += 1
            else:
                end = min(len(source), end + 1)
            command = source[index:end]
            name = command[1:]
            delimiter_commands = {
                "left",
                "right",
                "big",
                "Big",
                "bigg",
                "Bigg",
                "bigl",
                "bigr",
                "Bigl",
                "Bigr",
                "biggl",
                "biggr",
                "Biggl",
                "Biggr",
            }
            if name in delimiter_commands and end < len(source):
                delimiter_end = end + 1
                if source[end] == "\\" and delimiter_end < len(source):
                    if source[delimiter_end].isalpha():
                        delimiter_end += 1
                        while (
                            delimiter_end < len(source)
                            and source[delimiter_end].isalpha()
                        ):
                            delimiter_end += 1
                    else:
                        delimiter_end += 1
                command += source[end:delimiter_end]
                end = delimiter_end
            elif name in grouped_command_arities:
                groups_needed = grouped_command_arities[name]
                for _ in range(groups_needed):
                    if end < len(source) and source[end] == "{":
                        group, end = balanced_group(end)
                        command += group
            # Spacing commands affect TeX layout but have no SVG geometry.
            # Including them would make the semantic-token count differ from
            # Manim's submobject count and disable all partial-row matching.
            if command not in {r"\ ", r"\;", r"\,"}:
                tokens.append((command, start, end))
            index = end
            continue
        if character in "^_" and index + 1 < len(source):
            if source[index + 1] == "{":
                group, end = balanced_group(index + 1)
                suffix = character + group
            else:
                end = index + 2
                suffix = source[index:end]
            if tokens:
                token, start, _old_end = tokens[-1]
                tokens[-1] = (token + suffix, start, end)
            else:
                tokens.append((suffix, index, end))
            index = end
            continue
        if character == "{":
            start = index
            group, index = balanced_group(index)
            tokens.append((group, start, index))
            continue
        if character.isalnum():
            start = index
            end = index + 1
            while end < len(source) and source[end].isalnum():
                end += 1
            tokens.append((source[index:end], start, end))
            index = end
            continue
        tokens.append((character, index, index + 1))
        index += 1

    return tuple(tokens)


def _split_latex_lines(source: str, target_chars: int = 68) -> list[str]:
    """Greedily split at top-level mathematical relations and connectives."""
    markers = (r"\land", r"\lor", r"\Rightarrow", r"\rightarrow", r",\ ", r"\quad")
    result: list[str] = []
    remaining = source.strip()
    while len(remaining) > target_chars:
        brace_depth = 0
        candidates: list[int] = []
        index = 0
        while index < len(remaining):
            character = remaining[index]
            if character == "{" and (index == 0 or remaining[index - 1] != "\\"):
                brace_depth += 1
            elif character == "}" and (index == 0 or remaining[index - 1] != "\\"):
                brace_depth = max(0, brace_depth - 1)
            if brace_depth == 0:
                for marker in markers:
                    if remaining.startswith(marker, index):
                        candidates.append(index + len(marker))
                        break
            index += 1

        usable = [position for position in candidates if 24 <= position <= target_chars]
        if not usable:
            usable = [position for position in candidates if position > 24]
        if not usable:
            break
        split_at = min(usable, key=lambda position: abs(position - target_chars))
        result.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()

    if remaining:
        result.append(remaining)
    return result


def _sanitize_leantex(source: str) -> str:
    """Escape identifier punctuation inside LeanTeX ``\text{...}`` blocks."""

    def escape_text(match: re.Match[str]) -> str:
        content = match.group(1).replace("_", r"\_").replace("%", r"\%")
        return rf"\text{{{content}}}"

    return re.sub(r"\\text\{([^{}]*)\}", escape_text, source)


def _unwrap_leantex_fallback(source: str) -> str:
    """Convert LeanTeX's explicit unhandled-expression marker via legacy rules."""
    prefix = r"\operatorname{Lean}\left[\text{"
    suffix = r"}\right]"
    result = source
    while (start := result.find(prefix)) >= 0:
        content_start = start + len(prefix)
        end = result.find(suffix, content_start)
        if end < 0:
            break
        replacement = lean_to_latex(result[content_start:end])
        result = result[:start] + replacement + result[end + len(suffix) :]
    return result
