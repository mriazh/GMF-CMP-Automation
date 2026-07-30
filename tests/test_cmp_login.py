"""Tests for the CMP login flow with IMAP-based OTP retrieval."""

from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import pytest
from playwright.async_api import Error as PlaywrightError

from cmp_automation.cmp_login import CMPLogin
from cmp_automation.config import Config
from cmp_automation.exceptions import AuthenticationError, BrowserError
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

    @pytest.mark.asyncio
    async def test_diagnose_auth_default_off(self, config):
        """The auth diagnostic is opt-in and inactive by default."""
        mailbox = MailboxClient(config)
        login = CMPLogin(config, mailbox)
        assert login.diagnose_auth is False

        page = AsyncMock()
        with patch.object(login, "_is_authenticated", new=AsyncMock(return_value=False)), \
             patch.object(login, "_navigate_to_login", new=AsyncMock()), \
             patch.object(mailbox, "connect", new=AsyncMock()), \
             patch.object(login, "_fill_credentials", new=AsyncMock()), \
             patch.object(login, "_submit_login", new=AsyncMock()), \
             patch.object(login, "_wait_for_token_page", new=AsyncMock()), \
             patch.object(mailbox, "get_latest_otp", new=AsyncMock(return_value=_token())), \
             patch.object(login, "_submit_token", new=AsyncMock()), \
             patch.object(login, "_verify_authentication", new=AsyncMock()), \
             patch("cmp_automation.cmp_login.AuthDiag") as mock_diag_cls:
            await login.login(page)

        # With the flag off, no diagnostic is ever constructed or attached.
        mock_diag_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_diagnose_auth_attaches_before_token_submit_and_detaches_after(
        self, config
    ):
        """The auth diagnostic wraps exactly the token-submit/verify span.

        Attach happens immediately before ``_submit_token``; detach (and the
        summary log) happens after ``_verify_authentication`` completes or
        fails. The OTP value never reaches the diagnostic.
        """
        order: list[str] = []
        mailbox = MailboxClient(config)
        login = CMPLogin(config, mailbox, diagnose_auth=True)
        page = AsyncMock()

        fake_diag = AsyncMock()
        fake_diag.attach = AsyncMock(side_effect=lambda _p: order.append("attach"))
        fake_diag.detach = AsyncMock(side_effect=lambda: order.append("detach"))
        # summary() is called synchronously in production (not awaited), so
        # the test double must be a plain Mock, not an AsyncMock.
        fake_diag.summary = Mock(return_value="final_url=cas-login")

        with patch.object(login, "_is_authenticated", new=AsyncMock(return_value=False)), \
             patch.object(login, "_navigate_to_login", new=AsyncMock()), \
             patch.object(mailbox, "connect", new=AsyncMock()), \
             patch.object(login, "_fill_credentials", new=AsyncMock()), \
             patch.object(login, "_submit_login", new=AsyncMock()), \
             patch.object(login, "_wait_for_token_page", new=AsyncMock()), \
             patch.object(mailbox, "get_latest_otp", new=AsyncMock(return_value=_token())), \
             patch.object(
                 login, "_submit_token", new=AsyncMock(side_effect=lambda *a, **k: order.append("submit"))
             ), \
             patch.object(
                 login, "_verify_authentication", new=AsyncMock(side_effect=lambda _p: order.append("verify"))
             ), \
             patch("cmp_automation.cmp_login.AuthDiag", return_value=fake_diag) as mock_diag_cls:
            await login.login(page)

        assert order == ["attach", "submit", "verify", "detach"]
        mock_diag_cls.assert_called_once_with(
            approved_host="ep.iotcc.telkomsel.com"
        )
        fake_diag.attach.assert_awaited_once_with(page)
        fake_diag.detach.assert_awaited_once()
        # Verify summary was called synchronously (not awaited)
        fake_diag.summary.assert_called_once()

    @pytest.mark.asyncio
    async def test_diagnose_auth_detaches_when_verification_fails(self, config):
        """Detach runs even when authentication verification fails.

        The finally block guarantees the diagnostic is detached and summarized
        on the failure path too, so the live investigation captures the state
        where the SPA never mounted.
        """
        order: list[str] = []
        mailbox = MailboxClient(config)
        login = CMPLogin(config, mailbox, diagnose_auth=True)
        page = AsyncMock()

        fake_diag = AsyncMock()
        fake_diag.attach = AsyncMock(side_effect=lambda _p: order.append("attach"))
        fake_diag.detach = AsyncMock(side_effect=lambda: order.append("detach"))
        # summary() is called synchronously in production (not awaited), so
        # the test double must be a plain Mock, not an AsyncMock.
        fake_diag.summary = Mock(return_value="final_url=unavailable")

        async def _fail(_p):
            order.append("verify")
            raise AuthenticationError("Products page was not reached")

        with patch.object(login, "_is_authenticated", new=AsyncMock(return_value=False)), \
             patch.object(login, "_navigate_to_login", new=AsyncMock()), \
             patch.object(mailbox, "connect", new=AsyncMock()), \
             patch.object(login, "_fill_credentials", new=AsyncMock()), \
             patch.object(login, "_submit_login", new=AsyncMock()), \
             patch.object(login, "_wait_for_token_page", new=AsyncMock()), \
             patch.object(mailbox, "get_latest_otp", new=AsyncMock(return_value=_token())), \
             patch.object(login, "_submit_token", new=AsyncMock()), \
             patch.object(login, "_verify_authentication", new=AsyncMock(side_effect=_fail)), \
             patch("cmp_automation.cmp_login.AuthDiag", return_value=fake_diag):
            with pytest.raises(AuthenticationError, match="Products page was not reached"):
                await login.login(page)

        assert order == ["attach", "verify", "detach"]
        fake_diag.detach.assert_awaited_once()
        # Verify summary was called synchronously (not awaited)
        fake_diag.summary.assert_called_once()


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

    def test_approved_host_derived_from_products_url(self, login):
        """The approved host for the auth diagnostic comes from configuration."""
        assert login._approved_host() == "ep.iotcc.telkomsel.com"


