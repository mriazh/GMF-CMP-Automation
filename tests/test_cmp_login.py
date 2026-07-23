"""Tests for the CMP login flow with IMAP-based OTP retrieval."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from cmp_automation.cmp_login import CMPLogin
from cmp_automation.config import Config
from cmp_automation.exceptions import AuthenticationError
from cmp_automation.mailbox import MailboxClient, OTPToken


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


def _token() -> OTPToken:
    """Build an OTPToken fixture."""
    return OTPToken(
        token="123456",
        email_subject="CMP - YOUR TOKEN",
        email_received_at=datetime(2024, 1, 15, 12, 0, 0, tzinfo=ZoneInfo("Asia/Jakarta")),
        email_body_preview="",
    )


class TestLoginFlow:
    """Tests for the CMP login orchestration."""

    @pytest.mark.asyncio
    async def test_connects_imap_before_submitting_credentials(self, config):
        """IMAP must connect (and take its UID snapshot) before credentials are submitted.

        This ordering guarantees the snapshot predates the OTP email arrival so the
        current run's OTP is never rejected as a stale leftover.
        """
        order: list[str] = []
        mailbox = MailboxClient(config)
        login = CMPLogin(config, mailbox)
        page = AsyncMock()

        def record(name: str):
            def _record(*args, **kwargs) -> None:
                order.append(name)
            return _record

        with patch.object(login, "_is_authenticated", new=AsyncMock(return_value=False)), \
             patch.object(login, "_navigate_to_login", new=AsyncMock()), \
             patch.object(mailbox, "connect", new=AsyncMock(side_effect=record("connect"))), \
             patch.object(login, "_fill_credentials", new=AsyncMock(side_effect=record("fill"))), \
             patch.object(login, "_submit_login", new=AsyncMock(side_effect=record("submit"))), \
             patch.object(login, "_wait_for_token_page", new=AsyncMock()), \
             patch.object(mailbox, "get_latest_otp", new=AsyncMock(return_value=_token())), \
             patch.object(login, "_submit_token", new=AsyncMock()), \
             patch.object(login, "_verify_authentication", new=AsyncMock()):
            await login.login(page)

        assert order == ["connect", "fill", "submit"]

    @pytest.mark.asyncio
    async def test_skips_imap_connect_when_session_reused(self, config):
        """No IMAP connect happens when an authenticated portal session is reused."""
        mailbox = MailboxClient(config)
        login = CMPLogin(config, mailbox)
        page = AsyncMock()

        with patch.object(login, "_is_authenticated", new=AsyncMock(return_value=True)), \
             patch.object(mailbox, "connect", new=AsyncMock()) as mock_connect, \
             patch.object(login, "_fill_credentials", new=AsyncMock()) as mock_fill:
            await login.login(page)

        mock_connect.assert_not_awaited()
        mock_fill.assert_not_awaited()


class TestPortalUrlValidation:
    """Tests for strict portal URL validation helpers."""

    @pytest.fixture
    def login(self, config):
        """Create a CMPLogin for URL validation tests."""
        return CMPLogin(config, MailboxClient(config))

    def test_exact_products_url_accepted(self, login):
        """Test that the exact approved Products URL is accepted."""
        assert login._is_products_page("https://ep.iotcc.telkomsel.com/#!products") is True

    def test_http_products_url_rejected(self, login):
        """Test that the Products URL over plain HTTP is rejected."""
        assert login._is_products_page("http://ep.iotcc.telkomsel.com/#!products") is False

    def test_malicious_host_rejected(self, login):
        """Test that an unrelated host is rejected."""
        assert login._is_products_page("https://evil.com/#!products") is False

    def test_lookalike_host_rejected(self, login):
        """Test that a lookalike host with the approved name embedded is rejected."""
        assert login._is_products_page(
            "https://ep.iotcc.telkomsel.com.evil.com/#!products"
        ) is False

    def test_subdomain_host_rejected(self, login):
        """Test that a subdomain of the approved host is rejected."""
        assert login._is_products_page(
            "https://sub.ep.iotcc.telkomsel.com/#!products"
        ) is False

    def test_wrong_fragment_rejected(self, login):
        """Test that the Products check rejects other fragments."""
        assert login._is_products_page("https://ep.iotcc.telkomsel.com/#!dashboard") is False
        assert login._is_products_page("https://ep.iotcc.telkomsel.com/#!report") is False

    def test_non_root_path_rejected(self, login):
        """Test that a non-root path is rejected."""
        assert login._is_products_page(
            "https://ep.iotcc.telkomsel.com/portal/#!products"
        ) is False

    def test_explicit_port_rejected(self, login):
        """Test that an explicit port on the approved host is rejected."""
        assert login._is_products_page(
            "https://ep.iotcc.telkomsel.com:8443/#!products"
        ) is False
        assert login._is_products_page(
            "https://ep.iotcc.telkomsel.com:443/#!products"
        ) is False

    def test_dashboard_url_strictly_validated(self, login):
        """Test that the Dashboard URL is accepted only with strict validation."""
        assert login._is_dashboard_page("https://ep.iotcc.telkomsel.com/#!dashboard") is True
        assert login._is_dashboard_page("http://ep.iotcc.telkomsel.com/#!dashboard") is False
        assert login._is_dashboard_page("https://evil.com/#!dashboard") is False
        assert login._is_dashboard_page("https://ep.iotcc.telkomsel.com/#!products") is False


class TestAuthenticatedState:
    """Tests for _is_authenticated and _verify_authentication."""

    @pytest.fixture
    def login(self, config):
        """Create a CMPLogin for authentication state tests."""
        return CMPLogin(config, MailboxClient(config))

    @pytest.mark.asyncio
    async def test_products_url_is_authenticated(self, login):
        """Test that the strictly validated Products URL is authenticated."""
        page = AsyncMock()
        page.url = "https://ep.iotcc.telkomsel.com/#!products"
        assert await login._is_authenticated(page) is True

    @pytest.mark.asyncio
    async def test_dashboard_url_is_authenticated(self, login):
        """Test that the strictly validated Dashboard URL is authenticated."""
        page = AsyncMock()
        page.url = "https://ep.iotcc.telkomsel.com/#!dashboard"
        assert await login._is_authenticated(page) is True

    @pytest.mark.asyncio
    async def test_root_page_without_fragment_is_not_authenticated(self, login):
        """Test that the root portal page without an authenticated fragment is rejected."""
        page = AsyncMock()
        page.url = "https://ep.iotcc.telkomsel.com/"
        page.evaluate = AsyncMock(return_value="https://ep.iotcc.telkomsel.com/")
        assert await login._is_authenticated(page) is False

    @pytest.mark.asyncio
    async def test_lookalike_host_is_not_authenticated(self, login):
        """Test that a lookalike host is never classified as authenticated."""
        page = AsyncMock()
        page.url = "http://ep.iotcc.telkomsel.com.evil.com/#!products"
        page.evaluate = AsyncMock(
            return_value="http://ep.iotcc.telkomsel.com.evil.com/#!products"
        )
        assert await login._is_authenticated(page) is False

    @pytest.mark.asyncio
    async def test_verify_authentication_succeeds_on_products_url(self, login):
        """Live-success-shaped page: exact Products URL confirms auth with no DOM checks."""
        page = AsyncMock()
        page.url = "https://ep.iotcc.telkomsel.com/#!products"
        await login._verify_authentication(page)
        page.locator.assert_not_called()
        page.wait_for_timeout.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_verify_authentication_uses_window_location_fallback(self, login):
        """Test that a lagging page.url is covered by the window.location fallback."""
        page = AsyncMock()
        page.url = "https://ep.iotcc.telkomsel.com/"  # page.url lags behind the SPA
        page.evaluate = AsyncMock(
            return_value="https://ep.iotcc.telkomsel.com/#!products"
        )
        await login._verify_authentication(page)
        page.wait_for_timeout.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_verify_authentication_raises_when_products_never_reached(self, login):
        """Test that verification raises if the Products URL is never reached."""
        page = AsyncMock()
        page.url = "https://ep.iotcc.telkomsel.com/"
        page.evaluate = AsyncMock(return_value="https://ep.iotcc.telkomsel.com/")
        # Real strings for the diagnostic DOM summary so no mock coroutines leak.
        locator = MagicMock()
        locator.inner_text = AsyncMock(return_value="CMP Portal")
        page.locator = MagicMock(return_value=locator)
        with pytest.raises(AuthenticationError, match="Products page was not reached"):
            await login._verify_authentication(page)
