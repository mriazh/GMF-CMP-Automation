"""Dashboard screenshot capture for CMP Portal."""

import logging
from pathlib import Path

from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .config import Config
from .exceptions import DashboardError
from .utils import is_approved_portal_url, wait_for_portal_url

logger = logging.getLogger(__name__)


class DashboardCapture:
    """Handles dashboard screenshot capture."""

    # Selectors for dashboard container. The primary selector matches the
    # exact live DOM shape (class-based only) - the [width="100%"] attribute
    # does not exist in the rendered element.
    DASHBOARD_CONTAINER_SELECTORS = [
        "div.v-csslayout.v-layout.v-widget.sparks.v-csslayout-sparks.v-has-width",
        "div.sparks.v-csslayout-sparks.v-has-width",
        '[class*="sparks"][class*="v-csslayout"][style*="width: 100%"]',
        ".dashboard-container",
        ".v-dashboard",
        '[data-testid="dashboard"]',
    ]

    # Selectors for loading indicators
    LOADING_SELECTORS = [
        '.v-loading-indicator',
        '.loading',
        '[aria-busy="true"]',
        '.v-progressbar',
    ]

    # Safe structural DOM diagnostic for the dashboard failure path: reports
    # only element counts and tag/id/class/role summaries - never element
    # text, so dashboard cell content, customer names, or SIM numbers cannot
    # leak into the raised error.
    _DASHBOARD_DIAGNOSTIC_JS = """() => {
  const count = (sel) => document.querySelectorAll(sel).length;
  const summarize = (sel, max) => {
    const out = [];
    for (const el of Array.from(document.querySelectorAll(sel)).slice(0, max)) {
      const cls = typeof el.className === 'string'
        ? el.className.trim().split(' ').filter(Boolean).join('.')
        : '';
      out.push(
        el.tagName.toLowerCase() +
        (el.id ? '#' + el.id : '') +
        (cls ? '.' + cls : '') +
        (el.getAttribute('role') ? '[role=' + el.getAttribute('role') + ']' : '')
      );
    }
    return out;
  };
  return {
    sparks: count('div.sparks, .v-csslayout-sparks'),
    csslayouts: count('[class*="v-csslayout"]'),
    grids: count('[role="grid"], .v-grid'),
    windows: count('.v-window'),
    dialogs: count('[role="dialog"]'),
    bodyChildren: summarize('body > *', 10)
  };
}"""

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

    def _is_dashboard_url(self, url: str) -> bool:
        """Strict check that ``url`` is the exact configured Dashboard URL.

        Requires HTTPS, the exact approved hostname (never substring-only),
        a root path, and the exact ``!dashboard`` fragment. Mirrors the
        validation in ``CMPLogin`` so navigation and authentication agree.
        """
        return is_approved_portal_url(url, self.config.cmp_dashboard_url, "!dashboard")

    async def _navigate_to_dashboard(self, page: Page) -> None:
        """Ensure the page is at the exact configured Dashboard URL.

        The Vaadin SPA keeps background requests active, so ``networkidle``
        is not a valid readiness signal; ``domcontentloaded`` plus the
        dashboard container wait that follows are the readiness conditions.
        When the page is already at the exact Dashboard URL, navigation is
        skipped entirely.
        """
        logger.debug("Navigating to dashboard page")
        if self._is_dashboard_url(page.url):
            logger.debug("Already at exact dashboard URL; skipping navigation")
            return
        try:
            await page.goto(self.config.cmp_dashboard_url, wait_until="domcontentloaded")
        except PlaywrightTimeoutError as e:
            raise DashboardError("Timeout navigating to dashboard page", str(e)) from e
        if not await wait_for_portal_url(page, self._is_dashboard_url):
            raise DashboardError(
                "Dashboard URL was not reached after navigation",
                f"URL: {page.url}",
            )

    def _combined_dashboard_selector(self) -> str:
        """Combine all dashboard container candidates into a single CSS selector list."""
        return ", ".join(self.DASHBOARD_CONTAINER_SELECTORS)

    async def _wait_for_dashboard_load(self, page: Page) -> None:
        """Wait for dashboard widgets to finish loading.

        All container candidates are combined into one CSS selector list, so
        the wait costs at most 30 seconds regardless of candidate count (never
        30s per selector). The primary candidate is the exact live DOM shape
        (class-based); the ``[width="100%"]`` attribute does not exist.
        """
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
            await page.locator(self._combined_dashboard_selector()).filter(
                visible=True
            ).first.wait_for(state="visible", timeout=30000)
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
        """Collect a bounded, structural-only DOM diagnostic.

        The in-page script reports only tag names, ids, classes, roles, and
        element counts. Element text content is never read, so dashboard cell
        content, customer names, or SIM numbers cannot leak into the raised
        error.
        """
        try:
            info = await page.evaluate(self._DASHBOARD_DIAGNOSTIC_JS)
        except Exception:
            return "Unable to retrieve DOM summary"
        if not isinstance(info, dict):
            return "Unable to retrieve DOM summary"
        parts = []
        for key, value in info.items():
            if isinstance(value, list):
                parts.append(
                    f"{key}[{len(value)}]: " + " | ".join(str(v) for v in value[:10])
                )
            else:
                parts.append(f"{key}={value}")
        if not parts:
            return "Unable to retrieve DOM summary"
        summary = " | ".join(parts)
        return summary[:2000]
