from __future__ import annotations

from uuid import UUID

import asyncpg

from cina.models.document import Document, Section


class DocumentRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def upsert_document(self, document: Document, ingestion_id: UUID) -> UUID:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO documents (
                    id,
                    source,
                    source_id,
                    title,
                    authors,
                    publication_date,
                    raw_metadata,
                    ingestion_id
                ) VALUES ($1,$2::source_type,$3,$4,$5::jsonb,$6,$7::jsonb,$8)
                ON CONFLICT (source, source_id)
                DO UPDATE SET
                    title = EXCLUDED.title,
                    authors = EXCLUDED.authors,
                    publication_date = EXCLUDED.publication_date,
                    raw_metadata = EXCLUDED.raw_metadata,
                    updated_at = now()
                RETURNING id
                """,
                document.id,
                document.source,
                document.source_id,
                document.title,
                __import__("json").dumps(document.authors),
                document.publication_date,
                __import__("json").dumps(document.raw_metadata),
                ingestion_id,
            )
            if row is None:
                raise RuntimeError("Failed to upsert document")
            return UUID(str(row["id"]))

    async def replace_sections(self, document_id: UUID, sections: list[Section]) -> int:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM sections WHERE document_id = $1", document_id)
                if not sections:
                    return 0
                await conn.executemany(
                    """
                    INSERT INTO sections (id, document_id, section_type, heading, content, "order")
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    [
                        (
                            section.id,
                            document_id,
                            section.section_type,
                            section.heading,
                            section.content,
                            section.order,
                        )
                        for section in sections
                    ],
                )
            return len(sections)

    async def get_document_by_source_id(
        self, source: str, source_id: str
    ) -> dict[str, object] | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, source, source_id, title FROM documents WHERE source = $1::source_type AND source_id = $2",
                source,
                source_id,
            )
            if row is None:
                return None
            return dict(row)
