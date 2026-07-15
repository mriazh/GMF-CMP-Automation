"""Browser management for CMP Automation."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from .config import Config
from .exceptions import BrowserError

logger = logging.getLogger(__name__)


class BrowserManager:
    """Manages Firefox browser lifecycle with persistent context."""

    def __init__(self, config: Config, headed: bool = False):
        self.config = config
        self.headed = headed
        self._playwright: Playwright | None = None
        self._browser: BrowserContext | None = None

    async def start(self) -> BrowserContext:
        """Start Playwright and launch Firefox with persistent context."""
        logger.info("Starting Playwright and launching Firefox")
        self._playwright = await async_playwright().start()

        try:
            self._browser = await self._playwright.firefox.launch_persistent_context(
                user_data_dir=str(self.config.firefox_profile_dir),
                headless=not self.headed,
                downloads_path=str(self.config.download_dir),
                accept_downloads=True,
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
                timezone_id=self.config.timezone,
            )
            logger.info("Firefox launched successfully with persistent context")
            return self._browser
        except Exception as e:
            await self.cleanup()
            raise BrowserError("Failed to launch Firefox", str(e)) from e

    async def new_page(self) -> Page:
        """Create a new page in the persistent context."""
        if not self._browser:
            raise BrowserError("Browser not started. Call start() first.")
        page = await self._browser.new_page()
        page.set_default_timeout(30000)
        return page

    @property
    def context(self) -> BrowserContext:
        """Get the browser context."""
        if not self._browser:
            raise BrowserError("Browser not started. Call start() first.")
        return self._browser

    async def cleanup(self) -> None:
        """Clean up browser resources."""
        logger.info("Cleaning up browser resources")
        if self._browser:
            try:
                await self._browser.close()
            except Exception as e:
                logger.warning("Error closing browser context: %s", e)
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:
                logger.warning("Error stopping Playwright: %s", e)
            self._playwright = None

    async def __aenter__(self) -> "BrowserManager":
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        await self.cleanup()


@asynccontextmanager
async def browser_context(config: Config, headed: bool = False) -> AsyncGenerator[BrowserManager, None]:
    """Async context manager for browser lifecycle."""
    manager = BrowserManager(config, headed)
    try:
        await manager.start()
        yield manager
    finally:
        await manager.cleanup()
