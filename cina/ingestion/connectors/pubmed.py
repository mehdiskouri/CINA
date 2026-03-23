"""PubMed XML connector for document discovery and parsing."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from lxml import etree  # type: ignore[import-untyped]

from cina.ingestion.connectors.protocol import FetchConfig, RawDocument
from cina.models.document import Document, Section

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class PubMedConnector:
    """Connector for PubMed Open Access XML exports."""

    source_type = "pubmed"

    async def fetch_document_list(self, config: FetchConfig) -> AsyncIterator[RawDocument]:
        """Yield raw XML documents from source path."""
        source_path = config.source_path or Path("data/pubmed")
        count = 0
        for file_path in sorted(source_path.glob(config.glob_pattern or "*.xml")):
            if not file_path.is_file():
                continue
            yield RawDocument(
                source_id=file_path.stem,
                payload=file_path.read_text(encoding="utf-8", errors="ignore"),
                metadata={"path": str(file_path)},
            )
            count += 1
            if config.limit is not None and count >= config.limit:
                break

    def parse(self, raw: RawDocument) -> Document:
        """Parse one PubMed XML payload into a normalized document."""
        parser = etree.XMLParser(recover=True, remove_comments=True)
        root = etree.fromstring(raw.payload.encode("utf-8"), parser=parser)

        article_title = _first_text(root, ".//article-title") or raw.source_id
        authors = _extract_authors(root)
        publication_date = _extract_pub_date(root)
        pmcid = _first_text(root, ".//article-id[@pub-id-type='pmcid']") or raw.source_id

        document_id = uuid4()
        sections = _extract_sections(root, document_id=document_id)
        sections.extend(_extract_caption_sections(root, document_id=document_id))

        return Document(
            id=document_id,
            source=self.source_type,
            source_id=pmcid,
            title=article_title,
            authors=authors,
            publication_date=publication_date,
            raw_metadata={"raw_source_id": raw.source_id, **raw.metadata},
            sections=sections,
        )


def _first_text(root: etree._Element, xpath: str) -> str | None:
    """Extract and normalize text for the first node matching XPath."""
    node = root.find(xpath)
    if node is None:
        return None
    text = " ".join(node.itertext()).strip()
    return text or None


def _extract_authors(root: etree._Element) -> list[str]:
    """Extract author display names from an article payload."""
    authors: list[str] = []
    for contrib in root.findall(".//contrib[@contrib-type='author']"):
        given = _first_text(contrib, ".//given-names") or ""
        surname = _first_text(contrib, ".//surname") or ""
        name = " ".join(part for part in [given, surname] if part).strip()
        if name:
            authors.append(name)
    return authors


def _extract_pub_date(root: etree._Element) -> date | None:
    """Extract publication date if present and valid."""
    year_text = _first_text(root, ".//pub-date/year")
    month_text = _first_text(root, ".//pub-date/month") or "1"
    day_text = _first_text(root, ".//pub-date/day") or "1"
    if not year_text:
        return None
    try:
        return date(int(year_text), int(month_text), int(day_text))
    except ValueError:
        return None


def _extract_sections(root: etree._Element, document_id: UUID) -> list[Section]:
    """Extract body sections as normalized section records."""
    sections: list[Section] = []
    order = 0
    for sec in root.findall(".//body//sec"):
        heading = _first_text(sec, "./title")
        sec_type = sec.attrib.get("sec-type", "section")
        paragraphs = [" ".join(p.itertext()).strip() for p in sec.findall(".//p")]
        content = "\n\n".join(p for p in paragraphs if p)
        if not content:
            continue
        sections.append(
            Section(
                id=uuid4(),
                document_id=document_id,
                section_type=sec_type,
                heading=heading,
                content=content,
                order=order,
            ),
        )
        order += 1
    return sections


def _extract_caption_sections(root: etree._Element, document_id: UUID) -> list[Section]:
    """Extract figure/table captions as synthetic sections."""
    sections: list[Section] = []
    order = 10_000
    for caption in root.findall(".//fig/caption"):
        text = " ".join(caption.itertext()).strip()
        if text:
            sections.append(
                Section(
                    id=uuid4(),
                    document_id=document_id,
                    section_type="figure_caption",
                    heading="Figure Caption",
                    content=text,
                    order=order,
                ),
            )
            order += 1
    for caption in root.findall(".//table-wrap/caption"):
        text = " ".join(caption.itertext()).strip()
        if text:
            sections.append(
                Section(
                    id=uuid4(),
                    document_id=document_id,
                    section_type="table_caption",
                    heading="Table Caption",
                    content=text,
                    order=order,
                ),
            )
            order += 1
    return sections
