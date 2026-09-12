"""Tests for BrowserManager."""

import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cmp_automation.browser import (
    BrowserManager,
    _clear_stale_firefox_lock,
    _firefox_process_running,
)
from cmp_automation.config import Config


class TestFirefoxProcessRunning:
    """Tests for cross-platform Firefox process detection."""

    def test_windows_firefox_running(self) -> None:
        """Windows: tasklist reports firefox.exe -> True."""
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout="firefox.exe    1234")
        with (
            patch("cmp_automation.browser.sys.platform", "win32"),
            patch("cmp_automation.browser.subprocess.run", return_value=result) as mock_run,
        ):
            assert _firefox_process_running() is True
        mock_run.assert_called_once()

    def test_windows_no_firefox(self) -> None:
        """Windows: tasklist reports no firefox.exe -> False."""
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout="Image Name         PID")
        with (
            patch("cmp_automation.browser.sys.platform", "win32"),
            patch("cmp_automation.browser.subprocess.run", return_value=result),
        ):
            assert _firefox_process_running() is False

    def test_windows_tasklist_failure_is_undetermined(self) -> None:
        """Windows: tasklist crash -> None (cannot determine)."""
        with (
            patch("cmp_automation.browser.sys.platform", "win32"),
            patch("cmp_automation.browser.subprocess.run", side_effect=OSError("no tasklist")),
        ):
            assert _firefox_process_running() is None

    def test_darwin_firefox_running(self) -> None:
        """macOS: pgrep -x firefox exit 0 -> True."""
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout="1234")
        with (
            patch("cmp_automation.browser.sys.platform", "darwin"),
            patch("cmp_automation.browser.subprocess.run", return_value=result) as mock_run,
        ):
            assert _firefox_process_running() is True
        args = mock_run.call_args[0][0]
        assert args == ["pgrep", "-x", "firefox"]

    def test_posix_firefox_running(self) -> None:
        """POSIX: pgrep -f firefox exit 0 -> True."""
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout="5678")
        with (
            patch("cmp_automation.browser.sys.platform", "linux"),
            patch("cmp_automation.browser.subprocess.run", return_value=result) as mock_run,
        ):
            assert _firefox_process_running() is True
        args = mock_run.call_args[0][0]
        assert args == ["pgrep", "-f", "firefox"]

    def test_posix_no_firefox(self) -> None:
        """POSIX: pgrep -f firefox exit 1 -> False."""
        result = subprocess.CompletedProcess(args=[], returncode=1, stdout="")
        with (
            patch("cmp_automation.browser.sys.platform", "linux"),
            patch("cmp_automation.browser.subprocess.run", return_value=result),
        ):
            assert _firefox_process_running() is False

    def test_posix_falls_back_to_proc(self) -> None:
        """POSIX: pgrep unavailable -> /proc scan finds firefox comm -> True."""
        with (
            patch("cmp_automation.browser.sys.platform", "linux"),
            patch(
                "cmp_automation.browser.subprocess.run",
                side_effect=FileNotFoundError("pgrep"),
            ),
            patch("cmp_automation.browser.os.listdir", return_value=["123", "notapid"]),
            patch("cmp_automation.browser.Path.read_text", return_value="firefox"),
        ):
            assert _firefox_process_running() is True


class TestClearStaleFirefoxLock:
    """Tests for cross-platform stale lock deletion."""

    def test_lock_deleted_when_firefox_not_running(self, tmp_path) -> None:
        """Lock is unlinked when no firefox process is confirmed running."""
        lock = tmp_path / "parent.lock"
        lock.write_text("stale")
        with patch("cmp_automation.browser._firefox_process_running", return_value=False):
            _clear_stale_firefox_lock(tmp_path)
        assert not lock.exists()

    def test_lock_kept_when_firefox_running(self, tmp_path) -> None:
        """Lock is kept when firefox is confirmed running."""
        lock = tmp_path / "parent.lock"
        lock.write_text("live")
        with patch("cmp_automation.browser._firefox_process_running", return_value=True):
            _clear_stale_firefox_lock(tmp_path)
        assert lock.exists()

    def test_lock_kept_when_state_undetermined(self, tmp_path) -> None:
        """Lock is kept when firefox state cannot be determined (fail-closed)."""
        lock = tmp_path / "parent.lock"
        lock.write_text("unknown")
        with patch("cmp_automation.browser._firefox_process_running", return_value=None):
            _clear_stale_firefox_lock(tmp_path)
        assert lock.exists()

    def test_no_lock_no_op(self, tmp_path) -> None:
        """No lock file -> no process check, no error."""
        with patch("cmp_automation.browser._firefox_process_running", return_value=False) as mock:
            _clear_stale_firefox_lock(tmp_path)
        mock.assert_not_called()


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
            mock_pw.firefox.launch_persistent_context = AsyncMock(
                side_effect=Exception("Launch failed")
            )
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
