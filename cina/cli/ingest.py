import typer

app = typer.Typer(help="Ingestion commands")


@app.callback(invoke_without_command=True)
def ingest_root() -> None:
    typer.echo("not implemented")
