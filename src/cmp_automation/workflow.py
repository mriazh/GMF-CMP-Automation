"""Main workflow orchestration for CMP Automation."""

import logging
from pathlib import Path

from playwright.async_api import Page

from .browser import BrowserManager, browser_context
from .cmp_login import CMPLogin
from .config import Config, load_config, validate_paths
from .dashboard import DashboardCapture
from .excel_report import ExcelReportGenerator
from .exceptions import (
    CMPAutomationError,
)
from .mailbox import MailboxClient
from .products import ProductsExporter

logger = logging.getLogger(__name__)


class CMPAutomationWorkflow:
    """Orchestrates the complete CMP automation workflow."""

    def __init__(
        self,
        config: Config,
        headed: bool = False,
        dry_run: bool = False,
        diagnose_export: bool = False,
        diagnose_auth: bool = False,
    ):
        self.config = config
        self.headed = headed
        self.dry_run = dry_run
        self.diagnose_export = diagnose_export
        self.diagnose_auth = diagnose_auth
        self.browser_manager: BrowserManager | None = None
        self.page: Page | None = None

        # Initialize components
        self.mailbox = MailboxClient(config)
        self.login = CMPLogin(config, self.mailbox, diagnose_auth=diagnose_auth)
        self.products_exporter = ProductsExporter(config, diagnose_export=diagnose_export)
        self.dashboard_capture = DashboardCapture(config)
        self.excel_generator = ExcelReportGenerator(config)

    async def run(self) -> Path:
        """Execute the complete workflow and return final report path."""
        logger.info("Starting CMP Automation workflow")

        if self.dry_run:
            return await self._dry_run()

        async with browser_context(self.config, self.headed) as browser:
            self.browser_manager = browser
            self.page = await browser.new_page()

            try:
                # Step 1: Login and authenticate
                await self.login.login(self.page)

                # Step 2: Export products
                xlsx_path = await self.products_exporter.export_products(self.page)

                # Step 3: Capture dashboard screenshot
                screenshot_path = self.config.download_dir / f"dashboard_{xlsx_path.stem}.png"
                await self.dashboard_capture.capture_dashboard(self.page, screenshot_path)

                # Step 4: Generate final report
                final_path = self.excel_generator.generate_report(xlsx_path, screenshot_path)

                logger.info("Workflow completed successfully: %s", final_path)
                return final_path

            except CMPAutomationError:
                raise
            except Exception as e:
                logger.exception("Unexpected error in workflow")
                raise CMPAutomationError("Workflow failed unexpectedly", str(e)) from e
            finally:
                # Always release the IMAP connection (no-op if never connected)
                await self.mailbox.disconnect()

    async def _dry_run(self) -> Path:
        """Perform a dry run - verify configuration and browser launch without login."""
        logger.info("Running in dry-run mode")

        # Validate config
        validate_paths(self.config)
        logger.info("Configuration validated")

        # Test browser launch
        async with browser_context(self.config, self.headed) as browser:
            page = await browser.new_page()
            await page.goto("about:blank")
            logger.info("Browser launch successful")

        logger.info("Dry run completed successfully")
        return Path("dry-run-success")


async def run_workflow(
    config: Config | None = None,
    headed: bool = False,
    dry_run: bool = False,
    diagnose_export: bool = False,
    diagnose_auth: bool = False,
) -> Path:
    """Convenience function to run the workflow."""
    if config is None:
        config = load_config()

    workflow = CMPAutomationWorkflow(
        config,
        headed=headed,
        dry_run=dry_run,
        diagnose_export=diagnose_export,
        diagnose_auth=diagnose_auth,
    )
    return await workflow.run()
