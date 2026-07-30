"""Tests for ProductsExporter navigation and strict URL handling."""

from unittest.mock import AsyncMock, Mock, call

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from cmp_automation.config import Config
from cmp_automation.exceptions import DownloadError, ProductsExportError
from cmp_automation.products import ProductsExporter

PRODUCTS_URL = "https://ep.iotcc.telkomsel.com/#!products"
OTHER_URL = "https://ep.iotcc.telkomsel.com/#!dashboard"


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
def exporter(config):
    """Create a ProductsExporter instance."""
    return ProductsExporter(config)


def assert_no_networkidle_goto(page) -> None:
    """Assert goto was never called with the SPA-incompatible networkidle."""
    for c in page.goto.call_args_list:
        assert c.kwargs.get("wait_until") != "networkidle"


class TestProductsUrlValidation:
    """Strict Products URL validation used for skip decisions."""

    def test_exact_products_url_accepted(self, exporter):
        assert exporter._is_products_url(PRODUCTS_URL)

    def test_http_rejected(self, exporter):
        assert not exporter._is_products_url("http://ep.iotcc.telkomsel.com/#!products")

    def test_lookalike_host_rejected(self, exporter):
        assert not exporter._is_products_url(
            "https://ep.iotcc.telkomsel.com.evil.com/#!products"
        )

    def test_subdomain_host_rejected(self, exporter):
        assert not exporter._is_products_url(
            "https://sub.ep.iotcc.telkomsel.com/#!products"
        )

    def test_explicit_port_rejected(self, exporter):
        assert not exporter._is_products_url(
            "https://ep.iotcc.telkomsel.com:8443/#!products"
        )

    def test_wrong_fragment_rejected(self, exporter):
        assert not exporter._is_products_url(OTHER_URL)

    def test_non_root_path_rejected(self, exporter):
        assert not exporter._is_products_url(
            "https://ep.iotcc.telkomsel.com/app/#!products"
        )


class TestProductsNavigation:
    """Navigation is SPA-aware: skip redundant goto, never networkidle."""

    @pytest.mark.asyncio
    async def test_already_at_products_url_skips_goto(self, exporter):
        page = AsyncMock()
        page.url = PRODUCTS_URL
        page.goto = AsyncMock()
        await exporter._navigate_to_products(page)
        page.goto.assert_not_called()

    @pytest.mark.asyncio
    async def test_navigation_uses_domcontentloaded(self, exporter):
        page = AsyncMock()
        page.url = OTHER_URL
        page.goto = AsyncMock()
        page.evaluate = AsyncMock(return_value=PRODUCTS_URL)
        await exporter._navigate_to_products(page)
        page.goto.assert_called_once_with(
            exporter.config.cmp_products_url, wait_until="domcontentloaded"
        )
        assert_no_networkidle_goto(page)

    @pytest.mark.asyncio
    async def test_malicious_lookalike_url_not_treated_as_products(self, exporter):
        page = AsyncMock()
        page.url = "https://ep.iotcc.telkomsel.com.evil.com/#!products"
        page.goto = AsyncMock()
        page.evaluate = AsyncMock(
            return_value="https://ep.iotcc.telkomsel.com.evil.com/#!products"
        )
        with pytest.raises(ProductsExportError, match="Products URL was not reached"):
            await exporter._navigate_to_products(page)
        page.goto.assert_called_once()

    @pytest.mark.asyncio
    async def test_http_url_not_treated_as_products(self, exporter):
        page = AsyncMock()
        page.url = "http://ep.iotcc.telkomsel.com/#!products"
        page.goto = AsyncMock()
        page.evaluate = AsyncMock(return_value="http://ep.iotcc.telkomsel.com/#!products")
        with pytest.raises(ProductsExportError):
            await exporter._navigate_to_products(page)
        page.goto.assert_called_once()

    @pytest.mark.asyncio
    async def test_products_url_not_reached_after_navigation_raises(self, exporter):
        page = AsyncMock()
        page.url = OTHER_URL
        page.goto = AsyncMock()
        page.evaluate = AsyncMock(return_value=OTHER_URL)
        with pytest.raises(ProductsExportError, match="Products URL was not reached"):
            await exporter._navigate_to_products(page)
        assert_no_networkidle_goto(page)

    @pytest.mark.asyncio
    async def test_goto_timeout_raises_products_export_error(self, exporter):
        page = AsyncMock()
        page.url = OTHER_URL
        page.goto = AsyncMock(side_effect=PlaywrightTimeoutError("goto timed out"))
        with pytest.raises(ProductsExportError, match="Timeout navigating to products page"):
            await exporter._navigate_to_products(page)


class TestWaitForTable:
    """Table readiness: one 30s budget, structural diagnostics only."""

    @pytest.mark.asyncio
    async def test_table_wait_is_single_30_second_budget(self, exporter):
        # page.locator() is synchronous in the real API and returns a Locator;
        # only wait_for/is_visible are async, so locator itself is a plain Mock.
        page = AsyncMock()
        page.url = PRODUCTS_URL
        locator = Mock()
        locator.filter = Mock(return_value=locator)
        locator.first.wait_for = AsyncMock()
        locator.first.is_visible = AsyncMock(return_value=False)
        page.locator = Mock(return_value=locator)

        await exporter._wait_for_table(page)

        # First locator call is the combined CSS selector list (all candidates).
        combined = page.locator.call_args_list[0].args[0]
        assert ", " in combined
        assert "table.v-table" in combined
        assert "vaadin-grid" in combined
        assert ".product-table" in combined
        # Filtered to visible elements so a hidden earlier candidate cannot shadow.
        locator.filter.assert_called_once_with(visible=True)
        # Exactly one 30-second visibility wait in total - not one per selector.
        locator.first.wait_for.assert_called_once_with(state="visible", timeout=30000)

    @pytest.mark.asyncio
    async def test_identifies_which_selector_matched(self, exporter):
        page = AsyncMock()
        page.url = PRODUCTS_URL
        locator = Mock()
        locator.first.wait_for = AsyncMock()
        # vaadin-grid is the 5th candidate and the first visible one.
        locator.first.is_visible = AsyncMock(side_effect=[False] * 4 + [True])
        page.locator = Mock(return_value=locator)

        matched = await exporter._find_visible_table_selector(page)

        assert matched == "vaadin-grid"

    @pytest.mark.asyncio
    async def test_table_timeout_raises_with_structural_diagnostics(self, exporter):
        page = AsyncMock()
        page.url = PRODUCTS_URL
        locator = Mock()
        locator.filter = Mock(return_value=locator)
        locator.first.wait_for = AsyncMock(side_effect=PlaywrightTimeoutError("not visible"))
        page.locator = Mock(return_value=locator)
        page.evaluate = AsyncMock(
            return_value={
                "candidates": ["div.v-csslayout#main", "table.v-table"],
                "counts": {"tables": 1, "grids": 0, "vCsslayouts": 3},
            }
        )

        with pytest.raises(ProductsExportError) as excinfo:
            await exporter._wait_for_table(page)

        assert "Products table did not load" in str(excinfo.value)
        assert "v-csslayout#main" in str(excinfo.value)
        assert "tables=1" in str(excinfo.value)


