"""Configuration and settings."""

import logging
import sys
from pathlib import Path

import structlog

from src.config.settings import Settings


def setup_logging(settings: Settings) -> None:
    """Configure structlog and FeatureLogger according to settings."""

    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Base level for standard logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=log_level,
    )

    # Shared processors
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.set_exc_info,
    ]

    if settings.log_json or settings.log_file:
        processors = shared_processors + [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]
    else:
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True),
        ]

    structlog.configure(
        processors=processors,  # type: ignore[arg-type]
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )

    # If a file is specified — add a file handler to the root logger
    if settings.log_file:
        log_path = Path(settings.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(log_level)
        logging.getLogger().addHandler(file_handler)

    # Enable feature tracing for specified scenarios
    _setup_trace_scenarios(settings)


def _setup_trace_scenarios(settings: Settings) -> None:
    """Parse SPEC_EDITOR__TRACE_SCENARIOS and enable FeatureLogger filters."""
    from src.tracing import FeatureLogger

    raw = settings.trace_scenarios.strip()
    if not raw:
        return

    if raw == "*":
        FeatureLogger.enable_all()
    else:
        for sid in raw.split(","):
            sid = sid.strip()
            if sid:
                FeatureLogger.enable(sid)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a structured logger with the given name."""
    return structlog.get_logger(name or __name__)
