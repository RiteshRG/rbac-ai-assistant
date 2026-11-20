"""Split approved Markdown documents into Chroma-ready, access-labeled chunks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


TARGET_CHUNK_TOKENS = 190
MAX_CHUNK_TOKENS = 220
CHUNK_OVERLAP_TOKENS = 30
EMBEDDING_TOKEN_LIMIT = 256

DEPARTMENT_FOLDERS = {
    "engineering": "Engineering",
    "finance": "Finance",
    "general": "General",
    "hr": "HR",
    "marketing": "Marketing",
}
APPROVED_DOCUMENT_FOLDERS = {
    name: group for name, group in DEPARTMENT_FOLDERS.items() if name != "hr"
}

_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_DIVIDER_PATTERN = re.compile(r"^\s*[-*_]{3,}\s*$")
_FENCE_PATTERN = re.compile(r"^\s*(```+|~~~+)")
_SENTENCE_PATTERN = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class DocumentChunk:
    """One indexed text chunk, with the metadata required for access and citations."""

    text: str
    metadata: dict[str, str | int]


@dataclass(frozen=True)
class _Section:
    """A Markdown heading path and the body that belongs to it."""

    heading_path: str
    body: str


class ModelTokenCounter:
    """Count tokens with Chroma's tokenizer without its embedding-time truncation."""

    def __init__(self) -> None:
        """Load the tokenizer used to count complete embedding inputs.

        Args:
            None.

        Returns:
            None.

        Called by: ``vector_service.ingest_documents.main`` before ingestion.
        """
        # Clone Chroma's tokenizer so counting is accurate beyond its 256-token limit.
        from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
        from tokenizers import Tokenizer

        embedding_function = ONNXMiniLM_L6_V2()
        # Chroma downloads its bundled tokenizer/model files on first embedding use.
        embedding_function._download_model_if_not_exists()
        self._tokenizer = Tokenizer.from_str(embedding_function.tokenizer.to_str())
        self._tokenizer.no_truncation()
        self._tokenizer.no_padding()

    def __call__(self, text: str) -> int:
        """Count model tokens, including special tokens, in the supplied text.

        Args:
            text: Text to encode with the embedding model's tokenizer.

        Returns:
            The number of encoded token IDs.

        Called by: Chunking helpers through the callable passed to
            ``make_document_chunks``.
        """
        return len(self._tokenizer.encode(text).ids)


def _document_title(markdown: str, file_path: Path) -> str:
    """Find the first H1 title or derive a readable title from the filename.

    Args:
        markdown: Source Markdown text.
        file_path: Path used for the filename fallback.

    Returns:
        The trimmed H1 text, or a title-cased filename stem.

    Called by: ``make_document_chunks``.
    """
    for line in markdown.splitlines():
        match = _HEADING_PATTERN.match(line)
        if match and len(match.group(1)) == 1:
            return match.group(2).strip()
    return file_path.stem.replace("_", " ").strip().title()


def _parse_sections(markdown: str, document_title: str) -> list[_Section]:
    """Group Markdown body text by its heading path.

    Args:
        markdown: Source Markdown text.
        document_title: Document title used as the root of each section path.

    Returns:
        Ordered nonempty sections with their heading paths and body text.

    Called by: ``make_document_chunks``.
    """
    headings: list[tuple[int, str]] = [(1, document_title)]
    sections: list[_Section] = []
    body_lines: list[str] = []

    def save_current_section() -> None:
        """Append the current nonempty body under its active heading path.

        Args:
            None.

        Returns:
            None.

        Called by: ``_parse_sections`` when a heading changes or parsing ends.
        """
        body = "\n".join(body_lines).strip()
        if body:
            heading_path = " > ".join(title for _, title in headings)
            sections.append(_Section(heading_path, body))
        body_lines.clear()

    lines = markdown.splitlines()
    line_index = 0

    while line_index < len(lines):
        line = lines[line_index]
        heading_match = _HEADING_PATTERN.match(line)

        if heading_match:
            save_current_section()
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()

            if level == 1:
                # The H1 is already the document title; keep it once in the path.
                headings = [(1, document_title)]
            else:
                headings = [item for item in headings if item[0] < level]
                headings.append((level, heading_text))

            line_index += 1
            continue

        # Annual summaries use labels followed by dashed rules instead of headings.
        if (
            line.strip().endswith(":")
            and line_index + 1 < len(lines)
            and _DIVIDER_PATTERN.match(lines[line_index + 1])
        ):
            save_current_section()
            section_title = line.strip().rstrip(":").strip()
            headings = [item for item in headings if item[0] < 2]
            headings.append((2, section_title))
            line_index += 2
            continue

        body_lines.append(line)
        line_index += 1

    save_current_section()
    return sections


def _split_into_blocks(section_body: str) -> list[str]:
    """Separate paragraphs while preserving fenced code as a single block.

    Args:
        section_body: Text body of one parsed section.

    Returns:
        Nonempty paragraph or fenced-code blocks in source order.

    Called by: ``_chunk_section``.
    """
    blocks: list[str] = []
    current_lines: list[str] = []
    inside_fence = False

    for line in section_body.splitlines():
        if not line.strip() and not inside_fence:
            if current_lines:
                blocks.append("\n".join(current_lines).strip())
                current_lines.clear()
            continue

        current_lines.append(line)
        if _FENCE_PATTERN.match(line):
            inside_fence = not inside_fence

    if current_lines:
        blocks.append("\n".join(current_lines).strip())
    return blocks


