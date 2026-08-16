from unittest.mock import patch

from proof_video.models import Frame, Goal, LatexHypothesis, Movie
from proof_video.tex_precompile import (
    _matching_tex_expressions,
    collect_movie_tex_expressions,
    precompile_movie_tex,
)


def _movie() -> Movie:
    goal = Goal(
        "g",
        "",
        latex_target=r"f(x) \leq 0",
        latex_context=(LatexHypothesis("x", r"\mathbb{R}"),),
    )
    return Movie("demo", (Frame(0, "", (goal,)),))


def test_matching_expressions_deduplicate_repeated_tokens() -> None:
    expressions = _matching_tex_expressions(r"f(x)+f(x)")

    assert "f" in expressions
    assert "(" in expressions
    assert "f ( x ) + f ( x )" in expressions
    assert len(expressions) == len(set(expressions))


def test_movie_collection_is_deterministic_and_includes_qed() -> None:
    first = collect_movie_tex_expressions(_movie())
    second = collect_movie_tex_expressions(_movie())

    assert first == second
    assert tuple(sorted(set(first))) == first
    assert r"\square" in first
    assert r"\mathbb{R}" in first


def test_parallel_precompile_visits_each_content_key_once() -> None:
    seen: list[str] = []

    def compile_expression(expression: str, environment: str) -> None:
        assert environment == "align*"
        seen.append(expression)

    with patch(
        "proof_video.tex_precompile.tex_to_svg_file",
        side_effect=compile_expression,
    ):
        stats = precompile_movie_tex(_movie(), workers=3)

    assert stats.failures == ()
    assert stats.workers == 3
    assert sorted(seen) == list(collect_movie_tex_expressions(_movie()))