class TestDomSummarySafety:
    """Diagnostics must be structural-only and never leak body text."""

    @pytest.mark.asyncio
    async def test_contains_structural_attributes_only(self, exporter):
        page = AsyncMock()
        page.evaluate = AsyncMock(
            return_value={
                "candidates": ["div.v-csslayout#main", "table.v-table.products"],
                "counts": {"tables": 1, "grids": 0, "vCsslayouts": 3},
            }
        )
        summary = await exporter._get_dom_summary(page)
        assert "v-csslayout#main" in summary
        assert "table.v-table.products" in summary
        assert "tables=1" in summary

    @pytest.mark.asyncio
    async def test_stray_text_keys_are_never_included(self, exporter):
        page = AsyncMock()
        page.evaluate = AsyncMock(
            return_value={
                "candidates": ["table.v-table"],
                "counts": {"tables": 1},
                # Text-like/stray data must never reach the summary.
                "body_text": "SIM 08123456789 OTP 654321",
                "texts": ["1234567890", "John Doe"],
            }
        )
        summary = await exporter._get_dom_summary(page)
        assert "08123456789" not in summary
        assert "654321" not in summary
        assert "SIM" not in summary
        assert "OTP" not in summary
        assert "1234567890" not in summary
        assert "John Doe" not in summary
        assert "candidates[1]" in summary

    @pytest.mark.asyncio
    async def test_non_dict_evaluate_result_is_safe(self, exporter):
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value="textContent leak attempt")
        assert await exporter._get_dom_summary(page) == "Unable to retrieve DOM summary"

    @pytest.mark.asyncio
    async def test_diagnostic_js_never_reads_element_text(self, exporter):
        js = exporter._dom_diagnostic_js()
        assert "textContent" not in js
        assert "innerText" not in js
        assert "innerHTML" not in js
        assert ".value" not in js
        assert "document.body" not in js

    @pytest.mark.asyncio
    async def test_diagnostic_js_candidates_stay_in_sync(self, exporter):
        js = exporter._dom_diagnostic_js()
        assert "__TABLE_CANDIDATES__" not in js
        for selector in exporter.PRODUCTS_TABLE_SELECTORS:
            assert selector in js


class TestMenuDomDiagnosticSafety:
    """Menu diagnostics are structural-only and never leak body text."""

    def test_diagnostic_js_never_reads_element_text(self, exporter):
        js = exporter._MENU_DIAGNOSTIC_JS
        assert "textContent" not in js
        assert "innerText" not in js
        assert "innerHTML" not in js
        # The in-page script uses real CSS only - no Playwright pseudo-classes.
        assert ":has-text" not in js

    def test_confirm_diagnostic_js_never_reads_element_text(self, exporter):
        js = exporter._CONFIRM_DIAGNOSTIC_JS
        assert "textContent" not in js
        assert "innerText" not in js
        assert "innerHTML" not in js
        assert ":has-text" not in js
        assert "#confirmdialog-ok-button" in js

    @pytest.mark.asyncio
    async def test_confirm_diagnostic_formats_counts(self, exporter):
        page = AsyncMock()
        page.evaluate = AsyncMock(
            return_value={
                "confirmButtonTotal": 1,
                "confirmButtonVisible": 1,
                "windows": 0,
                "windowsVisible": 0,
                "dialogs": 0,
                "dialogsVisible": 0,
                "confirmDialogs": 1,
                "confirmDialogsVisible": 1,
            }
        )
        summary = await exporter._confirm_dom_diagnostic(page)
        assert "confirmButtonTotal=1" in summary
        assert "confirmButtonVisible=1" in summary

    @pytest.mark.asyncio
    async def test_diagnostic_formats_counts(self, exporter):
        page = AsyncMock()
        page.evaluate = AsyncMock(
            return_value={
                "captionTotal": 2,
                "captionVisible": 1,
                "menus": 1,
                "menusVisible": 1,
                "windows": 0,
                "windowsVisible": 0,
                "dialogs": 0,
                "dialogsVisible": 0,
            }
        )
        summary = await exporter._menu_dom_diagnostic(page)
        assert "captionTotal=2" in summary
        assert "captionVisible=1" in summary

    @pytest.mark.asyncio
    async def test_non_dict_result_is_safe(self, exporter):
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value="leak attempt")
        assert (
            await exporter._menu_dom_diagnostic(page)
            == "Unable to retrieve DOM diagnostic"
        )
        assert (
            await exporter._confirm_dom_diagnostic(page)
            == "Unable to retrieve DOM diagnostic"
        )


class TestExportFlowSelectors:
    """Exact live DOM selectors are primary in the export flow."""

    def test_billing_status_exact_selector_is_primary(self, exporter):
        assert (
            exporter.BILLING_STATUS_HEADER_SELECTORS[0]
            == '.v-grid-column-header-content:has-text("Billing Status")'
        )

    def test_export_menu_exact_selector_is_primary(self, exporter):
        assert (
            exporter.EXPORT_MENU_SELECTORS[0]
            == "span.v-menubar-menuitem:has(span.v-icon.IcoMoon-Ultimate)"
        )

    def test_xlsx_exact_selector_is_primary(self, exporter):
        assert exporter.EXPORT_XLSX_SELECTORS[0] == (
            'span.v-menubar-menuitem-caption:has(span.v-icon.IcoMoon-Lindua):has-text("To xlsx")'
        )

    def test_confirm_prefers_exact_id_then_role_fallback(self, exporter):
        assert exporter.CONFIRM_DIALOG_SELECTORS[0] == "#confirmdialog-ok-button"
        assert (
            '[role="button"]:has-text("Confirm")' in exporter.CONFIRM_DIALOG_SELECTORS
        )

    def test_download_prefers_role_button(self, exporter):
        assert (
            exporter.DOWNLOAD_BUTTON_SELECTORS[0]
            == '[role="button"]:has-text("Download")'
        )


