from __future__ import annotations

import json
from pathlib import Path

import pytest

from cina.ingestion.connectors.clinicaltrials import ClinicalTrialsConnector
from cina.ingestion.connectors.fda import FDAConnector
from cina.ingestion.connectors.protocol import FetchConfig, RawDocument
from cina.ingestion.connectors.pubmed import PubMedConnector


@pytest.mark.asyncio
async def test_pubmed_fetch_and_parse(tmp_path: Path) -> None:
    xml = """
    <article>
      <front>
        <article-meta>
          <article-id pub-id-type='pmcid'>PMC123</article-id>
          <title-group><article-title>HER2 Study</article-title></title-group>
          <contrib-group>
            <contrib contrib-type='author'><name><given-names>Jane</given-names><surname>Doe</surname></name></contrib>
          </contrib-group>
          <pub-date><year>2022</year><month>5</month><day>7</day></pub-date>
        </article-meta>
      </front>
      <body>
        <sec sec-type='intro'><title>Intro</title><p>Sentence one.</p></sec>
      </body>
      <fig><caption>Figure caption text</caption></fig>
    </article>
    """
    (tmp_path / "a.xml").write_text(xml, encoding="utf-8")

    connector = PubMedConnector()
    rows = [
        item
        async for item in connector.fetch_document_list(
            FetchConfig(limit=1, source_path=tmp_path, glob_pattern="*.xml")
        )
    ]
    doc = connector.parse(rows[0])

    assert len(rows) == 1
    assert doc.source_id == "PMC123"
    assert doc.title == "HER2 Study"
    assert any(section.section_type == "intro" for section in doc.sections)
    assert any(section.section_type == "figure_caption" for section in doc.sections)


def test_pubmed_parse_with_missing_date_and_invalid_date() -> None:
    connector = PubMedConnector()
    raw_missing = RawDocument(
        source_id="x", payload="<article><body></body></article>", metadata={}
    )
    raw_invalid = RawDocument(
        source_id="x",
        payload="<article><front><pub-date><year>2022</year><month>99</month><day>1</day></pub-date></front></article>",
        metadata={},
    )

    doc_missing = connector.parse(raw_missing)
    doc_invalid = connector.parse(raw_invalid)

    assert doc_missing.publication_date is None
    assert doc_invalid.publication_date is None


@pytest.mark.asyncio
async def test_fda_fetch_and_parse(tmp_path: Path) -> None:
    xml = """
    <document>
      <title>Drug Label</title>
      <setId root='set-1'/>
      <section>
        <code displayName='Warnings'/>
        <text>Important warning text.</text>
      </section>
    </document>
    """
    (tmp_path / "d.xml").write_text(xml, encoding="utf-8")
    connector = FDAConnector()

    rows = [
        item
        async for item in connector.fetch_document_list(
            FetchConfig(source_path=tmp_path, glob_pattern="*.xml")
        )
    ]
    doc = connector.parse(rows[0])

    assert len(rows) == 1
    assert doc.source_id == "set-1"
    assert doc.title == "Drug Label"
    assert doc.sections
    assert doc.sections[0].section_type == "warnings"


@pytest.mark.asyncio
async def test_clinicaltrials_fetch_and_parse(tmp_path: Path) -> None:
    payload = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT001", "briefTitle": "Trial"},
            "descriptionModule": {
                "briefSummary": "brief",
                "detailedDescription": "details",
            },
            "eligibilityModule": {"eligibilityCriteria": "adult"},
            "armsInterventionsModule": {
                "interventions": [{"type": "Drug", "name": "ABC", "description": "once daily"}]
            },
            "outcomesModule": {
                "primaryOutcomes": [{"measure": "OS", "timeFrame": "1y"}],
                "secondaryOutcomes": [],
            },
        },
        "resultsSection": {"status": "complete"},
    }
    (tmp_path / "ct.json").write_text(json.dumps(payload), encoding="utf-8")

    connector = ClinicalTrialsConnector()
    rows = [
        item
        async for item in connector.fetch_document_list(
            FetchConfig(source_path=tmp_path, glob_pattern="*.json")
        )
    ]
    doc = connector.parse(rows[0])

    assert len(rows) == 1
    assert doc.source_id == "NCT001"
    assert doc.title == "Trial"
    assert len(doc.sections) >= 4
