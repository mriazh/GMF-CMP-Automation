"""Browser management for CMP Automation."""

import logging
import subprocess
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from playwright.async_api import BrowserContext, Page, Playwright, ProxySettings, async_playwright

from .config import Config
from .exceptions import BrowserError

logger = logging.getLogger(__name__)


def _clear_stale_firefox_lock(profile_dir: Path) -> None:
    """Remove stale parent.lock from persistent profile if no Firefox process is running."""
    lock_file = profile_dir / "parent.lock"
    if not lock_file.exists():
        return
    try:
        if sys.platform == "win32":
            res = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq firefox.exe"],
                capture_output=True,
                text=True,
                check=False,
            )
            if "firefox.exe" in res.stdout.lower():
                return
        lock_file.unlink(missing_ok=True)
        logger.info("Cleared stale Firefox parent.lock from %s", profile_dir)
    except Exception as exc:
        logger.warning("Could not clear potential stale Firefox lock: %s", exc)


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

        _clear_stale_firefox_lock(self.config.firefox_profile_dir)

        # Prepare launch kwargs
        launch_kwargs: dict[str, object] = {
            "headless": not self.headed,
            "downloads_path": str(self.config.download_dir),
            "accept_downloads": True,
            "viewport": {"width": 1920, "height": 1080},
            "locale": "en-US",
            "timezone_id": self.config.timezone,
        }

        # Inject proxy if configured
        if self.config.cmp_proxy_server:
            launch_kwargs["proxy"] = ProxySettings(server=self.config.cmp_proxy_server)
            logger.info("Firefox proxy configured: %s", self.config.cmp_proxy_server)

        try:
            self._browser = await self._playwright.firefox.launch_persistent_context(
                user_data_dir=str(self.config.firefox_profile_dir),
                **launch_kwargs,  # type: ignore[arg-type]
            )
            logger.info("Firefox launched successfully with persistent context")
            return self._browser
        except Exception as e:
            await self.cleanup()
            raise BrowserError("Failed to launch Firefox") from e

    async def new_page(self) -> Page:
        """Create or reuse the initial page in the persistent context."""
        if not self._browser:
            raise BrowserError("Browser not started. Call start() first.")
        if self._browser.pages:
            page = self._browser.pages[0]
        else:
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
                logger.warning("Error closing browser context: %s", type(e).__name__)
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:
                logger.warning("Error stopping Playwright: %s", type(e).__name__)
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
