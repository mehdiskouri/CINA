"""Source connectors."""

from cina.ingestion.connectors.clinicaltrials import ClinicalTrialsConnector
from cina.ingestion.connectors.fda import FDAConnector
from cina.ingestion.connectors.pubmed import PubMedConnector

CONNECTOR_BY_SOURCE = {
	"pubmed": PubMedConnector,
	"fda": FDAConnector,
	"clinicaltrials": ClinicalTrialsConnector,
}

__all__ = [
	"CONNECTOR_BY_SOURCE",
	"ClinicalTrialsConnector",
	"FDAConnector",
	"PubMedConnector",
]
