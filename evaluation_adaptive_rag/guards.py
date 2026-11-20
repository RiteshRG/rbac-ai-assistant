"""Hash repository and vector-store files to detect evaluation side effects."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Iterable


def sha256_file(path: Path) -> str:
    """Hash a file incrementally without loading its full contents into memory.

    Args:
        path: File to hash.
    Returns:
        The lowercase hexadecimal SHA-256 digest.
    Called by: ``source_hashes`` for tracked files and vector-store files.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tracked_paths(repo_root: Path) -> list[Path]:
    """List Git-tracked paths beneath a repository root.

    Args:
        repo_root: Git working-tree root.
    Returns:
        Tracked paths as absolute or root-relative ``Path`` objects rooted at
        ``repo_root``.
    Called by: ``source_hashes``.
    """
    output = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    ).stdout
    names = output.decode("utf-8").split("\0")
    return [repo_root / name for name in names if name]


def source_hashes(repo_root: Path) -> dict[str, str]:
    """Hash tracked source files and generated vector data outside the eval package.

    Args:
        repo_root: Repository root to inspect.
    Returns:
        A path-to-SHA-256 mapping sorted by repository-relative path.
    Called by: the CLI before and after snapshot or evaluation work.
    """
    hashes: dict[str, str] = {}
    eval_prefix = (repo_root / "evaluation_adaptive_rag").resolve()
    for path in tracked_paths(repo_root):
        resolved = path.resolve()
        if not path.is_file():
            continue
        if resolved == eval_prefix or eval_prefix in resolved.parents:
            continue
        hashes[path.relative_to(repo_root).as_posix()] = sha256_file(path)

    vector_db = repo_root / "vector_db"
    if vector_db.is_dir():
        for path in sorted(vector_db.rglob("*")):
            if path.is_file():
                hashes[path.relative_to(repo_root).as_posix()] = sha256_file(path)
    return dict(sorted(hashes.items()))