def _split_long_text(text: str, max_tokens: int, count_tokens: Callable[[str], int]) -> list[str]:
    """Split oversized text first at sentence and then word boundaries.

    Args:
        text: Text to split.
        max_tokens: Maximum token count for each returned piece.
        count_tokens: Callable that counts tokens in a text string.

    Returns:
        Ordered text pieces that fit the token budget.

    Called by: ``_split_table`` and ``_split_block``.
    """
    pieces: list[str] = []
    current = ""

    for sentence in _SENTENCE_PATTERN.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue

        candidate = f"{current} {sentence}".strip()
        if count_tokens(candidate) <= max_tokens:
            current = candidate
            continue

        if current:
            pieces.append(current)
            current = ""

        if count_tokens(sentence) <= max_tokens:
            current = sentence
            continue

        # The final fallback handles a single sentence or list item that is huge.
        for word in sentence.split():
            if count_tokens(word) > max_tokens:
                raise ValueError("A single word exceeds the chunking token budget.")
            candidate = f"{current} {word}".strip()
            if current and count_tokens(candidate) > max_tokens:
                pieces.append(current)
                current = word
            else:
                current = candidate

    if current:
        pieces.append(current)
    return pieces


def _split_table(block: str, max_tokens: int, count_tokens: Callable[[str], int]) -> list[str]:
    """Split long Markdown tables by rows and repeat their header.

    Args:
        block: Markdown table text.
        max_tokens: Maximum token count for each returned table piece.
        count_tokens: Callable that counts tokens in a text string.

    Returns:
        Ordered table pieces, or general text pieces for non-table input.

    Called by: ``_split_block``.
    """
    lines = block.splitlines()
    if len(lines) < 3 or not all(line.lstrip().startswith("|") for line in lines):
        return _split_long_text(block, max_tokens, count_tokens)

    header = lines[:2]
    if count_tokens("\n".join(header)) > max_tokens:
        return _split_long_text(block, max_tokens, count_tokens)

    pieces: list[str] = []
    rows = header.copy()

    for row in lines[2:]:
        candidate = "\n".join([*rows, row])
        if count_tokens(candidate) > max_tokens and len(rows) > 2:
            pieces.append("\n".join(rows))
            rows = header.copy()
            candidate = "\n".join([*rows, row])

        if count_tokens(candidate) > max_tokens:
            row_budget = max_tokens - count_tokens("\n".join(header))
            if row_budget < 1:
                raise ValueError("Markdown table header is too large to split safely.")
            row_pieces = _split_long_text(row, row_budget, count_tokens)
            pieces.extend("\n".join([*header, piece]) for piece in row_pieces)
        else:
            rows.append(row)

    if len(rows) > 2:
        pieces.append("\n".join(rows))
    return pieces


def _split_block(block: str, max_tokens: int, count_tokens: Callable[[str], int]) -> list[str]:
    """Preserve a block when possible and otherwise split at safe boundaries.

    Args:
        block: Paragraph, table, list, or fenced block to split.
        max_tokens: Maximum token count for each returned piece.
        count_tokens: Callable that counts tokens in a text string.

    Returns:
        Ordered pieces within the token budget while preserving structure where
        possible.

    Called by: ``_chunk_section``.
    """
    if count_tokens(block) <= max_tokens:
        return [block]

    lines = block.splitlines()
    if lines and all(line.lstrip().startswith("|") for line in lines):
        return _split_table(block, max_tokens, count_tokens)

    fence_match = _FENCE_PATTERN.match(lines[0]) if lines else None
    if fence_match and len(lines) >= 2 and _FENCE_PATTERN.match(lines[-1]):
        # Preserve code/diagram fences when an unusually large block must be split.
        opening_fence, closing_fence = lines[0], lines[-1]
        interior = "\n".join(lines[1:-1])
        inner_budget = max_tokens - count_tokens(f"{opening_fence}\n{closing_fence}")
        inner_pieces = _split_long_text(interior, max(inner_budget, 1), count_tokens)
        return [f"{opening_fence}\n{piece}\n{closing_fence}" for piece in inner_pieces]

    # Lists often contain meaningful item boundaries, so prefer splitting by line.
    pieces: list[str] = []
    for line in lines:
        if count_tokens(line) <= max_tokens:
            pieces.append(line)
        else:
            pieces.extend(_split_long_text(line, max_tokens, count_tokens))
    return pieces


def _last_tokens(text: str, token_count: int, count_tokens: Callable[[str], int]) -> str:
    """Select a word-boundary suffix near the requested overlap size.

    Args:
        text: Source text to take the suffix from.
        token_count: Desired maximum overlap size in tokens.
        count_tokens: Callable that counts tokens in a text string.

    Returns:
        The trailing words selected for overlap.

    Called by: ``_chunk_section`` when adding non-table chunk overlap.
    """
    suffix: list[str] = []
    for word in reversed(text.split()):
        candidate = " ".join([word, *suffix])
        if count_tokens(candidate) > token_count and suffix:
            break
        suffix.insert(0, word)
    return " ".join(suffix)


