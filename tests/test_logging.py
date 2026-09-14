"""Tests for application logging configuration."""

import logging
from pathlib import Path

from cmp_automation.logging import setup_logging


class TestSetupLogging:
    """Tests for setup_logging level propagation."""

    def test_file_handler_honors_configured_level(self, tmp_path: Path) -> None:
        """The file handler level must match the requested log_level, not DEBUG."""
        logs_dir = tmp_path / "logs"
        log_path = setup_logging(logs_dir=logs_dir, log_level="INFO")
        assert log_path is not None
        assert log_path.exists()

        root_logger = logging.getLogger()
        file_handlers = [h for h in root_logger.handlers if isinstance(h, logging.FileHandler)]
        assert file_handlers, "expected a FileHandler to be attached to the root logger"
        assert all(h.level == logging.INFO for h in file_handlers)
        # No handler should silently fall back to capturing DEBUG when INFO is requested.
        assert not any(h.level == logging.DEBUG for h in file_handlers)
        root_logger.setLevel(logging.INFO)
        for h in root_logger.handlers[:]:
            root_logger.removeHandler(h)

    def test_file_handler_honors_debug_level(self, tmp_path: Path) -> None:
        """When DEBUG is requested, the file handler must be set to DEBUG."""
        logs_dir = tmp_path / "logs"
        log_path = setup_logging(logs_dir=logs_dir, log_level="DEBUG")
        assert log_path is not None

        root_logger = logging.getLogger()
        file_handlers = [h for h in root_logger.handlers if isinstance(h, logging.FileHandler)]
        assert file_handlers
        assert all(h.level == logging.DEBUG for h in file_handlers)
        for h in root_logger.handlers[:]:
            root_logger.removeHandler(h)
