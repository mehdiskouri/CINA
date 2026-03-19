from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

from cina.ingestion.connectors.protocol import FetchConfig, RawDocument
from cina.models.document import Document, Section


class ClinicalTrialsConnector:
    source_type = "clinicaltrials"

    async def fetch_document_list(self, config: FetchConfig) -> AsyncIterator[RawDocument]:
        source_path = config.source_path or Path("data/clinicaltrials")
        count = 0
        for file_path in sorted(source_path.glob(config.glob_pattern or "*.json")):
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
        payload = json.loads(raw.payload)
        protocol = payload.get("protocolSection", {})
        identification = protocol.get("identificationModule", {})
        description = protocol.get("descriptionModule", {})
        arms = protocol.get("armsInterventionsModule", {})
        outcomes = protocol.get("outcomesModule", {})
        eligibility = protocol.get("eligibilityModule", {})
        results = payload.get("resultsSection", {})

        document_id = uuid4()
        nct_id = identification.get("nctId") or raw.source_id
        title = identification.get("briefTitle") or nct_id

        section_payloads: list[tuple[str, str | None, str]] = [
            ("abstract", "Brief Summary", _text(description.get("briefSummary"))),
            (
                "detailed_description",
                "Detailed Description",
                _text(description.get("detailedDescription")),
            ),
            (
                "eligibility",
                "Eligibility",
                _text(eligibility.get("eligibilityCriteria")),
            ),
            (
                "intervention",
                "Interventions",
                "\n".join(_interventions_text(arms.get("interventions", []))),
            ),
            (
                "outcomes",
                "Outcomes",
                "\n".join(_outcomes_text(outcomes)),
            ),
            ("results", "Results", _text(results)),
        ]

        sections: list[Section] = []
        order = 0
        for sec_type, heading, content in section_payloads:
            normalized_content = content.strip()
            if not normalized_content:
                continue
            sections.append(
                Section(
                    id=uuid4(),
                    document_id=document_id,
                    section_type=sec_type,
                    heading=heading,
                    content=normalized_content,
                    order=order,
                )
            )
            order += 1

        return Document(
            id=document_id,
            source=self.source_type,
            source_id=nct_id,
            title=title,
            authors=[],
            publication_date=None,
            raw_metadata={"raw_source_id": raw.source_id, **raw.metadata},
            sections=sections,
        )


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_text(item) for item in value.values())
    return str(value)


def _interventions_text(interventions: list[dict[str, object]]) -> list[str]:
    results: list[str] = []
    for intervention in interventions:
        iv_type = _text(intervention.get("type"))
        name = _text(intervention.get("name"))
        description = _text(intervention.get("description"))
        line = " - ".join(part for part in [iv_type, name, description] if part)
        if line:
            results.append(line)
    return results


def _outcomes_text(outcomes: dict[str, object]) -> list[str]:
    lines: list[str] = []
    for key in ["primaryOutcomes", "secondaryOutcomes", "otherOutcomes"]:
        value = outcomes.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            lines.append(_text(item))
    return [line for line in lines if line.strip()]