class TestBillingStatusSort:
    """Sorting tries the exact grid column header first; state lives on <th>."""

    @pytest.mark.asyncio
    async def test_sort_tries_exact_grid_column_header(self, exporter):
        page = AsyncMock()
        table_loc = Mock()
        table_loc.first.count = AsyncMock(return_value=1)
        header_loc = Mock()
        header_loc.first.count = AsyncMock(return_value=1)
        header_loc.first.click = AsyncMock()
        th_loc = Mock()
        # The live grid never sets aria-sort; after the click the parent <th>
        # gains the sort-asc class. Reads happen in order: before class,
        # before aria, after class, after aria.
        state = [
            "v-grid-cell sortable",
            None,
            "v-grid-cell sortable sort-asc",
            None,
        ]
        th_loc.get_attribute = AsyncMock(
            side_effect=lambda _name: state.pop(0)
        )
        header_loc.first.locator = Mock(return_value=th_loc)
        table_loc.first.locator = Mock(return_value=header_loc)
        page.locator = Mock(return_value=table_loc)

        await exporter._sort_by_billing_status(page)

        # The exact live DOM selector is tried first, scoped to the grid.
        assert table_loc.first.locator.call_args_list[0].args[0] == (
            '.v-grid-column-header-content:has-text("Billing Status")'
        )
        # Sort state is read from the parent <th>, not the content div.
        assert header_loc.first.locator.call_args_list[0].args[0] == "xpath=.."
        header_loc.first.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sort_state_detected_from_th_class_without_aria_sort(self, exporter):
        """Sort completion is recognized even though aria-sort stays None."""
        page = AsyncMock()
        table_loc = Mock()
        table_loc.first.count = AsyncMock(return_value=1)
        header_loc = Mock()
        header_loc.first.count = AsyncMock(return_value=1)
        header_loc.first.click = AsyncMock()
        th_loc = Mock()
        # Before: unsorted. After: sorted via th class only (aria-sort None).
        state = [
            "v-grid-cell sortable",
            None,
            "v-grid-cell sortable sort-desc",
            None,
        ]
        th_loc.get_attribute = AsyncMock(
            side_effect=lambda _name: state.pop(0)
        )
        header_loc.first.locator = Mock(return_value=th_loc)
        table_loc.first.locator = Mock(return_value=header_loc)
        page.locator = Mock(return_value=table_loc)

        await exporter._sort_by_billing_status(page)

        header_loc.first.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sort_retries_when_first_click_is_lost(self, exporter):
        """A click lost during the grid's initial load is retried."""
        page = AsyncMock()
        table_loc = Mock()
        table_loc.first.count = AsyncMock(return_value=1)
        header_loc = Mock()
        header_loc.first.count = AsyncMock(return_value=1)
        header_loc.first.click = AsyncMock()
        th_loc = Mock()
        calls = {"n": 0}

        async def fake_get_attribute(name):
            calls["n"] += 1
            if name == "class":
                # Before-click check + 20 polls of attempt 1 stay unsorted;
                # the first poll of attempt 2 sees the sort state. The class
                # read of that poll is call #43.
                if calls["n"] >= 43:
                    return "v-grid-cell sortable sort-asc"
                return "v-grid-cell sortable"
            return None

        th_loc.get_attribute = AsyncMock(side_effect=fake_get_attribute)
        header_loc.first.locator = Mock(return_value=th_loc)
        table_loc.first.locator = Mock(return_value=header_loc)
        page.locator = Mock(return_value=table_loc)
        page.wait_for_timeout = AsyncMock()

        await exporter._sort_by_billing_status(page)

        assert header_loc.first.click.await_count == 2

    @pytest.mark.asyncio
    async def test_sort_timeout_raises_after_retries(self, exporter):
        page = AsyncMock()
        table_loc = Mock()
        table_loc.first.count = AsyncMock(return_value=1)
        header_loc = Mock()
        header_loc.first.count = AsyncMock(return_value=1)
        header_loc.first.click = AsyncMock()
        th_loc = Mock()
        # Sort state never appears on the th: unsorted class forever.
        th_loc.get_attribute = AsyncMock(
            side_effect=lambda name: (
                "v-grid-cell sortable" if name == "class" else None
            )
        )
        header_loc.first.locator = Mock(return_value=th_loc)
        table_loc.first.locator = Mock(return_value=header_loc)
        page.locator = Mock(return_value=table_loc)
        page.wait_for_timeout = AsyncMock()

        with pytest.raises(ProductsExportError, match="Billing Status sort did not complete"):
            await exporter._sort_by_billing_status(page)
        # The click is retried (bounded) instead of failing on one lost click.
        assert header_loc.first.click.await_count == 3


