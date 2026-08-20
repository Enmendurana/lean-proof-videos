from proof_video.latex import context_line_to_latex, lean_to_latex, parse_goal_state
from proof_video.animation.latex import (
    _latex_matching_tokens,
    _normalize_unicode_math,
    _sanitize_leantex,
    _unwrap_leantex_fallback,
)


def test_parse_goal_state() -> None:
    state = "case intro\na b : ℕ\nh : a ≤ b\n⊢ ∃ c, a + c = b"
    parsed = parse_goal_state(state)
    assert parsed.case_name == "intro"
    assert parsed.context == (("a b", "ℕ"), ("h", "a ≤ b"))
    assert parsed.target == "∃ c, a + c = b"


def test_common_lean_symbols_become_latex() -> None:
    rendered = lean_to_latex("∀ n : ℕ, n ≤ n → True")
    assert r"\forall" in rendered
    assert r"\mathbb{N}" in rendered
    assert r"\le" in rendered
    assert r"\to" in rendered
    assert r"\top" in rendered


def test_context_line_is_math_not_code() -> None:
    rendered = context_line_to_latex("h", "x ∈ Set.range f")
    assert rendered.startswith(r"h \;:\;")
    assert r"\in" in rendered
    assert r"\operatorname" in rendered


def test_modular_arithmetic_symbols_from_imo_demo() -> None:
    rendered = lean_to_latex("a ^ φ (1 + a * b) ≡ 1 [MOD 1 + a * b]")
    assert r"\varphi" in rendered
    assert r"\equiv" in rendered


def test_leantex_identifier_is_safe_for_mathtex() -> None:
    assert _sanitize_leantex(r"\text{Nat.some_name}(x)") == r"\text{Nat.some\_name}(x)"


def test_unhandled_leantex_expression_uses_legacy_math_fallback() -> None:
    source = r"\operatorname{Lean}\left[\text{∀ n : ℕ, n ≤ n}\right]"
    rendered = _unwrap_leantex_fallback(source)
    assert r"\forall" in rendered
    assert r"\mathbb{N}" in rendered
    assert r"\le" in rendered

    nested = "h : " + source + " = x"
    assert r"\operatorname{Lean}" not in _unwrap_leantex_fallback(nested)


def test_legacy_fallback_uses_latex_multiplication() -> None:
    source = (
        r"\operatorname{Lean}\left[\text{"
        "f (-1) ≤ -1 * f (-1) - -1 * f (-1) + f (f (-1))"
        r"}\right]"
    )
    rendered = _unwrap_leantex_fallback(source)

    assert "*" not in rendered
    assert rendered.count(r"\cdot") == 2


def test_unicode_math_is_normalized_before_manim_latex() -> None:
    source = "a \u21d4 b \u2227 n \u2265 0"
    assert _normalize_unicode_math(source) == (
        r"a \Longleftrightarrow  b \land  n \geq  0"
    )


def test_short_logical_relation_commands_are_displayed_as_long_arrows() -> None:
    assert _normalize_unicode_math(r"P \iff Q \implies R") == (
        r"P \Longleftrightarrow Q \Longrightarrow R"
    )


def test_latex_matching_tokens_preserve_algebraic_parts() -> None:
    assert _latex_matching_tokens(r"2 \cdot b + 2 \cdot a") == [
        "2",
        r"\cdot",
        "b",
        "+",
        "2",
        r"\cdot",
        "a",
    ]
    assert _latex_matching_tokens(r"2 \cdot (a + b)") == [
        "2",
        r"\cdot",
        "(",
        "a",
        "+",
        "b",
        ")",
    ]


def test_latex_matching_tokens_ignore_geometry_free_spacing_commands() -> None:
    assert _latex_matching_tokens(r"\forall x : \mathbb{R},\ x \leq 0") == [
        r"\forall",
        "x",
        ":",
        r"\mathbb{R}",
        ",",
        "x",
        r"\leq",
        "0",
    ]


def test_latex_matching_tokens_keep_complete_stretchy_delimiters() -> None:
    assert _latex_matching_tokens(r"\min\left(0,s\right)") == [
        r"\min",
        r"\left(",
        "0",
        ",",
        "s",
        r"\right)",
    ]
    assert _latex_matching_tokens(r"\left\{x\right\}") == [
        r"\left\{",
        "x",
        r"\right\}",
    ]
    assert _latex_matching_tokens(r"\bigl[x\bigr]") == [
        r"\bigl[",
        "x",
        r"\bigr]",
    ]


def test_latex_matching_tokens_keep_commands_with_required_arguments() -> None:
    tokens = _latex_matching_tokens(
        r"B : \mathcal{P}_{\mathbb{N}},\ "
        r"\mathsf{AddBasis}(B),\ "
        r"\overline{xy} = \frac{a}{b}"
    )

    assert r"\mathcal{P}_{\mathbb{N}}" in tokens
    assert r"\mathsf{AddBasis}" in tokens
    assert r"\overline{xy}" in tokens
    assert r"\frac{a}{b}" in tokens
    assert r"\mathcal" not in tokens
    assert r"\mathsf" not in tokens
