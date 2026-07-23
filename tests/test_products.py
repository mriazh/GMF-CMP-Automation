"""Tests for ProductsExporter navigation and strict URL handling."""

from unittest.mock import AsyncMock, Mock, call

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from cmp_automation.config import Config
from cmp_automation.exceptions import ProductsExportError
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
        locator.first.count = AsyncMock(return_value=1)
        locator.first.click = AsyncMock()
        locator.first.wait_for = AsyncMock()
        page.locator = Mock(return_value=locator)

        await exporter._open_export_menu(page)

        # Primary: the exact Vaadin menubar item with the IcoMoon-Ultimate icon.
        assert page.locator.call_args_list[0].args[0] == (
            "span.v-menubar-menuitem:has(span.v-icon.IcoMoon-Ultimate)"
        )
        # After the click: wait for the exact visible To xlsx caption.
        assert page.locator.call_args_list[1].args[0] == (
            'span.v-menubar-menuitem-caption:has(span.v-icon.IcoMoon-Lindua):has-text("To xlsx")'
        )
        locator.first.click.assert_awaited_once()
        locator.first.wait_for.assert_awaited_once_with(state="visible", timeout=10000)

    @pytest.mark.asyncio
    async def test_submenu_does_not_require_window_or_dialog(self, exporter):
        page = AsyncMock()
        locator = Mock()
        locator.first.count = AsyncMock(return_value=1)
        locator.first.click = AsyncMock()
        locator.first.wait_for = AsyncMock()
        page.locator = Mock(return_value=locator)

        await exporter._open_export_menu(page)

        for c in page.locator.call_args_list:
            assert ".v-window" not in c.args[0]
            assert "dialog" not in c.args[0]

    @pytest.mark.asyncio
    async def test_menu_not_found_raises(self, exporter):
        page = AsyncMock()
        locator = Mock()
        locator.first.count = AsyncMock(return_value=0)
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
        locator.first.count = AsyncMock(return_value=1)
        locator.first.click = AsyncMock()
        locator.first.wait_for = AsyncMock(
            side_effect=PlaywrightTimeoutError("submenu never appeared")
        )
        page.locator = Mock(return_value=locator)

        with pytest.raises(
            ProductsExportError,
            match="Export submenu \\(To xlsx\\) did not become visible",
        ):
            await exporter._open_export_menu(page)
        locator.first.click.assert_awaited_once()


class TestXlsxSelection:
    """To xlsx is found page-wide and waited for before clicking."""

    @pytest.mark.asyncio
    async def test_selects_xlsx_via_exact_caption_selector(self, exporter):
        page = AsyncMock()
        locator = Mock()
        locator.first.wait_for = AsyncMock()
        locator.first.click = AsyncMock()
        page.locator = Mock(return_value=locator)

        await exporter._select_xlsx_export(page)

        # Page-wide search (not scoped to .v-window/[role=dialog] popup).
        assert page.locator.call_args_list[0].args[0] == (
            'span.v-menubar-menuitem-caption:has(span.v-icon.IcoMoon-Lindua):has-text("To xlsx")'
        )
        locator.first.wait_for.assert_awaited_once_with(state="visible", timeout=10000)
        locator.first.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_force_click_used_first_for_submenu_item(self, exporter):
        """The caption never satisfies actionability stability, so force first."""
        page = AsyncMock()
        locator = Mock()
        locator.first.wait_for = AsyncMock()
        locator.first.click = AsyncMock()
        page.locator = Mock(return_value=locator)

        await exporter._select_xlsx_export(page)

        # The very first click attempt is the force click: normal clicks time
        # out on the re-rendering submenu and let the menu close meanwhile.
        assert locator.first.click.call_args_list[0] == call(force=True)

    @pytest.mark.asyncio
    async def test_synthetic_click_last_resort(self, exporter):
        """If the force click cannot land, a synthetic click is dispatched."""
        page = AsyncMock()
        locator = Mock()
        locator.first.wait_for = AsyncMock()
        locator.first.click = AsyncMock(
            side_effect=RuntimeError("element is not attached")
        )
        locator.first.evaluate = AsyncMock()
        page.locator = Mock(return_value=locator)

        await exporter._select_xlsx_export(page)

        assert locator.first.click.call_args_list[0] == call(force=True)
        locator.first.evaluate.assert_awaited_once_with("el => el.click()")


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
        locator.first.wait_for.assert_awaited_once_with(state="visible", timeout=15000)
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

        popup = Mock()
        popup.last.locator = Mock(return_value=locator)

        def fake_locator(selector):
            if selector == "#confirmdialog-ok-button":
                return locator
            return popup

        page.locator = Mock(side_effect=fake_locator)

        with pytest.raises(ProductsExportError, match="Confirm control was not found"):
            await exporter._confirm_export(page)
        locator.first.click.assert_not_called()


class TestDownloadFlow:
    """Download uses the role-based primary selector and validates XLSX."""

    @pytest.mark.asyncio
    async def test_download_via_role_button_selector(self, exporter, tmp_path, monkeypatch):
        page = AsyncMock()
        popup = Mock()
        # Processing-popup loop searches each candidate from the page, so the
        # returned locator needs an async wait_for on .first.
        popup.first.wait_for = AsyncMock()
        popup.last.wait_for = AsyncMock()
        btn_loc = Mock()
        btn_loc.first.wait_for = AsyncMock()
        btn_loc.first.click = AsyncMock()
        popup.last.locator = Mock(return_value=btn_loc)
        page.locator = Mock(return_value=popup)

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
        # Processing-popup loop searches each candidate from the page using
        # the selector variable (not a fixed _popup() locator).
        assert page.locator.call_args_list[0].args[0] == (
            exporter.PROCESSING_POPUP_SELECTORS[0]
        )
        # First download selector tried is the exact role-based one.
        assert popup.last.locator.call_args_list[0].args[0] == (
            '[role="button"]:has-text("Download")'
        )
        btn_loc.first.click.assert_awaited_once()
