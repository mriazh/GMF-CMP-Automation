"""GMF Webmail client for OTP retrieval."""

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from time import monotonic
from zoneinfo import ZoneInfo

from playwright.async_api import Locator, Page

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
class _EmailCandidate:
    """Internal candidate email found in the inbox list."""

    email_subject: str
    email_received_at: datetime
    list_selector: str
    row_identity: str  # Stable row identity (message-id, href, data-id, etc.)


class MailboxClient:
    """Client for interacting with GMF Webmail to retrieve OTP emails."""

    # Selectors for webmail login - GMF MDaemon specific
    LOGIN_EMAIL_SELECTORS = [
        "input#User",
        'input[name="username"]',
        'input[id="username"]',
        'input[type="email"]',
        'input[placeholder*="Email" i]',
    ]
    LOGIN_PASSWORD_SELECTORS = [
        "input#Password",
        'input[name="password"]',
        'input[id="password"]',
        'input[type="password"]',
    ]
    LOGIN_SUBMIT_SELECTORS = [
        "button#Logon",
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Login")',
        'button:has-text("Sign In")',
        'button:has-text("Masuk")',
    ]

    # Selectors for email list
    EMAIL_LIST_SELECTORS = [
        ".email-list .email-item",
        ".message-list .message-item",
        "tr[data-message-id]",
        "tr[data-id]",
        ".email-row",
        "tbody tr",
    ]

    # Selectors for email subject in list view
    EMAIL_SUBJECT_SELECTORS = [
        "[data-subject]",
        ".email-subject",
        ".message-subject",
        "td.subject",
        "th.subject",
        '[aria-label*="Subject" i]',
        'td:has-text("Subject") + td',
    ]

    # Selectors for email timestamp in list view
    EMAIL_TIMESTAMP_SELECTORS = [
        "[data-timestamp]",
        "[data-received]",
        ".email-date",
        ".message-date",
        ".email-time",
        ".message-time",
        "td.date",
        "td.time",
        '[aria-label*="Date" i]',
        '[aria-label*="Time" i]',
        '[title*="202"]',  # Year in title attribute
    ]

    # Selectors for email body in detail view
    EMAIL_BODY_SELECTORS = [
        "[data-body]",
        ".email-body",
        ".message-body",
        ".email-content",
        ".message-content",
        "#message-body",
        ".mail-body",
        '[role="main"] .content',
    ]

    # Selectors for sender information
    EMAIL_SENDER_SELECTORS = [
        "[data-sender]",
        "[data-from]",
        ".email-from",
        ".message-from",
        ".sender",
        "td.from",
        '[aria-label*="From" i]',
    ]

    # Account verification selectors (post-login)
    ACCOUNT_INDICATOR_SELECTORS = [
        "[data-user-email]",
        "[data-account-email]",
        ".user-email",
        ".account-email",
        ".user-info .email",
        "#user-email",
        '[aria-label*="Account" i]',
        ".user-menu .email",
    ]

    def __init__(self, config: Config):
        self.config = config
        self._authenticated = False
        self._workflow_start_time: datetime | None = None
        self._poll_start_monotonic: float | None = None

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

    def _start_poll_timer(self) -> None:
        """Start the monotonic timer for polling timeout."""
        self._poll_start_monotonic = monotonic()

    def _check_poll_timeout(self, timeout_seconds: int) -> bool:
        """Check if the polling timeout has been exceeded using monotonic clock."""
        if self._poll_start_monotonic is None:
            return False
        return (monotonic() - self._poll_start_monotonic) >= timeout_seconds

    async def ensure_authenticated(self, page: Page) -> None:
        """Ensure webmail is authenticated and verify the correct account."""
        logger.debug("Checking webmail authentication status")
        try:
            await page.goto(self.config.gmf_webmail_url, wait_until="networkidle")

            # Check if already logged in - verify account identity
            if await self._verify_account_identity(page):
                logger.debug("Webmail session already authenticated with correct account")
                self._authenticated = True
                return

            # Need to login
            await self._login(page)

            # Verify account after login
            if not await self._verify_account_identity(page):
                raise OTPError(
                    "Account verification failed after login - cannot confirm correct mailbox"
                )

            self._authenticated = True
        except OTPError:
            raise
        except Exception as e:
            raise OTPError("Failed to authenticate to webmail", str(e)) from e

    async def _verify_account_identity(self, page: Page) -> bool:
        """Verify that the displayed account matches the configured GMF_EMAIL.

        Returns True if the account matches, False if indicator not found or mismatch.
        """
        gmf_email_lower = self.config.gmf_email.lower()
        for selector in self.ACCOUNT_INDICATOR_SELECTORS:
            try:
                element = page.locator(selector).first
                if await element.count() > 0:
                    # Try to get email from various attributes
                    displayed_email = None
                    for attr in [
                        "data-user-email",
                        "data-account-email",
                        "data-email",
                        "title",
                        "aria-label",
                    ]:
                        try:
                            displayed_email = await element.get_attribute(attr)
                            if displayed_email:
                                break
                        except Exception:
                            continue

                    # Fallback to inner text
                    if not displayed_email:
                        try:
                            displayed_email = await element.inner_text()
                        except Exception:
                            continue

                    if displayed_email:
                        displayed_email = displayed_email.strip().lower()
                        if displayed_email == gmf_email_lower:
                            logger.debug("Verified account identity: %s", displayed_email)
                            return True
                        else:
                            logger.warning(
                                "Account mismatch: expected %s, found %s",
                                self.config.gmf_email,
                                displayed_email,
                            )
                            return False
            except Exception:
                continue

        logger.debug("Account indicator not found in DOM")
        return False

    async def _login(self, page: Page) -> None:
        """Login to GMF webmail using stable selectors with fallbacks."""
        logger.debug("Logging into GMF webmail")

        email_filled = await self._try_fill(
            page, self.LOGIN_EMAIL_SELECTORS, self.config.gmf_email, "email"
        )
        if not email_filled:
            raise OTPError("Could not find email input field on webmail login")

        password_filled = await self._try_fill(
            page, self.LOGIN_PASSWORD_SELECTORS, self.config.gmf_password, "password"
        )
        if not password_filled:
            raise OTPError("Could not find password input field on webmail login")

        for selector in self.LOGIN_SUBMIT_SELECTORS:
            try:
                button = page.locator(selector).first
                if await button.count() > 0:
                    await button.click()
                    logger.debug("Clicked webmail login button using selector: %s", selector)
                    # Wait for navigation or inbox to load
                    await page.wait_for_load_state("networkidle", timeout=30000)
                    return
            except Exception:
                continue
        raise OTPError("Could not find or click webmail login button")

    async def _try_fill(
        self, page: Page, selectors: list[str], value: str, field_name: str
    ) -> bool:
        """Try to fill a field using multiple selector fallbacks."""
        for selector in selectors:
            try:
                element = page.locator(selector).first
                if await element.count() > 0:
                    await element.fill(value)
                    logger.debug("Filled webmail %s using selector: %s", field_name, selector)
                    return True
            except Exception:
                continue
        return False

    async def get_latest_otp(
        self,
        page: Page,
        subject: str,
        timeout_seconds: int,
        poll_interval_seconds: int,
    ) -> OTPToken:
        """Poll for the latest OTP email matching criteria.

        Args:
            page: Playwright page for webmail
            subject: Expected email subject (case-insensitive exact match)
            timeout_seconds: Maximum time to poll (monotonic clock)
            poll_interval_seconds: Interval between poll attempts

        Returns:
            OTPToken with token, subject, received time, and body preview

        Raises:
            OTPTimeoutError: If no valid OTP found within timeout
            OTPError: On other failures
        """
        logger.info("Starting OTP polling for subject: %s", subject)

        if self._workflow_start_time is None:
            raise OTPError(
                "Workflow start time not set. Call set_workflow_start_time() before polling."
            )
        self._start_poll_timer()

        await self.ensure_authenticated(page)

        async def poll_once() -> OTPToken | None:
            return await self._find_latest_otp(page, subject)

        # Initial attempt
        result = await poll_once()
        if result:
            return result

        # Poll with monotonic timeout
        while not self._check_poll_timeout(timeout_seconds):
            await asyncio.sleep(poll_interval_seconds)
            result = await poll_once()
            if result:
                return result

        raise OTPTimeoutError(
            f"OTP polling timed out after {timeout_seconds}s for subject '{subject}' "
            f"(workflow started at {self._workflow_start_time.isoformat()})"
        )

    async def _navigate_to_inbox(self, page: Page) -> None:
        """Navigate to the webmail inbox list."""
        try:
            await page.goto(self.config.gmf_webmail_url, wait_until="networkidle")
        except Exception as e:
            raise OTPError("Failed to navigate to the GMF inbox") from e

    async def _find_latest_otp(self, page: Page, subject: str) -> OTPToken | None:
        """Find the newest OTP email matching subject and time criteria."""
        logger.debug("Searching for OTP email with subject: %s", subject)

        await self._navigate_to_inbox(page)
        candidates = await self._collect_email_candidates(page, subject)
        if not candidates:
            logger.debug("No matching OTP email found in inbox")
            return None

        # Try newest first; the token may be absent until the email is opened.
        candidates.sort(key=lambda c: c.email_received_at, reverse=True)
        for candidate in candidates:
            token = await self._open_email_and_extract_token(page, candidate)
            if token:
                logger.info(
                    "Selected OTP email received at %s",
                    candidate.email_received_at.isoformat(),
                )
                return OTPToken(
                    token=token,
                    email_subject=candidate.email_subject,
                    email_received_at=candidate.email_received_at,
                    email_body_preview="",
                )
            # Re-navigate to inbox before trying next candidate
            await self._navigate_to_inbox(page)
        return None

    async def _collect_email_candidates(
        self,
        page: Page,
        subject: str,
    ) -> list[_EmailCandidate]:
        """Collect inbox rows matching the subject and received-at criteria."""
        candidates: list[_EmailCandidate] = []

        for list_selector in self.EMAIL_LIST_SELECTORS:
            rows = page.locator(list_selector)
            try:
                count = await rows.count()
            except Exception:
                continue
            if count == 0:
                continue

            for i in range(count):
                row = rows.nth(i)
                metadata = await self._read_row_metadata(row)
                if metadata is None:
                    continue
                row_subject, ts_text, row_identity = metadata

                if row_subject.strip().lower() != subject.strip().lower():
                    continue

                if ts_text is None or ts_text.strip() == "":
                    logger.debug("Rejecting email at row %d: missing timestamp", i)
                    continue

                parsed = self.parse_email_timestamp(ts_text)
                if parsed is None:
                    logger.debug("Rejecting email at row %d: unparsable timestamp '%s'", i, ts_text)
                    continue

                received_at = parsed

                # Ensure timezone-aware and normalize to workflow timezone
                if received_at.tzinfo is None:
                    logger.debug(
                        "Rejecting email at row %d: timestamp not timezone-aware after parsing", i
                    )
                    continue

                workflow_tz = self.config.get_timezone()
                if received_at.tzinfo != workflow_tz:
                    received_at = received_at.astimezone(workflow_tz)

                workflow_start = self._get_workflow_start_time()
                if received_at < workflow_start:
                    logger.debug(
                        "Rejecting older email received at %s (workflow start: %s)",
                        received_at.isoformat(),
                        workflow_start.isoformat(),
                    )
                    continue

                candidates.append(
                    _EmailCandidate(
                        email_subject=row_subject.strip(),
                        email_received_at=received_at,
                        list_selector=list_selector,
                        row_identity=row_identity,
                    )
                )

            if candidates:
                break
        return candidates

    async def _read_row_metadata(self, row: Locator) -> tuple[str, str | None, str] | None:
        """Read subject, timestamp text, and stable row identity from an inbox row.

        Returns tuple of (subject_text, timestamp_text_or_none, row_identity) or None if row unusable.
        Row identity is a stable identifier like message-id, href, or data-id.
        """
        try:
            # Read subject - try data attribute first, then visible text
            subject_text = await self._get_row_attribute_or_text(
                row, self.EMAIL_SUBJECT_SELECTORS, "data-subject"
            )
            if not subject_text or not subject_text.strip():
                return None

            # Read timestamp - try data attribute first, then visible text
            ts_text = await self._get_row_attribute_or_text(
                row, self.EMAIL_TIMESTAMP_SELECTORS, "data-timestamp"
            )

            # Get stable row identity
            row_identity = await self._get_row_identity(row)
            if not row_identity:
                # Fallback: use subject + timestamp as quasi-identity
                row_identity = f"{subject_text.strip()}|{ts_text or 'no-timestamp'}"

            return (subject_text.strip(), ts_text.strip() if ts_text else None, row_identity)
        except Exception as e:
            logger.debug("Failed to read row metadata: %s", e)
            return None

    async def _get_row_attribute_or_text(
        self, row: Locator, selectors: list[str], attr_name: str
    ) -> str | None:
        """Get text from row using data attribute, aria-label, title, or inner_text."""
        for selector in selectors:
            try:
                el = row.locator(selector).first
                if await el.count() > 0:
                    # Try data attribute first
                    attr_value = await el.get_attribute(attr_name)
                    if attr_value and attr_value.strip():
                        return attr_value.strip()

                    # Try other common attributes
                    for attr in [
                        "data-value",
                        "title",
                        "aria-label",
                        "data-timestamp",
                        "data-subject",
                        "data-body",
                    ]:
                        attr_value = await el.get_attribute(attr)
                        if attr_value and attr_value.strip():
                            return attr_value.strip()

                    # Fallback to inner text
                    text = await el.inner_text()
                    if text and text.strip():
                        return text.strip()
            except Exception:
                continue
        return None

    async def _get_row_identity(self, row: Locator) -> str | None:
        """Extract a stable identity for the row (message-id, data-id, href, etc.)."""
        # Try common identity attributes on the row itself
        for attr in ["data-message-id", "data-id", "data-uid", "id", "data-key"]:
            try:
                value = await row.get_attribute(attr)
                if value and value.strip():
                    return value.strip()
            except Exception:
                continue

        # Try to find a link with href in the row
        try:
            link = row.locator("a[href]").first
            if await link.count() > 0:
                href = await link.get_attribute("href")
                if href and href.strip():
                    return href.strip()
        except Exception:
            pass

        return None

    async def _open_email_and_extract_token(
        self, page: Page, candidate: _EmailCandidate
    ) -> str | None:
        """Open an email row and extract the token from its body.

        Revalidates subject and timestamp before extracting to handle inbox changes.
        """
        try:
            # Re-locate the row using stable identity
            row = await self._locate_row_by_identity(page, candidate)
            if row is None:
                logger.debug(
                    "Could not re-locate candidate row with identity: %s", candidate.row_identity
                )
                return None

            # Revalidate subject and timestamp before opening
            metadata = await self._read_row_metadata(row)
            if metadata is None:
                logger.debug("Candidate row metadata no longer readable")
                return None

            row_subject, ts_text, _ = metadata
            if row_subject.strip().lower() != candidate.email_subject.strip().lower():
                logger.debug("Candidate row subject changed, skipping")
                return None

            if ts_text:
                parsed = self.parse_email_timestamp(ts_text)
                if parsed is None:
                    logger.debug("Candidate row timestamp became unparsable, skipping")
                    return None
                workflow_tz = self.config.get_timezone()
                if parsed.tzinfo != workflow_tz:
                    parsed = parsed.astimezone(workflow_tz)
                if parsed != candidate.email_received_at:
                    logger.debug("Candidate row timestamp changed, skipping")
                    return None

            # Open the email
            await row.click()
            # Wait for email detail view to render - wait for body selector
            await self._wait_for_email_body(page)

            body = await self._read_email_body(page)
            if not body:
                logger.debug("Email body empty or not found")
                return None

            # Extract sender if available
            sender = await self._read_email_sender(page)

            token = extract_token_from_email_body(body, sender)
            return token
        except Exception as e:
            logger.debug("Failed to open email and extract token: %s", e)
            return None

    async def _locate_row_by_identity(
        self, page: Page, candidate: _EmailCandidate
    ) -> Locator | None:
        """Locate a row by its stable identity."""
        # Try to find row by data attribute matching the identity
        for list_selector in self.EMAIL_LIST_SELECTORS:
            try:
                rows = page.locator(list_selector)
                count = await rows.count()
                for i in range(count):
                    row = rows.nth(i)
                    identity = await self._get_row_identity(row)
                    if identity and identity == candidate.row_identity:
                        return row
            except Exception:
                continue
        return None

    async def _wait_for_email_body(self, page: Page) -> None:
        """Wait for email body to render in detail view."""
        # Wait for at least one body selector to be visible with content
        for selector in self.EMAIL_BODY_SELECTORS:
            try:
                el = page.locator(selector).first
                await el.wait_for(state="visible", timeout=15000)
                # Additional check: ensure it has some content
                text = await el.inner_text()
                if text and text.strip():
                    return
            except Exception:
                continue
        # Fallback: wait for network idle
        await page.wait_for_load_state("networkidle", timeout=15000)

    async def _read_email_body(self, page: Page) -> str | None:
        """Read the email body text using available selectors."""
        for selector in self.EMAIL_BODY_SELECTORS:
            try:
                el = page.locator(selector).first
                if await el.count() > 0:
                    # Try data-body attribute first
                    body = await el.get_attribute("data-body")
                    if body and body.strip():
                        return body.strip()

                    # Try inner text
                    body = await el.inner_text()
                    if body and body.strip():
                        return body.strip()
            except Exception:
                continue

        # DO NOT fall back to page.body.inner_text() - that captures entire page including UI
        return None

    async def _read_email_sender(self, page: Page) -> str | None:
        """Read sender information from email detail view."""
        for selector in self.EMAIL_SENDER_SELECTORS:
            try:
                el = page.locator(selector).first
                if await el.count() > 0:
                    # Try data attributes first
                    for attr in ["data-sender", "data-from", "data-email", "title", "aria-label"]:
                        sender = await el.get_attribute(attr)
                        if sender and sender.strip():
                            return sender.strip()

                    sender = await el.inner_text()
                    if sender and sender.strip():
                        return sender.strip()
            except Exception:
                continue
        return None

    def parse_email_timestamp(self, timestamp_str: str) -> datetime | None:
        """Parse email timestamp string to timezone-aware datetime.

        Supports common MDaemon/webmail formats:
        - YYYY-MM-DD HH:MM:SS
        - YYYY-MM-DD HH:MM
        - ISO-8601 with T (2024-01-15T12:00:00)
        - ISO-8601 with Z (2024-01-15T12:00:00Z)
        - DD/MM/YYYY HH:MM:SS
        - DD/MM/YYYY HH:MM
        - DD MMM YYYY HH:MM:SS
        - DD MMM YYYY HH:MM
        - MMM DD, YYYY HH:MM:SS
        - MMM DD, YYYY HH:MM
        - With timezone: GMT+7, UTC, +0700, +07:00, etc.

        Returns None if timestamp cannot be parsed (fail closed - no fallback to now()).
        """
        if not timestamp_str or not timestamp_str.strip():
            return None

        clean_str = timestamp_str.strip()

        # Extract timezone info and strip it from the string before parsing
        # Supported tz tokens: GMT+7, GMT-5, GMT+07:00, UTC, UTC+7, +0700, +07:00, -0500, Z
        tz_match = re.search(
            r"(GMT[+-]\d{1,2}(?::\d{2})?|UTC[+-]\d{1,2}(?::\d{2})?|[+-]\d{2}:?\d{2}|UTC|GMT|Z\b)",
            clean_str,
            re.IGNORECASE,
        )
        tz_offset: tzinfo | None = None
        if tz_match:
            tz_token = tz_match.group(1)
            tz_offset = self._parse_tz_offset(tz_token)
            clean_str = (clean_str[: tz_match.start()] + clean_str[tz_match.end() :]).strip()

        # Common timestamp formats to try
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%dT%H:%M:%S",  # ISO with T
            "%Y-%m-%dT%H:%M",  # ISO with T, no seconds
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",
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