class TestExportMenuInteraction:
    """Export menu opens via the Vaadin menubar item, no window/dialog."""

    @pytest.mark.asyncio
    async def test_opens_menu_via_exact_menubar_selector(self, exporter):
        page = AsyncMock()
        locator = Mock()
        filtered = Mock()
        filtered.first.wait_for = AsyncMock()
        filtered.first.click = AsyncMock()
        locator.filter = Mock(return_value=filtered)

        # The popup container check uses a separate locator path: the
        # production code calls ``page.locator(...).filter(visible=True).count()``.
        popup_locator = Mock()
        popup_filtered = Mock()
        popup_filtered.count = AsyncMock(return_value=1)
        popup_locator.filter = Mock(return_value=popup_filtered)

        def fake_locator(selector):
            if ".v-menubar-popup" in selector:
                return popup_locator
            return locator

        page.locator = Mock(side_effect=fake_locator)

        await exporter._open_export_menu(page)

        # Primary: the exact Vaadin menubar item with the IcoMoon-Ultimate icon.
        assert page.locator.call_args_list[0].args[0] == (
            "span.v-menubar-menuitem:has(span.v-icon.IcoMoon-Ultimate)"
        )
        # After the click: wait for the exact visible To xlsx caption.
        assert page.locator.call_args_list[1].args[0] == (
            'span.v-menubar-menuitem-caption:has(span.v-icon.IcoMoon-Lindua):has-text("To xlsx")'
        )
        # After the submenu caption wait: verify the popup container exists.
        assert page.locator.call_args_list[2].args[0] == (
            '.v-menubar-popup, .v-menubar-submenu, [role="menu"]'
        )
        # The popup container check is filtered to visible before counting.
        popup_locator.filter.assert_called_once_with(visible=True)
        # Menu item and submenu are both filtered to visible before .first.
        assert locator.filter.call_args_list == [call(visible=True), call(visible=True)]
        # Menu item: 10s. Submenu (slow-portal render, same budget as confirm): 30s.
        assert filtered.first.wait_for.await_count == 2
        assert filtered.first.wait_for.await_args_list[0] == call(
            state="visible", timeout=10000
        )
        assert filtered.first.wait_for.await_args_list[1] == call(
            state="visible", timeout=30000
        )
        filtered.first.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_submenu_does_not_require_window_or_dialog(self, exporter):
        page = AsyncMock()
        locator = Mock()
        filtered = Mock()
        filtered.first.wait_for = AsyncMock()
        filtered.first.click = AsyncMock()
        locator.filter = Mock(return_value=filtered)

        # The popup container check uses a separate locator path.
        popup_locator = Mock()
        popup_filtered = Mock()
        popup_filtered.count = AsyncMock(return_value=1)
        popup_locator.filter = Mock(return_value=popup_filtered)

        def fake_locator(selector):
            if ".v-menubar-popup" in selector:
                return popup_locator
            return locator

        page.locator = Mock(side_effect=fake_locator)

        await exporter._open_export_menu(page)

        for c in page.locator.call_args_list:
            assert ".v-window" not in c.args[0]
            assert "dialog" not in c.args[0]

    @pytest.mark.asyncio
    async def test_menu_lookup_filters_visible_before_first(self, exporter):
        """Hidden Vaadin duplicates: .first is only reached after visible=True."""
        page = AsyncMock()
        locator = Mock()
        filtered = Mock()
        filtered.first.wait_for = AsyncMock()
        filtered.first.click = AsyncMock()
        locator.filter = Mock(return_value=filtered)

        # The popup container check uses a separate locator path.
        popup_locator = Mock()
        popup_filtered = Mock()
        popup_filtered.count = AsyncMock(return_value=1)
        popup_locator.filter = Mock(return_value=popup_filtered)

        def fake_locator(selector):
            if ".v-menubar-popup" in selector:
                return popup_locator
            return locator

        page.locator = Mock(side_effect=fake_locator)

        await exporter._open_export_menu(page)

        # Both the menu item and the submenu lookups filter visible=True.
        assert locator.filter.call_count == 2
        for c in locator.filter.call_args_list:
            assert c == call(visible=True)
        # The popup container check is also filtered to visible.
        popup_locator.filter.assert_called_once_with(visible=True)
        # All interactions go through the filtered locator, never raw .first.
        assert locator.first.wait_for.call_count == 0
        assert locator.first.click.call_count == 0

    @pytest.mark.asyncio
    async def test_menu_not_found_raises(self, exporter):
        page = AsyncMock()
        locator = Mock()
        filtered = Mock()
        filtered.first.wait_for = AsyncMock(
            side_effect=PlaywrightTimeoutError("menu never visible")
        )
        locator.filter = Mock(return_value=filtered)
        page.locator = Mock(return_value=locator)

        with pytest.raises(ProductsExportError, match="Could not find export menu button"):
            await exporter._open_export_menu(page)

    @pytest.mark.asyncio
    async def test_submenu_timeout_preserves_real_error(self, exporter):
        # The exact menubar item is clickable, but the To xlsx submenu never
        # appears: the real diagnostic must propagate instead of being
        # swallowed and replaced by the generic "Could not find export menu
        # button" fallback message.
        page = AsyncMock()
        locator = Mock()
        filtered = Mock()
        filtered.first.wait_for = AsyncMock(
            side_effect=[None, PlaywrightTimeoutError("submenu never appeared")]
        )
        filtered.first.click = AsyncMock()
        locator.filter = Mock(return_value=filtered)
        page.locator = Mock(return_value=locator)

        with pytest.raises(
            ProductsExportError,
            match="Export submenu \\(To xlsx\\) did not become visible",
        ):
            await exporter._open_export_menu(page)
        filtered.first.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_menu_container_false_positive_detected(self, exporter):
        """Caption visible but no popup container: static template, not a real submenu.

        Live-observed: a visible caption element belonging to a hidden Vaadin
        template satisfies the ``wait_for(visible=True)`` check, but the
        interactive submenu popup (``.v-menubar-popup``) never opened. Clicking
        the caption in that state is a no-op. The visible popup-container
        check catches this false positive before the XLSX selection proceeds.
        """
        page = AsyncMock()
        locator = Mock()
        filtered = Mock()
        filtered.first.wait_for = AsyncMock()  # caption wait succeeds
        filtered.first.click = AsyncMock()
        locator.filter = Mock(return_value=filtered)

        # The popup container check: filter(visible=True) finds zero visible.
        popup_locator = Mock()
        popup_filtered = Mock()
        popup_filtered.count = AsyncMock(return_value=0)
        popup_locator.filter = Mock(return_value=popup_filtered)

        def fake_locator(selector):
            if ".v-menubar-popup" in selector:
                return popup_locator
            return locator

        page.locator = Mock(side_effect=fake_locator)
        # The diagnostic runs inside the error path and must not crash.
        page.evaluate = AsyncMock(
            return_value={
                "captionTotal": 3,
                "captionVisible": 3,
                "popupContainer": 0,
                "popupContainerVisible": 0,
                "menus": 0,
                "menusVisible": 0,
                "windows": 0,
                "windowsVisible": 0,
                "dialogs": 0,
                "dialogsVisible": 0,
            }
        )

        with pytest.raises(
            ProductsExportError,
            match="no submenu container is open",
        ):
            await exporter._open_export_menu(page)

        # The menu item was clicked (the false-positive check catches the
        # problem after the click but before the caller can proceed).
        filtered.first.click.assert_awaited_once()
        # The popup container was checked via visible filter exactly once.
        popup_locator.filter.assert_called_once_with(visible=True)
        assert popup_filtered.count.await_count == 1
    @pytest.mark.asyncio
    async def test_menu_container_hidden_popup_rejected(self, exporter):
        """Popup exists in DOM but is hidden/off-screen: the visible check rejects it.

        A hidden ``.v-menubar-popup`` element (e.g. a Vaadin template that
        has not been shown) satisfies ``locator.count()`` but NOT
        ``locator.filter(visible=True).count()``. The guard must reject
        this case to prevent clicking a non-interactive caption.
        """
        page = AsyncMock()
        locator = Mock()
        filtered = Mock()
        filtered.first.wait_for = AsyncMock()  # caption wait succeeds
        filtered.first.click = AsyncMock()
        locator.filter = Mock(return_value=filtered)

        # The popup locator exists in DOM but is not visible.
        popup_locator = Mock()
        popup_filtered = Mock()
        popup_filtered.count = AsyncMock(return_value=0)  # 0 visible
        popup_locator.filter = Mock(return_value=popup_filtered)

        def fake_locator(selector):
            if ".v-menubar-popup" in selector:
                return popup_locator
            return locator

        page.locator = Mock(side_effect=fake_locator)
        page.evaluate = AsyncMock(
            return_value={
                "captionTotal": 3,
                "captionVisible": 3,
                "popupContainer": 1,
                "popupContainerVisible": 0,
                "menus": 1,
                "menusVisible": 0,
                "windows": 0,
                "windowsVisible": 0,
                "dialogs": 0,
                "dialogsVisible": 0,
            }
        )

        with pytest.raises(
            ProductsExportError,
            match="no submenu container is open",
        ):
            await exporter._open_export_menu(page)

        # The visible filter was applied — hidden popup did NOT pass.
        popup_locator.filter.assert_called_once_with(visible=True)
        assert popup_filtered.count.await_count == 1
        # The click happened but the guard caught the problem.
        filtered.first.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_duplicate_click_on_false_positive(self, exporter):
        """When the false-positive is detected, only one click was dispatched.

        The click on the menu item happens before the popup-container check,
        but the error propagates immediately — no retry or re-click occurs.
        """
        page = AsyncMock()
        locator = Mock()
        filtered = Mock()
        filtered.first.wait_for = AsyncMock()
        filtered.first.click = AsyncMock()
        locator.filter = Mock(return_value=filtered)

        popup_locator = Mock()
        popup_filtered = Mock()
        popup_filtered.count = AsyncMock(return_value=0)
        popup_locator.filter = Mock(return_value=popup_filtered)

        def fake_locator(selector):
            if ".v-menubar-popup" in selector:
                return popup_locator
            return locator

        page.locator = Mock(side_effect=fake_locator)
        page.evaluate = AsyncMock(return_value={})

        with pytest.raises(ProductsExportError):
            await exporter._open_export_menu(page)

        # Exactly one click — no duplicate, no retry.
        assert filtered.first.click.await_count == 1


