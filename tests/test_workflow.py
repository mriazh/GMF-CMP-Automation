"""Tests for workflow orchestration."""

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cmp_automation.cmp_login import CMPLogin
from cmp_automation.config import Config
from cmp_automation.dashboard import DashboardCapture
from cmp_automation.excel_report import ExcelReportGenerator
from cmp_automation.mailbox import MailboxClient
from cmp_automation.usage_query import UsageQueryExporter, UsageReportArtifact
from cmp_automation.workflow import UsageWorkflowRunner, run_workflow


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
