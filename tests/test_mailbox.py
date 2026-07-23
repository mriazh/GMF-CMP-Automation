"""Tests for the IMAP-based mailbox OTP retrieval with mocked IMAP dependencies."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from cmp_automation.config import Config
from cmp_automation.exceptions import OTPError, OTPTimeoutError
from cmp_automation.mailbox import MailboxClient, OTPToken, _OtpMessage


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


@pytest.fixture
def mailbox(config):
    """Create a mailbox client."""
    return MailboxClient(config)


def _token(received: datetime) -> OTPToken:
    """Build an OTPToken for the given received time."""
    return OTPToken(
        token="123456",
        email_subject="CMP - YOUR TOKEN",
        email_received_at=received,
        email_body_preview="",
    )


def _start() -> datetime:
    """Workflow start time used across tests."""
    return datetime(2024, 1, 15, 12, 0, 0, tzinfo=ZoneInfo("Asia/Jakarta"))


def _message(
    uid: str = "3",
    subject: str = "CMP - YOUR TOKEN",
    received: datetime | None = None,
    body: str = "Your CMP OTP token is 123456. Do not share it.",
) -> _OtpMessage:
    """Build an _OtpMessage fixture."""
    return _OtpMessage(
        uid=uid,
        subject=subject,
        received_at=received if received is not None else _start(),
        body=body,
    )


class TestGetLatestOtp:
    """Tests for OTP polling orchestration."""

    @pytest.mark.asyncio
    async def test_returns_token_when_found(self, mailbox):
        """Test that the token is returned immediately when found."""
        start = _start()
        expected = _token(start)
        mailbox.set_workflow_start_time(start)

        with patch.object(mailbox, "connect", new=AsyncMock()) as mock_connect, \
             patch.object(mailbox, "_poll_once", return_value=expected) as mock_poll:
            result = await mailbox.get_latest_otp("CMP - YOUR TOKEN", 120, 5)

        assert result is expected
        mock_connect.assert_awaited_once()
        mock_poll.assert_called_once_with("CMP - YOUR TOKEN", start)

    @pytest.mark.asyncio
    async def test_polls_until_token_found(self, mailbox):
        """Test that polling retries until the token appears."""
        start = _start()
        token = _token(start)
        mailbox.set_workflow_start_time(start)

        with patch.object(mailbox, "connect", new=AsyncMock()), \
             patch.object(mailbox, "_poll_once", side_effect=[None, token]) as mock_poll, \
             patch("asyncio.sleep", new=AsyncMock()):
            result = await mailbox.get_latest_otp("CMP - YOUR TOKEN", 10, 1)

        assert result is token
        assert mock_poll.call_count == 2

    @pytest.mark.asyncio
    async def test_times_out_when_token_never_appears(self, mailbox):
        """Test that OTPTimeoutError is raised when no email arrives in time."""
        start = _start()
        mailbox.set_workflow_start_time(start)

        with patch.object(mailbox, "connect", new=AsyncMock()), \
             patch.object(mailbox, "_poll_once", return_value=None):
            with pytest.raises(OTPTimeoutError, match="timed out"):
                await mailbox.get_latest_otp("CMP - YOUR TOKEN", 0, 1)

    @pytest.mark.asyncio
    async def test_timeout_message_contains_no_secrets(self, mailbox):
        """Test that the timeout message exposes no token or credential values."""
        start = _start()
        mailbox.set_workflow_start_time(start)

        with patch.object(mailbox, "connect", new=AsyncMock()), \
             patch.object(mailbox, "_poll_once", return_value=None):
            with pytest.raises(OTPTimeoutError) as excinfo:
                await mailbox.get_latest_otp("CMP - YOUR TOKEN", 0, 1)

        message = str(excinfo.value)
        assert "CMP - YOUR TOKEN" in message
        assert start.isoformat() in message
        assert "123456" not in message
        assert "test@test.com" not in message

    @pytest.mark.asyncio
    async def test_requires_workflow_start_time(self, mailbox):
        """Test that polling raises if workflow start time was never set."""
        with pytest.raises(OTPError, match="not set"):
            await mailbox.get_latest_otp("CMP - YOUR TOKEN", 0, 1)


class TestPollOnce:
    """Tests for the single IMAP poll cycle (filtering + newest selection)."""

    def test_returns_newest_valid_message(self, mailbox):
        """Test that the newest message at/after start time is selected."""
        start = _start()
        mailbox._connection = MagicMock()
        messages = [
            _message("1", received=datetime(2024, 1, 15, 12, 5, 0, tzinfo=ZoneInfo("Asia/Jakarta"))),
            _message("2", received=datetime(2024, 1, 15, 12, 10, 0, tzinfo=ZoneInfo("Asia/Jakarta"))),
        ]

        with patch.object(mailbox, "_refresh_mailbox", new=MagicMock()), \
             patch.object(mailbox, "_search_uids", return_value=[b"1", b"2"]), \
             patch.object(mailbox, "_fetch_message", side_effect=[("data",), ("data",)]), \
             patch.object(mailbox, "_parse_message", side_effect=messages):
            result = mailbox._poll_once("CMP - YOUR TOKEN", start)

        assert result is not None
        assert result.token == "123456"
        assert result.email_subject == "CMP - YOUR TOKEN"
        assert result.email_received_at == datetime(
            2024, 1, 15, 12, 10, 0, tzinfo=ZoneInfo("Asia/Jakarta")
        )

    def test_rejects_all_older_emails(self, mailbox):
        """Test that no email older than the tolerance cutoff is accepted."""
        start = _start()
        mailbox._connection = MagicMock()
        messages = [
            # 3 minutes early - outside the 120s clock-skew tolerance window
            _message("1", received=datetime(2024, 1, 15, 11, 57, 0, tzinfo=ZoneInfo("Asia/Jakarta"))),
            _message("2", received=datetime(2024, 1, 14, 23, 59, 0, tzinfo=ZoneInfo("Asia/Jakarta"))),
        ]

        with patch.object(mailbox, "_refresh_mailbox", new=MagicMock()), \
             patch.object(mailbox, "_search_uids", return_value=[b"1", b"2"]), \
             patch.object(mailbox, "_fetch_message", side_effect=[("data",), ("data",)]), \
             patch.object(mailbox, "_parse_message", side_effect=messages):
            result = mailbox._poll_once("CMP - YOUR TOKEN", start)

        assert result is None

    def test_accepts_email_exactly_at_start_time(self, mailbox):
        """Test that an email received exactly at start time is accepted."""
        start = _start()
        mailbox._connection = MagicMock()

        with patch.object(mailbox, "_refresh_mailbox", new=MagicMock()), \
             patch.object(mailbox, "_search_uids", return_value=[b"1"]), \
             patch.object(mailbox, "_fetch_message", return_value=("data",)), \
             patch.object(mailbox, "_parse_message", return_value=_message("1", received=start)):
            result = mailbox._poll_once("CMP - YOUR TOKEN", start)

        assert result is not None
        assert result.email_received_at == start

    def test_filters_by_exact_subject(self, mailbox):
        """Test that messages with a different subject are ignored."""
        start = _start()
        mailbox._connection = MagicMock()

        with patch.object(mailbox, "_refresh_mailbox", new=MagicMock()), \
             patch.object(mailbox, "_search_uids", return_value=[b"1", b"2"]), \
             patch.object(mailbox, "_fetch_message", side_effect=[("data",), ("data",)]), \
             patch.object(mailbox, "_parse_message", side_effect=[
                 _message("1", subject="CMP - YOUR TOKEN", received=datetime(
                     2024, 1, 15, 12, 6, 0, tzinfo=ZoneInfo("Asia/Jakarta"))),
                 _message("2", subject="Weekly Report", received=datetime(
                     2024, 1, 15, 12, 7, 0, tzinfo=ZoneInfo("Asia/Jakarta"))),
             ]):
            result = mailbox._poll_once("CMP - YOUR TOKEN", start)

        assert result is not None
        assert result.email_subject == "CMP - YOUR TOKEN"
        assert result.email_received_at == datetime(
            2024, 1, 15, 12, 6, 0, tzinfo=ZoneInfo("Asia/Jakarta")
        )

    def test_returns_none_when_body_has_no_token(self, mailbox):
        """Test that None is returned when the newest email body has no token."""
        start = _start()
        mailbox._connection = MagicMock()

        with patch.object(mailbox, "_refresh_mailbox", new=MagicMock()), \
             patch.object(mailbox, "_search_uids", return_value=[b"1"]), \
             patch.object(mailbox, "_fetch_message", return_value=("data",)), \
             patch.object(mailbox, "_parse_message", return_value=_message(
                 "1", body="No code here."
             )):
            result = mailbox._poll_once("CMP - YOUR TOKEN", start)

        assert result is None

    def test_skips_messages_at_or_below_uid_baseline(self, mailbox):
        """Test that leftover messages from a previous run (UID <= baseline) are skipped."""
        start = _start()
        mailbox._connection = MagicMock()
        mailbox._base_uid = 5

        with patch.object(mailbox, "_refresh_mailbox", new=MagicMock()), \
             patch.object(mailbox, "_search_uids", return_value=[b"3", b"7"]), \
             patch.object(mailbox, "_fetch_message", side_effect=[("data",), ("data",)]) as mock_fetch, \
             patch.object(mailbox, "_parse_message", side_effect=[
                 _message("3"), _message("7"),
             ]):
            result = mailbox._poll_once("CMP - YOUR TOKEN", start)

            # Only UID 7 (above baseline) is parsed and considered
            assert result is not None
            assert result.email_received_at == start
            assert mock_fetch.call_count == 1

    def test_accepts_email_within_clock_skew_tolerance(self, mailbox):
        """Test that an email received before start (within skew tolerance) is accepted."""
        start = _start()
        mailbox._connection = MagicMock()
        within_tolerance = start - timedelta(seconds=60)  # 60s early < 120s tolerance

        with patch.object(mailbox, "_refresh_mailbox", new=MagicMock()), \
             patch.object(mailbox, "_search_uids", return_value=[b"1"]), \
             patch.object(mailbox, "_fetch_message", return_value=("data",)), \
             patch.object(mailbox, "_parse_message", return_value=_message("1", received=within_tolerance)):
            result = mailbox._poll_once("CMP - YOUR TOKEN", start)

        assert result is not None
        assert result.email_received_at == within_tolerance

    def test_rejects_email_outside_clock_skew_tolerance(self, mailbox):
        """Test that an email older than the skew tolerance is rejected."""
        start = _start()
        mailbox._connection = MagicMock()
        too_old = start - timedelta(seconds=300)  # 5 min early > 120s tolerance

        with patch.object(mailbox, "_refresh_mailbox", new=MagicMock()), \
             patch.object(mailbox, "_search_uids", return_value=[b"1"]), \
             patch.object(mailbox, "_fetch_message", return_value=("data",)), \
             patch.object(mailbox, "_parse_message", return_value=_message("1", received=too_old)):
            result = mailbox._poll_once("CMP - YOUR TOKEN", start)

        assert result is None

    def test_clock_skew_tolerance_zero_preserves_strict_behavior(self, mailbox):
        """Test that tolerance=0 keeps the strict pre-tolerance timestamp check."""
        strict_config = mailbox.config.model_copy(
            update={"otp_clock_skew_tolerance_seconds": 0}
        )
        strict_mailbox = MailboxClient(strict_config)
        start = _start()
        strict_mailbox._connection = MagicMock()
        one_sec_early = start - timedelta(seconds=1)

        with patch.object(strict_mailbox, "_refresh_mailbox", new=MagicMock()), \
             patch.object(strict_mailbox, "_search_uids", return_value=[b"1"]), \
             patch.object(strict_mailbox, "_fetch_message", return_value=("data",)), \
             patch.object(strict_mailbox, "_parse_message", return_value=_message("1", received=one_sec_early)):
            result = strict_mailbox._poll_once("CMP - YOUR TOKEN", start)

        assert result is None

    def test_raises_when_not_connected(self, mailbox):
        """Test that polling raises when no IMAP connection exists."""
        start = _start()
        with pytest.raises(OTPError, match="not established"):
            mailbox._poll_once("CMP - YOUR TOKEN", start)


class TestConnectAndDisconnect:
    """Tests for IMAP connection lifecycle."""

    @pytest.mark.asyncio
    async def test_connect_success(self, mailbox, config):
        """Test a successful IMAP connect with login and read-only select."""
        mock_conn = MagicMock()
        mock_conn.select.return_value = ("OK", [])
        with patch("cmp_automation.mailbox.imaplib.IMAP4_SSL", return_value=mock_conn) as mock_ssl, \
             patch.object(mailbox, "_take_uid_snapshot", new=MagicMock()) as mock_snapshot:
            await mailbox.connect()

        mock_ssl.assert_called_once_with(
            config.gmf_imap_host, config.gmf_imap_port, timeout=mailbox.IMAP_TIMEOUT_SECONDS
        )
        mock_conn.login.assert_called_once_with(config.gmf_email, config.gmf_password)
        mock_conn.select.assert_called_once_with("INBOX", readonly=True)
        mock_snapshot.assert_called_once()
        assert mailbox._connection is mock_conn

    @pytest.mark.asyncio
    async def test_connect_login_failure(self, mailbox):
        """Test that a login failure raises a safe OTPError and closes the connection."""
        mock_conn = MagicMock()
        mock_conn.login.side_effect = Exception("auth failed")
        with patch("cmp_automation.mailbox.imaplib.IMAP4_SSL", return_value=mock_conn):
            with pytest.raises(OTPError, match="IMAP connection failed"):
                await mailbox.connect()

        assert mailbox._connection is None
        mock_conn.close.assert_called_once()
        mock_conn.logout.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_select_failure(self, mailbox):
        """Test that a failed mailbox select raises OTPError and closes the connection."""
        mock_conn = MagicMock()
        mock_conn.select.return_value = ("NO", ["Mailbox does not exist"])
        with patch("cmp_automation.mailbox.imaplib.IMAP4_SSL", return_value=mock_conn):
            with pytest.raises(OTPError, match="select INBOX"):
                await mailbox.connect()

        assert mailbox._connection is None
        mock_conn.close.assert_called_once()
        mock_conn.logout.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_closes_and_logs_out(self, mailbox):
        """Test that disconnect closes and logs out of the connection."""
        mock_conn = MagicMock()
        mailbox._connection = mock_conn
        await mailbox.disconnect()

        mock_conn.close.assert_called_once()
        mock_conn.logout.assert_called_once()
        assert mailbox._connection is None

    @pytest.mark.asyncio
    async def test_disconnect_without_connection_is_noop(self, mailbox):
        """Test that disconnect is a no-op when never connected."""
        await mailbox.disconnect()  # Should not raise
        assert mailbox._connection is None


class TestParseEmailTimestamp:
    """Tests for timestamp parsing."""

    def test_parse_iso_format(self, mailbox):
        """Test parsing ISO format timestamps."""
        jakarta_tz = ZoneInfo("Asia/Jakarta")
        parsed = mailbox.parse_email_timestamp("2024-01-15 12:00:00")
        assert parsed is not None
        assert parsed == datetime(2024, 1, 15, 12, 0, 0, tzinfo=jakarta_tz)
        assert parsed.tzinfo is not None

    def test_parse_iso_format_with_t(self, mailbox):
        """Test parsing ISO format with T separator."""
        jakarta_tz = ZoneInfo("Asia/Jakarta")
        parsed = mailbox.parse_email_timestamp("2024-01-15T12:00:00")
        assert parsed is not None
        assert parsed == datetime(2024, 1, 15, 12, 0, 0, tzinfo=jakarta_tz)

    def test_parse_iso_format_with_z(self, mailbox):
        """Test parsing ISO format with Z (UTC)."""
        parsed = mailbox.parse_email_timestamp("2024-01-15T12:00:00Z")
        assert parsed is not None
        assert parsed.tzinfo == ZoneInfo("UTC")
        assert parsed.hour == 12

    def test_parse_imap_internaldate_format(self, mailbox):
        """Test parsing the IMAP INTERNALDATE format."""
        parsed = mailbox.parse_email_timestamp("15-Jan-2024 12:00:00 +0700")
        assert parsed is not None
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timedelta(hours=7)
        assert parsed.hour == 12

    def test_parse_with_gmt_offset(self, mailbox):
        """Test parsing with GMT+7 offset."""
        parsed = mailbox.parse_email_timestamp("2024-01-15 12:00:00 GMT+7")
        assert parsed is not None
        assert parsed.utcoffset() is not None

    def test_parse_invalid_format(self, mailbox):
        """Test that unparseable timestamps return None."""
        assert mailbox.parse_email_timestamp("not-a-timestamp") is None
        assert mailbox.parse_email_timestamp("") is None
        assert mailbox.parse_email_timestamp(None) is None  # type: ignore[arg-type]


class TestWorkflowStartTime:
    """Tests for workflow start time handling."""

    def test_set_workflow_start_time(self, mailbox):
        """Test setting workflow start time."""
        start = _start()
        mailbox.set_workflow_start_time(start)
        assert mailbox._workflow_start_time == start

    def test_set_workflow_start_time_requires_tz_aware(self, mailbox):
        """Test that workflow start time must be timezone-aware."""
        naive_dt = datetime(2024, 1, 15, 12, 0, 0)
        with pytest.raises(ValueError, match="timezone-aware"):
            mailbox.set_workflow_start_time(naive_dt)

    def test_get_workflow_start_time_raises_if_not_set(self, mailbox):
        """Test that getting workflow start time raises if not set."""
        with pytest.raises(Exception, match="not set"):
            mailbox._get_workflow_start_time()