class TestXlsxSelection:
    """To xlsx is found page-wide, visible-filtered, and waited for."""

    @pytest.mark.asyncio
    async def test_selects_xlsx_via_exact_caption_selector(self, exporter):
        page = AsyncMock()
        locator = Mock()
        filtered = Mock()
        filtered.first.wait_for = AsyncMock()
        filtered.first.click = AsyncMock()
        locator.filter = Mock(return_value=filtered)
        page.locator = Mock(return_value=locator)

        await exporter._select_xlsx_export(page)

        # Page-wide search (not scoped to .v-window/[role=dialog] popup).
        assert page.locator.call_args_list[0].args[0] == (
            'span.v-menubar-menuitem-caption:has(span.v-icon.IcoMoon-Lindua):has-text("To xlsx")'
        )
        # The exact caption is filtered to visible before .first.
        locator.filter.assert_called_once_with(visible=True)
        filtered.first.wait_for.assert_awaited_once_with(state="visible", timeout=10000)

    @pytest.mark.asyncio
    async def test_xlsx_lookup_filters_visible_before_first(self, exporter):
        """The live submenu item is only ever touched through the visible filter."""
        page = AsyncMock()
        locator = Mock()
        filtered = Mock()
        filtered.first.wait_for = AsyncMock()
        filtered.first.click = AsyncMock()
        locator.filter = Mock(return_value=filtered)
        page.locator = Mock(return_value=locator)

        await exporter._select_xlsx_export(page)

        locator.filter.assert_called_once_with(visible=True)
        assert locator.first.wait_for.call_count == 0
        assert locator.first.click.call_count == 0

    @pytest.mark.asyncio
    async def test_force_click_used_first_for_submenu_item(self, exporter):
        """The caption never satisfies actionability stability, so force first."""
        page = AsyncMock()
        locator = Mock()
        filtered = Mock()
        filtered.first.wait_for = AsyncMock()
        filtered.first.click = AsyncMock()
        locator.filter = Mock(return_value=filtered)
        page.locator = Mock(return_value=locator)

        await exporter._select_xlsx_export(page)

        # The very first click attempt is the force click: normal clicks time
        # out on the re-rendering submenu and let the menu close meanwhile.
        assert filtered.first.click.call_args_list[0] == call(force=True)

    @pytest.mark.asyncio
    async def test_synthetic_click_last_resort(self, exporter):
        """If the force click cannot land, a synthetic click is dispatched."""
        page = AsyncMock()
        locator = Mock()
        filtered = Mock()
        filtered.first.wait_for = AsyncMock()
        filtered.first.click = AsyncMock(
            side_effect=RuntimeError("element is not attached")
        )
        filtered.first.evaluate = AsyncMock()
        locator.filter = Mock(return_value=filtered)
        page.locator = Mock(return_value=locator)

        await exporter._select_xlsx_export(page)

        assert filtered.first.click.call_args_list[0] == call(force=True)
        filtered.first.evaluate.assert_awaited_once_with("el => el.click()")

    @pytest.mark.asyncio
    async def test_primary_xlsx_selector_retried_after_rerender(self, exporter):
        """Vaadin re-render race: the exact selector fails once, then succeeds.

        Live-observed: the same caption is visible one moment and detached the
        next while the submenu re-renders. The primary selection must be
        retried in bounded attempts with a fresh locator, not given up after
        a single failure.
        """
        page = AsyncMock()
        locator = Mock()
        filtered = Mock()
        # Attempt 1 times out mid re-render; attempt 2 sees the caption.
        filtered.first.wait_for = AsyncMock(
            side_effect=[
                PlaywrightTimeoutError("submenu re-rendering"),
                None,
            ]
        )
        filtered.first.click = AsyncMock()
        locator.filter = Mock(return_value=filtered)
        page.locator = Mock(return_value=locator)
        # The structural diagnostic runs inside the retry path and must not
        # crash the loop; it reports counts only.
        page.evaluate = AsyncMock(return_value={})

        await exporter._select_xlsx_export(page)

        assert filtered.first.wait_for.await_count == 2
        assert filtered.first.click.await_count == 1
        # Each attempt builds a fresh locator (page.locator called per attempt).
        assert page.locator.call_count == 2

    @pytest.mark.asyncio
    async def test_primary_xlsx_selector_gives_up_after_attempts(self, exporter):
        """Persistent re-render failure falls through to the fallback loop."""
        page = AsyncMock()
        locator = Mock()
        filtered = Mock()
        filtered.first.wait_for = AsyncMock(
            side_effect=PlaywrightTimeoutError("never visible")
        )
        filtered.first.click = AsyncMock()
        locator.filter = Mock(return_value=filtered)
        page.locator = Mock(return_value=locator)
        # No visible popup fallback candidates either. The fallback chain is
        # ``_popup(page).locator(sel).filter(visible=True).first`` - the
        # visible filter precedes .first (same Vaadin discipline as primary).
        popup = Mock()
        popup_filtered = Mock()
        popup_filtered.first.count = AsyncMock(return_value=0)
        popup.locator.return_value.filter = Mock(return_value=popup_filtered)
        popup_locator = Mock()
        popup_locator.last = popup
        page.locator.return_value = locator
        # Distinguish the primary lookup from the popup lookup: the popup
        # selector is what _popup() builds.
        def popup_locator_side_effect(selector):
            if selector.startswith(".v-window:visible"):
                return popup_locator
            return locator

        page.locator = Mock(side_effect=popup_locator_side_effect)

        with pytest.raises(ProductsExportError, match="Could not find XLSX export option"):
            await exporter._select_xlsx_export(page)

        assert filtered.first.wait_for.await_count == 3  # XLSX_SELECT_ATTEMPTS
        assert filtered.first.click.await_count == 0
        # Every fallback candidate was filtered to visible before .first.
        assert popup.locator.return_value.filter.call_count == len(
            exporter.EXPORT_XLSX_SELECTORS
        ) - 1
        for c in popup.locator.return_value.filter.call_args_list:
            assert c == call(visible=True)


