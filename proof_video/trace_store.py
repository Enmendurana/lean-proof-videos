from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterator

from proof_video.cache import read_json, write_json
from proof_video.workers import ordered_parallel_map


def is_hybrid_manifest(value: dict[str, Any]) -> bool:
    version = str(value.get("schemaVersion", ""))
    return (version.startswith("3.1") or version.startswith("4.")) and isinstance(
        value.get("chapterRefs"), list
    )


def is_embedded_hybrid_trace(value: dict[str, Any]) -> bool:
    version = str(value.get("schemaVersion", ""))
    return (version.startswith("3.0") or version.startswith("4.")) and isinstance(
        value.get("chapters"), list
    )


def _chapter_digest(chapter: dict[str, Any]) -> str:
    canonical = json.dumps(
        chapter,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(b"hybrid-chapter-v1\0" + canonical).hexdigest()


def _resolve_object_path(reference: dict[str, Any], base_dir: Path | None) -> Path:
    path = Path(str(reference.get("objectPath", "")))
    if not path.is_absolute():
        if base_dir is None:
            raise ValueError(f"relative chapter object has no manifest base: {path}")
        path = base_dir / path
    return path.resolve()


def ingest_hybrid_manifest(
    manifest: dict[str, Any],
    object_store: Path,
    *,
    source_base: Path | None = None,
) -> dict[str, Any]:
    """Copy chapter objects into a canonical SHA-256 object store.

    Only one chapter is resident at a time. The returned manifest references
    immutable objects and can therefore be cached or copied without embedding
    the complete proof trace.
    """

    if is_embedded_hybrid_trace(manifest):
        object_store.mkdir(parents=True, exist_ok=True)
        references: list[dict[str, Any]] = []
        prepared = ordered_parallel_map(
            lambda chapter: (chapter, _chapter_digest(chapter)),
            manifest["chapters"],
        )
        for chapter, digest in prepared:
            destination = object_store / f"{digest}.json"
            if destination.exists():
                if _chapter_digest(read_json(destination)) != digest:
                    raise ValueError(f"corrupt trace object: {destination}")
            else:
                write_json(destination, chapter)
            references.append(
                {
                    "id": chapter.get("id", len(references)),
                    "theoremName": chapter.get("theoremName", ""),
                    "dependencies": chapter.get("dependencies", []),
                    "proofFingerprint": chapter.get("proofFingerprint", ""),
                    "axioms": chapter.get("axioms", []),
                    "validation": chapter.get("validation", {}),
                    "isMain": chapter.get("isMain", False),
                    "objectPath": str(destination.resolve()),
                    "objectHash": digest,
                }
            )
        return {
            "schemaVersion": (
                "4.0"
                if str(manifest.get("schemaVersion", "")).startswith("4.")
                else "3.1"
            ),
            "theoremName": manifest.get("theoremName", ""),
            "source": manifest.get("source", ""),
            "granularity": "source-tactic/content-addressed-local-theorem-chapters",
            "chapterRefs": references,
            "validation": manifest.get("validation", {}),
        }
    if not is_hybrid_manifest(manifest):
        return manifest
    object_store.mkdir(parents=True, exist_ok=True)
    rewritten: list[dict[str, Any]] = []

    def prepare_reference(reference: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
        source = _resolve_object_path(reference, source_base)
        chapter = read_json(source)
        digest = _chapter_digest(chapter)
        expected = str(reference.get("objectHash", ""))
        if expected and expected != digest:
            raise ValueError(
                f"hybrid chapter hash mismatch for "
                f"{reference.get('theoremName', source)}"
            )
        if str(chapter.get("proofFingerprint", "")) != str(
            reference.get("proofFingerprint", "")
        ):
            raise ValueError(
                f"hybrid chapter proof fingerprint mismatch for "
                f"{reference.get('theoremName', source)}"
            )
        return reference, chapter, digest

    prepared_references = ordered_parallel_map(
        prepare_reference,
        manifest["chapterRefs"],
    )
    for reference, chapter, digest in prepared_references:
        destination = object_store / f"{digest}.json"
        if destination.exists():
            existing = read_json(destination)
            if _chapter_digest(existing) != digest:
                raise ValueError(f"corrupt trace object: {destination}")
        else:
            write_json(destination, chapter)
        rewritten.append(
            {
                **reference,
                "objectHash": digest,
                "objectPath": str(destination.resolve()),
            }
        )
    return {**manifest, "chapterRefs": rewritten}


def hydrate_hybrid_manifest(
    manifest: dict[str, Any], *, base_dir: Path | None = None
) -> dict[str, Any]:
    """Materialize schema 3.1/v4 references for existing embedded consumers.

    Hydration remains a compatibility boundary. Export and cache operations are
    streaming; a future renderer may consume chapter iterators directly.
    """

    if not is_hybrid_manifest(manifest):
        return manifest
    chapters = list(iter_hybrid_chapters(manifest, base_dir=base_dir))
    return {
        "schemaVersion": (
            "4.0"
            if str(manifest.get("schemaVersion", "")).startswith("4.")
            else "3.0"
        ),
        "theoremName": manifest.get("theoremName", ""),
        "source": manifest.get("source", ""),
        "granularity": manifest.get("granularity", ""),
        "chapters": chapters,
        "validation": manifest.get("validation", {}),
        "manifest": manifest,
    }


def iter_hybrid_chapters(
    manifest: dict[str, Any], *, base_dir: Path | None = None
) -> Iterator[dict[str, Any]]:
    """Yield and validate one immutable theorem chapter at a time."""

    if not is_hybrid_manifest(manifest):
        yield from manifest.get("chapters", ())
        return
    for reference in manifest["chapterRefs"]:
        path = _resolve_object_path(reference, base_dir)
        chapter = read_json(path)
        expected = str(reference.get("objectHash", ""))
        actual = _chapter_digest(chapter)
        if expected and actual != expected:
            raise ValueError(
                f"hybrid chapter hash mismatch for {reference.get('theoremName', path)}"
            )
        if str(chapter.get("proofFingerprint", "")) != str(
            reference.get("proofFingerprint", "")
        ):
            raise ValueError(
                f"hybrid chapter proof fingerprint mismatch for "
                f"{reference.get('theoremName', path)}"
            )
        yield chapter


def relativize_hybrid_manifest(
    manifest: dict[str, Any], *, manifest_dir: Path
) -> dict[str, Any]:
    """Make object references portable relative to the manifest location."""

    if not is_hybrid_manifest(manifest):
        return manifest
    rewritten: list[dict[str, Any]] = []
    for reference in manifest["chapterRefs"]:
        path = _resolve_object_path(reference, manifest_dir)
        rewritten.append(
            {
                **reference,
                "objectPath": Path(os.path.relpath(path, manifest_dir.resolve())).as_posix(),
            }
        )
    return {**manifest, "chapterRefs": rewritten}
