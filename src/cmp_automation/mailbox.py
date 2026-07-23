"""IMAP-based GMF mailbox client for OTP retrieval.

Replaces the previous Playwright webmail UI scraping approach with a direct
IMAP client (proven pattern from ``GMF-CMP-Monitor``). Connects via IMAPS
(``imaplib.IMAP4_SSL``) to the GMF mailbox, polls for OTP emails matching the
exact subject, and accepts only messages received within the workflow's
freshness window (start time minus the configured clock-skew tolerance).
"""

import asyncio
import email
import email.utils
import imaplib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from email.header import decode_header
from typing import Any
from zoneinfo import ZoneInfo

from .config import Config
from .exceptions import OTPError, OTPTimeoutError
from .utils import extract_token_from_email_body

logger = logging.getLogger(__name__)


@dataclass
class OTPToken:
    """Represents an OTP token with metadata."""

    token: str
    email_subject: str
    email_received_at: datetime
    email_body_preview: str


@dataclass
class _OtpMessage:
    """Internal candidate message fetched from the IMAP server."""

    uid: str
    subject: str
    received_at: datetime
    body: str


class MailboxClient:
    """IMAP client for retrieving OTP emails from the GMF mailbox.

    Uses IMAPS with mandatory TLS. The mailbox is opened read-only; no
    message is ever modified or marked as seen.
    """

    IMAP_TIMEOUT_SECONDS = 30

    def __init__(self, config: Config):
        self.config = config
        self._connection: imaplib.IMAP4_SSL | None = None
        self._workflow_start_time: datetime | None = None
        self._base_uid = 0

    # ------------------------------------------------------------------
    # Workflow start time handling
    # ------------------------------------------------------------------

    def set_workflow_start_time(self, start_time: datetime) -> None:
        """Set the workflow start time for OTP freshness validation.

        This should be called once before the login workflow begins.
        The timestamp must be timezone-aware.
        """
        if start_time.tzinfo is None:
            raise ValueError("Workflow start time must be timezone-aware")
        self._workflow_start_time = start_time
        logger.debug("Workflow start time set to: %s", start_time.isoformat())

    def _get_workflow_start_time(self) -> datetime:
        """Get the workflow start time, raising if not set."""
        if self._workflow_start_time is None:
            raise OTPError(
                "Workflow start time not set. Call set_workflow_start_time() before polling."
            )
        return self._workflow_start_time

    # ------------------------------------------------------------------
    # Connection lifecycle (async wrappers over blocking IMAP calls)
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Connect and authenticate to the IMAP server (async wrapper)."""
        await asyncio.to_thread(self._connect_sync)

    def _connect_sync(self) -> None:
        """Connect, login, and select the INBOX read-only."""
        if self._connection is not None:
            return
        host = self.config.gmf_imap_host
        port = self.config.gmf_imap_port
        logger.info("Connecting to GMF IMAP %s:%s (IMAPS)", host, port)
        conn: imaplib.IMAP4_SSL | None = None
        try:
            conn = imaplib.IMAP4_SSL(host, port, timeout=self.IMAP_TIMEOUT_SECONDS)
            conn.login(self.config.gmf_email, self.config.gmf_password)
            status, _ = conn.select("INBOX", readonly=True)
            if status != "OK":
                raise OTPError("Failed to select INBOX mailbox")
            self._connection = conn
            # Snapshot the highest UID at connect time (before authentication)
            # so that stale OTP emails from a previous run are never accepted.
            self._base_uid = 0
            self._take_uid_snapshot()
            logger.info("Connected to GMF IMAP mailbox (read-only)")
        except OTPError:
            self._close_connection(conn)
            raise
        except Exception as exc:
            self._close_connection(conn)
            logger.warning("IMAP connection failed: %s", type(exc).__name__)
            raise OTPError("IMAP connection failed") from exc

    @staticmethod
    def _close_connection(conn: imaplib.IMAP4_SSL | None) -> None:
        """Best-effort close of a partially established IMAP connection."""
        if conn is None:
            return
        try:
            conn.close()
        except Exception:
            pass
        try:
            conn.logout()
        except Exception:
            pass

    def _take_uid_snapshot(self) -> None:
        """Record the highest UID matching the OTP criteria at connect time."""
        try:
            since = self._since_cutoff_date(datetime.now(UTC))
            max_uid = 0
            for raw_uid in self._search_uids(since):
                try:
                    max_uid = max(max_uid, int(raw_uid))
                except (TypeError, ValueError):
                    continue
            self._base_uid = max_uid
            logger.debug("IMAP UID snapshot at connect: %s", self._base_uid)
        except Exception as exc:
            logger.warning("Failed to take UID snapshot: %s", exc)
            self._base_uid = 0

    async def disconnect(self) -> None:
        """Close and log out of the IMAP connection (async wrapper)."""
        await asyncio.to_thread(self._disconnect_sync)

    def _disconnect_sync(self) -> None:
        """Close and log out of the IMAP connection, if connected."""
        if self._connection is None:
            return
        conn = self._connection
        self._connection = None
        try:
            conn.close()
        except Exception:
            logger.warning("Error during IMAP close")
        try:
            conn.logout()
        except Exception:
            logger.warning("Error during IMAP logout")

    # ------------------------------------------------------------------
    # IMAP polling
    # ------------------------------------------------------------------

    async def get_latest_otp(
        self,
        subject: str,
        timeout_seconds: int,
        poll_interval_seconds: int,
    ) -> OTPToken:
        """Poll for the latest OTP email matching the criteria.

        Args:
            subject: Expected email subject (case-insensitive exact match)
            timeout_seconds: Maximum time to poll (monotonic clock)
            poll_interval_seconds: Interval between poll attempts

        Returns:
            OTPToken with token, subject, received time, and body preview

        Raises:
            OTPTimeoutError: If no valid OTP found within timeout
            OTPError: On other failures
        """
        workflow_start = self._get_workflow_start_time()
        logger.info("Starting IMAP OTP polling for subject: %s", subject)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds

        while True:
            if self._connection is None:
                await self.connect()
            result = await asyncio.to_thread(self._poll_once, subject, workflow_start)
            if result is not None:
                logger.info(
                    "OTP retrieved successfully (email received at %s)",
                    result.email_received_at.isoformat(),
                )
                return result
            if loop.time() >= deadline:
                raise OTPTimeoutError(
                    f"OTP polling timed out after {timeout_seconds}s for subject '{subject}' "
                    f"(workflow started at {workflow_start.isoformat()})"
                )
            await asyncio.sleep(poll_interval_seconds)

    def _poll_once(self, subject: str, workflow_start: datetime) -> OTPToken | None:
        """Perform one IMAP search/fetch cycle and return the newest valid OTP.

        Accepts only messages whose subject matches (case-insensitive) and whose
        received time is at/after the freshness cutoff (workflow start minus the
        configured clock-skew tolerance). UID baseline filtering still rejects
        leftover messages from previous runs.
        """
        if self._connection is None:
            raise OTPError("IMAP connection not established")

        since = self._since_cutoff_date(workflow_start)

        try:
            self._refresh_mailbox()
            raw_uids = self._search_uids(since)
        except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError) as exc:
            logger.warning("IMAP error during polling: %s", type(exc).__name__)
            raise OTPError("IMAP polling failed") from exc

        messages: list[_OtpMessage] = []
        for raw_uid in raw_uids:
            uid = raw_uid.decode() if isinstance(raw_uid, bytes) else str(raw_uid)
            try:
                if int(uid) <= self._base_uid:
                    # Leftover message from a previous run - reject.
                    continue
            except (TypeError, ValueError):
                pass
            fetch_data = self._fetch_message(uid)
            if fetch_data:
                message = self._parse_message(uid, fetch_data)
                if message:
                    messages.append(message)

        # Apply the configured clock-skew tolerance to the freshness cutoff so
        # that a mail-server clock slightly behind the client clock does not
        # cause fresh OTP emails to be falsely rejected as stale. The UID
        # baseline above still rejects leftovers from previous runs.
        cutoff = workflow_start - timedelta(
            seconds=self.config.otp_clock_skew_tolerance_seconds
        )
        valid = [
            message
            for message in messages
            if message.subject.strip().lower() == subject.strip().lower()
            and message.received_at >= cutoff
        ]
        if not valid:
            logger.debug(
                "No valid OTP message found (polled %d candidate(s))", len(messages)
            )
            return None

        newest = max(valid, key=lambda message: message.received_at)
        token = extract_token_from_email_body(newest.body)
        if token is None:
            logger.debug("No token found in newest OTP message UID %s", newest.uid)
            return None

        return OTPToken(
            token=token,
            email_subject=newest.subject,
            email_received_at=newest.received_at,
            email_body_preview=newest.body[:200],
        )

    def _refresh_mailbox(self) -> None:
        """Refresh the mailbox view with NOOP so new emails are visible."""
        if self._connection is None:
            raise OTPError("IMAP connection not established")
        try:
            self._connection.noop()
        except Exception:
            logger.warning("NOOP failed, re-selecting mailbox")
            try:
                status, _ = self._connection.select("INBOX", readonly=True)
                if status != "OK":
                    raise OTPError("Failed to re-select INBOX mailbox")
            except OTPError:
                raise
            except Exception as exc:
                raise OTPError("Failed to refresh IMAP mailbox") from exc

    def _search_uids(self, since_date: str) -> list[bytes]:
        """Search for candidate UIDs with the OTP subject since the given date."""
        if self._connection is None:
            return []
        criteria = f'(SINCE "{since_date}" HEADER Subject "{self.config.otp_email_subject}")'
        status, data = self._connection.uid(
            "search", None, criteria  # type: ignore[arg-type]  # imaplib accepts None charset
        )
        if status != "OK" or not data:
            return []
        uids = data[0]
        if not isinstance(uids, bytes):
            return []
        return uids.split()

    def _fetch_message(self, uid: str) -> list[Any] | None:
        """Fetch a single message body (peek only - never marks as seen)."""
        if self._connection is None:
            return None
        status, data = self._connection.uid("fetch", uid, "(INTERNALDATE BODY.PEEK[])")
        if status != "OK":
            return None
        return data

    # ------------------------------------------------------------------
    # Message parsing
    # ------------------------------------------------------------------

    def _parse_message(self, uid: str, fetch_data: list[Any]) -> _OtpMessage | None:
        """Parse raw IMAP fetch data into an _OtpMessage, or None on failure."""
        try:
            raw_bytes = None
            for item in fetch_data:
                if isinstance(item, tuple) and len(item) >= 2:
                    raw_bytes = item[1]
                    break
            if not raw_bytes or not isinstance(raw_bytes, bytes):
                return None

            parsed = email.message_from_bytes(raw_bytes)
            subject = self._decode_subject(parsed.get("Subject"))

            received_at = self._extract_internal_date(fetch_data)
            if received_at is None:
                date_header = parsed.get("Date")
                if date_header:
                    try:
                        received_at = email.utils.parsedate_to_datetime(date_header)
                    except Exception:
                        received_at = None

            if received_at is None or received_at.tzinfo is None:
                logger.warning("Message UID %s missing/invalid date, rejecting", uid)
                return None

            body = self._extract_body(parsed)
            return _OtpMessage(
                uid=uid,
                subject=subject,
                received_at=received_at,
                body=body,
            )
        except Exception as exc:
            logger.warning("Failed to parse IMAP message UID %s: %s", uid, exc)
            return None

    def _extract_internal_date(self, fetch_data: list[Any]) -> datetime | None:
        """Extract the INTERNALDATE from IMAP fetch data as an aware datetime."""
        for item in fetch_data:
            if isinstance(item, tuple) and len(item) >= 1:
                header_line = item[0]
                if not isinstance(header_line, bytes):
                    header_line = str(header_line).encode("utf-8")
                match = re.search(rb'INTERNALDATE\s+"([^"]+)"', header_line, re.IGNORECASE)
                if match:
                    date_str = match.group(1).decode("ascii", errors="replace")
                    return self.parse_email_timestamp(date_str)
        return None

    def _decode_subject(self, subject_raw: str | None) -> str:
        """Decode an RFC 2047-encoded message subject."""
        if not subject_raw:
            return ""
        decoded_parts = decode_header(subject_raw)
        decoded = ""
        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                decoded += part.decode(charset or "utf-8", errors="replace")
            else:
                decoded += part
        return decoded

    def _extract_body(self, msg: email.message.Message) -> str:
        """Extract the plain-text (or HTML) body from a parsed message."""
        plain_body = ""
        html_body = ""
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain" and not plain_body:
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        plain_body = payload.decode("utf-8", errors="replace")
                    else:
                        plain_body = str(payload)
                elif content_type == "text/html" and not html_body:
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        html_body = payload.decode("utf-8", errors="replace")
                    else:
                        html_body = str(payload)
            return plain_body if plain_body else html_body
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            return payload.decode("utf-8", errors="replace")
        return str(payload)

    def _since_cutoff_date(self, run_start: datetime) -> str:
        """IMAP SINCE date covering the acceptance window.

        IMAP ``SINCE`` compares against the server's internal date (usually UTC),
        so subtract a full day from the login timestamp to absorb timezone and
        server-clock differences while keeping the candidate set small.
        """
        cutoff = run_start - timedelta(days=1)
        return f"{cutoff.day}-{cutoff.strftime('%b')}-{cutoff.year}"

    # ------------------------------------------------------------------
    # Timestamp parsing
    # ------------------------------------------------------------------

    def parse_email_timestamp(self, timestamp_str: str) -> datetime | None:
        """Parse email timestamp string to timezone-aware datetime.

        Supports common IMAP INTERNALDATE / webmail formats:
        - YYYY-MM-DD HH:MM:SS
        - YYYY-MM-DD HH:MM
        - ISO-8601 with T (2024-01-15T12:00:00)
        - ISO-8601 with Z (2024-01-15T12:00:00Z)
        - DD/MM/YYYY HH:MM:SS
        - DD/MM/YYYY HH:MM
        - DD MMM YYYY HH:MM:SS  (IMAP INTERNALDATE format, e.g. 15-Jan-2024)
        - DD MMM YYYY HH:MM
        - MMM DD, YYYY HH:MM:SS
        - MMM DD, YYYY HH:MM
        - With timezone: GMT+7, UTC, +0700, +07:00, etc.

        Returns None if timestamp cannot be parsed (fail closed - no fallback to now()).
        """
        if not timestamp_str or not timestamp_str.strip():
            return None

        clean_str = timestamp_str.strip()

        # Extract timezone info and strip it from the string before parsing.
        # The tz token must appear at the END of the string; anchoring on the end
        # prevents date parts like "-2024" in "15-Jan-2024" from being mistaken
        # for an offset. Supported tokens: GMT+7, GMT-5, GMT+07:00, UTC, UTC+7,
        # +0700, +07:00, -0500, Z.
        tz_match = re.search(
            r"(GMT[+-]\d{1,2}(?::\d{2})?|UTC[+-]\d{1,2}(?::\d{2})?|[+-]\d{2}:?\d{2}|UTC|GMT|Z)\s*$",
            clean_str,
            re.IGNORECASE,
        )
        tz_offset: tzinfo | None = None
        if tz_match:
            tz_token = tz_match.group(1)
            tz_offset = self._parse_tz_offset(tz_token)
            clean_str = clean_str[: tz_match.start(1)].strip()

        # Common timestamp formats to try
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%dT%H:%M:%S",  # ISO with T
            "%Y-%m-%dT%H:%M",  # ISO with T, no seconds
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",
            "%d-%b-%Y %H:%M:%S",  # IMAP INTERNALDATE, e.g. 15-Jan-2024 12:00:00
            "%d-%b-%Y %H:%M",
            "%d %b %Y %H:%M:%S",
            "%d %b %Y %H:%M",
            "%b %d, %Y %H:%M:%S",
            "%b %d, %Y %H:%M",
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(clean_str, fmt)
                if tz_offset is not None:
                    dt = dt.replace(tzinfo=tz_offset)
                else:
                    # No timezone in string - assume configured timezone
                    dt = dt.replace(tzinfo=self.config.get_timezone())
                return dt
            except ValueError:
                continue

        return None

    @staticmethod
    def _parse_tz_offset(token: str) -> tzinfo | None:
        """Parse a timezone token like GMT+7, UTC, +0700, or +07:00 into tzinfo."""
        token = token.strip().upper()
        if token in ("UTC", "GMT", "Z"):
            return ZoneInfo("UTC")
        # Match +0700, +07:00, -0500, -05:00, GMT+7, GMT-5, GMT+07:00, UTC+7, etc.
        m = re.match(r"(?:GMT|UTC)?([+-])(\d{1,2})(?::(\d{2}))?(?:00)?$", token)
        if m:
            sign = 1 if m.group(1) == "+" else -1
            hours = int(m.group(2))
            minutes = int(m.group(3) or 0)
            return timezone(timedelta(hours=sign * hours, minutes=sign * minutes))
        return None
