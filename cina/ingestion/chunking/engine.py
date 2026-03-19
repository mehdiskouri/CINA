from __future__ import annotations

import hashlib
from uuid import uuid4

import tiktoken

from cina.ingestion.chunking.config import ChunkConfig
from cina.ingestion.chunking.sentences import split_sentences
from cina.models.document import Chunk, Document, Section


class ChunkingEngine:
    def __init__(self, config: ChunkConfig) -> None:
        self.config = config
        self.encoding = tiktoken.get_encoding(config.tokenizer)

    def chunk_document(self, document: Document, embedding_model: str) -> list[Chunk]:
        chunks: list[Chunk] = []
        for section in sorted(document.sections, key=lambda item: item.order):
            chunks.extend(self._chunk_section(document, section, embedding_model=embedding_model))
        return chunks

    def _chunk_section(
        self, document: Document, section: Section, embedding_model: str
    ) -> list[Chunk]:
        tokens = self._count_tokens(section.content)
        if tokens <= self.config.max_chunk_tokens:
            return [self._build_chunk(document, section, section.content, 0, 0, embedding_model)]

        if self.config.sentence_boundary_alignment:
            return self._chunk_by_sentences(document, section, embedding_model=embedding_model)

        return self._chunk_by_token_window(document, section, embedding_model=embedding_model)

    def _chunk_by_sentences(
        self,
        document: Document,
        section: Section,
        embedding_model: str,
    ) -> list[Chunk]:
        sentences = split_sentences(section.content)
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
                            document,
                            section,
                            text,
                            chunk_index,
                            overlap,
                            embedding_model,
                        )
                    )
                    chunk_index += 1
                    buffer = []
                    buffer_tokens = 0
                for token_window_index, text in enumerate(
                    self._token_windows(
                        sentence, self.config.max_chunk_tokens, self.config.overlap_tokens
                    )
                ):
                    chunks.append(
                        self._build_chunk(
                            document,
                            section,
                            text,
                            chunk_index + token_window_index,
                            self.config.overlap_tokens if token_window_index > 0 else 0,
                            embedding_model,
                        )
                    )
                chunk_index += max(1, len(chunks) - chunk_index)
                continue

            if buffer_tokens + sentence_tokens > self.config.max_chunk_tokens and buffer:
                text = " ".join(buffer).strip()
                overlap_sentences = self._overlap_sentences(buffer)
                overlap = self._estimate_overlap_tokens(overlap_sentences)
                chunks.append(
                    self._build_chunk(
                        document, section, text, chunk_index, overlap, embedding_model
                    )
                )
                chunk_index += 1
                buffer = overlap_sentences
                buffer_tokens = self._count_tokens(" ".join(buffer))

            buffer.append(sentence)
            buffer_tokens += sentence_tokens

        if buffer:
            text = " ".join(buffer).strip()
            chunks.append(
                self._build_chunk(document, section, text, chunk_index, 0, embedding_model)
            )

        return chunks

    def _chunk_by_token_window(
        self,
        document: Document,
        section: Section,
        embedding_model: str,
    ) -> list[Chunk]:
        windows = self._token_windows(
            section.content,
            max_tokens=self.config.max_chunk_tokens,
            overlap_tokens=self.config.overlap_tokens,
        )
        chunks: list[Chunk] = []
        for idx, text in enumerate(windows):
            chunks.append(
                self._build_chunk(
                    document,
                    section,
                    text,
                    idx,
                    self.config.overlap_tokens if idx > 0 else 0,
                    embedding_model,
                )
            )
        return chunks

    def _token_windows(self, text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
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
        if not sentences:
            return 0
        return min(self.config.overlap_tokens, self._count_tokens(" ".join(sentences)))

    def _count_tokens(self, text: str) -> int:
        return len(self.encoding.encode(text))

    def _build_chunk(
        self,
        document: Document,
        section: Section,
        content: str,
        chunk_index: int,
        overlap_tokens: int,
        embedding_model: str,
    ) -> Chunk:
        normalized = content.strip()
        content_hash = hashlib.sha256(f"{normalized}{embedding_model}".encode()).hexdigest()
        metadata: dict[str, object] = {
            "source": document.source,
            "source_id": document.source_id,
            "title": document.title,
            "authors": document.authors,
            "section_type": section.section_type,
            "section_heading": section.heading,
        }
        return Chunk(
            id=uuid4(),
            section_id=section.id,
            document_id=document.id,
            content=normalized,
            content_hash=content_hash,
            token_count=self._count_tokens(normalized),
            chunk_index=chunk_index,
            overlap_tokens=overlap_tokens,
            embedding_model=embedding_model,
            embedding_dim=512,
            metadata=metadata,
        )
