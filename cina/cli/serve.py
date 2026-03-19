import typer

app = typer.Typer(help="Serve commands")


@app.callback(invoke_without_command=True)
def serve_root() -> None:
    typer.echo("not implemented")
