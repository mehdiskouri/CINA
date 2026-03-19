import typer

app = typer.Typer(help="DLQ commands")


@app.callback(invoke_without_command=True)
def dlq_root() -> None:
    typer.echo("not implemented")