class TestConfirmExport:
    """Confirm works through #confirmdialog-ok-button (div[role=button])."""

    @pytest.mark.asyncio
    async def test_confirms_via_exact_id_button(self, exporter):
        page = AsyncMock()
        locator = Mock()
        locator.first.count = AsyncMock(return_value=1)
        # The dialog renders after a server round-trip: the OK button is
        # waited for before clicking.
        locator.first.wait_for = AsyncMock()
        locator.first.click = AsyncMock()
        page.locator = Mock(return_value=locator)

        await exporter._confirm_export(page)

        # The id-based primary is searched page-wide, not scoped to popup.
        assert page.locator.call_args_list[0].args[0] == "#confirmdialog-ok-button"
        # 30s budget: the dialog renders after a server round-trip that can
        # take tens of seconds on a slow portal.
        locator.first.wait_for.assert_awaited_once_with(state="visible", timeout=30000)
        locator.first.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_waits_for_dialog_before_clicking(self, exporter):
        """count() alone cannot detect the dialog; a visibility wait comes first."""
        page = AsyncMock()
        locator = Mock()
        locator.first.count = AsyncMock(return_value=1)
        locator.first.wait_for = AsyncMock()
        locator.first.click = AsyncMock()
        page.locator = Mock(return_value=locator)

        await exporter._confirm_export(page)

        # The first interaction with the primary selector is a wait_for,
        # never a bare count()-then-click on a not-yet-rendered dialog.
        assert locator.first.wait_for.call_count >= 1
        locator.first.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dialog_timeout_falls_back_to_popup_selectors(self, exporter):
        """If the exact id never appears, generic popup selectors are tried."""
        page = AsyncMock()
        locator = Mock()
        locator.first.count = AsyncMock(return_value=0)
        locator.first.wait_for = AsyncMock(
            side_effect=PlaywrightTimeoutError("dialog never appeared")
        )
        locator.first.click = AsyncMock()

        # Fallback candidates are searched in the popup with the visible
        # filter before .first (same Vaadin discipline as the primary wait).
        popup = Mock()
        filtered = Mock()
        filtered.first.count = AsyncMock(return_value=0)
        filtered.first.click = AsyncMock()
        locator.filter = Mock(return_value=filtered)
        popup.last.locator = Mock(return_value=locator)

        def fake_locator(selector):
            if selector == "#confirmdialog-ok-button":
                return locator
            return popup

        page.locator = Mock(side_effect=fake_locator)

        with pytest.raises(ProductsExportError, match="Confirm control was not found"):
            await exporter._confirm_export(page)
        locator.first.click.assert_not_called()
        # Fallback candidates were filtered to visible before .first.
        locator.filter.assert_called_with(visible=True)

    @pytest.mark.asyncio
    async def test_confirm_dialog_retried_when_slow_to_render(self, exporter):
        """The dialog is a pure server response; a bounded re-wait is safe.

        Live-observed: on a degraded portal the confirm dialog can take well
        over 30s to render. Re-waiting (without any re-click) is harmless and
        self-heals the slow-response case.
        """
        page = AsyncMock()
        locator = Mock()
        locator.first.count = AsyncMock(return_value=1)
        # Attempt 1 times out; attempt 2 sees the dialog.
        locator.first.wait_for = AsyncMock(
            side_effect=[PlaywrightTimeoutError("dialog slow"), None]
        )
        locator.first.click = AsyncMock()
        page.locator = Mock(return_value=locator)
        page.evaluate = AsyncMock(return_value={})

        await exporter._confirm_export(page)

        assert locator.first.wait_for.await_count == 2
        for c in locator.first.wait_for.await_args_list:
            assert c == call(state="visible", timeout=30000)
        locator.first.click.assert_awaited_once()


