"""Tests for DashboardCapture navigation and strict URL handling."""

from unittest.mock import AsyncMock, Mock

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from cmp_automation.config import Config
from cmp_automation.dashboard import DashboardCapture
from cmp_automation.exceptions import DashboardError

DASHBOARD_URL = "https://ep.iotcc.telkomsel.com/#!dashboard"
OTHER_URL = "https://ep.iotcc.telkomsel.com/#!products"


@pytest.fixture
def config():
    """Create a test config with default approved URLs."""
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
def capture(config):
    """Create a DashboardCapture instance."""
    return DashboardCapture(config)


def assert_no_networkidle_goto(page) -> None:
    """Assert goto was never called with the SPA-incompatible networkidle."""
    for call in page.goto.call_args_list:
        assert call.kwargs.get("wait_until") != "networkidle"


class TestDashboardUrlValidation:
    """Strict Dashboard URL validation used for skip decisions."""

    def test_exact_dashboard_url_accepted(self, capture):
        assert capture._is_dashboard_url(DASHBOARD_URL)

    def test_http_rejected(self, capture):
        assert not capture._is_dashboard_url("http://ep.iotcc.telkomsel.com/#!dashboard")

    def test_lookalike_host_rejected(self, capture):
        assert not capture._is_dashboard_url(
            "https://ep.iotcc.telkomsel.com.evil.com/#!dashboard"
        )

    def test_subdomain_host_rejected(self, capture):
        assert not capture._is_dashboard_url(
            "https://sub.ep.iotcc.telkomsel.com/#!dashboard"
        )

    def test_explicit_port_rejected(self, capture):
        assert not capture._is_dashboard_url(
            "https://ep.iotcc.telkomsel.com:8443/#!dashboard"
        )

    def test_wrong_fragment_rejected(self, capture):
        assert not capture._is_dashboard_url(OTHER_URL)

    def test_non_root_path_rejected(self, capture):
        assert not capture._is_dashboard_url(
            "https://ep.iotcc.telkomsel.com/app/#!dashboard"
        )


class TestDashboardNavigation:
    """Navigation is SPA-aware: skip redundant goto, never networkidle."""

    @pytest.mark.asyncio
    async def test_already_at_dashboard_url_skips_goto(self, capture):
        page = AsyncMock()
        page.url = DASHBOARD_URL
        page.goto = AsyncMock()
        await capture._navigate_to_dashboard(page)
        page.goto.assert_not_called()

    @pytest.mark.asyncio
    async def test_navigation_uses_domcontentloaded(self, capture):
        page = AsyncMock()
        page.url = OTHER_URL
        page.goto = AsyncMock()
        page.evaluate = AsyncMock(return_value=DASHBOARD_URL)
        await capture._navigate_to_dashboard(page)
        page.goto.assert_called_once_with(
            capture.config.cmp_dashboard_url, wait_until="domcontentloaded"
        )
        assert_no_networkidle_goto(page)

    @pytest.mark.asyncio
    async def test_malicious_lookalike_url_not_treated_as_dashboard(self, capture):
        page = AsyncMock()
        page.url = "https://ep.iotcc.telkomsel.com.evil.com/#!dashboard"
        page.goto = AsyncMock()
        page.evaluate = AsyncMock(
            return_value="https://ep.iotcc.telkomsel.com.evil.com/#!dashboard"
        )
        with pytest.raises(DashboardError, match="Dashboard URL was not reached"):
            await capture._navigate_to_dashboard(page)
        page.goto.assert_called_once()

    @pytest.mark.asyncio
    async def test_http_url_not_treated_as_dashboard(self, capture):
        page = AsyncMock()
        page.url = "http://ep.iotcc.telkomsel.com/#!dashboard"
        page.goto = AsyncMock()
        page.evaluate = AsyncMock(return_value="http://ep.iotcc.telkomsel.com/#!dashboard")
        with pytest.raises(DashboardError):
            await capture._navigate_to_dashboard(page)
        page.goto.assert_called_once()

    @pytest.mark.asyncio
    async def test_dashboard_url_not_reached_after_navigation_raises(self, capture):
        page = AsyncMock()
        page.url = OTHER_URL
        page.goto = AsyncMock()
        page.evaluate = AsyncMock(return_value=OTHER_URL)
        with pytest.raises(DashboardError, match="Dashboard URL was not reached"):
            await capture._navigate_to_dashboard(page)
        assert_no_networkidle_goto(page)

    @pytest.mark.asyncio
    async def test_goto_timeout_raises_dashboard_error(self, capture):
        page = AsyncMock()
        page.url = OTHER_URL
        page.goto = AsyncMock(side_effect=PlaywrightTimeoutError("goto timed out"))
        with pytest.raises(DashboardError, match="Timeout navigating to dashboard page"):
            await capture._navigate_to_dashboard(page)


