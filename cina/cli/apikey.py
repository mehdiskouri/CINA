import typer

app = typer.Typer(help="API key commands")


@app.callback(invoke_without_command=True)
def apikey_root() -> None:
    typer.echo("not implemented")
