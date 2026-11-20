"""Repeatable ingestion of approved Markdown files into the local Chroma collection."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import chromadb

from vector_service.chunking import (
    APPROVED_DOCUMENT_FOLDERS,
    DocumentChunk,
    make_document_chunks,
)


APPROVED_DEPARTMENTS = tuple(APPROVED_DOCUMENT_FOLDERS)
UPSERT_BATCH_SIZE = 100


@dataclass(frozen=True)
class IngestionSummary:
    """Counts returned after successfully updating approved Markdown sources."""

    documents: int
    chunks: int


def discover_markdown_files(data_root: Path) -> list[Path]:
    """Find Markdown files only inside approved department folders.

    The HR folder is deliberately not traversed, so its CSV and other files are
    never opened by this document-vector ingestion path.

    Args:
        data_root: Root containing approved department document folders.

    Returns:
        Sorted Markdown paths from each approved department folder.

    Called by: ``ingest_all_documents``.
    """
    files: list[Path] = []
    for department in APPROVED_DEPARTMENTS:
        department_folder = data_root / department
        if not department_folder.is_dir():
            raise FileNotFoundError(f"Approved document folder is missing: {department_folder}")
        files.extend(sorted(department_folder.glob("*.md")))

    if not files:
        raise FileNotFoundError(f"No approved Markdown files found under {data_root}")
    return files


def _stable_chunk_id(source_file: str, chunk: DocumentChunk) -> str:
    """Derive a deterministic ID from a source path and chunk contents.

    Args:
        source_file: Dataset-relative source path.
        chunk: Chunk whose index and text identify the record.

    Returns:
        A SHA-256 hexadecimal ID.

    Called by: ``ingest_one_document`` for each generated chunk.
    """
    identity = f"{source_file}\0{chunk.metadata['chunk_index']}\0{chunk.text}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _batches(
    items: list[tuple[str, DocumentChunk]],
    batch_size: int,
) -> Iterable[list[tuple[str, DocumentChunk]]]:
    """Yield consecutive fixed-size groups of chunk records.

    Args:
        items: Ordered chunk ID and content records.
        batch_size: Maximum number of records in each yielded group.

    Returns:
        An iterable of bounded record batches.

    Called by: ``ingest_one_document`` to bound Chroma upserts.
    """
    for start in range(0, len(items), batch_size):
        yield items[start : start + batch_size]


def ingest_one_document(
    source_path: Path,
    data_root: Path,
    collection: chromadb.Collection,
    count_tokens: Callable[[str], int],
    batch_size: int = UPSERT_BATCH_SIZE,
) -> int:
    """Chunk and upsert one approved document, then remove its obsolete chunks.

    The path and data root identify the Markdown source; `count_tokens` must use
    the collection's embedding tokenizer. The return value is the current chunk
    count. Upserts finish before stale IDs are deleted, so failures are surfaced
    without discarding the previous indexed version of the source.

    Args:
        source_path: Approved Markdown file to ingest.
        data_root: Root directory containing approved department folders.
        collection: Chroma collection to update.
        count_tokens: Callable that counts tokens using the embedding tokenizer.
        batch_size: Maximum chunks per upsert operation.

    Returns:
        The number of chunks currently indexed for this source.

    Called by: ``ingest_all_documents`` and direct ingestion API callers.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be greater than zero")

    relative_source = source_path.resolve().relative_to(data_root.resolve())
    department = relative_source.parent.name.casefold()
    if department not in APPROVED_DEPARTMENTS:
        raise ValueError(f"Source file is not in an approved department folder: {source_path}")
    if department not in APPROVED_DOCUMENT_FOLDERS:
        raise ValueError(f"Department folder has no access-group mapping: {source_path}")
    if relative_source.suffix.casefold() != ".md":
        raise ValueError(f"Only Markdown files may be ingested: {source_path}")

    # Read only the approved Markdown source, then create its heading-aware chunks.
    markdown = source_path.read_text(encoding="utf-8")
    chunks = make_document_chunks(markdown, source_path, data_root, count_tokens)
    records = [
        (_stable_chunk_id(relative_source.as_posix(), chunk), chunk)
        for chunk in chunks
    ]

    # Keep old IDs so only this source's stale chunks are removed after success.
    existing = collection.get(
        where={"source_file": relative_source.as_posix()},
        include=["metadatas"],
    )
    existing_ids = set(existing["ids"])
    current_ids = {chunk_id for chunk_id, _ in records}

    # Chroma applies the collection's configured embedding function on upsert.
    for batch in _batches(records, batch_size):
        collection.upsert(
            ids=[chunk_id for chunk_id, _ in batch],
            documents=[chunk.text for _, chunk in batch],
            metadatas=[chunk.metadata for _, chunk in batch],
        )

    # Do not delete the previous version until all new chunks were stored.
    stale_ids = existing_ids - current_ids
    if stale_ids:
        collection.delete(ids=sorted(stale_ids))
    return len(chunks)


def ingest_all_documents(
    data_root: Path,
    collection: chromadb.Collection,
    count_tokens: Callable[[str], int],
    batch_size: int = UPSERT_BATCH_SIZE,
) -> IngestionSummary:
    """Ingest approved Markdown files and return source and chunk totals.

    Args:
        data_root: Root directory containing approved department folders.
        collection: Chroma collection to update.
        count_tokens: Callable that counts tokens using the embedding tokenizer.
        batch_size: Maximum chunks per upsert operation.

    Returns:
        Counts of successfully processed Markdown files and chunks.

    Called by: ``vector_service.ingest_documents.main``.
    """
    files = discover_markdown_files(data_root)
    total_chunks = 0

    for source_path in files:
        total_chunks += ingest_one_document(
            source_path=source_path,
            data_root=data_root,
            collection=collection,
            count_tokens=count_tokens,
            batch_size=batch_size,
        )

    # Remove chunks for approved Markdown files that have since been deleted.
    # This cleanup runs only after every current document was ingested successfully.
    live_sources = {
        path.resolve().relative_to(data_root.resolve()).as_posix()
        for path in files
    }
    old_records = collection.get(
        where={
            "access_group": {
                "$in": [APPROVED_DOCUMENT_FOLDERS[name] for name in APPROVED_DEPARTMENTS]
            }
        },
        include=["metadatas"],
    )
    stale_ids = [
        chunk_id
        for chunk_id, metadata in zip(old_records["ids"], old_records["metadatas"])
        if metadata
        and str(metadata.get("source_file", "")).casefold().endswith(".md")
        and str(metadata.get("source_file", "")).split("/", 1)[0].casefold()
        in APPROVED_DEPARTMENTS
        and metadata.get("source_file") not in live_sources
    ]
    if stale_ids:
        collection.delete(ids=stale_ids)

    return IngestionSummary(documents=len(files), chunks=total_chunks)
