"""Tests for the CLI entry point."""

from unittest.mock import MagicMock, patch

import pytest

from cmp_automation.cli import cli_main


class TestCliMain:
    """Tests for the cli_main entry point."""

    def test_normal_result_passed_to_sys_exit(self):
        """Test that a normal result code is passed to sys.exit."""
        with patch("cmp_automation.cli.main", new=MagicMock(return_value=0)) as mock_main, \
             patch("cmp_automation.cli.asyncio.run", return_value=0) as mock_run, \
             patch("cmp_automation.cli.sys.exit") as mock_exit:
            cli_main()

        mock_main.assert_called_once()
        mock_run.assert_called_once_with(0)
        mock_exit.assert_called_once_with(0)

    def test_keyboard_interrupt_exits_with_130(self):
        """Test that KeyboardInterrupt from asyncio.run exits with code 130."""
        with patch("cmp_automation.cli.main", new=MagicMock(return_value=0)), \
             patch("cmp_automation.cli.asyncio.run", side_effect=KeyboardInterrupt), \
             patch("cmp_automation.cli.sys.exit") as mock_exit, \
             patch("cmp_automation.cli.logger") as mock_logger:
            cli_main()

        mock_exit.assert_called_once_with(130)
        mock_logger.info.assert_called_once_with("Interrupted by user")

    def test_no_traceback_emitted_on_interrupt(self, capsys):
        """Test that the handled interruption emits no traceback.

        The real logger stays active so any unhandled KeyboardInterrupt would
        surface in stderr as a traceback - the handler must prevent that.
        """
        with patch("cmp_automation.cli.main", new=MagicMock(return_value=0)), \
             patch("cmp_automation.cli.asyncio.run", side_effect=KeyboardInterrupt), \
             patch("cmp_automation.cli.sys.exit"):
            cli_main()

        captured = capsys.readouterr()
        assert "Traceback" not in captured.err
        assert "KeyboardInterrupt" not in captured.err

    def test_unexpected_exceptions_not_suppressed(self):
        """Test that ordinary unexpected exceptions are not swallowed."""
        with patch("cmp_automation.cli.main", new=MagicMock(return_value=0)), \
             patch("cmp_automation.cli.asyncio.run", side_effect=RuntimeError("boom")), \
             patch("cmp_automation.cli.sys.exit") as mock_exit:
            with pytest.raises(RuntimeError, match="boom"):
                cli_main()

        mock_exit.assert_not_called()
