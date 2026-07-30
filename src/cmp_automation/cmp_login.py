"""CMP Portal login and authentication flow."""

import logging
from datetime import datetime
from urllib.parse import urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .auth_diag import AuthDiag
from .config import Config
from .exceptions import (
    AuthenticationError,
    BrowserError,
    OTPError,
    OTPTimeoutError,
)
from .mailbox import MailboxClient, OTPToken

logger = logging.getLogger(__name__)


class CMPLogin:
    """Handles CMP Portal login and OTP authentication."""

    # Selectors for CAS login page (exact proven selectors first)
    USERNAME_SELECTORS = [
        "#username",
        'input[name="username"]',
        'input[id="username"]',
        'input[type="text"][autocomplete="username"]',
        'input[placeholder*="Username" i]',
        'input[placeholder*="username" i]',
    ]
    PASSWORD_SELECTORS = [
        "#password",
        'input[name="password"]',
        'input[id="password"]',
        'input[type="password"][autocomplete="current-password"]',
        'input[placeholder*="Password" i]',
        'input[placeholder*="password" i]',
    ]
    SUBMIT_SELECTORS = [
        "#fm1 input[name='submit'][type='submit']",
        "#fm1 input[type='submit']",
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Login")',
        'button:has-text("Sign In")',
        'button:has-text("Log In")',
    ]

    # Selectors for token/OTP page (exact proven selectors first)
    TOKEN_INPUT_SELECTORS = [
        "#token",
        'input[name="token"]',
        'input[id="token"]',
        'input[name="otp"]',
        'input[id="otp"]',
        'input[placeholder*="Token" i]',
        'input[placeholder*="OTP" i]',
        'input[placeholder*="One Time" i]',
    ]
    TOKEN_SUBMIT_SELECTORS = [
        "#login input[name='_eventId_submit'][type='submit']",
        "#login input[type='submit']",
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Submit")',
        'button:has-text("Verify")',
        'button:has-text("Continue")',
    ]

    # Safe structural DOM diagnostic for the auth failure path: reports only
    # element counts and tag/id/class/role summaries - never element text, so
    # credentials, OTPs, or arbitrary page body text cannot leak into the
    # raised error.
    _AUTH_DIAGNOSTIC_JS = """() => {
  const count = (sel) => document.querySelectorAll(sel).length;
  const summarize = (sel, max) => {
    const out = [];
    for (const el of Array.from(document.querySelectorAll(sel)).slice(0, max)) {
      const cls = typeof el.className === 'string'
        ? el.className.trim().split(' ').filter(Boolean).join('.')
        : '';
      out.push(
        el.tagName.toLowerCase() +
        (el.id ? '#' + el.id : '') +
        (cls ? '.' + cls : '') +
        (el.getAttribute('role') ? '[role=' + el.getAttribute('role') + ']' : '')
      );
    }
    return out;
  };
  return {
    inputs: count('input'),
    passwords: count('input[type="password"]'),
    buttons: count('button'),
    forms: count('form'),
    grids: count('[role="grid"], .v-grid'),
    windows: count('.v-window'),
    dialogs: count('[role="dialog"]'),
    bodyChildren: summarize('body > *', 10)
  };
}"""

    def __init__(
        self,
        config: Config,
        mailbox: MailboxClient,
        diagnose_auth: bool = False,
    ):
        self.config = config
        self.mailbox = mailbox
        # Opt-in diagnostic only: passively records sanitized metadata (URL
        # state categories, navigation events, structural DOM counts, network
        # metadata) around the OTP submission / authentication verification
        # (see ``AuthDiag``). It never changes workflow behavior - no retries,
        # no reloads, no OTP resubmission - and is inactive unless
        # ``--diagnose-auth`` is passed.
        self.diagnose_auth = diagnose_auth
        self.workflow_start_time: datetime | None = None

    async def login(self, page: Page) -> None:
        """Authenticate CMP, reusing a strictly verified persistent session."""
        # Record the workflow start timestamp before initial credential submission
        # so OTP freshness validation only accepts emails received at/after this point.
        self.workflow_start_time = datetime.now(self.config.get_timezone())
        self.mailbox.set_workflow_start_time(self.workflow_start_time)
        logger.info("Starting CMP login flow at %s", self.workflow_start_time.isoformat())

        if await self._is_authenticated(page):
            logger.info("Reusing authenticated CMP session")
            return

        await self._navigate_to_login(page)
        if await self._is_authenticated(page):
            logger.info("CMP redirected to an authenticated portal session")
            return
        # Connect IMAP before submitting credentials so the UID snapshot taken at
        # connect time predates the OTP email arrival; otherwise the current run's
        # OTP could be mistaken for a stale leftover and rejected.
        await self.mailbox.connect()
        await self._fill_credentials(page)
        await self._submit_login(page)
        await self._wait_for_token_page(page)

        otp_token = await self._retrieve_otp()

        # Opt-in: attach the sanitized auth diagnostic immediately before the
        # OTP submission so the submission's requests, the URL-state
        # transitions through the CAS redirect / SPA mount, and the final DOM
        # are captured. Detached (and summarized) after verification completes
        # or fails - including on failure - so the diagnostic can tell whether
        # the submission fired a request, the redirect happened, or the SPA
        # never mounted. The diagnostic never touches the OTP value.
        diag: AuthDiag | None = None
        if self.diagnose_auth:
            diag = AuthDiag(approved_host=self._approved_host())
            await diag.attach(page)
        try:
            await self._submit_token(page, otp_token.token)
            await self._verify_authentication(page)
        finally:
            if diag is not None:
                await diag.detach()
                logger.info("Post-OTP auth diagnostic: %s", diag.summary())

        logger.info("CMP login completed successfully")

    async def _navigate_to_login(self, page: Page) -> None:
        """Navigate to CMP login page with a bounded retry.

        The CAS login page is server-rendered, so ``domcontentloaded`` is the
        readiness signal; ``networkidle`` can stall on background keep-alives
        and time out even when the page is usable. Transient network failures
        (``NS_ERROR_NET_TIMEOUT``, ``NS_ERROR_SOCKET_CREATE_FAILED``, ...) are
        retried up to ``max_attempts`` times with a short delay, so the whole
        navigation stays bounded. The credential fill that follows provides the
        real page-readiness wait.
        """
        logger.debug("Navigating to CMP login page")
        max_attempts = 3
        retry_delay_ms = 2000
        last_error_category: str | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                await page.goto(self.config.cmp_login_url, wait_until="domcontentloaded")
                return
            except PlaywrightError as e:
                # Only Playwright/browser-level failures are retried
                # (PlaywrightTimeoutError plus network-level errors such as
                # NS_ERROR_NET_TIMEOUT / NS_ERROR_SOCKET_CREATE_FAILED, which
                # surface as the base Playwright Error). Arbitrary programming
                # errors propagate immediately instead of being masked as a
                # transient network failure.
                #
                # Only the error *category* (class name) is recorded, logged,
                # and reported - never the raw exception message, which can
                # embed the destination URL or page content.
                last_error_category = type(e).__name__
            if attempt < max_attempts:
                logger.warning(
                    "CMP login page navigation failed (attempt %d/%d) - %s; retrying",
                    attempt,
                    max_attempts,
                    last_error_category,
                )
                try:
                    await page.wait_for_timeout(retry_delay_ms)
                except PlaywrightError:
                    # The retry delay itself can fail when the page is closed
                    # or the browser is dying. That must not escape as a raw
                    # Playwright error: normalize to BrowserError with the
                    # safe category already recorded. No further retries - a
                    # page that cannot wait cannot navigate either.
                    raise BrowserError(
                        "Timeout navigating to CMP login page",
                        f"last error category: {last_error_category}"
                        if last_error_category
                        else None,
                    )
        raise BrowserError(
            "Timeout navigating to CMP login page",
            f"last error category: {last_error_category}"
            if last_error_category
            else None,
        )

    async def _fill_credentials(self, page: Page) -> None:
        """Fill username and password fields."""
        logger.debug("Filling credentials")

        username_filled = await self._try_fill(page, self.USERNAME_SELECTORS, self.config.cmp_username, "username")
        if not username_filled:
            raise AuthenticationError("Could not find username input field")

        password_filled = await self._try_fill(page, self.PASSWORD_SELECTORS, self.config.cmp_password, "password")
        if not password_filled:
            raise AuthenticationError("Could not find password input field")

    async def _try_fill(self, page: Page, selectors: list[str], value: str, field_name: str) -> bool:
        """Try to fill a field using multiple selector fallbacks."""
        for selector in selectors:
            try:
                element = page.locator(selector).first
                if await element.count() > 0:
                    await element.fill(value)
                    logger.debug("Filled %s using selector: %s", field_name, selector)
                    return True
            except Exception:
                continue
        return False

    async def _submit_login(self, page: Page) -> None:
        """Submit the login form."""
        logger.debug("Submitting login form")
        for selector in self.SUBMIT_SELECTORS:
            try:
                button = page.locator(selector).first
                if await button.count() > 0:
                    await button.click()
                    logger.debug("Clicked login button using selector: %s", selector)
                    return
            except Exception:
                continue
        raise AuthenticationError("Could not find login submit button")

    async def _wait_for_token_page(self, page: Page) -> None:
        """Wait for the token/OTP page to appear."""
        logger.debug("Waiting for token page")
        try:
            # Wait for token input field to appear
            for selector in self.TOKEN_INPUT_SELECTORS:
                try:
                    await page.locator(selector).first.wait_for(state="visible", timeout=30000)
                    logger.debug("Token page detected with selector: %s", selector)
                    return
                except PlaywrightTimeoutError:
                    continue
            raise AuthenticationError("Token page did not load - token input not found")
        except PlaywrightTimeoutError as e:
            raise AuthenticationError("Timeout waiting for token page", str(e)) from e

    async def _retrieve_otp(self) -> OTPToken:
        """Retrieve OTP directly from the GMF mailbox via IMAP."""
        logger.debug("Retrieving OTP via IMAP")
        if not self.workflow_start_time:
            raise OTPError("Workflow start time not set")

        # Workflow start time is propagated to the mailbox client in login();
        # set it here again to guard against direct calls.
        self.mailbox.set_workflow_start_time(self.workflow_start_time)

        try:
            otp_token = await self.mailbox.get_latest_otp(
                subject=self.config.otp_email_subject,
                timeout_seconds=self.config.otp_timeout_seconds,
                poll_interval_seconds=self.config.otp_poll_interval_seconds,
            )
            logger.info("OTP retrieved successfully")
            return otp_token
        except OTPTimeoutError:
            raise
        except Exception as e:
            raise OTPError("Failed to retrieve OTP", str(e)) from e

    async def _submit_token(self, page: Page, token: str) -> None:
        """Submit the OTP token on the CMP token page."""
        logger.debug("Submitting OTP token")

        token_filled = await self._try_fill(page, self.TOKEN_INPUT_SELECTORS, token, "token")
        if not token_filled:
            raise AuthenticationError("Could not find token input field")

        for selector in self.TOKEN_SUBMIT_SELECTORS:
            try:
                button = page.locator(selector).first
                if await button.count() > 0:
                    await button.click()
                    logger.debug("Clicked token submit button using selector: %s", selector)
                    return
            except Exception:
                continue
        raise AuthenticationError("Could not find token submit button")

    def _approved_host(self) -> str | None:
        """Derive the approved portal host from the configured Products URL."""
        parsed = urlparse(self.config.cmp_products_url)
        return parsed.hostname

    def _is_portal_page(self, url: str, fragment: str) -> bool:
        """Strict portal URL validation: HTTPS, approved host, root path, exact fragment.

        No substring-only host checks and no trusting untrusted hosts. The
        approved host is derived from the configured products URL so the check
        follows configuration.
        """
        try:
            if not isinstance(url, str):
                return False
            parsed = urlparse(url)
            approved = urlparse(self.config.cmp_products_url)
            if parsed.scheme != "https" or approved.scheme != "https":
                return False
            if approved.hostname is None or parsed.hostname != approved.hostname:
                return False
            # Reject any explicit port: hostname alone would accept :443/:8443 tricks.
            if parsed.port is not None:
                return False
            if parsed.path not in ("", "/"):
                return False
            if parsed.fragment != fragment:
                return False
            return True
        except Exception:
            return False

    def _is_products_page(self, url: str) -> bool:
        """Strict check for the approved Products page URL (fragment '!products')."""
        return self._is_portal_page(url, "!products")

    def _is_dashboard_page(self, url: str) -> bool:
        """Strict check for the approved Dashboard page URL (fragment '!dashboard')."""
        return self._is_portal_page(url, "!dashboard")

    async def _window_href(self, page: Page) -> str | None:
        """Read the current SPA URL via ``window.location.href``, or None on failure.

        SPA hash navigation may update ``window.location`` before ``page.url``;
        this is the fallback used by the authentication checks below.
        """
        try:
            href = await page.evaluate("window.location.href")
            return href if isinstance(href, str) else None
        except Exception:
            return None

    def _read_page_url(self, page: Page) -> str:
        """Safely read ``page.url``; a closed/crashed page yields '<unavailable>'.

        A raw Playwright error from the URL read must never escape the poll
        loop or the final raise path - the bounded recovery and the final
        ``AuthenticationError`` carry the normalized message instead.
        """
        try:
            return page.url
        except PlaywrightError:
            return "<unavailable>"

    async def _is_authenticated(self, page: Page) -> bool:
        """Return true only for a strictly validated portal session.

        Recognizes the approved Products or Dashboard URL with exact HTTPS,
        host, root path, and fragment validation. Never relies on weak
        substring matches or DOM selectors.
        """
        if self._is_products_page(page.url) or self._is_dashboard_page(page.url):
            return True
        # SPA hash navigation may lag behind page.url - fall back to JS state.
        href = await self._window_href(page)
        if href is not None and (
            self._is_products_page(href) or self._is_dashboard_page(href)
        ):
            return True
        return False

    async def _verify_authentication(self, page: Page) -> None:
        """Verify authentication by reaching the strictly validated Products URL.

        After the OTP is submitted, CAS redirects to the approved portal and the
        SPA navigates to ``#!products``. The exact Products URL (HTTPS, approved
        host, root path, fragment ``!products``) is the authentication guarantee;
        no speculative DOM selectors are required.

        The portal SPA occasionally fails to mount after the CAS redirect (blank
        body at the root URL). Bounded page reloads are attempted before giving
        up, so transient SPA boot failures self-heal; a sustained slow mount
        (observed live: two consecutive 30-second windows both stuck at the root)
        is covered by a second reload window. The whole check stays bounded
        (three 30-second windows plus up to two reloads).
        """
        logger.debug("Verifying authentication success")
        deadline = 30000
        interval = 2000
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            for _ in range(deadline // interval):
                # Every iteration guards the URL read: a closed/crashed page
                # must never surface a raw Playwright error from the poll
                # loop - '<unavailable>' simply fails the strict URL check and
                # the bounded recovery (reload) continues normally.
                if self._is_products_page(self._read_page_url(page)):
                    logger.debug("CMP portal authentication verified")
                    return
                # SPA hash navigation may update window.location before page.url.
                href = await self._window_href(page)
                if href is not None and self._is_products_page(href):
                    logger.debug("CMP portal authentication verified (window.location.href)")
                    return
                try:
                    await page.wait_for_timeout(interval)
                except PlaywrightError as e:
                    # A dying/closed page must not surface a raw Playwright
                    # error from the poll loop. Log only the safe category and
                    # move this window onto the bounded reload path; the final
                    # failure is always a normalized AuthenticationError.
                    logger.warning(
                        "Portal poll wait failed (%s); moving to reload path",
                        type(e).__name__,
                    )
                    break
            if attempt < max_attempts:
                logger.warning(
                    "Products URL not reached after %d ms; reloading portal and retrying",
                    deadline,
                )
                # Normalize reload failures: a failed reload must not crash the
                # whole workflow with a raw Playwright error - the next bounded
                # poll window may still observe the SPA completing its boot.
                # Only the safe error category is logged - never the raw
                # exception message, which can embed page content.
                try:
                    await page.reload()
                except PlaywrightError as e:
                    logger.warning(
                        "Portal reload failed (attempt %d/%d) - %s; continuing to poll",
                        attempt,
                        max_attempts,
                        type(e).__name__,
                    )

        # Get diagnostic info and raise with it - do not lose the failure context.
        current_url = self._read_page_url(page)
        dom_summary = await self._get_dom_summary(page)
        raise AuthenticationError(
            "Authentication verification failed - Products page was not reached",
            f"URL: {current_url}, DOM: {dom_summary}",
        )

    async def _get_dom_summary(self, page: Page) -> str:
        """Collect a bounded, structural-only DOM diagnostic.

        The in-page script reports only tag names, ids, classes, roles, and
        element counts. Element text content is never read, so credentials,
        OTPs, or arbitrary page body text cannot leak into the raised error.
        """
        try:
            info = await page.evaluate(self._AUTH_DIAGNOSTIC_JS)
        except Exception:
            return "Unable to retrieve DOM summary"
        if not isinstance(info, dict):
            return "Unable to retrieve DOM summary"
        parts = []
        for key, value in info.items():
            if isinstance(value, list):
                parts.append(
                    f"{key}[{len(value)}]: " + " | ".join(str(v) for v in value[:10])
                )
            else:
                parts.append(f"{key}={value}")
        if not parts:
            return "Unable to retrieve DOM summary"
        summary = " | ".join(parts)
        return summary[:2000]
