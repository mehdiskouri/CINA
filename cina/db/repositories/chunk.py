from __future__ import annotations

import json
from collections.abc import Mapping
from uuid import UUID

import asyncpg

from cina.models.document import Chunk
from cina.models.search import SearchResult


def _metadata_to_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return {str(key): item for key, item in parsed.items()}
        return {}
    return {}


class ChunkRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def bulk_upsert(self, chunks: list[Chunk]) -> int:
        if not chunks:
            return 0
        async with self.pool.acquire() as conn, conn.transaction():
            inserted = 0
            for chunk in chunks:
                result = await conn.execute(
                    """
                    INSERT INTO chunks (
                        id,
                        section_id,
                        document_id,
                        content,
                        content_hash,
                        token_count,
                        chunk_index,
                        overlap_tokens,
                        embedding_model,
                        embedding_dim,
                        metadata
                    )
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb)
                    ON CONFLICT (content_hash, embedding_model) DO NOTHING
                    """,
                    chunk.id,
                    chunk.section_id,
                    chunk.document_id,
                    chunk.content,
                    chunk.content_hash,
                    chunk.token_count,
                    chunk.chunk_index,
                    chunk.overlap_tokens,
                    chunk.embedding_model,
                    chunk.embedding_dim,
                    json.dumps(chunk.metadata),
                )
                if result.endswith("1"):
                    inserted += 1
            return inserted

    async def update_embeddings(
        self,
        chunk_ids: list[str],
        embeddings: list[list[float]],
        *,
        embedding_model: str,
        embedding_dim: int,
    ) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            for chunk_id, embedding in zip(chunk_ids, embeddings, strict=True):
                vector_str = "[" + ",".join(f"{x:.8f}" for x in embedding) + "]"
                await conn.execute(
                    """
                    UPDATE chunks
                    SET embedding = $2::vector,
                        embedding_model = $3,
                        embedding_dim = $4
                    WHERE id = $1::uuid
                    """,
                    chunk_id,
                    vector_str,
                    embedding_model,
                    embedding_dim,
                )

    async def vector_search(self, embedding: list[float], top_k: int) -> list[SearchResult]:
        vector_str = "[" + ",".join(f"{x:.8f}" for x in embedding) + "]"
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, content, token_count, metadata, 1 - (embedding <=> $1::vector) AS score
                FROM chunks
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> $1::vector
                LIMIT $2
                """,
                vector_str,
                top_k,
            )
        return [
            SearchResult(
                chunk_id=row["id"],
                content=row["content"],
                token_count=row["token_count"],
                metadata=_metadata_to_dict(row["metadata"]),
                score=float(row["score"]),
            )
            for row in rows
        ]

    async def bm25_search(self, query: str, top_k: int) -> list[SearchResult]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, content, token_count, metadata,
                       ts_rank_cd(content_tsvector, plainto_tsquery('english', $1)) AS score
                FROM chunks
                WHERE content_tsvector @@ plainto_tsquery('english', $1)
                ORDER BY score DESC
                LIMIT $2
                """,
                query,
                top_k,
            )
        return [
            SearchResult(
                chunk_id=row["id"],
                content=row["content"],
                token_count=row["token_count"],
                metadata=_metadata_to_dict(row["metadata"]),
                score=float(row["score"]),
            )
            for row in rows
        ]

    async def get_by_ids(self, chunk_ids: list[UUID]) -> list[Chunk]:
        if not chunk_ids:
            return []
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, section_id, document_id, content, content_hash, token_count,
                       chunk_index, overlap_tokens, embedding_model, embedding_dim, metadata
                FROM chunks
                WHERE id = ANY($1::uuid[])
                """,
                chunk_ids,
            )
        return [
            Chunk(
                id=row["id"],
                section_id=row["section_id"],
                document_id=row["document_id"],
                content=row["content"],
                content_hash=row["content_hash"],
                token_count=row["token_count"],
                chunk_index=row["chunk_index"],
                overlap_tokens=row["overlap_tokens"],
                embedding_model=row["embedding_model"],
                embedding_dim=row["embedding_dim"],
                metadata=_metadata_to_dict(row["metadata"]),
            )
            for row in rows
        ]

    async def get_unembedded_by_hashes(
        self,
        *,
        embedding_model: str,
        content_hashes: list[str],
    ) -> list[dict[str, object]]:
        if not content_hashes:
            return []
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, content, content_hash
                FROM chunks
                WHERE embedding_model = $1
                  AND embedding IS NULL
                  AND content_hash = ANY($2::text[])
                """,
                embedding_model,
                content_hashes,
            )
        return [
            {
                "id": str(row["id"]),
                "content": row["content"],
                "content_hash": row["content_hash"],
            }
            for row in rows
        ]
