"""Tests for the CMP login flow with IMAP-based OTP retrieval."""

from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from cmp_automation.cmp_login import CMPLogin
from cmp_automation.config import Config
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
