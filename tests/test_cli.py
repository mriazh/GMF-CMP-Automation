"""Tests for the CLI entry point."""

import argparse
import sys
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cmp_automation.cli import apply_overrides, cli_main, main, parse_args, parse_date_arg
from cmp_automation.config import Config
from cmp_automation.exceptions import CMPAutomationError, ConfigurationError


class TestParseDateArg:
    """Tests for parse_date_arg function."""

    def test_parse_valid_date(self) -> None:
        """Parse valid YYYY-MM-DD date."""
        assert parse_date_arg("2026-03-01") == date(2026, 3, 1)

    def test_parse_none_or_empty(self) -> None:
        """Parse None or empty string returns None."""
        assert parse_date_arg(None) is None
        assert parse_date_arg("") is None
        assert parse_date_arg("   ") is None

    def test_parse_invalid_date_format(self) -> None:
        """Invalid date format raises ConfigurationError."""
        with pytest.raises(ConfigurationError, match="Invalid date format"):
            parse_date_arg("01-03-2026")

        with pytest.raises(ConfigurationError, match="Invalid date format"):
            parse_date_arg("not-a-date")


class TestApplyOverrides:
    """Tests for apply_overrides helper."""

    @pytest.fixture
    def base_config(self, tmp_path: Path) -> Config:
        return Config(
            cmp_username="user",
            cmp_password="pw",
            gmf_email="email@example.com",
            gmf_password="pw",
            firefox_profile_dir=tmp_path / "profile",
            download_dir=tmp_path / "downloads",
            excel_output_dir=tmp_path / "reports",
            excel_template_path=tmp_path / "template.xlsx",
            otp_timeout_seconds=60,
        )

    def test_valid_overrides(self, base_config: Config, tmp_path: Path) -> None:
        """Test applying valid overrides."""
        args = argparse.Namespace(
            timeout=120,
            download_dir=tmp_path / "new_down",
            profile_dir=tmp_path / "new_prof",
            excel_output_dir=tmp_path / "new_out",
            excel_template=tmp_path / "new_tmpl.xlsx",
        )
        apply_overrides(base_config, args)
        assert base_config.otp_timeout_seconds == 120
        assert base_config.download_dir == (tmp_path / "new_down").resolve()
        assert base_config.firefox_profile_dir == (tmp_path / "new_prof").resolve()
        assert base_config.excel_output_dir == (tmp_path / "new_out").resolve()
        assert base_config.excel_template_path == (tmp_path / "new_tmpl.xlsx").resolve()

    def test_invalid_timeout_too_low(self, base_config: Config) -> None:
        """Timeout below 10 raises ConfigurationError."""
        args = argparse.Namespace(
            timeout=5,
            download_dir=None,
            profile_dir=None,
            excel_output_dir=None,
            excel_template=None,
        )
        with pytest.raises(ConfigurationError, match="--timeout must be between 10 and 600"):
            apply_overrides(base_config, args)

    def test_invalid_timeout_too_high(self, base_config: Config) -> None:
        """Timeout above 600 raises ConfigurationError."""
        args = argparse.Namespace(
            timeout=1000,
            download_dir=None,
            profile_dir=None,
            excel_output_dir=None,
            excel_template=None,
        )
        with pytest.raises(ConfigurationError, match="--timeout must be between 10 and 600"):
            apply_overrides(base_config, args)


