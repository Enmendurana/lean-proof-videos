from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from manim.mobject.text.tex_mobject import SingleStringMathTex
from manim.utils.tex_file_writing import tex_to_svg_file

from proof_video.animation.latex import (
    _latex_matching_token_spans,
    _normalize_unicode_math,
    _sanitize_leantex,
    _split_latex_lines,
    _unwrap_leantex_fallback,
)
from proof_video.models import Movie
from proof_video.animation.scene_helpers import (
    _goal_latex,
    _initial_context_lines,
)


@dataclass(frozen=True)
class TexPrecompileStats:
    expressions: int
    workers: int
    elapsed_seconds: float
    failures: tuple[str, ...] = ()


def _modified_expression(expression: str) -> str:
    """Apply the same final normalization as Manim's SVG cache key."""

    instance = SingleStringMathTex.__new__(SingleStringMathTex)
    return instance._get_modified_expression(expression)


def _matching_tex_expressions(source: str) -> set[str]:
    """Return every TeX expression created by ``_matching_mathtex``.

    ``MathTex('{{a}}{{+}}{{b}}')`` first compiles the complete expression
    ``a + b`` and then loads each isolated token.  Prewarming only the visible
    source string therefore misses the expensive whole-row cache entry.
    """

    normalized = _sanitize_leantex(
        _normalize_unicode_math(_unwrap_leantex_fallback(source))
    )
    tokens = [token for token, _start, _end in _latex_matching_token_spans(normalized)]
    if not tokens:
        return {_modified_expression(normalized)}
    return {
        _modified_expression(" ".join(tokens)),
        *(_modified_expression(token) for token in tokens),
    }


def collect_movie_tex_expressions(movie: Movie) -> tuple[str, ...]:
    """Collect the exact, content-addressable expressions used by a scene."""

    expressions = {_modified_expression(r"\square")}
    for frame in movie.semantic_frames():
        for goal in frame.display_goals[:1]:
            sources = [*_initial_context_lines(goal), _goal_latex(goal)]
            for source in sources:
                # The scene always measures the whole row and may additionally
                # render deterministic wrapped pieces when it is too wide.
                expressions.update(_matching_tex_expressions(source))
                for piece in _split_latex_lines(source):
                    expressions.update(_matching_tex_expressions(piece))
    return tuple(sorted(expressions))


def precompile_movie_tex(
    movie: Movie,
    *,
    workers: int | None = None,
) -> TexPrecompileStats:
    """Warm Manim's immutable TeX-to-SVG cache in parallel.

    Failures are deliberately non-fatal here.  The normal scene construction
    still owns error handling and can use ``_safe_mathtex``'s readable fallback.
    """

    expressions = collect_movie_tex_expressions(movie)
    configured = os.environ.get("LEAN_PROOF_TEX_WORKERS")
    if workers is None and configured:
        try:
            workers = int(configured)
        except ValueError:
            workers = None
    worker_count = max(1, min(workers or (os.cpu_count() or 1), 8))
    started = time.perf_counter()
    failures: list[str] = []

    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="lean-proof-tex",
    ) as executor:
        futures = {
            executor.submit(tex_to_svg_file, expression, "align*"): expression
            for expression in expressions
        }
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as error:  # scene fallback remains authoritative
                expression = futures[future]
                failures.append(f"{expression}: {error}")

    return TexPrecompileStats(
        expressions=len(expressions),
        workers=worker_count,
        elapsed_seconds=time.perf_counter() - started,
        failures=tuple(failures),
    )
