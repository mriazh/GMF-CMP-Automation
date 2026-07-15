"""Tests for workflow orchestration."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from cmp_automation.cmp_login import CMPLogin
from cmp_automation.config import Config
from cmp_automation.dashboard import DashboardCapture
from cmp_automation.excel_report import ExcelReportGenerator
from cmp_automation.products import ProductsExporter
from cmp_automation.workflow import CMPAutomationWorkflow, run_workflow


@pytest.fixture
def config():
    """Create a test config."""
    return Config(
        cmp_username="test",
        cmp_password="test",
        gmf_email="test@test.com",
        gmf_password="test",
        firefox_profile_dir="/tmp/profile",
        download_dir="/tmp/downloads",
        timezone="Asia/Jakarta",
    )


class TestWorkflow:
    """Tests for workflow orchestration."""

    @pytest.mark.asyncio
    async def test_dry_run_success(self, config):
        """Test dry run completes successfully."""
        with patch("cmp_automation.workflow.browser_context") as mock_browser_context, \
             patch("cmp_automation.workflow.validate_paths") as mock_validate:
            mock_manager = AsyncMock()
            mock_page = AsyncMock()
            mock_manager.new_page.return_value = mock_page
            mock_browser_context.return_value.__aenter__.return_value = mock_manager
            workflow = CMPAutomationWorkflow(config, dry_run=True)
            result = await workflow.run()

            assert result.name == "dry-run-success"
            mock_browser_context.assert_called_once()
            mock_validate.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_workflow_calls_components(self, config):
        """Test that run_workflow calls all components in order."""
        with patch("cmp_automation.workflow.browser_context") as mock_browser_context, \
             patch.object(CMPLogin, "login") as mock_login, \
             patch.object(ProductsExporter, "export_products") as mock_products, \
             patch.object(DashboardCapture, "capture_dashboard") as mock_dashboard, \
             patch.object(ExcelReportGenerator, "generate_report") as mock_excel:

            mock_manager = AsyncMock()
            mock_page = AsyncMock()
            mock_manager.new_page.return_value = mock_page
            mock_browser_context.return_value.__aenter__.return_value = mock_manager

            mock_excel.return_value = Path("/tmp/final_report.xlsx")

            workflow = CMPAutomationWorkflow(config, dry_run=False)
            result = await workflow.run()

            mock_login.assert_called_once_with(mock_page)
            mock_products.assert_called_once_with(mock_page)
            mock_dashboard.assert_called_once()
            mock_excel.assert_called_once()

            assert result == Path("/tmp/final_report.xlsx")

    @pytest.mark.asyncio
    async def test_run_workflow_handles_errors(self, config):
        """Test that workflow handles errors properly."""
        with patch("cmp_automation.workflow.browser_context") as mock_browser_context, \
             patch.object(CMPLogin, "login") as mock_login:
            mock_manager = AsyncMock()
            mock_page = AsyncMock()
            mock_manager.new_page.return_value = mock_page
            mock_browser_context.return_value.__aenter__.return_value = mock_manager

            workflow = CMPAutomationWorkflow(config, dry_run=False)

            # Simulate login failure
            mock_login.side_effect = Exception("Login failed")
            with pytest.raises(Exception, match="Login failed"):
                await workflow.run()

    @pytest.mark.asyncio
    async def test_run_workflow_convenience_function(self, config):
        """Test the run_workflow convenience function."""
        with patch("cmp_automation.workflow.CMPAutomationWorkflow") as mock_workflow_class:
            mock_workflow = AsyncMock()
            mock_workflow.run.return_value = Path("/tmp/result.xlsx")
            mock_workflow_class.return_value = mock_workflow

            result = await run_workflow(config, headed=True, dry_run=False)

            assert result == Path("/tmp/result.xlsx")
            mock_workflow_class.assert_called_once_with(config, headed=True, dry_run=False)
            mock_workflow.run.assert_called_once()


class TestWorkflowDryRun:
    """Tests for dry run mode."""

    @pytest.mark.asyncio
    async def test_dry_run_validates_config(self, config):
        """Test that dry run validates configuration."""
        with patch("cmp_automation.workflow.validate_paths") as mock_validate, \
             patch("cmp_automation.workflow.browser_context") as mock_browser_context:

            mock_manager = AsyncMock()
            mock_page = AsyncMock()
            mock_manager.new_page.return_value = mock_page
            mock_browser_context.return_value.__aenter__.return_value = mock_manager

            workflow = CMPAutomationWorkflow(config, dry_run=True)
            await workflow.run()

            mock_validate.assert_called_once_with(config)

    @pytest.mark.asyncio
    async def test_dry_run_launches_browser(self, config):
        """Test that dry run launches browser."""
        with patch("cmp_automation.workflow.validate_paths") as mock_validate, \
             patch("cmp_automation.workflow.browser_context") as mock_browser_context:
            mock_manager = AsyncMock()
            mock_page = AsyncMock()
            mock_manager.new_page.return_value = mock_page
            mock_browser_context.return_value.__aenter__.return_value = mock_manager

            workflow = CMPAutomationWorkflow(config, dry_run=True)
            await workflow.run()

            mock_browser_context.assert_called_once()
            call_args = mock_browser_context.call_args
            assert call_args[0][0] is config  # First positional arg is config
            assert call_args[0][1] is False  # Second positional arg is headed=False
            mock_manager.new_page.assert_called_once()
            mock_page.goto.assert_called_once_with("about:blank")
            mock_validate.assert_called_once()
