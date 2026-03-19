# Phase A Data Acquisition

This directory stores local raw ingestion inputs and is gitignored.

## Targets

- PubMed OA XML: 2,000-3,000
- FDA DailyMed SPL XML: ~500
- ClinicalTrials.gov JSON: ~1,000

## Quickstart

```bash
python scripts/data_acquisition/download_pubmed_oa.py --limit 2000 --out data/pubmed
python scripts/data_acquisition/download_fda_spl.py --limit 500 --out data/fda
python scripts/data_acquisition/download_clinicaltrials.py --limit 1000 --out data/clinicaltrials
```

## Notes

- PubMed data is sourced from Europe PMC open-access records and saved as XML keyed by PMCID.
- FDA labels are pulled from DailyMed SPL API and saved as XML keyed by SPL setid.
- ClinicalTrials records are saved as one JSON object per file keyed by NCT ID.