class TestDashboardWaitForLoad:
    """Dashboard readiness: class-based primary, single 30s budget."""

    def test_primary_selector_matches_live_dom_without_width_attribute(self, capture):
        assert capture.DASHBOARD_CONTAINER_SELECTORS[0] == (
            "div.v-csslayout.v-layout.v-widget.sparks.v-csslayout-sparks.v-has-width"
        )
        # The live DOM has style="width: 100%", not a [width="100%"] attribute.
        assert "[width=" not in capture.DASHBOARD_CONTAINER_SELECTORS[0]

    @pytest.mark.asyncio
    async def test_wait_for_load_is_single_30_second_budget(self, capture):
        page = AsyncMock()
        page.wait_for_load_state = AsyncMock()
        locator = Mock()
        locator.filter = Mock(return_value=locator)
        locator.first.wait_for = AsyncMock()
        page.locator = Mock(return_value=locator)

        await capture._wait_for_dashboard_load(page)

        # The container wait uses the combined selector list (all candidates
        # in one CSS selector list).
        combined_call = None
        for call in page.locator.call_args_list:
            if ", " in call.args[0]:
                combined_call = call
                break
        assert combined_call is not None
        combined = combined_call.args[0]
        assert (
            "div.v-csslayout.v-layout.v-widget.sparks.v-csslayout-sparks.v-has-width"
            in combined
        )
        assert '[data-testid="dashboard"]' in combined
        # Exactly one 30-second visibility wait in total - not one per
        # selector - and the wait is filtered to visible elements.
        visible_waits = [
            call
            for call in locator.first.wait_for.call_args_list
            if call.kwargs.get("state") == "visible"
        ]
        assert len(visible_waits) == 1
        assert visible_waits[0].kwargs == {"state": "visible", "timeout": 30000}
        locator.filter.assert_called_once_with(visible=True)

    @pytest.mark.asyncio
    async def test_find_container_matches_live_dom_shape(self, capture):
        # Real live shape: class="v-csslayout v-layout v-widget sparks
        # v-csslayout-sparks v-has-width" with style="width: 100%".
        page = AsyncMock()
        locator = Mock()
        locator.first.count = AsyncMock(return_value=1)
        locator.first.is_visible = AsyncMock(return_value=True)
        locator.first.bounding_box = AsyncMock(return_value={"width": 100, "height": 50})
        locator.first.evaluate = AsyncMock(return_value="100%")
        page.locator = Mock(return_value=locator)

        element = await capture._find_dashboard_container(page)

        assert element is locator.first
        # The primary selector is the exact live class shape.
        assert page.locator.call_args_list[0].args[0] == (
            "div.v-csslayout.v-layout.v-widget.sparks.v-csslayout-sparks.v-has-width"
        )


class TestDomSummary:
    """Dashboard diagnostics are structural-only; body text/HTML never read."""

    @pytest.mark.asyncio
    async def test_diagnostic_js_never_reads_text_or_html(self, capture):
        """The in-page script reports counts/summaries, never element text."""
        js = capture._DASHBOARD_DIAGNOSTIC_JS
        assert "textContent" not in js
        assert "innerText" not in js
        assert "innerHTML" not in js
        assert "body.textContent" not in js
        assert ".value" not in js

    @pytest.mark.asyncio
    async def test_summary_is_structural_counts_only(self, capture):
        """Only tag/id/class/role summaries and counts are rendered."""
        page = AsyncMock()
        page.evaluate = AsyncMock(
            return_value={
                "sparks": 1,
                "csslayouts": 3,
                "grids": 0,
                "windows": 1,
                "dialogs": 0,
                "bodyChildren": ["div.v-app", "div.v-view"],
            }
        )
        summary = await capture._get_dom_summary(page)

        assert "sparks=1" in summary
        assert "csslayouts=3" in summary
        assert "bodyChildren[2]" in summary
        # Only structural entries - element text (SIM numbers, customer names)
        # never appears in the summary.
        assert "SIM" not in summary
        assert "customer" not in summary
        # Body text/HTML is never read via locators either.
        page.locator.assert_not_called()

    @pytest.mark.asyncio
    async def test_text_shaped_result_rejected(self, capture):
        """A body-text-shaped evaluate result is rejected, never rendered."""
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value="leaked SIM 08123456789")
        assert await capture._get_dom_summary(page) == "Unable to retrieve DOM summary"

    @pytest.mark.asyncio
    async def test_evaluate_failure_returns_safe_placeholder(self, capture):
        """A failing evaluate degrades to the safe placeholder, never a raw error."""
        page = AsyncMock()
        page.evaluate = AsyncMock(side_effect=RuntimeError("page crashed"))
        assert await capture._get_dom_summary(page) == "Unable to retrieve DOM summary"