class TestNavigateToLogin:
    """Tests for the bounded navigation retry on the CAS login page."""

    @pytest.fixture
    def login(self, config):
        """Create a CMPLogin for navigation tests."""
        return CMPLogin(config, MailboxClient(config))

    @pytest.mark.asyncio
    async def test_navigate_to_login_uses_domcontentloaded(self, login):
        """Test that the server-rendered login page waits for domcontentloaded."""
        page = AsyncMock()
        await login._navigate_to_login(page)
        page.goto.assert_awaited_once_with(
            login.config.cmp_login_url, wait_until="domcontentloaded"
        )

    @pytest.mark.asyncio
    async def test_navigate_to_login_retries_transient_network_error(self, login):
        """Test that transient network failures self-heal within the retry budget."""
        page = AsyncMock()
        state = {"n": 0}

        async def _goto(*_args, **_kwargs):
            state["n"] += 1
            if state["n"] < 3:
                raise PlaywrightError("NS_ERROR_SOCKET_CREATE_FAILED")
            return None

        page.goto = AsyncMock(side_effect=_goto)
        await login._navigate_to_login(page)
        assert page.goto.await_count == 3
        page.wait_for_timeout.assert_awaited()

    @pytest.mark.asyncio
    async def test_navigate_to_login_raises_after_all_attempts_fail(self, login):
        """Test that a persistent network failure raises BrowserError, bounded."""
        page = AsyncMock()
        page.goto = AsyncMock(side_effect=PlaywrightError("NS_ERROR_NET_TIMEOUT"))
        with pytest.raises(BrowserError, match="Timeout navigating to CMP login page"):
            await login._navigate_to_login(page)
        assert page.goto.await_count == 3
        assert page.wait_for_timeout.await_count == 2

    @pytest.mark.asyncio
    async def test_navigate_to_login_propagates_non_playwright_errors(self, login):
        """Programming errors must not be masked as transient network failures.

        Only Playwright/browser-level errors qualify for the bounded retry; a
        plain exception (e.g. a bug in the caller) propagates immediately so
        it is never swallowed or retried.
        """
        page = AsyncMock()
        page.goto = AsyncMock(side_effect=RuntimeError("programming bug"))
        with pytest.raises(RuntimeError, match="programming bug"):
            await login._navigate_to_login(page)
        # No retry and no BrowserError masking.
        assert page.goto.await_count == 1
        assert page.wait_for_timeout.await_count == 0

    @pytest.mark.asyncio
    async def test_navigate_to_login_does_not_leak_raw_error(self, login, caplog):
        """The raw Playwright error must never leak into logs or BrowserError details.

        The error string can embed the destination URL (and any query content),
        so only the safe error category (class name) is logged and reported.
        """
        page = AsyncMock()
        page.goto = AsyncMock(
            side_effect=PlaywrightError(
                "NS_ERROR_NET_TIMEOUT navigating to "
                "https://ep.iotcc.telkomsel.com/login?token=SECRET123"
            )
        )
        with caplog.at_level("WARNING", logger="cmp_automation.cmp_login"):
            with pytest.raises(
                BrowserError, match="Timeout navigating to CMP login page"
            ) as excinfo:
                await login._navigate_to_login(page)

        # The raw error text (including the URL and its embedded secret) never
        # leaks into the raised error or the logs.
        assert "SECRET123" not in str(excinfo.value)
        assert "NS_ERROR_NET_TIMEOUT navigating" not in str(excinfo.value)
        assert "SECRET123" not in caplog.text
        assert "NS_ERROR_NET_TIMEOUT navigating" not in caplog.text
        # The safe category is still reported so the failure stays diagnosable.
        assert "last error category: Error" in str(excinfo.value)
        assert "Error" in caplog.text

    @pytest.mark.asyncio
    async def test_navigate_to_login_delay_failure_normalized(self, login, caplog):
        """A failing retry delay must normalize to BrowserError, never raw.

        The retry delay itself can fail when the page is closed or the browser
        is dying. That failure must not escape as a raw Playwright error: it
        normalizes to ``BrowserError`` with the safe error category, and no
        further goto retries are attempted (a page that cannot wait cannot
        navigate either). Programming errors from ``page.goto`` still propagate
        (covered by ``test_navigate_to_login_propagates_non_playwright_errors``).
        """
        page = AsyncMock()
        page.goto = AsyncMock(side_effect=PlaywrightError("NS_ERROR_NET_TIMEOUT"))
        page.wait_for_timeout = AsyncMock(
            side_effect=PlaywrightError("delay failed token=SECRET123")
        )
        with caplog.at_level("WARNING", logger="cmp_automation.cmp_login"):
            with pytest.raises(
                BrowserError, match="Timeout navigating to CMP login page"
            ) as excinfo:
                await login._navigate_to_login(page)

        # Immediate normalization: no further goto attempts after the delay dies.
        assert page.goto.await_count == 1
        # The raw delay error never leaks into the raised error or the logs.
        assert "SECRET123" not in str(excinfo.value)
        assert "SECRET123" not in caplog.text
        # The safe category is still reported.
        assert "last error category: Error" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_reload_failure_is_normalized_not_crashed(self, login):
        """A failed portal reload must not crash with a raw Playwright error.

        The verification stays bounded: after the reload error is logged, the
        next poll window runs and the final failure is a normalized
        ``AuthenticationError`` (never an uncaught Playwright error).
        """
        page = AsyncMock()
        page.url = "https://ep.iotcc.telkomsel.com/"
        page.evaluate = AsyncMock(
            return_value="https://ep.iotcc.telkomsel.com/"
        )
        page.reload = AsyncMock(side_effect=PlaywrightError("NS_ERROR_NET_TIMEOUT"))

        with pytest.raises(AuthenticationError, match="Products page was not reached"):
            await login._verify_authentication(page)

        # Both bounded reload attempts were made and their failures logged
        # instead of crashing the workflow.
        assert page.reload.await_count == 2


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
        page.reload.assert_not_awaited()

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
        page.reload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_verify_authentication_recovers_after_reload(self, login):
        """Test that one bounded reload retry recovers a transient SPA boot failure.

        The first 30s window is stuck at the portal root; after the reload the
        SPA hash navigation completes, so verification returns instead of raising.
        """
        state = {"href": "https://ep.iotcc.telkomsel.com/"}
        page = AsyncMock()
        page.url = "https://ep.iotcc.telkomsel.com/"

        async def _evaluate(_js: str) -> str:
            return state["href"]

        async def _reload() -> None:
            state["href"] = "https://ep.iotcc.telkomsel.com/#!products"

        page.evaluate = AsyncMock(side_effect=_evaluate)
        page.reload = AsyncMock(side_effect=_reload)
        await login._verify_authentication(page)
        page.reload.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_verify_authentication_raises_when_products_never_reached(self, login):
        """Test that verification raises if the Products URL is never reached."""
        page = AsyncMock()
        page.url = "https://ep.iotcc.telkomsel.com/"

        async def _evaluate(js: str):
            if "window.location.href" in js:
                return "https://ep.iotcc.telkomsel.com/"
            # Structural diagnostic: counts only, never body text.
            return {
                "inputs": 2,
                "passwords": 1,
                "buttons": 1,
                "forms": 1,
                "grids": 0,
                "windows": 0,
                "dialogs": 0,
                "bodyChildren": ["div.v-app"],
            }

        page.evaluate = AsyncMock(side_effect=_evaluate)
        with pytest.raises(AuthenticationError, match="Products page was not reached") as excinfo:
            await login._verify_authentication(page)
        # The diagnostic in the raised error is structural (counts), not text.
        assert "inputs=2" in str(excinfo.value)
        assert "bodyChildren[1]" in str(excinfo.value)
        # The bounded retry must fire twice (3 windows, 2 reloads) and still
        # raise on a sustained slow mount.
        assert page.reload.await_count == 2

    @pytest.mark.asyncio
    async def test_auth_dom_summary_is_structural_only(self, login):
        """The auth diagnostic never reads element text or page HTML."""
        js = login._AUTH_DIAGNOSTIC_JS
        assert "textContent" not in js
        assert "innerText" not in js
        assert "innerHTML" not in js
        assert ".value" not in js
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value={"inputs": 1})
        summary = await login._get_dom_summary(page)
        assert "inputs=1" in summary
        # A text-shaped evaluate result is rejected, never rendered.
        page.evaluate = AsyncMock(return_value="leaked body text SIM 08123456789")
        assert await login._get_dom_summary(page) == "Unable to retrieve DOM summary"

    @pytest.mark.asyncio
    async def test_verify_authentication_second_reload_recovers_slow_mount(self, login):
        """A sustained slow mount needs the second bounded reload window.

        Live-observed at 12:01 on 2026-08-12: the portal stayed stuck at the
        root past the first reload's 30s window; a second reload is what lets
        verification eventually succeed instead of raising.
        """
        state = {"href": "https://ep.iotcc.telkomsel.com/"}
        reloads = {"n": 0}
        page = AsyncMock()
        page.url = "https://ep.iotcc.telkomsel.com/"

        async def _evaluate(_js: str) -> str:
            return state["href"]

        async def _reload() -> None:
            # First reload: still stuck at the root. Second reload: the SPA
            # finally completes its hash navigation.
            reloads["n"] += 1
            if reloads["n"] >= 2:
                state["href"] = "https://ep.iotcc.telkomsel.com/#!products"

        page.evaluate = AsyncMock(side_effect=_evaluate)
        page.reload = AsyncMock(side_effect=_reload)
        await login._verify_authentication(page)
        assert page.reload.await_count == 2

    @pytest.mark.asyncio
    async def test_verify_authentication_reload_failure_does_not_leak_raw_error(
        self, login, caplog
    ):
        """The raw reload error must never leak into logs or the raised error.

        A sentinel (``token=SECRET123``) embedded in the reload failure must be
        absent from both the captured logs and the final ``AuthenticationError``
        - only the safe error category is logged.
        """
        page = AsyncMock()
        page.url = "https://ep.iotcc.telkomsel.com/"
        page.evaluate = AsyncMock(return_value="https://ep.iotcc.telkomsel.com/")
        page.reload = AsyncMock(
            side_effect=PlaywrightError("reload failed token=SECRET123")
        )
        with caplog.at_level("WARNING", logger="cmp_automation.cmp_login"):
            with pytest.raises(
                AuthenticationError, match="Products page was not reached"
            ) as excinfo:
                await login._verify_authentication(page)

        # The sentinel never leaks into the logs or the raised error.
        assert "SECRET123" not in caplog.text
        assert "SECRET123" not in str(excinfo.value)
        # Bounds preserved: two bounded reload attempts, both failures logged
        # with only the safe category.
        assert page.reload.await_count == 2
        assert "Portal reload failed" in caplog.text

    @pytest.mark.asyncio
    async def test_verify_authentication_url_read_failure_is_normalized(self, login):
        """A page.url read failure during polling must not surface raw errors.

        When the page is closed, ``page.url`` raises ``PlaywrightError`` on every
        poll iteration. The poll loop must degrade to ``<unavailable>`` (failing
        the strict URL check), continue the bounded recovery, and finish with a
        normalized ``AuthenticationError`` carrying ``URL: <unavailable>``.
        """

        class _ClosedPage:
            @property
            def url(self) -> str:
                raise PlaywrightError(
                    "Target page, context or browser has been closed"
                )

        page = _ClosedPage()
        page.evaluate = AsyncMock(return_value="https://ep.iotcc.telkomsel.com/")
        page.wait_for_timeout = AsyncMock()
        page.reload = AsyncMock(side_effect=PlaywrightError("Target closed"))

        with pytest.raises(
            AuthenticationError, match="Products page was not reached"
        ) as excinfo:
            await login._verify_authentication(page)

        # Normalized: no raw error text, and the URL is reported unavailable.
        assert "Target page, context or browser has been closed" not in str(
            excinfo.value
        )
        assert "URL: <unavailable>" in str(excinfo.value)
        # Bounded recovery still ran: 3 windows, 2 reloads.
        assert page.reload.await_count == 2

    @pytest.mark.asyncio
    async def test_verify_authentication_poll_wait_failure_is_normalized(self, login):
        """A failing poll wait must never surface a raw Playwright error.

        The page dies mid-poll: ``wait_for_timeout`` raises, and the bounded
        reload path also fails. The final failure is a normalized
        ``AuthenticationError`` (with the structural diagnostic), never an
        uncaught Playwright error, and the bounds stay at 3 windows / 2
        reloads.
        """
        page = AsyncMock()
        page.url = "https://ep.iotcc.telkomsel.com/"
        page.evaluate = AsyncMock(return_value="https://ep.iotcc.telkomsel.com/")
        page.wait_for_timeout = AsyncMock(
            side_effect=PlaywrightError(
                "Target page, context or browser has been closed"
            )
        )
        page.reload = AsyncMock(side_effect=PlaywrightError("Target closed"))

        with pytest.raises(AuthenticationError, match="Products page was not reached"):
            await login._verify_authentication(page)

        # Bounded: one failing wait per window (3 windows), two reloads.
        assert page.wait_for_timeout.await_count == 3
        assert page.reload.await_count == 2
