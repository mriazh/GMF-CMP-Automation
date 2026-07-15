"""Dashboard screenshot capture for CMP Portal."""

import logging
from pathlib import Path

from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .config import Config
from .exceptions import DashboardError

logger = logging.getLogger(__name__)


class DashboardCapture:
    """Handles dashboard screenshot capture."""

    # Selectors for dashboard container
    DASHBOARD_CONTAINER_SELECTORS = [
        'div.v-csslayout.v-layout.v-widget.sparks.v-csslayout-sparks.v-has-width[width="100%"]',
        'div.v-csslayout.v-layout.v-widget.sparks.v-csslayout-sparks.v-has-width',
        'div.sparks.v-csslayout-sparks.v-has-width',
        '[class*="sparks"][class*="v-csslayout"][style*="width: 100%"]',
        '.dashboard-container',
        '.v-dashboard',
        '[data-testid="dashboard"]',
    ]

    # Selectors for loading indicators
    LOADING_SELECTORS = [
        '.v-loading-indicator',
        '.loading',
        '[aria-busy="true"]',
        '.v-progressbar',
    ]

    def __init__(self, config: Config):
        self.config = config

    async def capture_dashboard(self, page: Page, output_path: Path) -> Path:
        """Capture dashboard screenshot and save to output path."""
        logger.info("Starting dashboard capture")

        await self._navigate_to_dashboard(page)
        await self._wait_for_dashboard_load(page)
        container = await self._find_dashboard_container(page)
        await self._capture_element_screenshot(container, output_path)

        logger.info("Dashboard screenshot saved to: %s", output_path)
        return output_path

    async def _navigate_to_dashboard(self, page: Page) -> None:
        """Navigate to dashboard page."""
        logger.debug("Navigating to dashboard page")
        try:
            await page.goto(self.config.cmp_dashboard_url, wait_until="networkidle")
        except PlaywrightTimeoutError as e:
            raise DashboardError("Timeout navigating to dashboard page", str(e)) from e

    async def _wait_for_dashboard_load(self, page: Page) -> None:
        """Wait for dashboard widgets to finish loading."""
        logger.debug("Waiting for dashboard to load")

        # Wait for loading indicators to disappear
        for selector in self.LOADING_SELECTORS:
            try:
                await page.locator(selector).first.wait_for(state="hidden", timeout=30000)
                logger.debug("Loading indicator hidden: %s", selector)
            except PlaywrightTimeoutError:
                # Loading indicator might not exist or already hidden
                pass

        await page.wait_for_load_state("domcontentloaded")
        try:
            await page.locator(self.DASHBOARD_CONTAINER_SELECTORS[0]).first.wait_for(
                state="visible", timeout=30000
            )
        except PlaywrightTimeoutError as e:
            raise DashboardError("Dashboard container did not become visible") from e

    async def _find_dashboard_container(self, page: Page) -> Locator:
        """Find the dashboard container element."""
        logger.debug("Finding dashboard container")

        for selector in self.DASHBOARD_CONTAINER_SELECTORS:
            try:
                element = page.locator(selector).first
                if await element.count() > 0 and await element.is_visible():
                    box = await element.bounding_box()
                    width = await element.evaluate("el => getComputedStyle(el).width")
                    if box and box["width"] > 0 and box["height"] > 0 and ("100%" in str(width) or selector != self.DASHBOARD_CONTAINER_SELECTORS[0]):
                        logger.debug("Found dashboard container with selector: %s", selector)
                        return element
            except Exception:
                continue

        # Diagnostic info
        current_url = page.url
        dom_summary = await self._get_dom_summary(page)
        raise DashboardError(
            "Dashboard container not found",
            f"URL: {current_url}, DOM: {dom_summary}",
        )

    async def _capture_element_screenshot(self, element: Locator, output_path: Path) -> None:
        """Capture element screenshot."""
        logger.debug("Capturing element screenshot to: %s", output_path)
        try:
            await element.screenshot(path=str(output_path))
        except Exception as e:
            raise DashboardError("Failed to capture element screenshot", str(e)) from e

    async def _get_dom_summary(self, page: Page) -> str:
        """Get a brief DOM summary for diagnostics."""
        try:
            # Look for relevant dashboard-related elements
            body = page.locator("body")
            html = await body.inner_html()
            # Extract relevant parts
            lines = html.split("\n")[:50]
            return " | ".join(line.strip()[:200] for line in lines if line.strip())
        except Exception:
            return "Unable to retrieve DOM summary"
