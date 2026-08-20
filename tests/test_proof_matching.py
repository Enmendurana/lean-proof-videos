from proof_video.proof.matching import (
    adapted_expression_path,
    path_without_sequent_prefix,
)


def test_certified_substitution_keeps_expression_paths_local() -> None:
    path = ("0", "1", "0", "2")
    assert adapted_expression_path(path, "certified-substitution") == path


def test_legacy_chapter_namespace_is_removed_from_expression_paths() -> None:
    assert path_without_sequent_prefix(("chapter-3/0", "1", "0")) == (
        "0",
        "1",
        "0",
    )
    assert path_without_sequent_prefix(("context", 17, "chapter-3/0", "1", "0")) == (
        "0",
        "1",
        "0",
    )


def test_non_hierarchical_expression_paths_are_unchanged() -> None:
    assert path_without_sequent_prefix(("0", "1")) == ("0", "1")
