import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from cina.ingestion.pipeline import IngestionProgress, run_ingestion

app = typer.Typer(help="Ingestion commands")
console = Console()


@app.callback(invoke_without_command=True)
def ingest_root(
    ctx: typer.Context,
    source: str | None = typer.Option(None, help="Source name: pubmed|fda|clinicaltrials"),
    path: str | None = typer.Option(None, help="Path to source files"),
    limit: int | None = typer.Option(None, help="Optional max number of documents"),
    batch_size: int = typer.Option(64, help="Embedding batch size"),
    concurrency: int = typer.Option(8, help="Document processing concurrency"),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    if source is None or path is None:
        typer.echo(
            "Provide both --source and --path, or use the subcommand: "
            "cina ingest run --source <name> --path <dir>"
        )
        raise typer.Exit(code=2)
    _run_ingestion(
        source=source,
        path=path,
        limit=limit,
        batch_size=batch_size,
        concurrency=concurrency,
    )


@app.command("run")
def ingest_run(
    source: str = typer.Option(..., help="Source name: pubmed|fda|clinicaltrials"),
    path: str = typer.Option(..., help="Path to source files"),
    limit: int | None = typer.Option(None, help="Optional max number of documents"),
    batch_size: int = typer.Option(64, help="Embedding batch size"),
    concurrency: int = typer.Option(8, help="Document processing concurrency"),
) -> None:
    """Run ingestion pipeline for a selected source."""

    _run_ingestion(
        source=source,
        path=path,
        limit=limit,
        batch_size=batch_size,
        concurrency=concurrency,
    )


def _run_ingestion(
    *,
    source: str,
    path: str,
    limit: int | None,
    batch_size: int,
    concurrency: int,
) -> None:

    import asyncio
    from pathlib import Path

    with Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        BarColumn(),
        TextColumn("docs={task.fields[docs]} chunks={task.fields[chunks]} embedded={task.fields[embedded]} errors={task.fields[errors]}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task(
            "Ingestion pipeline",
            total=None,
            docs=0,
            chunks=0,
            embedded=0,
            errors=0,
        )

        def on_progress(update: IngestionProgress) -> None:
            description = "Ingestion pipeline"
            if update.phase == "documents":
                description = "Parsing and chunking"
            elif update.phase == "embeddings":
                description = "Embedding chunks"
            elif update.phase == "finalized":
                description = "Finalizing"

            progress.update(
                task_id,
                description=description,
                docs=update.documents_processed,
                chunks=update.chunks_created,
                embedded=update.chunks_embedded,
                errors=update.errors_count,
            )

        result = asyncio.run(
            run_ingestion(
                source=source,
                path=Path(path),
                limit=limit,
                concurrency=concurrency,
                batch_size=batch_size,
                progress_callback=on_progress,
            )
        )

        progress.update(
            task_id,
            description="Completed",
            docs=result.documents_processed,
            chunks=result.chunks_created,
            embedded=result.chunks_embedded,
            errors=len(result.errors),
        )

    console.print(f"[green]Ingestion job:[/green] {result.job_id}")
    console.print(f"Documents processed: {result.documents_processed}")
    console.print(f"Chunks created: {result.chunks_created}")
    console.print(f"Chunks embedded: {result.chunks_embedded}")
    if result.errors:
        console.print(f"[yellow]Errors:[/yellow] {len(result.errors)}")
