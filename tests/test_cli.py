"""Tests for the CLI entry point."""

import argparse
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cmp_automation.cli import apply_overrides, cli_main, main, parse_args, parse_date_arg
from cmp_automation.config import Config
from cmp_automation.exceptions import CMPAutomationError, ConfigurationError

DUMMY_GOWA_URL = "https://gowa.example.invalid"
DUMMY_GOWA_JID = "6280000000000@s.whatsapp.net"


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

    def test_image_dir_override_sets_config_image_dir(self, base_config: Config, tmp_path: Path) -> None:
        """--image-dir maps strictly to config.image_dir, never download_dir."""
        original_download_dir = base_config.download_dir
        args = argparse.Namespace(
            timeout=None,
            download_dir=None,
            profile_dir=None,
            excel_output_dir=None,
            report_dir=None,
            xlsx_dir=None,
            image_dir=tmp_path / "new_images",
            excel_template=None,
            raw_xlsx=None,
            image=None,
        )
        apply_overrides(base_config, args)
        assert base_config.image_dir == (tmp_path / "new_images").resolve()
        assert base_config.download_dir == original_download_dir

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


@dataclass
class RecordedNotification:
    """A single notification request captured at the transport seam."""

    url: str
    payload: dict[str, object]
    headers: dict[str, str]
    timeout: float


