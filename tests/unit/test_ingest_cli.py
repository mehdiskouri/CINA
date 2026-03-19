from __future__ import annotations

from uuid import uuid4

from typer.testing import CliRunner

from cina.cli.main import app
from cina.ingestion.pipeline import IngestionResult

runner = CliRunner()


def test_ingest_requires_source_and_path() -> None:
    result = runner.invoke(app, ["ingest"])

    assert result.exit_code == 2
    assert "Provide both --source and --path" in result.output


def test_ingest_runs_with_root_options(monkeypatch) -> None:
    async def fake_run_ingestion(**kwargs):  # type: ignore[no-untyped-def]
        return IngestionResult(
            job_id=uuid4(),
            documents_processed=1,
            chunks_created=2,
            chunks_embedded=2,
            errors=[],
        )

    monkeypatch.setattr("cina.cli.ingest.run_ingestion", fake_run_ingestion)

    result = runner.invoke(
        app,
        ["ingest", "--source", "pubmed", "--path", "data/pubmed", "--limit", "1"],
    )

    assert result.exit_code == 0
    assert "Documents processed: 1" in result.output
    assert "Chunks created: 2" in result.output


def test_ingest_run_subcommand(monkeypatch) -> None:
    async def fake_run_ingestion(**kwargs):  # type: ignore[no-untyped-def]
        return IngestionResult(
            job_id=uuid4(),
            documents_processed=3,
            chunks_created=6,
            chunks_embedded=6,
            errors=[],
        )

    monkeypatch.setattr("cina.cli.ingest.run_ingestion", fake_run_ingestion)

    result = runner.invoke(
        app,
        ["ingest", "run", "--source", "fda", "--path", "data/fda", "--limit", "2"],
    )

    assert result.exit_code == 0
    assert "Documents processed: 3" in result.output
    assert "Chunks embedded: 6" in result.output
