"""Structured logging configuration and logger helpers."""

from __future__ import annotations

import contextvars
import logging
import sys
from typing import cast

import structlog

correlation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id",
    default=None,
)


def _add_correlation_id(
    _: structlog.typing.WrappedLogger,
    __: str,
    event_dict: structlog.typing.EventDict,
) -> structlog.typing.EventDict:
    """Inject correlation ID from context vars into structured log events."""
    correlation_id = correlation_id_var.get()
    if correlation_id is not None:
        event_dict["correlation_id"] = correlation_id
    return event_dict


def configure_logging(log_level: str = "INFO") -> None:
    """Configure stdlib logging and structlog JSON rendering pipeline."""
    logging.basicConfig(level=log_level.upper(), format="%(message)s", stream=sys.stdout)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _add_correlation_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(log_level.upper()),
        ),
        logger_factory=structlog.PrintLoggerFactory(),
    )


def get_logger(name: str = "cina") -> structlog.typing.FilteringBoundLogger:
    """Return a named structured logger."""
    return cast("structlog.typing.FilteringBoundLogger", structlog.get_logger(name))
