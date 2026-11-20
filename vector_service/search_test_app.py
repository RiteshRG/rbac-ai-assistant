"""Simple developer-only console for testing semantic search in ChromaDB.

This utility has no login and searches every department. Use it only on your
local development machine; application routes must use profile-backed retrieval.
"""

from __future__ import annotations

from typing import Sequence

from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_chroma import Chroma

from vector_service.chroma_client import COLLECTION_NAME, get_chroma_client
from vector_service.retrieval import ALL_ACCESS_GROUPS


class ChromaMiniLMEmbeddings(Embeddings):
    """Adapt Chroma's all-MiniLM-L6-v2 model to LangChain's embedding interface."""

    def __init__(self) -> None:
        """Create the embedding adapter using Chroma's configured ONNX model.

        Args:
            None.

        Returns:
            None.

        Called by: ``main`` when constructing the LangChain Chroma store.
        """
        # Use the same ONNX model that created the vectors already stored in Chroma.
        self._model = ONNXMiniLM_L6_V2()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Convert document texts into vectors for LangChain.

        Args:
            texts: Document strings to embed.

        Returns:
            One numeric embedding vector per input document.

        Called by: LangChain when it requests document embeddings.
        """
        vectors = self._model(texts)
        return [vector.tolist() for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        """Convert one search question into the stored-vector embedding space.

        Args:
            text: Search question to embed.

        Returns:
            The numeric embedding vector for the question.

        Called by: LangChain when executing semantic similarity search.
        """
        vector = self._model([text])[0]
        return vector.tolist()


def ask_for_result_count() -> int:
    """Prompt for and validate the number of nearest chunks to display.

    Args:
        None.

    Returns:
        An integer result count from 1 through 20.

    Called by: ``main`` in the developer-only search utility.
    """
    while True:
        answer = input("How many similar results should I show? (1-20): ").strip()
        try:
            result_count = int(answer)
        except ValueError:
            print("Please enter a whole number from 1 to 20.")
            continue

        if 1 <= result_count <= 20:
            return result_count
        print("Please enter a whole number from 1 to 20.")


def print_results(results: Sequence[tuple[Document, float]]) -> None:
    """Print matching chunks with distance and citation metadata.

    Args:
        results: Ordered document and distance pairs returned by LangChain.

    Returns:
        None.

    Called by: ``main`` after the similarity search.
    """
    if not results:
        print("\nNo matching documents were found.")
        return

    for number, (document, distance) in enumerate(results, start=1):
        metadata = document.metadata
        print(
            f"\n--- Result {number} "
            f"(distance: {distance:.4f}; lower is more similar) ---"
        )
        print(f"Department: {metadata.get('access_group', 'Unknown')}")
        print(f"Source: {metadata.get('source_file', 'Unknown')}")
        print(f"Section: {metadata.get('section_path', 'Unknown')}")
        print("\nContent:")
        print(document.page_content)


def main() -> None:
    """Run an interactive, all-department local Chroma search.

    Args:
        None.

    Returns:
        None.

    Called by: ``python -m vector_service.search_test_app`` or direct CLI use.
    """
    print("Local ChromaDB search test (developer-only; no user login or RBAC).")
    query = input("\nEnter your search question: ").strip()
    if not query:
        print("Search question cannot be empty.")
        return

    result_count = ask_for_result_count()

    # Attach LangChain to the existing persistent collection without creating a
    # second database or adding any new documents.
    vector_store = Chroma(
        client=get_chroma_client(),
        collection_name=COLLECTION_NAME,
        embedding_function=ChromaMiniLMEmbeddings(),
    )

    # This local test intentionally searches every department. The normal app must
    # use DocumentSearchService, which derives the filter from the trusted profile.
    results = vector_store.similarity_search_with_score(
        query=query,
        k=result_count,
        filter={"access_group": {"$in": list(ALL_ACCESS_GROUPS)}},
    )
    print_results(results)


if __name__ == "__main__":
    main()
