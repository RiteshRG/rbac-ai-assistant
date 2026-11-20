"""Cached Chroma client and collection access for local document storage."""

from functools import lru_cache
import chromadb
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

from vector_service.settings import get_vector_settings


VECTOR_DB_PATH = get_vector_settings().chroma_persist_directory
COLLECTION_NAME = "finsolve_documents"


@lru_cache(maxsize=1)
def get_chroma_client() -> chromadb.ClientAPI:
    """Return the process-cached persistent Chroma client.

    Args:
        None.

    Returns:
        A persistent client rooted at the configured local database directory.

    Called by: ``get_document_collection``.
    """
    return chromadb.PersistentClient(path=str(VECTOR_DB_PATH))


@lru_cache(maxsize=1)
def get_document_collection() -> chromadb.Collection:
    """Return the configured document collection, creating it when needed.

    Args:
        None.

    Returns:
        The persistent ``finsolve_documents`` collection with cosine distance
        and the configured ONNX embedding function.

    Called by: The API, ingestion CLI, developer search utility, and local
        application adapter.
    """
    # Chroma's ONNX preset uses the all-MiniLM-L6-v2 model.
    embedding_function = ONNXMiniLM_L6_V2()
    return get_chroma_client().get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=embedding_function,
        metadata={"hnsw:space": "cosine"},
    )