class TestDownloadFlow:
    """Download: single 15s control wait + one click + one bounded event window."""

    @pytest.mark.asyncio
    async def test_download_via_role_button_selector(
        self, exporter, tmp_path, monkeypatch
    ):
        page = AsyncMock()
        locator = Mock()
        filtered = Mock()
        filtered.first.wait_for = AsyncMock()
        filtered.first.click = AsyncMock()
        locator.filter = Mock(return_value=filtered)
        page.locator = Mock(return_value=locator)

        target = tmp_path / "sim_export_20260718_120000.xlsx"

        def fake_save_as(path):
            path.write_bytes(b"PK\x03\x04 fake xlsx")

        download = Mock()
        download.suggested_filename = "export.xlsx"
        download.save_as = AsyncMock(side_effect=fake_save_as)

        # ``download_info.value`` is awaited directly (not called), so it must
        # be a real coroutine - a bare AsyncMock is not awaitable without a
        # call on Python 3.14.
        async def fake_download_value():
            return download

        download_info = Mock()
        download_info.value = fake_download_value()
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=download_info)
        cm.__aexit__ = AsyncMock(return_value=False)
        page.expect_download = Mock(return_value=cm)

        monkeypatch.setattr(
            "cmp_automation.products.generate_export_filename", lambda *a, **k: target
        )
        monkeypatch.setattr(
            "cmp_automation.products.zipfile.is_zipfile", lambda *a, **k: True
        )
        monkeypatch.setattr(
            "cmp_automation.products.load_workbook",
            lambda *a, **k: Mock(close=Mock()),
        )

        result = await exporter._wait_for_download(page)

        assert result == target
        # The exact role-based Download selector is searched page-wide.
        assert page.locator.call_args_list[0].args[0] == (
            '[role="button"]:has-text("Download")'
        )
        # Filtered to visible elements so a hidden template button cannot
        # shadow the real live Download control.
        locator.filter.assert_called_once_with(visible=True)
        # One visibility wait, 15000 ms max - no sequential 30/60s loops.
        filtered.first.wait_for.assert_awaited_once_with(
            state="visible", timeout=15000
        )
        filtered.first.click.assert_awaited_once()
        # Exactly one download window with the bounded single-window timeout.
        page.expect_download.assert_called_once_with(
            timeout=exporter.DOWNLOAD_TIMEOUT_MS
        )

    @pytest.mark.asyncio
    async def test_download_timeout_raises_download_error(self, exporter):
        """If Download does not appear within 15s, DownloadError is raised."""
        page = AsyncMock()
        locator = Mock()
        filtered = Mock()
        filtered.first.wait_for = AsyncMock(
            side_effect=PlaywrightTimeoutError("Download never appeared")
        )
        locator.filter = Mock(return_value=filtered)
        page.locator = Mock(return_value=locator)

        with pytest.raises(
            DownloadError,
            match="Download button did not appear within 15 seconds",
        ):
            await exporter._wait_for_download(page)

        filtered.first.wait_for.assert_awaited_once_with(
            state="visible", timeout=15000
        )

    def test_no_sequential_waits_remain(self, exporter):
        """Only the single 15s Download wait stays on the critical path."""
        import inspect

        source = inspect.getsource(ProductsExporter._wait_for_download)
        assert "PROCESSING_POPUP_SELECTORS" not in source
        # Exactly one locator wait (``.wait_for(``) - the method name
        # ``_wait_for_download`` itself also contains "wait_for". The only
        # wait is the 15s visibility bound on the Download control; the
        # download event gets a single bounded ``expect_download`` window
        # with exactly one click (no re-click, no re-verify, no retry loop).
        assert source.count(".wait_for(") == 1
        assert "timeout=15000" in source
        assert "timeout=5000" not in source
        assert "DOWNLOAD_MAX_ATTEMPTS" not in source
        assert "expect_download" in source

    @pytest.mark.asyncio
    async def test_download_single_window_timeout_no_reclick(self, exporter):
        """A slow server must NOT trigger a second click (duplicate download).

        The click is issued exactly once; if the ``download`` event does not
        arrive within the single bounded window, ``DownloadError`` is raised
        without re-clicking - a re-click while the first server-side export
        may still be running would start a second export.
        """
        page = AsyncMock()
        locator = Mock()
        filtered = Mock()
        filtered.first.wait_for = AsyncMock()
        filtered.first.click = AsyncMock()
        locator.filter = Mock(return_value=filtered)
        page.locator = Mock(return_value=locator)

        download_info = Mock()
        download_info.value = None
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=download_info)
        cm.__aexit__ = AsyncMock(
            side_effect=PlaywrightTimeoutError("download event never fired")
        )
        page.expect_download = Mock(return_value=cm)

        with pytest.raises(
            DownloadError,
            match="Download did not complete within the bounded window",
        ):
            await exporter._wait_for_download(page)

        # Exactly one click and one bounded window - never a re-click.
        assert filtered.first.click.await_count == 1
        page.expect_download.assert_called_once_with(
            timeout=exporter.DOWNLOAD_TIMEOUT_MS
        )
        assert filtered.first.wait_for.await_count == 1