class TestParseArgs:
    """Tests for CLI argument parsing (parse_args reads sys.argv)."""

    def test_diagnose_auth_defaults_off(self) -> None:
        """The auth diagnostic flag is opt-in and off by default."""
        with patch.object(sys, "argv", ["cmp_automation"]):
            args = parse_args()
        assert args.diagnose_auth is False

    def test_diagnose_auth_flag_enables(self) -> None:
        """The auth diagnostic flag turns the opt-in diagnostic on."""
        with patch.object(sys, "argv", ["cmp_automation", "--diagnose-auth"]):
            args = parse_args()
        assert args.diagnose_auth is True

    def test_diagnose_flags_are_independent(self) -> None:
        """The export and auth diagnostic flags do not affect each other."""
        with patch.object(sys, "argv", ["cmp_automation", "--diagnose-auth"]):
            args = parse_args()
        assert args.diagnose_auth is True
        assert args.diagnose_export is False

        with patch.object(sys, "argv", ["cmp_automation", "--diagnose-export"]):
            args = parse_args()
        assert args.diagnose_export is True
        assert args.diagnose_auth is False

    def test_date_arguments_parsed(self) -> None:
        """Date arguments are parsed correctly."""
        with patch.object(
            sys,
            "argv",
            [
                "cmp_automation",
                "--date",
                "2026-03-01",
                "--start-date",
                "2026-03-02",
                "--end-date",
                "2026-03-03",
            ],
        ):
            args = parse_args()
        assert args.date == "2026-03-01"
        assert args.start_date == "2026-03-02"
        assert args.end_date == "2026-03-03"

    def test_pipeline_modes_parsed(self) -> None:
        """--mode accepts full, scrape, and generate."""
        with patch.object(sys, "argv", ["cmp_automation", "--mode", "scrape"]):
            args = parse_args()
        assert args.mode == "scrape"

        with patch.object(sys, "argv", ["cmp_automation", "--mode", "generate", "--raw-xlsx", "r.xlsx"]):
            args = parse_args()
        assert args.mode == "generate"
        assert args.raw_xlsx == Path("r.xlsx")

    def test_menu_flag_parsed(self) -> None:
        """--menu flag is parsed."""
        with patch.object(sys, "argv", ["cmp_automation", "--menu"]):
            args = parse_args()
        assert args.menu is True


