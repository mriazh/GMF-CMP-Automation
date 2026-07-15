"""Tests for mailbox OTP retrieval with mocked browser dependencies."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from cmp_automation.config import Config
from cmp_automation.exceptions import OTPTimeoutError
from cmp_automation.mailbox import MailboxClient, OTPToken, _EmailCandidate


class _FakeRows:
    """Fake Playwright locator that yields row indices."""

    def __init__(self, count: int):
        self._count = count

    async def count(self) -> int:
        return self._count

    def nth(self, index: int) -> int:
        return index


class _FakeLocator:
    """Fake Playwright locator with configurable element count."""

    def __init__(self, count_value: int = 0):
        self._count_value = count_value
        self._attributes = {}

    @property
    def first(self) -> "_FakeLocator":
        return self

    async def count(self) -> int:
        return self._count_value

    async def click(self) -> None:
        return None

    async def wait_for(self, *args, **kwargs) -> None:
        return None

    async def get_attribute(self, attr: str) -> str | None:
        return self._attributes.get(attr)

    async def inner_text(self) -> str:
        return ""

    def set_attribute(self, attr: str, value: str) -> None:
        self._attributes[attr] = value


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


class TestGetLatestOtp:
    """Tests for OTP polling orchestration."""

    @pytest.mark.asyncio
    async def test_returns_token_when_found(self, mailbox):
        """Test that the token is returned immediately when found."""
        start = _start()
        expected = _token(start)
        page = AsyncMock()

        mailbox.set_workflow_start_time(start)
        with patch.object(mailbox, "ensure_authenticated", new=AsyncMock()) as mock_auth, \
             patch.object(mailbox, "_find_latest_otp", new=AsyncMock(return_value=expected)) as mock_find:
            result = await mailbox.get_latest_otp(page, "CMP - YOUR TOKEN", 120, 5)

        assert result is expected
        mock_auth.assert_awaited_once_with(page)
        mock_find.assert_awaited_once_with(page, "CMP - YOUR TOKEN")

    @pytest.mark.asyncio
    async def test_polls_until_token_found(self, mailbox):
        """Test that polling retries until the token appears."""
        start = _start()
        token = _token(start)
        page = AsyncMock()

        mailbox.set_workflow_start_time(start)
        with patch.object(mailbox, "ensure_authenticated", new=AsyncMock()), \
             patch.object(mailbox, "_find_latest_otp", new=AsyncMock(side_effect=[None, token])) as mock_find, \
             patch("asyncio.sleep", new=AsyncMock()):
            result = await mailbox.get_latest_otp(page, "CMP - YOUR TOKEN", 10, 1)

        assert result is token
        assert mock_find.await_count == 2

    @pytest.mark.asyncio
    async def test_times_out_when_token_never_appears(self, mailbox):
        """Test that OTPTimeoutError is raised when no email arrives in time."""
        start = _start()
        page = AsyncMock()

        mailbox.set_workflow_start_time(start)
        with patch.object(mailbox, "ensure_authenticated", new=AsyncMock()), \
             patch.object(mailbox, "_find_latest_otp", new=AsyncMock(return_value=None)):
            with pytest.raises(OTPTimeoutError, match="timed out"):
                await mailbox.get_latest_otp(page, "CMP - YOUR TOKEN", 1, 1)

    @pytest.mark.asyncio
    async def test_timeout_message_contains_no_secrets(self, mailbox):
        """Test that the timeout message exposes no token or credential values."""
        start = _start()
        page = AsyncMock()

        mailbox.set_workflow_start_time(start)
        with patch.object(mailbox, "ensure_authenticated", new=AsyncMock()), \
             patch.object(mailbox, "_find_latest_otp", new=AsyncMock(return_value=None)):
            with pytest.raises(OTPTimeoutError) as excinfo:
                await mailbox.get_latest_otp(page, "CMP - YOUR TOKEN", 1, 1)

        message = str(excinfo.value)
        assert "CMP - YOUR TOKEN" in message
        assert start.isoformat() in message
        assert "123456" not in message
        assert "test@test.com" not in message


class TestFindLatestOtp:
    """Tests for email filtering and newest-selection logic."""

    @pytest.mark.asyncio
    async def test_selects_newest_valid_email(self, mailbox):
        """Test that the newest email at/after start time is opened and returned."""
        start = _start()
        page = AsyncMock()
        page.locator = MagicMock(return_value=_FakeRows(4))

        rows_data = [
            ("CMP - YOUR TOKEN", "2024-01-15 12:10:00", "msg-id-1"),
            ("CMP - YOUR TOKEN", "2024-01-15 12:20:00", "msg-id-2"),
            ("CMP - YOUR TOKEN", "2024-01-15 12:05:00", "msg-id-3"),
            ("Weekly Report", "2024-01-15 12:30:00", "msg-id-4"),
        ]

        async def fake_read(row: int):
            subject, ts, identity = rows_data[row]
            return (subject, ts, identity)

        mailbox.set_workflow_start_time(start)
        with patch.object(mailbox, "_read_row_metadata", new=AsyncMock(side_effect=fake_read)), \
             patch.object(mailbox, "_navigate_to_inbox", new=AsyncMock()) as mock_nav, \
             patch.object(mailbox, "_open_email_and_extract_token", new=AsyncMock(return_value="654321")) as mock_open:
            result = await mailbox._find_latest_otp(page, "CMP - YOUR TOKEN")

        assert result is not None
        assert result.token == "654321"
        assert result.email_received_at == datetime(2024, 1, 15, 12, 20, 0, tzinfo=ZoneInfo("Asia/Jakarta"))
        mock_nav.assert_awaited_once()
        mock_open.assert_awaited_once()
        opened_candidate = mock_open.await_args.args[1]
        assert opened_candidate.row_identity == "msg-id-2"

    @pytest.mark.asyncio
    async def test_rejects_all_older_emails(self, mailbox):
        """Test that no email older than start time is accepted."""
        start = _start()
        page = AsyncMock()
        page.locator = MagicMock(return_value=_FakeRows(2))

        rows_data = [
            ("CMP - YOUR TOKEN", "2024-01-15 11:59:00", "msg-id-1"),
            ("CMP - YOUR TOKEN", "2024-01-14 23:59:00", "msg-id-2"),
        ]

        async def fake_read(row: int):
            subject, ts, identity = rows_data[row]
            return (subject, ts, identity)

        mailbox.set_workflow_start_time(start)
        with patch.object(mailbox, "_read_row_metadata", new=AsyncMock(side_effect=fake_read)), \
             patch.object(mailbox, "_navigate_to_inbox", new=AsyncMock()), \
             patch.object(mailbox, "_open_email_and_extract_token", new=AsyncMock()) as mock_open:
            result = await mailbox._find_latest_otp(page, "CMP - YOUR TOKEN")

        assert result is None
        mock_open.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_accepts_email_exactly_at_start_time(self, mailbox):
        """Test that an email received exactly at start time is accepted."""
        start = _start()
        page = AsyncMock()
        page.locator = MagicMock(return_value=_FakeRows(1))

        async def fake_read(row: int):
            return ("CMP - YOUR TOKEN", "2024-01-15 12:00:00", "msg-id-1")

        mailbox.set_workflow_start_time(start)
        with patch.object(mailbox, "_read_row_metadata", new=AsyncMock(side_effect=fake_read)), \
             patch.object(mailbox, "_navigate_to_inbox", new=AsyncMock()), \
             patch.object(mailbox, "_open_email_and_extract_token", new=AsyncMock(return_value="111111")):
            result = await mailbox._find_latest_otp(page, "CMP - YOUR TOKEN")

        assert result is not None
        assert result.email_received_at == start

    @pytest.mark.asyncio
    async def test_tries_next_candidate_when_newest_has_no_token(self, mailbox):
        """Test that older candidates are tried if the newest has no token."""
        start = _start()
        page = AsyncMock()
        page.locator = MagicMock(return_value=_FakeRows(2))

        rows_data = [
            ("CMP - YOUR TOKEN", "2024-01-15 12:10:00", "msg-id-1"),
            ("CMP - YOUR TOKEN", "2024-01-15 12:05:00", "msg-id-2"),
        ]

        async def fake_read(row: int):
            subject, ts, identity = rows_data[row]
            return (subject, ts, identity)

        mailbox.set_workflow_start_time(start)
        with patch.object(mailbox, "_read_row_metadata", new=AsyncMock(side_effect=fake_read)), \
             patch.object(mailbox, "_navigate_to_inbox", new=AsyncMock()) as mock_nav, \
             patch.object(mailbox, "_open_email_and_extract_token", new=AsyncMock(side_effect=[None, "222222"])) as mock_open:
            result = await mailbox._find_latest_otp(page, "CMP - YOUR TOKEN")

        assert result is not None
        assert result.token == "222222"
        assert result.email_received_at == datetime(2024, 1, 15, 12, 5, 0, tzinfo=ZoneInfo("Asia/Jakarta"))
        assert mock_open.await_count == 2
        # Re-navigated to the inbox between the two open attempts
        assert mock_nav.await_count == 2

    @pytest.mark.asyncio
    async def test_filters_by_exact_subject(self, mailbox):
        """Test that rows with a different subject are ignored."""
        start = _start()
        page = AsyncMock()
        page.locator = MagicMock(return_value=_FakeRows(3))

        rows_data = [
            ("CMP - YOUR TOKEN", "2024-01-15 12:05:00", "msg-id-1"),
            ("CMP - Your Token", "2024-01-15 12:06:00", "msg-id-2"),
            ("Weekly Report", "2024-01-15 12:07:00", "msg-id-3"),
        ]

        async def fake_read(row: int):
            subject, ts, identity = rows_data[row]
            return (subject, ts, identity)

        mailbox.set_workflow_start_time(start)
        with patch.object(mailbox, "_read_row_metadata", new=AsyncMock(side_effect=fake_read)), \
             patch.object(mailbox, "_navigate_to_inbox", new=AsyncMock()), \
             patch.object(mailbox, "_open_email_and_extract_token", new=AsyncMock(return_value="333333")) as mock_open:
            result = await mailbox._find_latest_otp(page, "CMP - YOUR TOKEN")

        assert result is not None
        # Case-insensitive exact match is accepted; unrelated subjects are rejected
        assert result.email_subject == "CMP - Your Token"
        assert result.email_received_at == datetime(2024, 1, 15, 12, 6, 0, tzinfo=ZoneInfo("Asia/Jakarta"))
        opened_candidate = mock_open.await_args.args[1]
        assert opened_candidate.row_identity == "msg-id-2"

    @pytest.mark.asyncio
    async def test_collect_candidates_skips_rows_without_metadata(self, mailbox):
        """Test that rows with no readable subject are skipped."""
        start = _start()
        page = AsyncMock()
        page.locator = MagicMock(return_value=_FakeRows(3))

        async def fake_read(row: int):
            if row == 1:
                return None
            return ("CMP - YOUR TOKEN", "2024-01-15 12:05:00", f"msg-id-{row}")

        mailbox.set_workflow_start_time(start)
        with patch.object(mailbox, "_read_row_metadata", new=AsyncMock(side_effect=fake_read)):
            candidates = await mailbox._collect_email_candidates(page, "CMP - YOUR TOKEN")

        assert len(candidates) == 2


class TestOpenEmailAndExtractToken:
    """Tests for opening an email and extracting its token."""

    @pytest.mark.asyncio
    async def test_extracts_token_from_body(self, mailbox):
        """Test that the token is read from the email body."""
        page = AsyncMock()
        row = AsyncMock()
        locator_mock = MagicMock()
        locator_mock.nth.return_value = row
        page.locator = MagicMock(return_value=locator_mock)
        candidate = _EmailCandidate(
            email_subject="CMP - YOUR TOKEN",
            email_received_at=_start(),
            list_selector=".email-item",
            row_identity="msg-id-1",
        )

        with patch.object(mailbox, "_read_email_body", new=AsyncMock(return_value="Your OTP is 123456. Do not share it.")), \
             patch.object(mailbox, "_locate_row_by_identity", new=AsyncMock(return_value=row)), \
             patch.object(mailbox, "_wait_for_email_body", new=AsyncMock()), \
             patch.object(mailbox, "_read_email_sender", new=AsyncMock(return_value=None)), \
             patch.object(mailbox, "_read_row_metadata", new=AsyncMock(return_value=("CMP - YOUR TOKEN", "2024-01-15 12:00:00", "msg-id-1"))):
            token = await mailbox._open_email_and_extract_token(page, candidate)

        assert token == "123456"
        row.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_none_when_body_has_no_token(self, mailbox):
        """Test that None is returned when the email body has no token."""
        page = AsyncMock()
        row = AsyncMock()
        locator_mock = MagicMock()
        locator_mock.nth.return_value = row
        page.locator = MagicMock(return_value=locator_mock)
        candidate = _EmailCandidate(
            email_subject="CMP - YOUR TOKEN",
            email_received_at=_start(),
            list_selector=".email-item",
            row_identity="msg-id-1",
        )

        with patch.object(mailbox, "_read_email_body", new=AsyncMock(return_value="No code here.")), \
             patch.object(mailbox, "_locate_row_by_identity", new=AsyncMock(return_value=row)), \
             patch.object(mailbox, "_wait_for_email_body", new=AsyncMock()), \
             patch.object(mailbox, "_read_email_sender", new=AsyncMock(return_value=None)), \
             patch.object(mailbox, "_read_row_metadata", new=AsyncMock(return_value=("CMP - YOUR TOKEN", "2024-01-15 12:00:00", "msg-id-1"))):
            token = await mailbox._open_email_and_extract_token(page, candidate)

        assert token is None


class TestEnsureAuthenticated:
    """Tests for webmail session handling."""

    @pytest.mark.asyncio
    async def test_skips_login_when_session_valid(self, mailbox):
        """Test that login is skipped when the session is already authenticated with correct account."""
        page = AsyncMock()
        fake_locator = _FakeLocator(count_value=1)
        fake_locator.set_attribute("data-user-email", "test@test.com")
        page.locator = MagicMock(return_value=fake_locator)

        with patch.object(mailbox, "_login", new=AsyncMock()) as mock_login:
            await mailbox.ensure_authenticated(page)

        mock_login.assert_not_awaited()
        assert mailbox._authenticated is True

    @pytest.mark.asyncio
    async def test_logs_in_when_session_invalid(self, mailbox):
        """Test that login is performed when no session indicator is present."""
        page = AsyncMock()
        fake_locator = _FakeLocator(count_value=0)
        page.locator = MagicMock(return_value=fake_locator)

        with patch.object(mailbox, "_login", new=AsyncMock()), \
             patch.object(mailbox, "_verify_account_identity", new=AsyncMock(return_value=True)):
            await mailbox.ensure_authenticated(page)

        assert mailbox._authenticated is True

    @pytest.mark.asyncio
    async def test_rejects_wrong_account(self, mailbox):
        """Test that login is performed when account indicator shows wrong email."""
        page = AsyncMock()
        fake_locator = _FakeLocator(count_value=1)
        fake_locator.set_attribute("data-user-email", "wrong@test.com")
        page.locator = MagicMock(return_value=fake_locator)

        with patch.object(mailbox, "_login", new=AsyncMock()) as mock_login:
            with pytest.raises(Exception, match="Account verification failed"):
                await mailbox.ensure_authenticated(page)

        mock_login.assert_awaited_once_with(page)

    @pytest.mark.asyncio
    async def test_rechecks_cached_session_identity(self, mailbox):
        """Cached sessions still require account identity verification."""
        mailbox._authenticated = True
        page = AsyncMock()
        with patch.object(mailbox, "_verify_account_identity", new=AsyncMock(return_value=True)):
            await mailbox.ensure_authenticated(page)
        page.goto.assert_awaited_once()
        assert mailbox._authenticated is True


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

    def test_parse_with_gmt_offset(self, mailbox):
        """Test parsing with GMT+7 offset."""
        parsed = mailbox.parse_email_timestamp("2024-01-15 12:00:00 GMT+7")
        assert parsed is not None
        assert parsed.utcoffset() is not None

    def test_parse_invalid_format(self, mailbox):
        """Test that unparseable timestamps return None."""
        assert mailbox.parse_email_timestamp("not-a-timestamp") is None
        assert mailbox.parse_email_timestamp("") is None
        assert mailbox.parse_email_timestamp(None) is None

    def test_parse_missing_timestamp_returns_none(self, mailbox):
        """Test that missing/empty timestamp returns None (fail closed)."""
        assert mailbox.parse_email_timestamp("") is None
        assert mailbox.parse_email_timestamp("   ") is None

    def test_timezone_aware_comparison(self, mailbox):
        """Test that timestamps are compared with timezone awareness."""
        jakarta_tz = ZoneInfo("Asia/Jakarta")
        utc_tz = ZoneInfo("UTC")

        start_time = datetime(2024, 1, 15, 12, 0, 0, tzinfo=jakarta_tz)

        email_old = datetime(2024, 1, 15, 11, 59, 0, tzinfo=jakarta_tz)
        assert email_old < start_time

        email_at_start = datetime(2024, 1, 15, 12, 0, 0, tzinfo=jakarta_tz)
        assert email_at_start >= start_time

        email_new = datetime(2024, 1, 15, 12, 1, 0, tzinfo=jakarta_tz)
        assert email_new >= start_time

        email_utc = datetime(2024, 1, 15, 5, 0, 0, tzinfo=utc_tz)  # 12:00 Jakarta = 05:00 UTC
        email_utc_normalized = email_utc.astimezone(jakarta_tz)
        assert email_utc_normalized >= start_time


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
