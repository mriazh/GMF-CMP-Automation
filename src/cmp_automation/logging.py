"""Application logging configuration for CMP Automation."""

import logging
import sys
from pathlib import Path


def setup_logging(
    logs_dir: Path | str | None = None,
    log_level: str = "INFO",
    log_filename: str = "app.log",
) -> Path | None:
    """Setup application-wide logging to file and console.

    Args:
        logs_dir: Directory where log file should be placed.
        log_level: Logging level (e.g. DEBUG, INFO, WARNING, ERROR).
        log_filename: Name of the log file (default: app.log).

    Returns:
        Path to the configured log file, or None if logging to file could not be set up.
    """
    root_logger = logging.getLogger()
    level = getattr(logging, log_level.upper(), logging.INFO)
    root_logger.setLevel(logging.DEBUG)

    # Remove existing handlers to prevent duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Console handler
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(level)
    console_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    log_path: Path | None = None
    if logs_dir is not None:
        target_dir = Path(logs_dir).expanduser().resolve()
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            log_path = target_dir / log_filename
            file_handler = logging.FileHandler(log_path, encoding="utf-8")
            file_handler.setLevel(logging.DEBUG)
            file_formatter = logging.Formatter(
                "[%(asctime)s] [%(levelname)8s] %(name)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            file_handler.setFormatter(file_formatter)
            root_logger.addHandler(file_handler)
        except OSError as exc:
            root_logger.warning("Could not create log file at %s: %s", target_dir, exc)

    # Suppress verbose third-party loggers
    logging.getLogger("PIL").setLevel(logging.WARNING)
    logging.getLogger("openpyxl").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    return log_path


def log_run_boundary(label: str, message: str) -> None:
    """Log a structured run boundary separator."""
    logger = logging.getLogger("cmp_automation.cli")
    line = "=" * 70
    logger.info(line)
    logger.info("%s: %s", label, message)
    logger.info(line)
