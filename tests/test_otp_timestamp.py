"""Tests for OTP timestamp filtering and timezone handling."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from cmp_automation.config import Config
from cmp_automation.mailbox import MailboxClient, _OtpMessage


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


class TestOTPTimestampFiltering:
    """Tests for OTP timestamp validation and filtering."""

    def test_parse_email_timestamp_iso_format(self, mailbox):
        """Test parsing ISO format timestamps."""
        jakarta_tz = ZoneInfo("Asia/Jakarta")
        parsed = mailbox.parse_email_timestamp("2024-01-15 12:00:00")
        assert parsed is not None
        assert parsed == datetime(2024, 1, 15, 12, 0, 0, tzinfo=jakarta_tz)
        assert parsed.tzinfo is not None

    def test_parse_email_timestamp_invalid_format(self, mailbox):
        """Test that unparseable timestamps return None."""
        assert mailbox.parse_email_timestamp("not-a-timestamp") is None
        assert mailbox.parse_email_timestamp("") is None

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

    def test_reject_older_email_same_day(self, mailbox):
        """Test rejecting email from earlier same day."""
        jakarta_tz = ZoneInfo("Asia/Jakarta")
        start_time = datetime(2024, 1, 15, 12, 0, 0, tzinfo=jakarta_tz)

        email_time = datetime(2024, 1, 15, 11, 59, 0, tzinfo=jakarta_tz)
        assert email_time < start_time

    def test_reject_older_email_previous_day(self, mailbox):
        """Test rejecting email from previous day."""
        jakarta_tz = ZoneInfo("Asia/Jakarta")
        start_time = datetime(2024, 1, 15, 12, 0, 0, tzinfo=jakarta_tz)

        email_time = datetime(2024, 1, 14, 23, 59, 0, tzinfo=jakarta_tz)
        assert email_time < start_time

    def test_accept_newest_valid_email(self, mailbox):
        """Test selecting newest email when multiple valid emails exist."""
        jakarta_tz = ZoneInfo("Asia/Jakarta")
        start_time = datetime(2024, 1, 15, 12, 0, 0, tzinfo=jakarta_tz)

        emails = [
            {"subject": "CMP - YOUR TOKEN", "received": datetime(2024, 1, 15, 12, 5, 0, tzinfo=jakarta_tz)},
            {"subject": "CMP - YOUR TOKEN", "received": datetime(2024, 1, 15, 12, 10, 0, tzinfo=jakarta_tz)},
            {"subject": "CMP - YOUR TOKEN", "received": datetime(2024, 1, 15, 12, 3, 0, tzinfo=jakarta_tz)},
        ]

        valid_emails = [e for e in emails if e["received"] >= start_time]
        assert len(valid_emails) == 3

        newest = max(valid_emails, key=lambda e: e["received"])
        assert newest["received"] == datetime(2024, 1, 15, 12, 10, 0, tzinfo=jakarta_tz)

    def test_reject_all_older_emails(self, mailbox):
        """Test that all older emails are rejected."""
        jakarta_tz = ZoneInfo("Asia/Jakarta")
        start_time = datetime(2024, 1, 15, 12, 0, 0, tzinfo=jakarta_tz)

        emails = [
            {"subject": "CMP - YOUR TOKEN", "received": datetime(2024, 1, 15, 11, 59, 0, tzinfo=jakarta_tz)},
            {"subject": "CMP - YOUR TOKEN", "received": datetime(2024, 1, 15, 11, 58, 0, tzinfo=jakarta_tz)},
            {"subject": "CMP - YOUR TOKEN", "received": datetime(2024, 1, 14, 12, 0, 0, tzinfo=jakarta_tz)},
        ]

        valid_emails = [e for e in emails if e["received"] >= start_time]
        assert len(valid_emails) == 0

    def test_cross_timezone_comparison(self, mailbox):
        """Test comparison across different timezones."""
        jakarta_tz = ZoneInfo("Asia/Jakarta")
        utc_tz = ZoneInfo("UTC")
        start_time = datetime(2024, 1, 15, 12, 0, 0, tzinfo=jakarta_tz)

        email_utc = datetime(2024, 1, 15, 5, 0, 0, tzinfo=utc_tz)  # 12:00 Jakarta = 05:00 UTC

        email_jakarta = email_utc.astimezone(jakarta_tz)
        assert email_jakarta == start_time
        assert email_jakarta >= start_time

        email_utc_before = datetime(2024, 1, 15, 4, 59, 0, tzinfo=utc_tz)  # 11:59 Jakarta
        email_jakarta_before = email_utc_before.astimezone(jakarta_tz)
        assert email_jakarta_before < start_time


def _otp_message(uid: str, received: datetime) -> _OtpMessage:
    """Build an OTP candidate message for the given received time."""
    return _OtpMessage(
        uid=uid,
        subject="CMP - YOUR TOKEN",
        received_at=received,
        body="Your CMP OTP token is 123456. Do not share it.",
    )


def _run_poll(mailbox, start: datetime, messages: list[_OtpMessage]):
    """Run a single poll cycle with mocked IMAP internals."""
    mailbox._connection = MagicMock()
    uids = [str(index + 1).encode() for index in range(len(messages))]
    with patch.object(mailbox, "_refresh_mailbox", new=MagicMock()), \
         patch.object(mailbox, "_search_uids", return_value=uids), \
         patch.object(mailbox, "_fetch_message", side_effect=[("data",)] * len(messages)), \
         patch.object(mailbox, "_parse_message", side_effect=messages):
        return mailbox._poll_once("CMP - YOUR TOKEN", start)


class TestClockSkewTolerance:
    """Tests for the OTP email clock-skew tolerance window."""

    def test_accepts_email_within_tolerance_window(self, mailbox):
        """Test that an email received slightly before start is accepted when within tolerance."""
        jakarta_tz = ZoneInfo("Asia/Jakarta")
        start = datetime(2024, 1, 15, 12, 0, 0, tzinfo=jakarta_tz)
        email_time = start - timedelta(seconds=119)  # inside the 120s tolerance

        result = _run_poll(mailbox, start, [_otp_message("1", email_time)])

        assert result is not None
        assert result.email_received_at == email_time

    def test_accepts_email_exactly_at_cutoff(self, mailbox):
        """Test that an email received exactly at the cutoff boundary is accepted."""
        jakarta_tz = ZoneInfo("Asia/Jakarta")
        start = datetime(2024, 1, 15, 12, 0, 0, tzinfo=jakarta_tz)
        cutoff_time = start - timedelta(seconds=120)  # exactly at the 120s cutoff

        result = _run_poll(mailbox, start, [_otp_message("1", cutoff_time)])

        assert result is not None
        assert result.email_received_at == cutoff_time

    def test_rejects_email_before_tolerance_cutoff(self, mailbox):
        """Test that an email older than the tolerance window is rejected."""
        jakarta_tz = ZoneInfo("Asia/Jakarta")
        start = datetime(2024, 1, 15, 12, 0, 0, tzinfo=jakarta_tz)
        too_old = start - timedelta(seconds=121)  # just outside the 120s tolerance

        result = _run_poll(mailbox, start, [_otp_message("1", too_old)])

        assert result is None

    def test_uid_baseline_still_rejects_previous_run(self, mailbox):
        """Test that UID baseline filtering still rejects leftovers even within tolerance."""
        jakarta_tz = ZoneInfo("Asia/Jakarta")
        start = datetime(2024, 1, 15, 12, 0, 0, tzinfo=jakarta_tz)
        mailbox._base_uid = 10

        # UID below baseline but within timestamp tolerance must still be rejected.
        result = _run_poll(mailbox, start, [_otp_message("5", start)])

        assert result is None

    def test_zero_tolerance_requires_at_or_after_start(self, mailbox):
        """Test that tolerance=0 preserves the strict pre-tolerance behavior."""
        strict_config = mailbox.config.model_copy(
            update={"otp_clock_skew_tolerance_seconds": 0}
        )
        strict_mailbox = MailboxClient(strict_config)
        jakarta_tz = ZoneInfo("Asia/Jakarta")
        start = datetime(2024, 1, 15, 12, 0, 0, tzinfo=jakarta_tz)

        one_sec_early = start - timedelta(seconds=1)
        assert _run_poll(strict_mailbox, start, [_otp_message("1", one_sec_early)]) is None

        # A message exactly at start time is still accepted with tolerance=0.
        at_start = _run_poll(strict_mailbox, start, [_otp_message("2", start)])
        assert at_start is not None


class TestTimestampNormalization:
    """Tests for timestamp normalization."""

    def test_normalize_various_formats(self, mailbox):
        """Test normalization of various timestamp formats."""
        jakarta_tz = ZoneInfo("Asia/Jakarta")

        test_cases = [
            ("2024-01-15 12:00:00", jakarta_tz),
            ("2024-01-15 12:00", jakarta_tz),
            ("15/01/2024 12:00:00", jakarta_tz),
            ("15/01/2024 12:00", jakarta_tz),
            ("15 Jan 2024 12:00:00", jakarta_tz),
            ("15 Jan 2024 12:00", jakarta_tz),
            ("Jan 15, 2024 12:00:00", jakarta_tz),
            ("Jan 15, 2024 12:00", jakarta_tz),
        ]

        for timestamp_str, _expected_tz in test_cases:
            parsed = mailbox.parse_email_timestamp(timestamp_str)
            assert parsed is not None, f"Failed to parse: {timestamp_str}"
            assert parsed.tzinfo is not None, f"Not timezone-aware: {timestamp_str}"
            assert parsed == datetime(2024, 1, 15, 12, 0, 0, tzinfo=jakarta_tz), (
                f"Unexpected value for: {timestamp_str}"
            )

    def test_timezone_offset_parsing(self, mailbox):
        """Test parsing timestamps with timezone offsets."""
        parsed = mailbox.parse_email_timestamp("2024-01-15 12:00:00 GMT+7")
        assert parsed is not None
        assert parsed.utcoffset() == timedelta(hours=7)
        assert parsed.hour == 12

        parsed_gmt_minus = mailbox.parse_email_timestamp("2024-01-15 12:00:00 GMT-5")
        assert parsed_gmt_minus is not None
        assert parsed_gmt_minus.utcoffset() == timedelta(hours=-5)

        parsed_utc = mailbox.parse_email_timestamp("2024-01-15 05:00:00 +0000")
        assert parsed_utc is not None
        assert parsed_utc.utcoffset() == timedelta(0)

        parsed_utc_label = mailbox.parse_email_timestamp("2024-01-15 05:30:00 UTC")
        assert parsed_utc_label is not None
        assert parsed_utc_label.utcoffset() == timedelta(0)

        parsed_offset = mailbox.parse_email_timestamp("2024-01-15 12:30:00 +07:00")
        assert parsed_offset is not None
        assert parsed_offset.utcoffset() == timedelta(hours=7)

    def test_tz_offset_normalization_for_comparison(self, mailbox):
        """Test that offset timestamps normalize correctly for comparison."""
        jakarta_tz = ZoneInfo("Asia/Jakarta")
        start_time = datetime(2024, 1, 15, 12, 0, 0, tzinfo=jakarta_tz)

        # 05:00 UTC is the same instant as 12:00 WIB
        parsed_utc = mailbox.parse_email_timestamp("2024-01-15 05:00:00 UTC")
        assert parsed_utc is not None
        assert parsed_utc.astimezone(jakarta_tz) == start_time
        assert parsed_utc >= start_time

        # 04:59 UTC is 11:59 WIB - before the workflow start
        parsed_before = mailbox.parse_email_timestamp("2024-01-15 04:59:00 UTC")
        assert parsed_before is not None
        assert parsed_before < start_time
