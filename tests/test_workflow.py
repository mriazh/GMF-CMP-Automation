"""Tests for workflow orchestration."""

import logging
import os
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cmp_automation.cmp_login import CMPLogin
from cmp_automation.config import Config
from cmp_automation.connectivity import ConnectivityController, WarpClient
from cmp_automation.dashboard import DashboardCapture
from cmp_automation.excel_report import ExcelReportGenerator
from cmp_automation.exceptions import WorkflowError
from cmp_automation.mailbox import MailboxClient
from cmp_automation.usage_query import UsageQueryExporter, UsageReportArtifact
from cmp_automation.workflow import UsageWorkflowRunner, run_workflow


def _write_usage_xlsx(path: Path, record_date: date) -> Path:
    """Write a minimal Usage Query XLSX whose rows carry ``record_date``."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Date", "ICCID", "Total Data Usage"])
    ws.append([record_date.strftime("%Y-%m-%d"), "8962100012747108709", 1024])
    wb.save(path)
    wb.close()
    return path


@pytest.fixture
def config(tmp_path: Path) -> Config:
    """Create a test config."""
    return Config(
        cmp_username="test",
        cmp_password="test",
        gmf_email="test@test.com",
        gmf_password="test",
        firefox_profile_dir=tmp_path / "profile",
        download_dir=tmp_path / "downloads",
        excel_output_dir=tmp_path / "reports",
        excel_template_path=tmp_path / "template.xlsx",
        timezone="Asia/Jakarta",
        warp_auto_connect=False,
    )


class TestWorkflow:
    """Tests for workflow orchestration."""

    @pytest.mark.asyncio
    async def test_dry_run_success(self, config: Config) -> None:
        """Test dry run completes successfully."""
        with (
            patch("cmp_automation.workflow.browser_context") as mock_browser_context,
            patch("cmp_automation.workflow.validate_paths") as mock_validate,
        ):
            mock_browser = AsyncMock()
            mock_page = AsyncMock()
            mock_browser.new_page = AsyncMock(return_value=mock_page)
            mock_browser_context.return_value.__aenter__.return_value = mock_browser

            workflow = UsageWorkflowRunner(config, dry_run=True)
            result = await workflow.run()

            assert result.name == "dry-run-success"
            mock_browser_context.assert_called_once()
            mock_validate.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_workflow_single_date(self, config: Config) -> None:
        """Test running workflow for a single date."""
        with (
            patch("cmp_automation.workflow.browser_context") as mock_browser_context,
            patch.object(CMPLogin, "login", new=AsyncMock()) as mock_login,
            patch.object(UsageQueryExporter, "export", new=AsyncMock()) as mock_export,
            patch.object(DashboardCapture, "capture", new=AsyncMock()) as mock_dashboard,
            patch.object(ExcelReportGenerator, "generate_report") as mock_excel,
            patch.object(MailboxClient, "disconnect", new=AsyncMock()),
        ):
            mock_browser = AsyncMock()
            mock_page = AsyncMock()
            mock_browser.new_page = AsyncMock(return_value=mock_page)
            mock_browser_context.return_value.__aenter__.return_value = mock_browser

            target_date = date(2026, 3, 1)
            artifact = UsageReportArtifact(
                raw_path=Path("/tmp/raw.xlsx"),
                query_date=target_date,
                rows=[{"date": "2026-03-01", "iccid": "123", "total_usage_bytes": 100}],
            )
            mock_export.return_value = artifact
            mock_dashboard.return_value = Path("/tmp/screenshot.png")
            mock_excel.return_value = Path("/tmp/monthly_report.xlsx")

            workflow = UsageWorkflowRunner(config, query_date=target_date)
            result = await workflow.run()

            mock_login.assert_called_once_with(mock_page)
            mock_export.assert_called_once_with(mock_page, query_date=target_date)
            mock_dashboard.assert_called_once_with(mock_page)
            mock_excel.assert_called_once_with(
                artifact_or_path=artifact,
                screenshot_path=Path("/tmp/screenshot.png"),
                query_date=target_date,
            )
            assert result == Path("/tmp/monthly_report.xlsx")

    @pytest.mark.asyncio
    async def test_run_workflow_date_range(self, config: Config) -> None:
        """Test running workflow across a date range."""
        with (
            patch("cmp_automation.workflow.browser_context") as mock_browser_context,
            patch.object(CMPLogin, "login", new=AsyncMock()) as mock_login,
            patch.object(UsageQueryExporter, "export", new=AsyncMock()) as mock_export,
            patch.object(DashboardCapture, "capture", new=AsyncMock()) as mock_dashboard,
            patch.object(ExcelReportGenerator, "generate_report") as mock_excel,
            patch.object(MailboxClient, "disconnect", new=AsyncMock()),
        ):
            mock_browser = AsyncMock()
            mock_page = AsyncMock()
            mock_browser.new_page = AsyncMock(return_value=mock_page)
            mock_browser_context.return_value.__aenter__.return_value = mock_browser

            d1 = date(2026, 3, 1)
            d2 = date(2026, 3, 2)
            mock_export.side_effect = [
                UsageReportArtifact(raw_path=Path("/tmp/1.xlsx"), query_date=d1, rows=[]),
                UsageReportArtifact(raw_path=Path("/tmp/2.xlsx"), query_date=d2, rows=[]),
            ]
            mock_dashboard.return_value = Path("/tmp/screenshot.png")
            mock_excel.return_value = Path("/tmp/monthly_report.xlsx")

            workflow = UsageWorkflowRunner(config, start_date=d1, end_date=d2)
            result = await workflow.run()

            mock_login.assert_called_once_with(mock_page)
            assert mock_export.call_count == 2
            assert mock_dashboard.call_count == 2
            assert mock_excel.call_count == 2
            assert result == Path("/tmp/monthly_report.xlsx")

    @pytest.mark.asyncio
    async def test_run_workflow_disconnects_mailbox(self, config: Config) -> None:
        """Test that the workflow releases the IMAP connection after the run or error."""
        with (
            patch("cmp_automation.workflow.browser_context") as mock_browser_context,
            patch.object(CMPLogin, "login", new=AsyncMock()),
            patch.object(UsageQueryExporter, "export", new=AsyncMock()) as mock_export,
            patch.object(MailboxClient, "disconnect", new=AsyncMock()) as mock_disconnect,
        ):
            mock_browser = AsyncMock()
            mock_page = AsyncMock()
            mock_browser.new_page = AsyncMock(return_value=mock_page)
            mock_browser_context.return_value.__aenter__.return_value = mock_browser
            mock_export.side_effect = Exception("Export error")

            workflow = UsageWorkflowRunner(config, query_date=date(2026, 3, 1))
            with pytest.raises(Exception, match="Export error"):
                await workflow.run()

            mock_disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_workflow_convenience_function(self, config: Config) -> None:
        """Test the run_workflow convenience function."""
        with patch("cmp_automation.workflow.UsageWorkflowRunner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run = AsyncMock(return_value=Path("/tmp/report.xlsx"))
            mock_runner_cls.return_value = mock_runner

            result = await run_workflow(
                config,
                headed=True,
                dry_run=False,
                query_date=date(2026, 3, 1),
            )

            mock_runner_cls.assert_called_once_with(
                config=config,
                headed=True,
                dry_run=False,
                diagnose_export=False,
                diagnose_auth=False,
                query_date=date(2026, 3, 1),
                start_date=None,
                end_date=None,
                monitor_cycles=None,
                connectivity_enabled=None,
                allow_connectivity_mutation=None,
                mode="full",
                raw_xlsx=None,
                image_path=None,
                skip_screenshot=False,
            )
            assert result == Path("/tmp/report.xlsx")

    @pytest.mark.asyncio
    async def test_scrape_mode_only_runs_export_and_capture(self, config: Config) -> None:
        """Scrape mode returns the raw artifact and does not generate Excel."""
        with (
            patch("cmp_automation.workflow.browser_context") as mock_browser_context,
            patch.object(CMPLogin, "login", new=AsyncMock()),
            patch.object(UsageQueryExporter, "export", new=AsyncMock()) as mock_export,
            patch.object(DashboardCapture, "capture", new=AsyncMock()) as mock_dashboard,
            patch.object(ExcelReportGenerator, "generate_report") as mock_excel,
            patch.object(MailboxClient, "disconnect", new=AsyncMock()),
        ):
            mock_browser = AsyncMock()
            mock_page = AsyncMock()
            mock_browser.new_page = AsyncMock(return_value=mock_page)
            mock_browser_context.return_value.__aenter__.return_value = mock_browser

            d = date(2026, 3, 1)
            raw_path = config.excel_output_dir / "raw" / "report.xlsx"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(b"dummy")
            mock_export.return_value = UsageReportArtifact(raw_path=raw_path, query_date=d, rows=[])
            mock_dashboard.return_value = config.excel_output_dir / "images" / "dashboard_test.png"

            workflow = UsageWorkflowRunner(config, query_date=d, mode="scrape")
            result = await workflow.run()

            assert result == raw_path
            mock_export.assert_called_once()
            mock_dashboard.assert_called_once()
            mock_excel.assert_not_called()

    @pytest.mark.asyncio
    async def test_generate_mode_auto_discovery_matches_next_day_export(
        self, config: Config
    ) -> None:
        """Discovery accepts a next-day timestamp file holding target_date rows.

        The daily run on day H at 00:30 exports H-1 data under day H's
        timestamp, so `report_{target+1}_*` is the correct file and the
        same-day-timestamp file holding other-day data must be rejected.
        """
        target = date(2026, 3, 1)
        raw_dir = config.excel_output_dir / "raw"
        image_dir = config.excel_output_dir / "images"
        # Pin artifact dirs: the local .env may point them elsewhere.
        config.raw_xlsx_dir = raw_dir
        config.image_dir = image_dir

        raw_dir.mkdir(parents=True, exist_ok=True)

        correct = _write_usage_xlsx(
            raw_dir / "report_20260302_003000_DAILY_USAGE_by_SIM.xlsx", target
        )
        wrong_day = _write_usage_xlsx(
            raw_dir / "report_20260301_120000_DAILY_USAGE_by_SIM.xlsx", date(2026, 2, 28)
        )
        # Wrong-day file is newer on disk; only its content decides, not mtime.
        os.utime(wrong_day, (1_800_000_000, 1_800_000_000))

        image_dir.mkdir(parents=True, exist_ok=True)
        next_day_image = image_dir / "dashboard_20260302_003000.png"
        same_day_image = image_dir / "dashboard_20260301_120000.png"
        next_day_image.write_bytes(b"next-day")
        same_day_image.write_bytes(b"same-day")
        os.utime(same_day_image, (1_800_000_000, 1_800_000_000))

        with patch.object(ExcelReportGenerator, "generate_report") as mock_excel:
            mock_excel.return_value = config.excel_output_dir / "report.xlsx"
            workflow = UsageWorkflowRunner(config, query_date=target, mode="generate")
            await workflow.run()

            mock_excel.assert_called_once_with(
                artifact_or_path=correct,
                screenshot_path=next_day_image,
                query_date=target,
            )

    @pytest.mark.asyncio
    async def test_generate_mode_auto_discovery_falls_back_to_download_dir(
        self, config: Config
    ) -> None:
        """A matching export staged in download_dir is still discovered."""
        target = date(2026, 3, 1)
        # Pin artifact dirs: the local .env may point them elsewhere.
        config.raw_xlsx_dir = config.excel_output_dir / "raw"
        config.image_dir = config.excel_output_dir / "images"
        config.excel_output_dir.joinpath("raw").mkdir(parents=True, exist_ok=True)
        config.excel_output_dir.joinpath("images").mkdir(parents=True, exist_ok=True)
        config.download_dir.mkdir(parents=True, exist_ok=True)
        staged = _write_usage_xlsx(
            config.download_dir / "report_20260302_003000_DAILY_USAGE_by_SIM.xlsx", target
        )

        with patch.object(ExcelReportGenerator, "generate_report") as mock_excel:
            mock_excel.return_value = config.excel_output_dir / "report.xlsx"
            workflow = UsageWorkflowRunner(config, query_date=target, mode="generate")
            await workflow.run()

            mock_excel.assert_called_once_with(
                artifact_or_path=staged,
                screenshot_path=None,
                query_date=target,
            )

    @pytest.mark.asyncio
    async def test_generate_mode_auto_discovery_raises_when_no_date_matches(
        self, config: Config
    ) -> None:
        """No candidate carries target_date rows, so discovery fails loudly."""
        target = date(2026, 3, 1)
        raw_dir = config.excel_output_dir / "raw"
        # Pin artifact dirs: the local .env may point them elsewhere.
        config.raw_xlsx_dir = raw_dir
        raw_dir.mkdir(parents=True, exist_ok=True)
        _write_usage_xlsx(
            raw_dir / "report_20260301_120000_DAILY_USAGE_by_SIM.xlsx", date(2026, 2, 28)
        )

        workflow = UsageWorkflowRunner(config, query_date=target, mode="generate")
        with pytest.raises(WorkflowError, match="matching raw file"):
            await workflow.run()

    @pytest.mark.asyncio
    async def test_generate_mode_consumes_raw_file(self, config: Config, tmp_path: Path) -> None:
        """Generate mode does not launch browser and runs ExcelReportGenerator."""
        raw_file = tmp_path / "raw.xlsx"
        raw_file.write_bytes(b"dummy")
        img_file = tmp_path / "img.png"
        img_file.write_bytes(b"dummy")

        with patch.object(ExcelReportGenerator, "generate_report") as mock_excel:
            mock_excel.return_value = tmp_path / "report.xlsx"
            workflow = UsageWorkflowRunner(
                config,
                query_date=date(2026, 3, 1),
                mode="generate",
                raw_xlsx=raw_file,
                image_path=img_file,
            )
            result = await workflow.run()
            assert result == tmp_path / "report.xlsx"
            mock_excel.assert_called_once_with(
                artifact_or_path=raw_file,
                screenshot_path=img_file,
                query_date=date(2026, 3, 1),
            )

    @pytest.mark.asyncio
    async def test_auto_warp_proxy_injection_success(self, config: Config) -> None:
        """Test auto-WARP proxy is prepared and injected into config.cmp_proxy_server."""
        config.warp_auto_connect = True
        config.warp_mode = "proxy"
        config.warp_proxy_port = 40000
        config.cmp_proxy_server = None

        with (
            patch("cmp_automation.workflow.browser_context") as mock_browser_context,
            patch.object(WarpClient, "prepare_proxy", return_value=True) as mock_prepare,
            patch("cmp_automation.workflow.shutil.which", return_value="/usr/bin/warp-cli"),
            patch.object(CMPLogin, "login", new=AsyncMock()),
            patch.object(UsageQueryExporter, "export", new=AsyncMock()) as mock_export,
            patch.object(DashboardCapture, "capture", return_value=Path("/tmp/screenshot.png")),
            patch.object(ExcelReportGenerator, "generate_report"),
            patch.object(MailboxClient, "disconnect", new=AsyncMock()),
        ):
            mock_browser = AsyncMock()
            mock_page = AsyncMock()
            mock_browser.new_page = AsyncMock(return_value=mock_page)
            mock_browser_context.return_value.__aenter__.return_value = mock_browser

            raw_path = config.excel_output_dir / "raw" / "report.xlsx"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(b"dummy")
            mock_export.return_value = UsageReportArtifact(raw_path=raw_path, query_date=date(2026, 3, 1), rows=[])

            workflow = UsageWorkflowRunner(config, query_date=date(2026, 3, 1), mode="scrape")
            await workflow.run()

            mock_prepare.assert_called_once_with(40000)
            assert config.cmp_proxy_server == "socks5://127.0.0.1:40000"

    @pytest.mark.asyncio
    async def test_auto_warp_proxy_preserves_existing_proxy(self, config: Config) -> None:
        """Test auto-WARP proxy does not overwrite already configured proxy server."""
        config.warp_auto_connect = True
        config.warp_mode = "proxy"
        config.warp_proxy_port = 40000
        config.cmp_proxy_server = "http://my-proxy:8080"

        with (
            patch("cmp_automation.workflow.browser_context") as mock_browser_context,
            patch.object(WarpClient, "prepare_proxy", return_value=True) as mock_prepare,
            patch("cmp_automation.workflow.shutil.which", return_value="/usr/bin/warp-cli"),
            patch.object(CMPLogin, "login", new=AsyncMock()),
            patch.object(UsageQueryExporter, "export", new=AsyncMock()) as mock_export,
            patch.object(DashboardCapture, "capture", return_value=Path("/tmp/screenshot.png")),
            patch.object(ExcelReportGenerator, "generate_report"),
            patch.object(MailboxClient, "disconnect", new=AsyncMock()),
        ):
            mock_browser = AsyncMock()
            mock_page = AsyncMock()
            mock_browser.new_page = AsyncMock(return_value=mock_page)
            mock_browser_context.return_value.__aenter__.return_value = mock_browser

            raw_path = config.excel_output_dir / "raw" / "report.xlsx"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(b"dummy")
            mock_export.return_value = UsageReportArtifact(raw_path=raw_path, query_date=date(2026, 3, 1), rows=[])

            workflow = UsageWorkflowRunner(config, query_date=date(2026, 3, 1), mode="scrape")
            await workflow.run()

            mock_prepare.assert_called_once_with(40000)
            assert config.cmp_proxy_server == "http://my-proxy:8080"

    @pytest.mark.asyncio
    async def test_auto_warp_proxy_failure_soft(self, config: Config, caplog: pytest.LogCaptureFixture) -> None:
        """Test that failure to prepare WARP proxy logs warning and continues."""
        config.warp_auto_connect = True
        config.warp_mode = "proxy"
        config.warp_proxy_port = 40000
        config.cmp_proxy_server = None

        with (
            patch("cmp_automation.workflow.browser_context") as mock_browser_context,
            patch.object(WarpClient, "prepare_proxy", return_value=False) as mock_prepare,
            patch("cmp_automation.workflow.shutil.which", return_value="/usr/bin/warp-cli"),
            patch.object(CMPLogin, "login", new=AsyncMock()),
            patch.object(UsageQueryExporter, "export", new=AsyncMock()) as mock_export,
            patch.object(DashboardCapture, "capture", return_value=Path("/tmp/screenshot.png")),
            patch.object(ExcelReportGenerator, "generate_report"),
            patch.object(MailboxClient, "disconnect", new=AsyncMock()),
        ):
            mock_browser = AsyncMock()
            mock_page = AsyncMock()
            mock_browser.new_page = AsyncMock(return_value=mock_page)
            mock_browser_context.return_value.__aenter__.return_value = mock_browser

            raw_path = config.excel_output_dir / "raw" / "report.xlsx"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(b"dummy")
            mock_export.return_value = UsageReportArtifact(raw_path=raw_path, query_date=date(2026, 3, 1), rows=[])

            workflow = UsageWorkflowRunner(config, query_date=date(2026, 3, 1), mode="scrape")
            await workflow.run()

            mock_prepare.assert_called_once_with(40000)
            assert config.cmp_proxy_server is None
            assert "WARP auto-connect could not be established" in caplog.text

    @pytest.mark.asyncio
    async def test_auto_warp_proxy_fallback_when_warp_mode_is_warp(
        self, config: Config, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test warp_mode='warp' without full-tunnel mutation falls back to SOCKS5 proxy."""
        caplog.set_level(logging.INFO)
        config.warp_auto_connect = True
        config.warp_mode = "warp"
        config.warp_proxy_port = 40000
        config.cmp_proxy_server = None

        with (
            patch("cmp_automation.workflow.browser_context") as mock_browser_context,
            patch.object(WarpClient, "prepare_proxy", return_value=True) as mock_prepare,
            patch("cmp_automation.workflow.shutil.which", return_value="/usr/bin/warp-cli"),
            patch.object(CMPLogin, "login", new=AsyncMock()),
            patch.object(UsageQueryExporter, "export", new=AsyncMock()) as mock_export,
            patch.object(DashboardCapture, "capture", return_value=Path("/tmp/screenshot.png")),
            patch.object(ExcelReportGenerator, "generate_report"),
            patch.object(MailboxClient, "disconnect", new=AsyncMock()),
        ):
            mock_browser = AsyncMock()
            mock_page = AsyncMock()
            mock_browser.new_page = AsyncMock(return_value=mock_page)
            mock_browser_context.return_value.__aenter__.return_value = mock_browser

            raw_path = config.excel_output_dir / "raw" / "report.xlsx"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(b"dummy")
            mock_export.return_value = UsageReportArtifact(raw_path=raw_path, query_date=date(2026, 3, 1), rows=[])

            workflow = UsageWorkflowRunner(config, query_date=date(2026, 3, 1), mode="scrape")
            await workflow.run()

            mock_prepare.assert_called_once_with(40000)
            assert config.cmp_proxy_server == "socks5://127.0.0.1:40000"
            assert "WARP auto-connect utilizing isolated SOCKS5 proxy mode on port 40000" in caplog.text

    @pytest.mark.asyncio
    async def test_auto_warp_proxy_skipped_when_full_tunnel_mutation_enabled(
        self, config: Config, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test warp_mode='warp' with full-tunnel mutation skips SOCKS5 proxy preparation."""
        caplog.set_level(logging.INFO)
        config.warp_auto_connect = True
        config.warp_mode = "warp"
        config.warp_proxy_port = 40000
        config.cmp_proxy_server = None
        config.connectivity_enabled = True
        config.connectivity_allow_connect = True

        with (
            patch("cmp_automation.workflow.browser_context") as mock_browser_context,
            patch.object(WarpClient, "prepare_proxy") as mock_prepare,
            patch("cmp_automation.workflow.shutil.which", return_value="/usr/bin/warp-cli"),
            patch.object(ConnectivityController, "ensure_authentication_connectivity", new=AsyncMock()),
            patch.object(ConnectivityController, "release_authentication_connectivity", new=AsyncMock()),
            patch.object(ConnectivityController, "prepare_monitoring_connectivity", new=AsyncMock()),
            patch.object(CMPLogin, "login", new=AsyncMock()),
            patch.object(UsageQueryExporter, "export", new=AsyncMock()) as mock_export,
            patch.object(DashboardCapture, "capture", return_value=Path("/tmp/screenshot.png")),
            patch.object(ExcelReportGenerator, "generate_report"),
            patch.object(MailboxClient, "disconnect", new=AsyncMock()),
        ):
            mock_browser = AsyncMock()
            mock_page = AsyncMock()
            mock_browser.new_page = AsyncMock(return_value=mock_page)
            mock_browser_context.return_value.__aenter__.return_value = mock_browser

            raw_path = config.excel_output_dir / "raw" / "report.xlsx"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(b"dummy")
            mock_export.return_value = UsageReportArtifact(raw_path=raw_path, query_date=date(2026, 3, 1), rows=[])

            workflow = UsageWorkflowRunner(config, query_date=date(2026, 3, 1), mode="scrape")
            await workflow.run()

            mock_prepare.assert_not_called()
            assert config.cmp_proxy_server is None
            assert "WARP auto-connect utilizing isolated SOCKS5 proxy mode" not in caplog.text

    @pytest.mark.asyncio
    async def test_dry_run_calls_ensure_proxy_ready_before_browser_context(self, config: Config) -> None:
        """Test dry run calls _ensure_proxy_ready before launching browser_context."""
        call_order = []

        workflow = UsageWorkflowRunner(config, dry_run=True)

        async def fake_ensure_proxy():
            call_order.append("ensure_proxy")

        with (
            patch.object(workflow, "_ensure_proxy_ready", side_effect=fake_ensure_proxy) as mock_ensure,
            patch("cmp_automation.workflow.browser_context") as mock_browser_context,
            patch("cmp_automation.workflow.validate_paths"),
        ):
            mock_browser = AsyncMock()
            mock_browser.new_page = AsyncMock()

            class FakeBrowserContext:
                async def __aenter__(self):
                    call_order.append("browser_context")
                    return mock_browser

                async def __aexit__(self, *args):
                    pass

            mock_browser_context.return_value = FakeBrowserContext()

            await workflow.run()

            mock_ensure.assert_called_once()
            assert call_order == ["ensure_proxy", "browser_context"]

    @pytest.mark.asyncio
    async def test_ensure_proxy_ready_sets_socks5_proxy_and_logs(
        self, config: Config, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test that _ensure_proxy_ready sets SOCKS5 proxy URL and logs appropriate message."""
        caplog.set_level(logging.INFO)
        config.warp_auto_connect = True
        config.warp_mode = "proxy"
        config.warp_proxy_port = 40000
        config.cmp_proxy_server = None

        with (
            patch.object(WarpClient, "prepare_proxy", return_value=True) as mock_prepare,
            patch("cmp_automation.workflow.shutil.which", return_value="/usr/bin/warp-cli"),
        ):
            workflow = UsageWorkflowRunner(config)
            await workflow._ensure_proxy_ready()

            mock_prepare.assert_called_once_with(40000)
            assert config.cmp_proxy_server == "socks5://127.0.0.1:40000"
            assert "Auto-configured WARP SOCKS5 proxy: socks5://127.0.0.1:40000" in caplog.text
class TestSkipScreenshot:
    """skip_screenshot=True bypasses dashboard capture and embedding."""

    @pytest.mark.asyncio
    async def test_full_mode_skips_dashboard_capture(self, config: Config) -> None:
        """Full mode never calls DashboardCapture.capture and passes screenshot=None."""
        with (
            patch("cmp_automation.workflow.browser_context") as mock_browser_context,
            patch.object(CMPLogin, "login", new=AsyncMock()),
            patch.object(UsageQueryExporter, "export", new=AsyncMock()) as mock_export,
            patch.object(DashboardCapture, "capture", new=AsyncMock()) as mock_dashboard,
            patch.object(ExcelReportGenerator, "generate_report") as mock_excel,
            patch.object(MailboxClient, "disconnect", new=AsyncMock()),
        ):
            mock_browser = AsyncMock()
            mock_page = AsyncMock()
            mock_browser.new_page = AsyncMock(return_value=mock_page)
            mock_browser_context.return_value.__aenter__.return_value = mock_browser

            target_date = date(2026, 3, 1)
            artifact = UsageReportArtifact(
                raw_path=Path("/tmp/raw.xlsx"),
                query_date=target_date,
                rows=[{"date": "2026-03-01", "iccid": "123", "total_usage_bytes": 100}],
            )
            mock_export.return_value = artifact
            mock_excel.return_value = Path("/tmp/monthly_report.xlsx")

            workflow = UsageWorkflowRunner(
                config, query_date=target_date, mode="full", skip_screenshot=True
            )
            result = await workflow.run()

            mock_dashboard.assert_not_called()
            mock_excel.assert_called_once_with(
                artifact_or_path=artifact,
                screenshot_path=None,
                query_date=target_date,
            )
            assert result == Path("/tmp/monthly_report.xlsx")

    @pytest.mark.asyncio
    async def test_scrape_mode_skips_dashboard_capture(self, config: Config) -> None:
        """Scrape mode also bypasses capture when screenshots are suppressed."""
        with (
            patch("cmp_automation.workflow.browser_context") as mock_browser_context,
            patch.object(CMPLogin, "login", new=AsyncMock()),
            patch.object(UsageQueryExporter, "export", new=AsyncMock()) as mock_export,
            patch.object(DashboardCapture, "capture", new=AsyncMock()) as mock_dashboard,
            patch.object(MailboxClient, "disconnect", new=AsyncMock()),
        ):
            mock_browser = AsyncMock()
            mock_page = AsyncMock()
            mock_browser.new_page = AsyncMock(return_value=mock_page)
            mock_browser_context.return_value.__aenter__.return_value = mock_browser
            raw_path = config.excel_output_dir / "raw" / "report.xlsx"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(b"dummy")
            mock_export.return_value = UsageReportArtifact(
                raw_path=raw_path, query_date=date(2026, 3, 1), rows=[]
            )

            workflow = UsageWorkflowRunner(
                config, query_date=date(2026, 3, 1), mode="scrape", skip_screenshot=True
            )
            result = await workflow.run()

            mock_dashboard.assert_not_called()
            assert result == raw_path

    @pytest.mark.asyncio
    async def test_generate_mode_skips_image_discovery(self, config: Config) -> None:
        """Generate mode ignores an existing image and passes screenshot=None."""
        target = date(2026, 3, 1)
        config.raw_xlsx_dir = config.excel_output_dir / "raw"
        config.image_dir = config.excel_output_dir / "images"
        config.excel_output_dir.joinpath("raw").mkdir(parents=True, exist_ok=True)
        config.excel_output_dir.joinpath("images").mkdir(parents=True, exist_ok=True)
        raw_file = _write_usage_xlsx(
            config.raw_xlsx_dir / "report_20260302_003000_DAILY_USAGE_by_SIM.xlsx", target
        )
        # A matching screenshot exists but must never be picked up.
        (config.image_dir / "dashboard_20260302_003000.png").write_bytes(b"next-day")

        with patch.object(ExcelReportGenerator, "generate_report") as mock_excel:
            mock_excel.return_value = config.excel_output_dir / "report.xlsx"
            workflow = UsageWorkflowRunner(
                config, query_date=target, mode="generate", skip_screenshot=True
            )
            await workflow.run()

            mock_excel.assert_called_once_with(
                artifact_or_path=raw_file,
                screenshot_path=None,
                query_date=target,
            )

    @pytest.mark.asyncio
    async def test_generate_mode_ignores_explicit_image_path(
        self, config: Config, tmp_path: Path
    ) -> None:
        """An explicit --image is dropped when screenshots are suppressed."""
        raw_file = tmp_path / "raw.xlsx"
        raw_file.write_bytes(b"dummy")
        img_file = tmp_path / "img.png"
        img_file.write_bytes(b"dummy")

        with patch.object(ExcelReportGenerator, "generate_report") as mock_excel:
            mock_excel.return_value = tmp_path / "report.xlsx"
            workflow = UsageWorkflowRunner(
                config,
                query_date=date(2026, 3, 1),
                mode="generate",
                raw_xlsx=raw_file,
                image_path=img_file,
                skip_screenshot=True,
            )
            await workflow.run()

            mock_excel.assert_called_once_with(
                artifact_or_path=raw_file,
                screenshot_path=None,
                query_date=date(2026, 3, 1),
            )

    @pytest.mark.asyncio
    async def test_run_workflow_forwards_skip_screenshot(self, config: Config) -> None:
        """run_workflow() passes skip_screenshot into the runner."""
        with patch("cmp_automation.workflow.UsageWorkflowRunner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run = AsyncMock(return_value=Path("/tmp/report.xlsx"))
            mock_runner_cls.return_value = mock_runner

            await run_workflow(config, query_date=date(2026, 3, 1), skip_screenshot=True)

            assert mock_runner_cls.call_args.kwargs["skip_screenshot"] is True
