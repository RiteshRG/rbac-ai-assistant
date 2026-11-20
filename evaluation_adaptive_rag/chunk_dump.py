"""Export the isolated vector collection as stable evaluation chunk records."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .dataset import stable_chunk_id, write_jsonl


def dump_collection(collection: Any, path: Path) -> list[dict]:
    """Serialize a Chroma collection's documents and metadata to JSONL.

    Args:
        collection: Chroma-compatible collection to export.
        path: Destination JSONL file.
    Returns:
        Exported chunk records with stable IDs.
    Called by: the ``chunk-dump`` and ``all`` CLI commands.
    """
    records = collection.get(include=["documents", "metadatas"])
    rows: list[dict] = []
    ids = records.get("ids", [])
    documents = records.get("documents", [])
    metadatas = records.get("metadatas", [])
    for index, (chunk_id, text, metadata) in enumerate(zip(ids, documents, metadatas)):
        metadata = metadata or {}
        source_file = str(metadata.get("source_file", ""))
        chunk_index = int(metadata.get("chunk_index", index))
        row = {
            "id": stable_chunk_id(source_file, chunk_index, text),
            "stored_id": chunk_id,
            "source_file": source_file,
            "access_group": str(metadata.get("access_group", "")),
            "document_title": str(metadata.get("document_title", "")),
            "section_path": str(metadata.get("section_path", "")),
            "chunk_index": chunk_index,
            "token_count": len(text.split()),
            "text": text,
        }
        rows.append(row)
    write_jsonl(path, rows)
    return rows
