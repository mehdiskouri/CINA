"""Chunking engine for section-aware and token-window segmentation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

import tiktoken

from cina.ingestion.chunking.sentences import split_sentences
from cina.models.document import Chunk, Document, Section

if TYPE_CHECKING:
    from cina.ingestion.chunking.config import ChunkConfig


_CURRENT_BUILD_CHUNK_ARGS = 4
_LEGACY_BUILD_CHUNK_ARGS = 6


def _to_int(value: object) -> int:
    """Coerce arbitrary legacy values into integer form."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        return int(value)
    return int(str(value))


@dataclass(frozen=True, slots=True)
class _ChunkTarget:
    """Context bundle used when materializing chunk records."""

    document: Document
    section: Section
    embedding_model: str


class ChunkingEngine:
    """Document chunking implementation with sentence and token strategies."""

    def __init__(self, config: ChunkConfig) -> None:
        """Initialize chunking engine with tokenization config."""
        self.config = config
        self.encoding = tiktoken.get_encoding(config.tokenizer)

    def chunk_document(self, document: Document, embedding_model: str) -> list[Chunk]:
        """Chunk all sections in one document."""
        chunks: list[Chunk] = []
        for section in sorted(document.sections, key=lambda item: item.order):
            target = _ChunkTarget(
                document=document,
                section=section,
                embedding_model=embedding_model,
            )
            chunks.extend(self._chunk_section(target))
        return chunks

    def _chunk_section(self, target: _ChunkTarget) -> list[Chunk]:
        """Chunk one section using configured strategy."""
        tokens = self._count_tokens(target.section.content)
        if tokens <= self.config.max_chunk_tokens:
            return [self._build_chunk(target, target.section.content, 0, 0)]

        if self.config.sentence_boundary_alignment:
            return self._chunk_by_sentences(target)

        return self._chunk_by_token_window(target)

    def _chunk_by_sentences(
        self,
        target: _ChunkTarget,
    ) -> list[Chunk]:
        """Chunk a section by accumulating sentence spans."""
        sentences = split_sentences(target.section.content)
        if not sentences:
            return []

        chunks: list[Chunk] = []
        buffer: list[str] = []
        buffer_tokens = 0
        chunk_index = 0

        for sentence in sentences:
            sentence_tokens = self._count_tokens(sentence)
            if sentence_tokens >= self.config.max_chunk_tokens:
                if buffer:
                    text = " ".join(buffer).strip()
                    overlap = self._estimate_overlap_tokens(buffer)
                    chunks.append(
                        self._build_chunk(
                            target,
                            text,
                            chunk_index,
                            overlap,
                        ),
                    )
                    chunk_index += 1
                    buffer = []
                    buffer_tokens = 0
                for token_window_index, text in enumerate(
                    self._token_windows(
                        sentence,
                        self.config.max_chunk_tokens,
                        self.config.overlap_tokens,
                    ),
                ):
                    chunks.append(
                        self._build_chunk(
                            target,
                            text,
                            chunk_index + token_window_index,
                            self.config.overlap_tokens if token_window_index > 0 else 0,
                        ),
                    )
                chunk_index += max(1, len(chunks) - chunk_index)
                continue

            if buffer_tokens + sentence_tokens > self.config.max_chunk_tokens and buffer:
                text = " ".join(buffer).strip()
                overlap_sentences = self._overlap_sentences(buffer)
                overlap = self._estimate_overlap_tokens(overlap_sentences)
                chunks.append(
                    self._build_chunk(
                        target,
                        text,
                        chunk_index,
                        overlap,
                    ),
                )
                chunk_index += 1
                buffer = overlap_sentences
                buffer_tokens = self._count_tokens(" ".join(buffer))

            buffer.append(sentence)
            buffer_tokens += sentence_tokens

        if buffer:
            text = " ".join(buffer).strip()
            chunks.append(
                self._build_chunk(target, text, chunk_index, 0),
            )

        return chunks

    def _chunk_by_token_window(
        self,
        target: _ChunkTarget,
    ) -> list[Chunk]:
        """Chunk a section by fixed-size token windows."""
        windows = self._token_windows(
            target.section.content,
            max_tokens=self.config.max_chunk_tokens,
            overlap_tokens=self.config.overlap_tokens,
        )
        chunks: list[Chunk] = []
        for idx, text in enumerate(windows):
            chunks.append(
                self._build_chunk(
                    target,
                    text,
                    idx,
                    self.config.overlap_tokens if idx > 0 else 0,
                ),
            )
        return chunks

    def _token_windows(self, text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
        """Split text into overlapping token windows."""
        token_ids = self.encoding.encode(text)
        if not token_ids:
            return []

        windows: list[str] = []
        step = max(1, max_tokens - overlap_tokens)
        for start in range(0, len(token_ids), step):
            end = start + max_tokens
            segment = token_ids[start:end]
            if not segment:
                break
            windows.append(self.encoding.decode(segment).strip())
            if end >= len(token_ids):
                break
        return windows

    def _overlap_sentences(self, sentences: list[str]) -> list[str]:
        """Select trailing sentences that fit overlap-token budget."""
        selected: list[str] = []
        token_budget = 0
        for sentence in reversed(sentences):
            sentence_tokens = self._count_tokens(sentence)
            if token_budget + sentence_tokens > self.config.overlap_tokens:
                break
            selected.insert(0, sentence)
            token_budget += sentence_tokens
        return selected

    def _estimate_overlap_tokens(self, sentences: list[str]) -> int:
        """Estimate token overlap for sentence slice."""
        if not sentences:
            return 0
        return min(self.config.overlap_tokens, self._count_tokens(" ".join(sentences)))

    def _count_tokens(self, text: str) -> int:
        """Count tokens with configured tokenizer."""
        return len(self.encoding.encode(text))

    def _build_chunk(self, *args: object) -> Chunk:
        """Create a `Chunk` model from normalized content and context.

        Supports both the current internal signature and the legacy test-facing
        signature used by older unit tests.
        """
        if len(args) == _CURRENT_BUILD_CHUNK_ARGS and isinstance(args[0], _ChunkTarget):
            target = args[0]
            content = str(args[1])
            chunk_index = _to_int(args[2])
            overlap_tokens = _to_int(args[3])
            return self._build_chunk_from_target(target, content, chunk_index, overlap_tokens)

        if (
            len(args) == _LEGACY_BUILD_CHUNK_ARGS
            and isinstance(args[0], Document)
            and isinstance(args[1], Section)
            and isinstance(args[5], str)
        ):
            target = _ChunkTarget(document=args[0], section=args[1], embedding_model=args[5])
            content = str(args[2])
            chunk_index = _to_int(args[3])
            overlap_tokens = _to_int(args[4])
            return self._build_chunk_from_target(target, content, chunk_index, overlap_tokens)

        message = "Unsupported _build_chunk call signature"
        raise TypeError(message)

    def _build_chunk_from_target(
        self,
        target: _ChunkTarget,
        content: str,
        chunk_index: int,
        overlap_tokens: int,
    ) -> Chunk:
        """Build chunk from normalized target and index metadata."""
        normalized = content.strip()
        content_hash = hashlib.sha256(f"{normalized}{target.embedding_model}".encode()).hexdigest()
        metadata: dict[str, object] = {
            "source": target.document.source,
            "source_id": target.document.source_id,
            "title": target.document.title,
            "authors": target.document.authors,
            "section_type": target.section.section_type,
            "section_heading": target.section.heading,
        }
        return Chunk(
            id=uuid4(),
            section_id=target.section.id,
            document_id=target.document.id,
            content=normalized,
            content_hash=content_hash,
            token_count=self._count_tokens(normalized),
            chunk_index=chunk_index,
            overlap_tokens=overlap_tokens,
            embedding_model=target.embedding_model,
            embedding_dim=512,
            metadata=metadata,
        )