class TestCliMain:
    """Tests for the cli_main entry point."""

    def test_normal_result_passed_to_sys_exit(self) -> None:
        """Test that a normal result code is passed to sys.exit."""
        with (
            patch("cmp_automation.cli.main", new=MagicMock(return_value=0)) as mock_main,
            patch("cmp_automation.cli.asyncio.run", return_value=0) as mock_run,
            patch("cmp_automation.cli.sys.exit") as mock_exit,
        ):
            cli_main()

        mock_main.assert_called_once()
        mock_run.assert_called_once_with(0)
        mock_exit.assert_called_once_with(0)

    def test_keyboard_interrupt_exits_with_130(self) -> None:
        """Test that KeyboardInterrupt from asyncio.run exits with code 130."""
        with (
            patch("cmp_automation.cli.main", new=MagicMock(return_value=0)),
            patch("cmp_automation.cli.asyncio.run", side_effect=KeyboardInterrupt),
            patch("cmp_automation.cli.sys.exit") as mock_exit,
            patch("cmp_automation.cli.logger") as mock_logger,
        ):
            cli_main()

        mock_exit.assert_called_once_with(130)
        mock_logger.info.assert_called_once_with("Interrupted by user")

    def test_no_traceback_emitted_on_interrupt(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Test that the handled interruption emits no traceback."""
        with (
            patch("cmp_automation.cli.main", new=MagicMock(return_value=0)),
            patch("cmp_automation.cli.asyncio.run", side_effect=KeyboardInterrupt),
            patch("cmp_automation.cli.sys.exit"),
        ):
            cli_main()

        captured = capsys.readouterr()
        assert "Traceback" not in captured.err
        assert "KeyboardInterrupt" not in captured.err

    def test_unexpected_exceptions_not_suppressed(self) -> None:
        """Test that ordinary unexpected exceptions are not swallowed."""
        with (
            patch("cmp_automation.cli.main", new=MagicMock(return_value=0)),
            patch("cmp_automation.cli.asyncio.run", side_effect=RuntimeError("boom")),
            patch("cmp_automation.cli.sys.exit") as mock_exit,
        ):
            with pytest.raises(RuntimeError, match="boom"):
                cli_main()

        mock_exit.assert_not_called()


class TestMainFunction:
    """Tests for the main async function."""

    @pytest.mark.asyncio
    async def test_main_success(self, tmp_path: Path) -> None:
        """Test successful execution returns 0."""
        config = Config(
            cmp_username="user",
            cmp_password="pw",
            gmf_email="email@example.com",
            gmf_password="pw",
            firefox_profile_dir=tmp_path / "profile",
            download_dir=tmp_path / "downloads",
            excel_output_dir=tmp_path / "reports",
            excel_template_path=tmp_path / "template.xlsx",
        )
        with (
            patch("cmp_automation.cli.parse_args") as mock_parse,
            patch("cmp_automation.cli.load_config", return_value=config),
            patch("cmp_automation.cli.validate_paths"),
            patch(
                "cmp_automation.cli.run_workflow", new=AsyncMock(return_value=Path("/tmp/out.xlsx"))
            ),
        ):
            mock_parse.return_value = argparse.Namespace(
                log_level="INFO",
                date="2026-03-01",
                start_date=None,
                end_date=None,
                headed=False,
                dry_run=False,
                diagnose_export=False,
                diagnose_auth=False,
                timeout=None,
                download_dir=None,
                profile_dir=None,
                excel_output_dir=None,
                excel_template=None,
            )
            exit_code = await main()
            assert exit_code == 0

    @pytest.mark.asyncio
    async def test_main_start_date_after_end_date_error(self, tmp_path: Path) -> None:
        """Test invalid date range returns 1."""
        config = Config(
            cmp_username="user",
            cmp_password="pw",
            gmf_email="email@example.com",
            gmf_password="pw",
            firefox_profile_dir=tmp_path / "profile",
            download_dir=tmp_path / "downloads",
            excel_output_dir=tmp_path / "reports",
            excel_template_path=tmp_path / "template.xlsx",
        )
        with (
            patch("cmp_automation.cli.parse_args") as mock_parse,
            patch("cmp_automation.cli.load_config", return_value=config),
            patch("cmp_automation.cli.validate_paths"),
        ):
            mock_parse.return_value = argparse.Namespace(
                log_level="INFO",
                date=None,
                start_date="2026-03-10",
                end_date="2026-03-01",
                headed=False,
                dry_run=False,
                diagnose_export=False,
                diagnose_auth=False,
                timeout=None,
                download_dir=None,
                profile_dir=None,
                excel_output_dir=None,
                excel_template=None,
            )
            exit_code = await main()
            assert exit_code == 1

    @pytest.mark.asyncio
    async def test_main_handles_cmp_automation_error(self, tmp_path: Path) -> None:
        """Test CMPAutomationError returns 1."""
        config = Config(
            cmp_username="user",
            cmp_password="pw",
            gmf_email="email@example.com",
            gmf_password="pw",
            firefox_profile_dir=tmp_path / "profile",
            download_dir=tmp_path / "downloads",
            excel_output_dir=tmp_path / "reports",
            excel_template_path=tmp_path / "template.xlsx",
        )
        with (
            patch("cmp_automation.cli.parse_args") as mock_parse,
            patch("cmp_automation.cli.load_config", return_value=config),
            patch("cmp_automation.cli.validate_paths"),
            patch(
                "cmp_automation.cli.run_workflow",
                new=AsyncMock(side_effect=CMPAutomationError("Failed")),
            ),
        ):
            mock_parse.return_value = argparse.Namespace(
                log_level="INFO",
                date=None,
                start_date=None,
                end_date=None,
                headed=False,
                dry_run=False,
                diagnose_export=False,
                diagnose_auth=False,
                timeout=None,
                download_dir=None,
                profile_dir=None,
                excel_output_dir=None,
                excel_template=None,
            )
            exit_code = await main()
            assert exit_code == 1
