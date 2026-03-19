from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

from lxml import etree  # type: ignore[import-untyped]

from cina.ingestion.connectors.protocol import FetchConfig, RawDocument
from cina.models.document import Document, Section


class FDAConnector:
    source_type = "fda"

    async def fetch_document_list(self, config: FetchConfig) -> AsyncIterator[RawDocument]:
        source_path = config.source_path or Path("data/fda")
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
        parser = etree.XMLParser(recover=True, remove_comments=True)
        root = etree.fromstring(raw.payload.encode("utf-8"), parser=parser)

        title = _first_text(root, ".//title") or raw.source_id
        setid = _first_text(root, ".//setId/@root") or raw.source_id
        if setid == raw.source_id:
            id_node = root.find(".//setId")
            if id_node is not None:
                setid = id_node.attrib.get("root", raw.source_id)

        document_id = uuid4()
        sections = _extract_structured_sections(root, document_id)

        return Document(
            id=document_id,
            source=self.source_type,
            source_id=setid,
            title=title,
            authors=[],
            publication_date=None,
            raw_metadata={"raw_source_id": raw.source_id, **raw.metadata},
            sections=sections,
        )


def _first_text(root: etree._Element, xpath: str) -> str | None:
    result = root.xpath(xpath)
    if not result:
        return None
    value = result[0]
    if isinstance(value, etree._Element):
        text = " ".join(value.itertext()).strip()
        return text or None
    text = str(value).strip()
    return text or None


def _extract_structured_sections(root: etree._Element, document_id: UUID) -> list[Section]:
    sections: list[Section] = []
    order = 0
    for component in root.xpath(".//*[local-name()='section']"):
        heading = _first_text(component, "./*[local-name()='code']/@displayName") or _first_text(
            component, "./*[local-name()='title']"
        )
        text_blocks = [
            " ".join(t.itertext()).strip() for t in component.xpath(".//*[local-name()='text']")
        ]
        content = "\n\n".join(t for t in text_blocks if t)
        if not content:
            continue
        section_type = (heading or "section").lower().replace(" ", "_")
        sections.append(
            Section(
                id=uuid4(),
                document_id=document_id,
                section_type=section_type,
                heading=heading,
                content=content,
                order=order,
            )
        )
        order += 1
    return sections
