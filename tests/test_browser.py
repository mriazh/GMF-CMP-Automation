"""Tests for BrowserManager."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cmp_automation.browser import BrowserManager
from cmp_automation.config import Config


class TestBrowserManager:
    """Tests for BrowserManager class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_config = MagicMock(spec=Config)
        self.mock_config.firefox_profile_dir = "/tmp/test_profile"
        self.mock_config.download_dir = "/tmp/test_downloads"
        self.mock_config.timezone = "Asia/Jakarta"
        self.mock_config.cmp_proxy_server = None

    @pytest.mark.asyncio
    async def test_start_without_proxy(self):
        """Test browser start without proxy configuration."""
        with patch("cmp_automation.browser.async_playwright") as mock_playwright:
            mock_pw = AsyncMock()
            mock_browser_context = AsyncMock()
            mock_pw.firefox.launch_persistent_context = AsyncMock(return_value=mock_browser_context)
            mock_playwright.return_value.start = AsyncMock(return_value=mock_pw)

            with patch("cmp_automation.browser._clear_stale_firefox_lock"):
                manager = BrowserManager(self.mock_config, headed=False)
                result = await manager.start()

                # Verify launch_persistent_context was called without proxy
                call_kwargs = mock_pw.firefox.launch_persistent_context.call_args.kwargs
                assert "proxy" not in call_kwargs
                assert call_kwargs["user_data_dir"] == "/tmp/test_profile"
                assert call_kwargs["headless"] is True
                assert call_kwargs["downloads_path"] == "/tmp/test_downloads"
                assert call_kwargs["viewport"] == {"width": 1920, "height": 1080}
                assert call_kwargs["locale"] == "en-US"
                assert call_kwargs["timezone_id"] == "Asia/Jakarta"

                assert result == mock_browser_context

    @pytest.mark.asyncio
    async def test_start_with_proxy_socks5(self):
        """Test browser start with SOCKS5 proxy configuration."""
        self.mock_config.cmp_proxy_server = "socks5://127.0.0.1:40000"

        with patch("cmp_automation.browser.async_playwright") as mock_playwright:
            mock_pw = AsyncMock()
            mock_browser_context = AsyncMock()
            mock_pw.firefox.launch_persistent_context = AsyncMock(return_value=mock_browser_context)
            mock_playwright.return_value.start = AsyncMock(return_value=mock_pw)

            with patch("cmp_automation.browser._clear_stale_firefox_lock"):
                manager = BrowserManager(self.mock_config, headed=False)
                await manager.start()

                # Verify launch_persistent_context was called with proxy
                call_kwargs = mock_pw.firefox.launch_persistent_context.call_args.kwargs
                assert "proxy" in call_kwargs
                assert call_kwargs["proxy"] == {"server": "socks5://127.0.0.1:40000"}

    @pytest.mark.asyncio
    async def test_start_with_proxy_http(self):
        """Test browser start with HTTP proxy configuration."""
        self.mock_config.cmp_proxy_server = "http://proxy:8080"

        with patch("cmp_automation.browser.async_playwright") as mock_playwright:
            mock_pw = AsyncMock()
            mock_browser_context = AsyncMock()
            mock_pw.firefox.launch_persistent_context = AsyncMock(return_value=mock_browser_context)
            mock_playwright.return_value.start = AsyncMock(return_value=mock_pw)

            with patch("cmp_automation.browser._clear_stale_firefox_lock"):
                manager = BrowserManager(self.mock_config, headed=False)
                await manager.start()

                # Verify launch_persistent_context was called with proxy
                call_kwargs = mock_pw.firefox.launch_persistent_context.call_args.kwargs
                assert "proxy" in call_kwargs
                assert call_kwargs["proxy"] == {"server": "http://proxy:8080"}

    @pytest.mark.asyncio
    async def test_start_cleans_up_on_failure(self):
        """Test that cleanup is called on launch failure."""
        with patch("cmp_automation.browser.async_playwright") as mock_playwright:
            mock_pw = AsyncMock()
            mock_pw.firefox.launch_persistent_context = AsyncMock(side_effect=Exception("Launch failed"))
            mock_playwright.return_value.start = AsyncMock(return_value=mock_pw)

            with patch("cmp_automation.browser._clear_stale_firefox_lock"):
                manager = BrowserManager(self.mock_config, headed=False)

                with pytest.raises(Exception, match="Failed to launch Firefox"):
                    await manager.start()

                # Verify cleanup was called
                mock_pw.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_new_page_creates_if_no_pages(self):
        """Test new_page creates a new page when none exist."""
        with patch("cmp_automation.browser.async_playwright") as mock_playwright:
            mock_pw = AsyncMock()
            mock_browser_context = AsyncMock()
            mock_browser_context.pages = []
            mock_page = AsyncMock()
            mock_browser_context.new_page = AsyncMock(return_value=mock_page)
            mock_pw.firefox.launch_persistent_context = AsyncMock(return_value=mock_browser_context)
            mock_playwright.return_value.start = AsyncMock(return_value=mock_pw)

            with patch("cmp_automation.browser._clear_stale_firefox_lock"):
                manager = BrowserManager(self.mock_config, headed=False)
                await manager.start()
                page = await manager.new_page()

                assert page == mock_page
                mock_browser_context.new_page.assert_called_once()
                mock_page.set_default_timeout.assert_called_once_with(30000)

    @pytest.mark.asyncio
    async def test_new_page_reuses_existing(self):
        """Test new_page reuses existing page."""
        with patch("cmp_automation.browser.async_playwright") as mock_playwright:
            mock_pw = AsyncMock()
            mock_browser_context = AsyncMock()
            mock_page = AsyncMock()
            mock_browser_context.pages = [mock_page]
            mock_pw.firefox.launch_persistent_context = AsyncMock(return_value=mock_browser_context)
            mock_playwright.return_value.start = AsyncMock(return_value=mock_pw)

            with patch("cmp_automation.browser._clear_stale_firefox_lock"):
                manager = BrowserManager(self.mock_config, headed=False)
                await manager.start()
                page = await manager.new_page()

                assert page == mock_page
                mock_browser_context.new_page.assert_not_called()
                mock_page.set_default_timeout.assert_called_once_with(30000)

    @pytest.mark.asyncio
    async def test_cleanup_stops_playwright(self):
        """Test cleanup stops playwright."""
        with patch("cmp_automation.browser.async_playwright") as mock_playwright:
            mock_pw = AsyncMock()
            mock_browser_context = AsyncMock()
            mock_pw.firefox.launch_persistent_context = AsyncMock(return_value=mock_browser_context)
            mock_playwright.return_value.start = AsyncMock(return_value=mock_pw)

            with patch("cmp_automation.browser._clear_stale_firefox_lock"):
                manager = BrowserManager(self.mock_config, headed=False)
                await manager.start()
                await manager.cleanup()

                mock_pw.stop.assert_called_once()
