from functools import lru_cache
import chromadb
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

from vector_service.settings import get_vector_settings


VECTOR_DB_PATH = get_vector_settings().chroma_persist_directory
COLLECTION_NAME = "finsolve_documents"


@lru_cache(maxsize=1)
def get_chroma_client() -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=str(VECTOR_DB_PATH))


@lru_cache(maxsize=1)
def get_document_collection() -> chromadb.Collection:
    # Chroma's ONNX preset uses the all-MiniLM-L6-v2 model.
    embedding_function = ONNXMiniLM_L6_V2()
    return get_chroma_client().get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=embedding_function,
        metadata={"hnsw:space": "cosine"},
    )
