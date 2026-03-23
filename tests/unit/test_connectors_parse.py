import json

import pytest

from cina.ingestion.connectors.clinicaltrials import ClinicalTrialsConnector
from cina.ingestion.connectors.fda import FDAConnector
from cina.ingestion.connectors.protocol import RawDocument
from cina.ingestion.connectors.pubmed import PubMedConnector


@pytest.mark.parametrize(
    ("source_id", "pmcid", "title", "section_type"),
    [
        ("file-1", "PMC12345", "PubMed A", "methods"),
        ("file-2", "PMC12346", "PubMed B", "results"),
        ("file-3", "PMC12347", "PubMed C", "discussion"),
        ("file-4", "PMC12348", "PubMed D", "intro"),
        ("file-5", "PMC12349", "PubMed E", "conclusion"),
    ],
)
def test_pubmed_connector_parse_cases(
    source_id: str,
    pmcid: str,
    title: str,
    section_type: str,
) -> None:
    xml = f"""
    <article>
      <front>
        <article-meta>
          <article-id pub-id-type='pmcid'>{pmcid}</article-id>
          <title-group><article-title>{title}</article-title></title-group>
          <contrib-group>
            <contrib contrib-type='author'><name><given-names>Jane</given-names><surname>Doe</surname></name></contrib>
          </contrib-group>
          <pub-date><year>2024</year><month>1</month><day>2</day></pub-date>
        </article-meta>
      </front>
      <body>
        <sec sec-type='{section_type}'>
          <title>Section</title>
          <p>Method paragraph one.</p>
        </sec>
      </body>
    </article>
    """
    connector = PubMedConnector()

    document = connector.parse(RawDocument(source_id=source_id, payload=xml, metadata={}))

    assert document.source == "pubmed"
    assert document.source_id == pmcid
    assert document.title == title
    assert len(document.sections) == 1
    assert document.sections[0].section_type == section_type


@pytest.mark.parametrize(
    ("source_id", "set_id", "title", "heading"),
    [
        ("fda-1", "set-a", "Label A", "Indications and Usage"),
        ("fda-2", "set-b", "Label B", "Dosage and Administration"),
        ("fda-3", "set-c", "Label C", "Warnings"),
        ("fda-4", "set-d", "Label D", "Adverse Reactions"),
        ("fda-5", "set-e", "Label E", "Drug Interactions"),
    ],
)
def test_fda_connector_parse_cases(source_id: str, set_id: str, title: str, heading: str) -> None:
    xml = f"""
    <document>
      <setId root='{set_id}' />
      <title>{title}</title>
      <section>
        <title>{heading}</title>
        <text>Use this product carefully.</text>
      </section>
    </document>
    """
    connector = FDAConnector()

    document = connector.parse(RawDocument(source_id=source_id, payload=xml, metadata={}))

    assert document.source == "fda"
    assert document.source_id == set_id
    assert document.title == title
    assert len(document.sections) == 1
    assert document.sections[0].section_type


@pytest.mark.parametrize(
    ("source_id", "nct_id", "title", "summary"),
    [
        ("ct-1", "NCT00000001", "Trial A", "Summary A"),
        ("ct-2", "NCT00000002", "Trial B", "Summary B"),
        ("ct-3", "NCT00000003", "Trial C", "Summary C"),
        ("ct-4", "NCT00000004", "Trial D", "Summary D"),
        ("ct-5", "NCT00000005", "Trial E", "Summary E"),
    ],
)
def test_clinicaltrials_connector_parse_cases(
    source_id: str,
    nct_id: str,
    title: str,
    summary: str,
) -> None:
    payload = json.dumps(
        {
            "protocolSection": {
                "identificationModule": {
                    "nctId": nct_id,
                    "briefTitle": title,
                },
                "descriptionModule": {
                    "briefSummary": summary,
                },
                "eligibilityModule": {
                    "eligibilityCriteria": "Adults",
                },
            },
            "resultsSection": {
                "participantFlowModule": "Flow",
            },
        },
    )
    connector = ClinicalTrialsConnector()

    document = connector.parse(RawDocument(source_id=source_id, payload=payload, metadata={}))

    assert document.source == "clinicaltrials"
    assert document.source_id == nct_id
    assert document.title == title
    assert len(document.sections) >= 2