class TestDiagnoseExportSafety:
    """Opt-in network diagnostic attaches/detaches safely on all failure paths."""

    @pytest.mark.asyncio
    async def test_diagnostic_detached_on_open_menu_failure(self, exporter):
        """If _open_export_menu raises, the diagnostic is still detached.

        The diagnostic is attached before _open_export_menu (to capture the
        menu-opening network traffic). If the menu-open fails (e.g. false
        positive), the finally block must detach and summarize it.
        """
        exporter.diagnose_export = True
        page = AsyncMock()
        # NetworkDiag.attach()/detach() call page.on/remove_listener
        # synchronously, so override the inherited AsyncMock with a regular
        # Mock to avoid RuntimeWarning about unawaited coroutines.
        page.on = Mock()
        page.remove_listener = Mock()
        # Page is already at the products URL (skip navigation).
        page.url = PRODUCTS_URL

        # _wait_for_table: the combined selector filter().first.wait_for
        # must succeed immediately.
        table_locator = Mock()
        table_filtered = Mock()
        table_filtered.first.wait_for = AsyncMock()
        table_filtered.first.is_visible = AsyncMock(return_value=True)
        table_locator.filter = Mock(return_value=table_filtered)

        # _sort_by_billing_status: table.locator returns a table locator
        # whose first.count returns 0 (no table found) — this would raise,
        # so we need the sort to succeed.  Instead, make the table locator
        # find the header and sort it.
        header_locator = Mock()
        header_locator.first.count = AsyncMock(return_value=1)
        header_locator.first.click = AsyncMock()
        th_locator = Mock()
        th_locator.get_attribute = AsyncMock(
            side_effect=lambda name: (
                "v-grid-cell sortable sort-asc" if name == "class" else None
            )
        )
        header_locator.first.locator = Mock(return_value=th_locator)
        table_locator.first.locator = Mock(return_value=header_locator)
        table_locator.first.count = AsyncMock(return_value=1)

        # _open_export_menu: menu button never visible.
        menu_locator = Mock()
        menu_filtered = Mock()
        menu_filtered.first.wait_for = AsyncMock(
            side_effect=PlaywrightTimeoutError("menu never visible")
        )
        menu_locator.filter = Mock(return_value=menu_filtered)

        def fake_locator(selector):
            # Route: combined table selectors → table_locator
            # menu selectors → menu_locator
            if "v-menubar-menuitem" in selector and "IcoMoon-Ultimate" in selector:
                return menu_locator
            return table_locator

        page.locator = Mock(side_effect=fake_locator)

        with pytest.raises(ProductsExportError, match="Could not find export menu button"):
            await exporter.export_products(page)

        # The diagnostic was attached (page.on called) and then detached
        # (page.remove_listener called) — no leak.
        assert page.on.call_count >= 4  # 4 event listeners attached
        assert page.remove_listener.call_count >= 4
        assert exporter._network_diag is None

    @pytest.mark.asyncio
    async def test_diagnostic_not_attached_when_disabled(self, exporter):
        """When diagnose_export is False, no listener activity occurs."""
        exporter.diagnose_export = False
        page = AsyncMock()
        page.url = PRODUCTS_URL

        # Same setup as above but diagnose_export is False.
        table_locator = Mock()
        table_filtered = Mock()
        table_filtered.first.wait_for = AsyncMock()
        table_filtered.first.is_visible = AsyncMock(return_value=True)
        table_locator.filter = Mock(return_value=table_filtered)

        header_locator = Mock()
        header_locator.first.count = AsyncMock(return_value=1)
        header_locator.first.click = AsyncMock()
        th_locator = Mock()
        th_locator.get_attribute = AsyncMock(
            side_effect=lambda name: (
                "v-grid-cell sortable sort-asc" if name == "class" else None
            )
        )
        header_locator.first.locator = Mock(return_value=th_locator)
        table_locator.first.locator = Mock(return_value=header_locator)
        table_locator.first.count = AsyncMock(return_value=1)

        menu_locator = Mock()
        menu_filtered = Mock()
        menu_filtered.first.wait_for = AsyncMock(
            side_effect=PlaywrightTimeoutError("menu never visible")
        )
        menu_locator.filter = Mock(return_value=menu_filtered)

        def fake_locator(selector):
            if "v-menubar-menuitem" in selector and "IcoMoon-Ultimate" in selector:
                return menu_locator
            return table_locator

        page.locator = Mock(side_effect=fake_locator)

        with pytest.raises(ProductsExportError):
            await exporter.export_products(page)

        page.on.assert_not_called()
        page.remove_listener.assert_not_called()


class TestCloseExportPopup:
    """Closing the export popup is best-effort: visible-first, bounded retries."""

    def _make_page(self, count_result=1):
        """Build a page whose popup returns a visible close candidate."""
        page = AsyncMock()
        popup = Mock()
        locator = Mock()
        filtered = Mock()
        filtered.first.count = AsyncMock(return_value=count_result)
        filtered.first.click = AsyncMock()
        locator.filter = Mock(return_value=filtered)
        popup_locator = Mock()
        popup_locator.last = popup
        popup.locator = Mock(return_value=locator)
        page.locator = Mock(return_value=popup_locator)
        page.wait_for_timeout = AsyncMock()
        return page, popup, locator, filtered

    @pytest.mark.asyncio
    async def test_closes_via_visible_close_button(self, exporter):
        """A visible close control is clicked on the first attempt."""
        page, popup, locator, filtered = self._make_page()

        await exporter._close_export_popup(page)

        # Search happens inside the visible popup (window/dialog), not page-wide.
        assert page.locator.call_args_list[0].args[0] == (
            '.v-window:visible, [role="dialog"]:visible'
        )
        assert popup.locator.call_count >= 1
        # The candidate is filtered to visible before .first.
        assert locator.filter.call_args_list[0] == call(visible=True)
        assert filtered.first.count.await_count == 1
        filtered.first.click.assert_awaited_once()
        # No retry needed.
        assert page.wait_for_timeout.await_count == 0

    @pytest.mark.asyncio
    async def test_close_filters_visible_before_first(self, exporter):
        """Hidden Vaadin duplicates: .first is only reached after visible=True."""
        page, popup, locator, filtered = self._make_page()

        await exporter._close_export_popup(page)

        for c in locator.filter.call_args_list:
            assert c == call(visible=True)
        # All interactions go through the filtered locator, never raw .first.
        assert locator.first.count.call_count == 0
        assert locator.first.click.call_count == 0

    @pytest.mark.asyncio
    async def test_retries_then_warns_when_button_never_appears(self, exporter, caplog):
        """No closable control: bounded retries with delays, then a warning."""
        page, popup, locator, filtered = self._make_page(count_result=0)

        with caplog.at_level("WARNING", logger="cmp_automation.products"):
            await exporter._close_export_popup(page)

        # Retried CLOSE_MAX_ATTEMPTS times with a delay between attempts.
        assert page.wait_for_timeout.await_count == exporter.CLOSE_MAX_ATTEMPTS - 1
        assert filtered.first.click.await_count == 0
        assert "Could not find close button for export popup" in caplog.text

    @pytest.mark.asyncio
    async def test_recovers_when_close_button_appears_late(self, exporter):
        """The dialog settles after download: a later attempt succeeds."""
        page = AsyncMock()
        popup = Mock()
        locator = Mock()
        filtered = Mock()
        # First two count checks find nothing (dialog settling), third finds it.
        filtered.first.count = AsyncMock(
            side_effect=[0, 0, 0, 0, 0, 0, 0, 0, 1]
        )
        filtered.first.click = AsyncMock()
        locator.filter = Mock(return_value=filtered)
        popup_locator = Mock()
        popup_locator.last = popup
        popup.locator = Mock(return_value=locator)
        page.locator = Mock(return_value=popup_locator)
        page.wait_for_timeout = AsyncMock()

        await exporter._close_export_popup(page)

        # Succeeded on the third attempt: two delays, then a successful click.
        assert filtered.first.click.await_count == 1
        assert page.wait_for_timeout.await_count == 2
