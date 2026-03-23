"""CLI command to launch the query-serving API."""

from typing import Literal

import typer

app = typer.Typer(help="Serve commands")


@app.callback(invoke_without_command=True)
def serve_root(
    host: str = typer.Option("127.0.0.1", help="Bind address"),
    port: int = typer.Option(8000, help="Bind port"),
    reload_mode: Literal["on", "off"] = typer.Option(
        "off",
        "--reload",
        help="Enable auto-reload for development (on|off)",
        case_sensitive=False,
    ),
    workers: int = typer.Option(1, help="Number of uvicorn workers"),
    reload: bool | None = None,  # noqa: FBT001
) -> None:
    """Start the CINA query serving API."""
    import uvicorn  # noqa: PLC0415

    resolved_reload = bool(reload) if reload is not None else reload_mode == "on"
    uvicorn.run(
        "cina.api.app:app",
        host=host,
        port=port,
        reload=resolved_reload,
        workers=workers,
        log_level="info",
    )
