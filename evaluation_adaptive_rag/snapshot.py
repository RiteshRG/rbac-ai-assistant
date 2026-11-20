"""Copy and open an isolated local Chroma snapshot for evaluation runs."""

from __future__ import annotations

import shutil
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2


def copy_vector_snapshot(repo_root: Path, eval_root: Path) -> tuple[Path, Path]:
    """Replace the evaluation snapshot with a copy of the repository vector store.

    Args:
        repo_root: Repository root containing ``vector_db``.
        eval_root: Evaluation root that receives the isolated snapshot.
    Returns:
        The source vector-store path and copied snapshot path.
    Called by: the ``chunk-dump``, ``run``, and ``all`` CLI commands.
    """
    source = repo_root / "vector_db"
    snapshot_parent = eval_root / "_snapshot"
    snapshot = snapshot_parent / "vector_db"
    if not source.is_dir():
        raise FileNotFoundError("The deployed vector_db snapshot does not exist.")
    if snapshot.exists():
        shutil.rmtree(snapshot)
    snapshot_parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, snapshot)
    return source, snapshot


def open_snapshot_collection(snapshot: Path, collection_name: str):
    """Open a named collection in a persistent Chroma snapshot.

    Args:
        snapshot: Path to the copied Chroma database.
        collection_name: Collection to retrieve from the snapshot.
    Returns:
        The Chroma collection configured with the project embedding function.
    Called by: the ``chunk-dump``, ``run``, and ``all`` CLI commands.
    """
    client = chromadb.PersistentClient(path=str(snapshot))
    return client.get_collection(
        name=collection_name,
        embedding_function=ONNXMiniLM_L6_V2(),
    )