@dataclass
class RecordingNotifierTransport:
    """Injected HTTP seam for CLI notification tests (never performs HTTP)."""

    error: BaseException | None = None
    calls: list[RecordedNotification] = field(default_factory=list)

    def __call__(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> int:
        self.calls.append(
            RecordedNotification(
                url=url,
                payload=json.loads(body.decode("utf-8")),
                headers=dict(headers),
                timeout=timeout,
            )
        )
        if self.error is not None:
            raise self.error
        return 200

    @property
    def messages(self) -> list[str]:
        """Delivered message texts, in send order."""
        return [str(call.payload["message"]) for call in self.calls]


def build_args(**overrides: object) -> argparse.Namespace:
    """Build a CLI argument namespace with the documented defaults."""
    values: dict[str, object] = {
        "mode": "full",
        "menu": False,
        "log_level": "INFO",
        "date": "2026-03-01",
        "start_date": None,
        "end_date": None,
        "headed": False,
        "dry_run": False,
        "diagnose_export": False,
        "diagnose_auth": False,
        "monitor_cycles": None,
        "enable_connectivity": False,
        "allow_connectivity_mutation": False,
        "raw_xlsx": None,
        "image": None,
        "timeout": None,
        "download_dir": None,
        "profile_dir": None,
        "excel_output_dir": None,
        "report_dir": None,
        "xlsx_dir": None,
        "image_dir": None,
        "excel_template": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)  # type: ignore[arg-type]


def build_notified_config(tmp_path: Path, **overrides: object) -> Config:
    """Build an offline Config with GOWA notifications enabled."""
    values: dict[str, object] = {
        "cmp_username": "user",
        "cmp_password": "pw",
        "gmf_email": "email@example.com",
        "gmf_password": "pw",
        "firefox_profile_dir": tmp_path / "profile",
        "download_dir": tmp_path / "downloads",
        "excel_output_dir": tmp_path / "reports",
        "excel_template_path": tmp_path / "template.xlsx",
        "whatsapp_notifications_enabled": True,
        "gowa_base_url": DUMMY_GOWA_URL,
        "gowa_target_jid": DUMMY_GOWA_JID,
    }
    values.update(overrides)
    return Config(**values)  # type: ignore[arg-type]


class TestLifecycleNotifications:
    """Notification wiring in main(): START plus exactly one terminal event."""

    @staticmethod
    async def run_main(
        config: Config,
        args: argparse.Namespace,
        *,
        workflow_result: object = Path("/tmp/out.xlsx"),
        workflow_error: BaseException | None = None,
        validate_error: BaseException | None = None,
    ) -> tuple[int, RecordingNotifierTransport]:
        """Drive main() with the workflow stubbed out and notifications recorded."""
        transport = RecordingNotifierTransport()
        workflow: Any
        if workflow_error is not None:
            workflow = AsyncMock(side_effect=workflow_error)
        else:
            workflow = AsyncMock(return_value=workflow_result)
        validate = (
            MagicMock(side_effect=validate_error) if validate_error is not None else MagicMock()
        )

        with (
            patch("cmp_automation.cli.parse_args", return_value=args),
            patch("cmp_automation.cli.load_config", return_value=config),
            patch("cmp_automation.cli.validate_paths", new=validate),
            patch("cmp_automation.cli.run_workflow", new=workflow),
            patch("cmp_automation.notifier.http_post_json", new=transport),
        ):
            exit_code = await main()
        return exit_code, transport

    @pytest.mark.asyncio
    async def test_disabled_notifications_make_no_request(self, tmp_path: Path) -> None:
        """Default configuration sends nothing at all."""
        config = build_notified_config(tmp_path, whatsapp_notifications_enabled=False)
        exit_code, transport = await self.run_main(config, build_args())

        assert exit_code == 0
        assert transport.calls == []

    @pytest.mark.asyncio
    async def test_unconfigured_notifications_make_no_request(self, tmp_path: Path) -> None:
        """Enabled without a target JID stays silent."""
        config = build_notified_config(tmp_path, gowa_target_jid=None)
        exit_code, transport = await self.run_main(config, build_args())

        assert exit_code == 0
        assert transport.calls == []

    @pytest.mark.asyncio
    async def test_full_mode_sends_start_then_one_success(self, tmp_path: Path) -> None:
        """full mode emits START followed by exactly one SUCCESS."""
        config = build_notified_config(tmp_path)
        exit_code, transport = await self.run_main(config, build_args(mode="full"))

        assert exit_code == 0
        assert len(transport.calls) == 2
        assert "START" in transport.messages[0]
        assert "SUCCESS" in transport.messages[1]
        assert all(call.url == f"{DUMMY_GOWA_URL}/send/message" for call in transport.calls)
        assert all(call.payload["phone"] == DUMMY_GOWA_JID for call in transport.calls)
        assert all(call.timeout > 0 for call in transport.calls)

    @pytest.mark.asyncio
    async def test_scrape_mode_sends_start_then_one_terminal_event(self, tmp_path: Path) -> None:
        """scrape mode emits START followed by exactly one SUCCESS."""
        config = build_notified_config(tmp_path)
        exit_code, transport = await self.run_main(config, build_args(mode="scrape"))

        assert exit_code == 0
        assert len(transport.calls) == 2
        assert "START" in transport.messages[0]
        assert "SUCCESS" in transport.messages[1]

    @pytest.mark.asyncio
    async def test_generate_mode_sends_nothing(self, tmp_path: Path) -> None:
        """generate mode is unchanged: no notifications at all."""
        config = build_notified_config(tmp_path)
        exit_code, transport = await self.run_main(config, build_args(mode="generate"))

        assert exit_code == 0
        assert transport.calls == []

    @pytest.mark.asyncio
    async def test_workflow_failure_sends_single_failed_event(self, tmp_path: Path) -> None:
        """A workflow error produces START plus exactly one FAILED."""
        config = build_notified_config(tmp_path)
        exit_code, transport = await self.run_main(
            config,
            build_args(mode="full"),
            workflow_error=CMPAutomationError("portal exploded for ICCID 8991122334455667788"),
        )

        assert exit_code == 1
        assert len(transport.calls) == 2
        assert "START" in transport.messages[0]
        assert "FAILED" in transport.messages[1]
        assert "8991122334455667788" not in transport.messages[1]
        assert "portal exploded" not in transport.messages[1]

    @pytest.mark.asyncio
    async def test_configuration_error_sends_single_failed_event(self, tmp_path: Path) -> None:
        """validate_paths failures report only the sanitized error category."""
        config = build_notified_config(tmp_path)
        exit_code, transport = await self.run_main(
            config,
            build_args(mode="full"),
            validate_error=ConfigurationError(
                "Missing required environment variable(s): CMP_PASSWORD"
            ),
        )

        assert exit_code == 1
        assert len(transport.calls) == 1
        assert "FAILED" in transport.messages[0]
        assert "error=ConfigurationError" in transport.messages[0]
        assert "CMP_PASSWORD" not in transport.messages[0]

    @pytest.mark.asyncio
    async def test_unexpected_exception_sends_single_failed_event(self, tmp_path: Path) -> None:
        """An unexpected exception is reported by class name only."""
        config = build_notified_config(tmp_path)
        exit_code, transport = await self.run_main(
            config,
            build_args(mode="full"),
            workflow_error=RuntimeError("raw secret detail"),
        )

        assert exit_code == 1
        assert len(transport.calls) == 2
        assert "error=RuntimeError" in transport.messages[1]
        assert "raw secret detail" not in transport.messages[1]

    @pytest.mark.asyncio
    async def test_transport_failure_does_not_change_success_exit_code(
        self, tmp_path: Path
    ) -> None:
        """A broken notifier never masks a successful pipeline."""
        config = build_notified_config(tmp_path)
        transport = RecordingNotifierTransport(error=OSError("connection refused"))

        with (
            patch("cmp_automation.cli.parse_args", return_value=build_args()),
            patch("cmp_automation.cli.load_config", return_value=config),
            patch("cmp_automation.cli.validate_paths", new=MagicMock()),
            patch(
                "cmp_automation.cli.run_workflow", new=AsyncMock(return_value=Path("/tmp/out.xlsx"))
            ),
            patch("cmp_automation.notifier.http_post_json", new=transport),
        ):
            exit_code = await main()

        assert exit_code == 0
        assert len(transport.calls) == 2

    @pytest.mark.asyncio
    async def test_transport_failure_does_not_change_failure_exit_code(
        self, tmp_path: Path
    ) -> None:
        """A broken notifier never masks a failing pipeline either."""
        config = build_notified_config(tmp_path)
        transport = RecordingNotifierTransport(error=OSError("connection refused"))

        with (
            patch("cmp_automation.cli.parse_args", return_value=build_args()),
            patch("cmp_automation.cli.load_config", return_value=config),
            patch("cmp_automation.cli.validate_paths", new=MagicMock()),
            patch(
                "cmp_automation.cli.run_workflow",
                new=AsyncMock(side_effect=CMPAutomationError("Failed")),
            ),
            patch("cmp_automation.notifier.http_post_json", new=transport),
        ):
            exit_code = await main()

        assert exit_code == 1
        assert len(transport.calls) == 2

    @pytest.mark.asyncio
    async def test_notifier_guard_swallows_adapter_explosion(self, tmp_path: Path) -> None:
        """Even a raising notifier adapter leaves the run outcome untouched."""
        config = build_notified_config(tmp_path)

        with (
            patch("cmp_automation.cli.parse_args", return_value=build_args()),
            patch("cmp_automation.cli.load_config", return_value=config),
            patch("cmp_automation.cli.validate_paths", new=MagicMock()),
            patch(
                "cmp_automation.cli.run_workflow", new=AsyncMock(return_value=Path("/tmp/out.xlsx"))
            ),
            patch(
                "cmp_automation.notifier.Notifier.send_event",
                side_effect=RuntimeError("notifier exploded"),
            ),
        ):
            exit_code = await main()

        assert exit_code == 0

    @pytest.mark.asyncio
    async def test_keyboard_interrupt_sends_no_terminal_event(self, tmp_path: Path) -> None:
        """An interrupted run keeps its 130 exit code and sends no terminal event."""
        config = build_notified_config(tmp_path)
        exit_code, transport = await self.run_main(
            config,
            build_args(mode="full"),
            workflow_error=KeyboardInterrupt(),
        )

        assert exit_code == 130
        assert len(transport.calls) == 1
        assert "START" in transport.messages[0]

    @pytest.mark.asyncio
    async def test_success_message_omits_output_path_and_secrets(self, tmp_path: Path) -> None:
        """The SUCCESS body is concise and clean: no output path, no secrets."""
        config = build_notified_config(tmp_path, cmp_password="super-secret-pw")
        _, transport = await self.run_main(
            config, build_args(mode="full"), workflow_result=Path("output/reports/wb.xlsx")
        )

        success_message = transport.messages[1]
        assert "SUCCESS" in success_message
        assert "output=" not in success_message
        assert "wb.xlsx" not in success_message
        assert "output/reports" not in success_message
        assert "/tmp" not in success_message
        assert "super-secret-pw" not in success_message
        assert DUMMY_GOWA_JID not in success_message
        # Concise: one allow-listed fragment per pipeline fact, nothing else.
        assert success_message.startswith("[GMF CMP Automation] SUCCESS | mode=full | date=")
        assert success_message.count(" | ") <= 3

    @pytest.mark.asyncio
    async def test_start_message_includes_mode_and_date(self, tmp_path: Path) -> None:
        """The START body carries the pipeline mode and target query date."""
        config = build_notified_config(tmp_path)
        _, transport = await self.run_main(config, build_args(mode="full", date="2026-03-01"))

        start_message = transport.messages[0]
        assert "mode=full" in start_message
        assert "date=2026-03-01" in start_message

    @pytest.mark.asyncio
    async def test_date_range_start_message_lists_both_dates(self, tmp_path: Path) -> None:
        """A range run reports both boundaries."""
        config = build_notified_config(tmp_path)
        _, transport = await self.run_main(
            config,
            build_args(mode="full", date=None, start_date="2026-03-01", end_date="2026-03-02"),
        )

        assert "2026-03-01" in transport.messages[0]
        assert "2026-03-02" in transport.messages[0]
