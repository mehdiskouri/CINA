import typer

app = typer.Typer(help="Serve commands")


@app.callback(invoke_without_command=True)
def serve_root(
    host: str = typer.Option("0.0.0.0", help="Bind address"),
    port: int = typer.Option(8000, help="Bind port"),
    reload: bool = typer.Option(False, help="Enable auto-reload for development"),
    workers: int = typer.Option(1, help="Number of uvicorn workers"),
) -> None:
    """Start the CINA query serving API."""
    import uvicorn

    uvicorn.run(
        "cina.api.app:app",
        host=host,
        port=port,
        reload=reload,
        workers=workers,
        log_level="info",
    )