def _chunk_section(
    section: _Section,
    document_title: str,
    count_tokens: Callable[[str], int],
) -> list[str]:
    """Build bounded, heading-prefixed chunks from one coherent section.

    Args:
        section: Parsed section to convert to chunks.
        document_title: Document title included in each chunk prefix.
        count_tokens: Callable that counts tokens in a text string.

    Returns:
        Ordered chunks that fit the chunk and embedding token limits.

    Called by: ``make_document_chunks`` for each parsed section.
    """
    heading_prefix = f"Document: {document_title}\nSection: {section.heading_path}\n\n"
    content_budget = TARGET_CHUNK_TOKENS - count_tokens(heading_prefix)
    if content_budget < 1:
        raise ValueError(f"Heading path is too long to embed: {section.heading_path}")

    body_chunks: list[str] = []
    current_lines: list[str] = []

    def save_current_chunk() -> None:
        """Append the accumulated nonempty text as a section body chunk.

        Args:
            None.

        Returns:
            None.

        Called by: ``_chunk_section`` when the token budget is reached and at
            the end of the section.
        """
        body = "\n\n".join(current_lines).strip()
        if body:
            body_chunks.append(body)
        current_lines.clear()

    for block in _split_into_blocks(section.body):
        for piece in _split_block(block, content_budget, count_tokens):
            candidate = "\n\n".join([*current_lines, piece]).strip()
            if current_lines and count_tokens(candidate) > content_budget:
                save_current_chunk()
            current_lines.append(piece)
    save_current_chunk()

    final_chunks: list[str] = []
    for index, body in enumerate(body_chunks):
        overlap = ""
        # Table pieces already repeat their header; token overlap would break rows.
        has_table = any(line.lstrip().startswith("|") for line in body.splitlines())
        previous_has_table = index and any(
            line.lstrip().startswith("|")
            for line in body_chunks[index - 1].splitlines()
        )
        if index and not has_table and not previous_has_table:
            previous_size = count_tokens(body_chunks[index - 1])
            overlap = _last_tokens(
                body_chunks[index - 1],
                min(CHUNK_OVERLAP_TOKENS, previous_size),
                count_tokens,
            )

        body_with_overlap = f"{overlap}\n\n{body}".strip() if overlap else body
        text = f"{heading_prefix}{body_with_overlap}"
        if count_tokens(text) > MAX_CHUNK_TOKENS:
            raise ValueError(
                f"Generated chunk exceeds {MAX_CHUNK_TOKENS} tokens "
                f"in section {section.heading_path!r}"
            )
        final_chunks.append(text)
    return final_chunks


def make_document_chunks(
    markdown: str,
    source_path: Path,
    data_root: Path,
    count_tokens: Callable[[str], int],
) -> list[DocumentChunk]:
    """Parse one approved Markdown source and return chunks with required metadata.

    Inputs are the source text, its path, the approved dataset root, and a tokenizer
    compatible with the embedding model. Output is an ordered list of bounded,
    heading-aware chunks. The split happens at headings first, then paragraphs,
    list/table rows, sentences, and words as a last resort.

    Args:
        markdown: Markdown source text to parse.
        source_path: Path to the source file, used for access group and citation
            metadata.
        data_root: Root directory under which approved department folders reside.
        count_tokens: Callable compatible with the collection's embedding model.

    Returns:
        Ordered chunks containing text and required retrieval metadata.

    Called by: ``vector_service.ingestion.ingest_one_document``.
    """
    try:
        relative_source = source_path.resolve().relative_to(data_root.resolve())
    except ValueError as exc:
        raise ValueError(f"Source file is outside the approved data root: {source_path}") from exc

    # Access is assigned only from the file's immediate department folder.
    access_group = APPROVED_DOCUMENT_FOLDERS.get(relative_source.parent.name.casefold())
    if access_group is None:
        raise ValueError(f"Source file is not in an approved department folder: {source_path}")

    document_title = _document_title(markdown, source_path)
    sections = _parse_sections(markdown, document_title)
    chunks: list[DocumentChunk] = []

    for section in sections:
        for text in _chunk_section(section, document_title, count_tokens):
            # Validate the complete embedded input, not only its body text.
            if count_tokens(text) >= EMBEDDING_TOKEN_LIMIT:
                raise ValueError(
                    f"Chunk for {relative_source.as_posix()} is not below the "
                    f"{EMBEDDING_TOKEN_LIMIT}-token embedding limit"
                )
            chunks.append(
                DocumentChunk(
                    text=text,
                    metadata={
                        "access_group": access_group,
                        "source_file": relative_source.as_posix(),
                        "document_title": document_title,
                        "section_path": section.heading_path,
                        "chunk_index": len(chunks),
                    },
                )
            )

    if not chunks:
        raise ValueError(f"Markdown source has no indexable content: {source_path}")
    return chunks
