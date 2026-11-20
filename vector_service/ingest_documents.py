"""Command-line entry point for approved local Markdown document ingestion."""

from pathlib import Path

from vector_service.chunking import ModelTokenCounter
from vector_service.chroma_client import get_document_collection
from vector_service.ingestion import ingest_all_documents


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = PROJECT_ROOT / "DS-RPC-01" / "data"


def main() -> None:
    """Load approved Markdown documents into the persistent Chroma collection.

    Args:
        None.

    Returns:
        None.

    Called by: ``python -m vector_service.ingest_documents`` or direct CLI use.
    """
    collection = get_document_collection()
    token_counter = ModelTokenCounter()

    # Discovery inside ingestion reads Markdown only from Engineering/Finance/
    # General/Marketing; it never opens or embeds the HR folder.
    summary = ingest_all_documents(
        data_root=DATA_ROOT,
        collection=collection,
        count_tokens=token_counter,
    )
    print(
        f"Ingested {summary.chunks} chunks from {summary.documents} Markdown documents "
        f"into collection {collection.name!r}."
    )


if __name__ == "__main__":
    main()
