"""CMP Portal login and authentication flow."""

import logging
from datetime import datetime

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .config import Config
from .exceptions import (
    AuthenticationError,
    BrowserError,
    OTPError,
    OTPExpiredError,
    OTPNotFoundError,
    OTPTimeoutError,
)
from .mailbox import MailboxClient, OTPToken

logger = logging.getLogger(__name__)


class CMPLogin:
    """Handles CMP Portal login and OTP authentication."""

    # Selectors for login page
    USERNAME_SELECTORS = [
        'input[name="username"]',
        'input[id="username"]',
        'input[type="text"][autocomplete="username"]',
        'input[placeholder*="Username" i]',
        'input[placeholder*="username" i]',
    ]
    PASSWORD_SELECTORS = [
        'input[name="password"]',
        'input[id="password"]',
        'input[type="password"][autocomplete="current-password"]',
        'input[placeholder*="Password" i]',
        'input[placeholder*="password" i]',
    ]
    SUBMIT_SELECTORS = [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Login")',
        'button:has-text("Sign In")',
        'button:has-text("Log In")',
    ]

    # Selectors for token/OTP page
    TOKEN_INPUT_SELECTORS = [
        'input[name="token"]',
        'input[id="token"]',
        'input[name="otp"]',
        'input[id="otp"]',
        'input[placeholder*="Token" i]',
        'input[placeholder*="OTP" i]',
        'input[placeholder*="One Time" i]',
    ]
    TOKEN_SUBMIT_SELECTORS = [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Submit")',
        'button:has-text("Verify")',
        'button:has-text("Continue")',
    ]

    # Selectors for authenticated state verification
    AUTHENTICATED_SELECTORS = [
        '[data-testid="app-layout"]',
        '[data-testid="products-table"]',
        '[data-testid="dashboard"]',
        '.v-csslayout.sparks',
        'a[href*="#products"]',
        'a[href*="#dashboard"]',
    ]

    def __init__(self, config: Config, mailbox: MailboxClient):
        self.config = config
        self.mailbox = mailbox
        self.workflow_start_time: datetime | None = None

    async def login(self, page: Page) -> None:
        """Authenticate CMP, reusing a strictly verified persistent session."""
        self.workflow_start_time = datetime.now(self.config.get_timezone())
        logger.info("Starting CMP login flow at %s", self.workflow_start_time.isoformat())

        if await self._is_authenticated(page):
            logger.info("Reusing authenticated CMP session")
            return

        await self._navigate_to_login(page)
        if await self._is_authenticated(page):
            logger.info("CMP redirected to an authenticated portal session")
            return
        await self._fill_credentials(page)
        await self._submit_login(page)
        await self._wait_for_token_page(page)

        otp_token = await self._retrieve_otp(page)
        await self._submit_token(page, otp_token.token)
        await self._verify_authentication(page)

        logger.info("CMP login completed successfully")

    async def _navigate_to_login(self, page: Page) -> None:
        """Navigate to CMP login page."""
        logger.debug("Navigating to CMP login page")
        try:
            await page.goto(self.config.cmp_login_url, wait_until="networkidle")
        except PlaywrightTimeoutError as e:
            raise BrowserError("Timeout navigating to CMP login page", str(e)) from e

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

    async def _retrieve_otp(self, page: Page) -> OTPToken:
        """Retrieve OTP from GMF webmail."""
        logger.debug("Retrieving OTP from GMF webmail")
        if not self.workflow_start_time:
            raise OTPError("Workflow start time not set")

        # Set workflow start time on mailbox client for OTP freshness validation
        self.mailbox.set_workflow_start_time(self.workflow_start_time)

        # Use a separate page for webmail so the CMP token page is not navigated away.
        webmail_page = await page.context.new_page()
        try:
            otp_token = await self.mailbox.get_latest_otp(
                page=webmail_page,
                subject=self.config.otp_email_subject,
                timeout_seconds=self.config.otp_timeout_seconds,
                poll_interval_seconds=self.config.otp_poll_interval_seconds,
            )
            logger.info("OTP retrieved successfully")
            return otp_token
        except (OTPTimeoutError, OTPNotFoundError, OTPExpiredError):
            raise
        except Exception as e:
            raise OTPError("Failed to retrieve OTP", str(e)) from e
        finally:
            await webmail_page.close()

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

    async def _is_authenticated(self, page: Page) -> bool:
        """Return true only for a portal fragment plus a known portal element."""
        url = page.url
        if "#!products" not in url and "#!dashboard" not in url:
            return False
        for selector in self.AUTHENTICATED_SELECTORS:
            try:
                locator = page.locator(selector).first
                if await locator.count() and await locator.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _verify_authentication(self, page: Page) -> None:
        """Verify authentication using portal URL and a known portal element."""
        logger.debug("Verifying authentication success")
        try:
            deadline = 30000
            for _ in range(6):
                if await self._is_authenticated(page):
                    logger.debug("CMP portal authentication verified")
                    return
                await page.wait_for_timeout(deadline // 6)
            raise AuthenticationError("Authentication verification failed - portal state was not confirmed")
        except PlaywrightTimeoutError as e:
            # Get diagnostic info
            current_url = page.url
            dom_summary = await self._get_dom_summary(page)
            raise AuthenticationError(
                "Authentication verification timed out",
                f"URL: {current_url}, DOM: {dom_summary}",
            ) from e

    async def _get_dom_summary(self, page: Page) -> str:
        """Get a brief DOM summary for diagnostics without exposing secrets."""
        try:
            body_text = await page.locator("body").inner_text()
            # Truncate and sanitize
            lines = body_text.split("\n")[:20]
            return " | ".join(line.strip()[:100] for line in lines if line.strip())
        except Exception:
            return "Unable to retrieve DOM summary"
