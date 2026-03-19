import typer

from cina.cli.apikey import app as apikey_app
from cina.cli.db import app as db_app
from cina.cli.dlq import app as dlq_app
from cina.cli.ingest import app as ingest_app
from cina.cli.serve import app as serve_app

app = typer.Typer(help="CINA command line interface")
app.add_typer(ingest_app, name="ingest")
app.add_typer(serve_app, name="serve")
app.add_typer(db_app, name="db")
app.add_typer(apikey_app, name="apikey")
app.add_typer(dlq_app, name="dlq")


@app.callback(invoke_without_command=False)
def main() -> None:
    """Root CLI callback."""
