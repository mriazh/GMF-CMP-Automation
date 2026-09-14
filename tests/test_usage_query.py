"""Tests for UsageQueryExporter navigation, interaction order, and export handling."""

import asyncio
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from cmp_automation.config import Config
from cmp_automation.exceptions import (
    ExportDialogCloseError,
    UsageQueryError,
)
from cmp_automation.usage_query import UsageQueryExporter, UsageReportArtifact

PRODUCTS_URL = "https://ep.iotcc.telkomsel.com/#!products"
GLOBAL_REPORTS_URL = "https://ep.iotcc.telkomsel.com/#!globalReports"
REPORTS_URL = "https://ep.iotcc.telkomsel.com/#!reports"
DASHBOARD_URL = "https://ep.iotcc.telkomsel.com/#!dashboard"


def make_mock_locator():
    """Create a Playwright-compatible mock locator."""
    loc = MagicMock()
    loc.wait_for = AsyncMock()
    loc.click = AsyncMock()
    loc.focus = AsyncMock()
    loc.fill = AsyncMock()
    loc.press = AsyncMock()
    loc.evaluate = AsyncMock()
    loc.count = AsyncMock(return_value=1)
    loc.is_visible = AsyncMock(return_value=True)
    loc.first = loc
    loc.last = loc
    loc.filter = MagicMock(return_value=loc)
    loc.nth = MagicMock(return_value=loc)
    return loc


@pytest.fixture
def config(tmp_path: Path) -> Config:
    """Create a test config with approved URLs and temp directories."""
    profile_dir = tmp_path / "firefox_profile"
    download_dir = tmp_path / "downloads"
    profile_dir.mkdir()
    download_dir.mkdir()
    return Config(
        cmp_username="testuser",
        cmp_password="testpassword",
        gmf_email="test@gmf-aeroasia.co.id",
        gmf_password="mailpassword",
        firefox_profile_dir=profile_dir,
        download_dir=download_dir,
        timezone="Asia/Jakarta",
    )


@pytest.fixture
def exporter(config: Config) -> UsageQueryExporter:
    """Create a UsageQueryExporter instance."""
    return UsageQueryExporter(config)


class TestUsageQueryExporterInteractions:
    """Tests for strict UI interaction sequence in UsageQueryExporter."""

    @pytest.mark.asyncio
    async def test_full_export_sequence_success(self, exporter: UsageQueryExporter, tmp_path: Path):
        """Test complete interaction order from #!products to download and close."""
        raw_download_path = (
            tmp_path / "downloads" / "report_20260907_125433_DAILY_USAGE_by_SIM.xlsx"
        )

        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(
            [
                "Date",
                "ICCID",
                "Total Data Usage",
                "Average Daily Usage",
                "Min Daily Usage",
                "Max Daily Usage",
            ]
        )
        ws.append(["2026-09-07", "8962100012747108709", 271479072, 271479072, 271479072, 271479072])
        ws.append(["2026-09-07", "8962100014905470830", 703600954, 703600954, 703600954, 703600954])
        wb.save(raw_download_path)

        page = AsyncMock()
        page.url = PRODUCTS_URL

        reports_menu = make_mock_locator()
        usage_query_submenu = make_mock_locator()
        filterselect_btn = make_mock_locator()
        daily_menu_item = make_mock_locator()
        date_inputs = make_mock_locator()
        date_input_first = make_mock_locator()
        date_input_last = make_mock_locator()
        date_inputs.count = AsyncMock(return_value=2)
        date_inputs.nth = MagicMock(
            side_effect=lambda idx: date_input_first if idx == 0 else date_input_last
        )
        search_btn = make_mock_locator()
        loading_indicator = make_mock_locator()
        total_usage_header = make_mock_locator()
        export_menu = make_mock_locator()
        export_xlsx_option = make_mock_locator()
        download_btn = make_mock_locator()
        close_btn = make_mock_locator()
        export_dialog = make_mock_locator()

        download_mock = AsyncMock()
        download_mock.suggested_filename = "report_20260907_125433_DAILY_USAGE_by_SIM.xlsx"
        download_mock.save_as = AsyncMock()

        def locator_side_effect(selector: str):
            if "span.main-menu-item-caption" in selector or "Reports" in selector:
                return reports_menu
            elif "span.valo-menu-item-caption" in selector or "Usage Query" in selector:
                return usage_query_submenu
            elif "v-filterselect-button" in selector:
                return filterselect_btn
            elif "gwt-MenuItem" in selector or "Daily" in selector:
                return daily_menu_item
            elif "v-datefield-textfield" in selector:
                return date_inputs
            elif "icon-only.primary" in selector or "primary" in selector:
                return search_btn
            elif "v-loading-indicator" in selector:
                return loading_indicator
            elif "Total Data Usage" in selector or "header" in selector:
                return total_usage_header
            elif "v-menubar-menuitem-caption" in selector or "Export to xlsx" in selector:
                return export_xlsx_option
            elif "v-menubar-menuitem" in selector or "Export" in selector:
                return export_menu
            elif "Download" in selector:
                return download_btn
            elif "Close" in selector or "v-window-closebox" in selector:
                return close_btn
            elif "v-window" in selector:
                return export_dialog
            return make_mock_locator()

        page.locator = MagicMock(side_effect=locator_side_effect)

        @asynccontextmanager
        async def mock_expect_download(*args, **kwargs):
            mock_info = MagicMock()
            fut = asyncio.Future()
            fut.set_result(download_mock)
            mock_info.value = fut
            yield mock_info

        page.expect_download = mock_expect_download

        async def mock_wait_for_portal_url(p, target_url=None, fragment=None, *args, **kwargs):
            if target_url and "globalReports" in str(target_url) or fragment == "!globalReports":
                p.url = GLOBAL_REPORTS_URL
            elif target_url and "reports" in str(target_url) or fragment == "!reports":
                p.url = REPORTS_URL

        exporter._wait_for_url = AsyncMock(side_effect=mock_wait_for_portal_url)

        artifact = await exporter.export_usage_query(page, query_date=date(2026, 9, 7))

        assert isinstance(artifact, UsageReportArtifact)
        assert artifact.query_date == date(2026, 9, 7)
        assert artifact.raw_path.name == "report_20260907_125433_DAILY_USAGE_by_SIM.xlsx"
        assert len(artifact.rows) == 2

        reports_menu.click.assert_awaited()
        usage_query_submenu.click.assert_awaited()
        filterselect_btn.click.assert_awaited()
        daily_menu_item.click.assert_awaited()
        date_input_first.fill.assert_awaited_with("2026-09-07")
        date_input_last.fill.assert_awaited_with("2026-09-07")
        search_btn.click.assert_awaited()
        assert total_usage_header.click.await_count >= 2
        export_xlsx_option.click.assert_awaited()
        download_btn.click.assert_awaited_once()
        close_btn.click.assert_awaited()

    @pytest.mark.asyncio
    async def test_close_popup_failure_raises_export_dialog_close_error(
        self, exporter: UsageQueryExporter
    ):
        """Failure to close export dialog must raise ExportDialogCloseError."""
        page = AsyncMock()
        page.url = REPORTS_URL

        dialog_mock = make_mock_locator()
        dialog_mock.wait_for = AsyncMock(side_effect=PlaywrightTimeoutError("Close timeout"))
        close_btn = make_mock_locator()

        def locator_side_effect(selector: str):
            if "Close" in selector or "v-window-closebox" in selector:
                return close_btn
            elif ".v-window" in selector:
                return dialog_mock
            return make_mock_locator()

        page.locator = MagicMock(side_effect=locator_side_effect)

        with pytest.raises(ExportDialogCloseError):
            await exporter._close_export_dialog(page)

    @pytest.mark.asyncio
    async def test_invalid_schema_rejects_products_export(
        self, exporter: UsageQueryExporter, tmp_path: Path
    ):
        """Reject raw file if it has legacy Products schema instead of Usage Query."""
        invalid_path = tmp_path / "invalid_products.xlsx"
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["MSISDN", "IMSI", "ICCID", "SIM Status", "Billing Status"])
        ws.append(["08123456789", "5101012345", "896210001", "Active", "Active"])
        wb.save(invalid_path)

        with pytest.raises(UsageQueryError):
            exporter._parse_usage_data(invalid_path)

    def test_parse_usage_data_is_public_and_parses_rows(
        self, exporter: UsageQueryExporter, tmp_path: Path
    ):
        """parse_usage_data is a public method that parses valid Usage Query files."""
        import openpyxl

        raw_path = tmp_path / "usage_query.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Date", "ICCID", "Total Data Usage"])
        ws.append(["2026-09-07", "8962100012747108709", 271479072])
        ws.append(["2026-09-07", "8962100014905470830", 703600954])
        wb.save(raw_path)

        # Access via the public API without touching the private alias.
        rows = exporter.parse_usage_data(raw_path)
        assert len(rows) == 2
        assert rows[0]["iccid"] == "8962100012747108709"
        assert rows[0]["total_usage_bytes"] == 271479072
        assert rows[1]["total_usage_bytes"] == 703600954
        assert "Date" not in rows[0]  # normalized schema keys only

    def test_parse_usage_data_rejects_legacy_schema(
        self, exporter: UsageQueryExporter, tmp_path: Path
    ):
        """The public parser raises UsageQueryError for legacy Products schema."""
        import openpyxl

        legacy_path = tmp_path / "legacy.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["MSISDN", "IMSI", "ICCID", "SIM Status", "Billing Status"])
        ws.append(["08123456789", "5101012345", "896210001", "Active", "Active"])
        wb.save(legacy_path)

        with pytest.raises(UsageQueryError):
            exporter.parse_usage_data(legacy_path)

    @pytest.mark.asyncio
    async def test_fill_query_dates_focus_fallback_and_no_tab(
        self, exporter: UsageQueryExporter
    ):
        page = AsyncMock()
        first_input = make_mock_locator()
        first_input.click = AsyncMock(side_effect=PlaywrightTimeoutError("click timeout"))
        second_input = make_mock_locator()
        second_input.click = AsyncMock(side_effect=PlaywrightTimeoutError("click timeout"))

        date_inputs = make_mock_locator()
        date_inputs.count = AsyncMock(return_value=2)
        date_inputs.nth = MagicMock(
            side_effect=lambda idx: first_input if idx == 0 else second_input
        )

        page.locator = MagicMock(return_value=date_inputs)
        exporter._wait_for_loading = AsyncMock()

        await exporter._fill_query_dates(page, "2026-09-07")

        first_input.focus.assert_awaited_once()
        first_input.fill.assert_awaited_with("2026-09-07")
        second_input.focus.assert_awaited_once()
        second_input.fill.assert_awaited_with("2026-09-07")
        first_input.press.assert_not_awaited()
        second_input.press.assert_not_awaited()
        escape_calls = [
            call for call in page.keyboard.press.await_args_list if call.args == ("Escape",)
        ]
        assert len(escape_calls) >= 3

    @pytest.mark.asyncio
    async def test_fill_query_dates_evaluate_fallback_dismisses_popups(
        self, exporter: UsageQueryExporter
    ):
        page = AsyncMock()
        first_input = make_mock_locator()
        first_input.fill = AsyncMock(side_effect=RuntimeError("fill error"))

        date_inputs = make_mock_locator()
        date_inputs.count = AsyncMock(return_value=2)
        date_inputs.nth = MagicMock(return_value=first_input)
        page.locator = MagicMock(return_value=date_inputs)
        exporter._wait_for_loading = AsyncMock()
        page.evaluate = AsyncMock(return_value=True)

        await exporter._fill_query_dates(page, "2026-09-07")

        page.evaluate.assert_awaited_once()
        eval_script = page.evaluate.await_args[0][0]
        assert "v-datefield-popup" in eval_script
        assert "v-popupview-popup" in eval_script
        page.keyboard.press.assert_awaited_with("Escape")

    @pytest.mark.asyncio
    async def test_submit_search_falls_back_to_force_click(
        self, exporter: UsageQueryExporter
    ):
        page = AsyncMock()
        search_btn = make_mock_locator()
        search_btn.click = AsyncMock(
            side_effect=[PlaywrightTimeoutError("intercepted"), None]
        )
        page.locator = MagicMock(return_value=search_btn)
        exporter._wait_for_loading = AsyncMock()

        await exporter._submit_search(page)

        assert search_btn.click.await_count == 2
        search_btn.click.assert_awaited_with(force=True, timeout=10000)
        exporter._wait_for_loading.assert_awaited()

    @pytest.mark.asyncio
    async def test_submit_search_both_clicks_fail_raises_usage_query_error(
        self, exporter: UsageQueryExporter
    ):
        page = AsyncMock()
        search_btn = make_mock_locator()
        search_btn.click = AsyncMock(
            side_effect=[PlaywrightTimeoutError("intercepted"), PlaywrightTimeoutError("force failed")]
        )
        search_btn.evaluate = AsyncMock(side_effect=PlaywrightTimeoutError("evaluate failed"))
        page.locator = MagicMock(return_value=search_btn)
        exporter._wait_for_loading = AsyncMock()

        with pytest.raises(UsageQueryError, match="Failed to find or click primary search button"):
            await exporter._submit_search(page)

        page.screenshot.assert_awaited_once()
        call_kwargs = page.screenshot.await_args.kwargs
        assert "error_search_failed.png" in call_kwargs.get("path", "")

    @pytest.mark.asyncio
    async def test_submit_search_falls_back_to_evaluate_click(
        self, exporter: UsageQueryExporter
    ):
        page = AsyncMock()
        search_btn = make_mock_locator()
        search_btn.click = AsyncMock(
            side_effect=[PlaywrightTimeoutError("intercepted"), PlaywrightTimeoutError("force failed")]
        )
        search_btn.evaluate = AsyncMock(return_value=None)
        page.locator = MagicMock(return_value=search_btn)
        exporter._wait_for_loading = AsyncMock()

        await exporter._submit_search(page)

        assert search_btn.click.await_count == 2
        search_btn.evaluate.assert_awaited_once_with("el => el.click()")
        exporter._wait_for_loading.assert_awaited()
        page.screenshot.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_submit_search_failure_saves_screenshot(
        self, exporter: UsageQueryExporter
    ):
        page = AsyncMock()
        search_btn = make_mock_locator()
        search_btn.wait_for = AsyncMock(side_effect=PlaywrightTimeoutError("search button not visible"))
        page.locator = MagicMock(return_value=search_btn)
        exporter._wait_for_loading = AsyncMock()

        with pytest.raises(UsageQueryError, match="Failed to find or click primary search button"):
            await exporter._submit_search(page)

        page.screenshot.assert_awaited_once()
        call_kwargs = page.screenshot.await_args.kwargs
        assert "error_search_failed.png" in call_kwargs.get("path", "")

    def test_search_button_selector_scoped(self, exporter: UsageQueryExporter):
        expected = (
            ".v-button.icon-only.primary, "
            "div[role='button'].v-button.icon-only.primary, "
            "div[role='button'].v-button.primary, "
            ".v-button.primary"
        )
        assert exporter.SEARCH_BUTTON_SELECTOR == expected

    @pytest.mark.asyncio
    async def test_wait_for_loading_uses_direct_timeout(
        self, exporter: UsageQueryExporter
    ):
        page = AsyncMock()
        indicator = make_mock_locator()
        indicator.count = AsyncMock(return_value=1)
        indicator.is_visible = AsyncMock(return_value=True)
        page.locator = MagicMock(return_value=indicator)

        await exporter._wait_for_loading(page, timeout_ms=60000)

        indicator.wait_for.assert_awaited_once_with(state="hidden", timeout=60000)
        page.wait_for_load_state.assert_awaited_once_with("domcontentloaded", timeout=60000)

    @pytest.mark.asyncio
    async def test_wait_for_loading_enforces_minimum_1000ms(
        self, exporter: UsageQueryExporter
    ):
        page = AsyncMock()
        indicator = make_mock_locator()
        indicator.count = AsyncMock(return_value=1)
        indicator.is_visible = AsyncMock(return_value=True)
        page.locator = MagicMock(return_value=indicator)

        await exporter._wait_for_loading(page, timeout_ms=500)

        indicator.wait_for.assert_awaited_once_with(state="hidden", timeout=1000)
        page.wait_for_load_state.assert_awaited_once_with("domcontentloaded", timeout=1000)

    @pytest.mark.asyncio
    async def test_wait_for_loading_default_timeout_15000ms(
        self, exporter: UsageQueryExporter
    ):
        page = AsyncMock()
        indicator = make_mock_locator()
        indicator.count = AsyncMock(return_value=1)
        indicator.is_visible = AsyncMock(return_value=True)
        page.locator = MagicMock(return_value=indicator)

        await exporter._wait_for_loading(page)

        indicator.wait_for.assert_awaited_once_with(state="hidden", timeout=15000)
        page.wait_for_load_state.assert_awaited_once_with("domcontentloaded", timeout=15000)

    @pytest.mark.asyncio
    async def test_wait_for_loading_uses_smaller_timeout_if_given(
        self, exporter: UsageQueryExporter
    ):
        page = AsyncMock()
        indicator = make_mock_locator()
        indicator.count = AsyncMock(return_value=1)
        indicator.is_visible = AsyncMock(return_value=True)
        page.locator = MagicMock(return_value=indicator)

        await exporter._wait_for_loading(page, timeout_ms=2000)

        indicator.wait_for.assert_awaited_once_with(state="hidden", timeout=2000)
        page.wait_for_load_state.assert_awaited_once_with("domcontentloaded", timeout=2000)

    @pytest.mark.asyncio
    async def test_wait_for_loading_handles_exceptions_gracefully(
        self, exporter: UsageQueryExporter
    ):
        page = AsyncMock()
        indicator = make_mock_locator()
        indicator.count = AsyncMock(return_value=1)
        indicator.is_visible = AsyncMock(return_value=True)
        indicator.wait_for = AsyncMock(side_effect=PlaywrightTimeoutError("still loading"))
        page.wait_for_load_state = AsyncMock(side_effect=Exception("state error"))
        page.locator = MagicMock(return_value=indicator)

        # Must not raise
        await exporter._wait_for_loading(page, timeout_ms=1000)

    @pytest.mark.asyncio
    async def test_submit_search_waits_for_loading_and_table(
        self, exporter: UsageQueryExporter
    ):
        page = AsyncMock()
        search_btn = make_mock_locator()
        table_loc = make_mock_locator()

        def loc_side_effect(selector: str):
            if "icon-only.primary" in selector or "primary" in selector:
                return search_btn
            elif ".v-table-table" in selector:
                return table_loc
            return make_mock_locator()

        page.locator = MagicMock(side_effect=loc_side_effect)
        exporter._wait_for_loading = AsyncMock()

        await exporter._submit_search(page)

        exporter._wait_for_loading.assert_awaited_with(page, timeout_ms=60000)
        table_loc.first.wait_for.assert_awaited_once_with(state="visible", timeout=30000)

    @pytest.mark.asyncio
    async def test_sort_header_wait_failure_captures_screenshot_and_does_not_crash(
        self, exporter: UsageQueryExporter
    ):
        page = AsyncMock()
        header = make_mock_locator()
        header.wait_for = AsyncMock(side_effect=PlaywrightTimeoutError("header not found"))
        page.locator = MagicMock(return_value=header)

        # Should log warning and not raise
        await exporter._sort_total_data_usage_descending(page)

        page.screenshot.assert_awaited_once()
        call_kwargs = page.screenshot.await_args.kwargs
        assert "error_sort_failed.png" in call_kwargs.get("path", "")

    @pytest.mark.asyncio
    async def test_sort_marker_mismatch_warns_and_does_not_crash(
        self, exporter: UsageQueryExporter
    ):
        page = AsyncMock()
        header = make_mock_locator()
        header.get_attribute = AsyncMock(
            side_effect=lambda attr: "ascending" if attr == "aria-sort" else "v-table-header-cell"
        )
        page.locator = MagicMock(return_value=header)
        exporter._wait_for_loading = AsyncMock()

        # Should log warning and continue without raising
        await exporter._sort_total_data_usage_descending(page)
        assert header.click.await_count == 2

    @pytest.mark.asyncio
    async def test_sort_total_data_usage_descending_uses_force_and_bounded_wait(
        self, exporter: UsageQueryExporter
    ):
        page = AsyncMock()
        header = make_mock_locator()
        header.get_attribute = AsyncMock(
            side_effect=lambda attr: "descending" if attr == "aria-sort" else "v-table-header-cell"
        )
        page.locator = MagicMock(return_value=header)
        exporter._wait_for_loading = AsyncMock()

        await exporter._sort_total_data_usage_descending(page)

        assert header.click.await_count == 2
        header.click.assert_awaited_with(force=True, timeout=5000)
        exporter._wait_for_loading.assert_awaited_with(page, timeout_ms=5000)

    @pytest.mark.asyncio
    async def test_sort_total_data_usage_descending_screenshot_fallback_to_output_images(
        self, config: Config
    ):
        config.image_dir = None
        exp = UsageQueryExporter(config)
        page = AsyncMock()
        header = make_mock_locator()
        header.wait_for = AsyncMock(side_effect=PlaywrightTimeoutError("timeout"))
        page.locator = MagicMock(return_value=header)

        await exp._sort_total_data_usage_descending(page)

        page.screenshot.assert_awaited_once()
        call_kwargs = page.screenshot.await_args.kwargs
        expected_path = str(Path("output/images") / "error_sort_failed.png")
        assert call_kwargs.get("path") == expected_path

    @pytest.mark.asyncio
    async def test_export_and_download_waits_for_loading_first(
        self, exporter: UsageQueryExporter, tmp_path: Path
    ):
        page = AsyncMock()
        raw_download_path = tmp_path / "downloads" / "test.xlsx"
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Date", "ICCID", "Total Data Usage"])
        wb.save(raw_download_path)

        menu = make_mock_locator()
        option = make_mock_locator()
        download_btn = make_mock_locator()

        page.locator = MagicMock(
            side_effect=lambda sel: menu
            if ":has-text('Export')" in sel or "aria-label*='Export'" in sel
            else (
                option
                if "role='menuitem'" in sel or "v-menubar-popup" in sel or "Export to xlsx" in sel
                else download_btn
            )
        )
        exporter._wait_for_loading = AsyncMock()

        download_mock = AsyncMock()
        download_mock.suggested_filename = "test.xlsx"
        download_mock.save_as = AsyncMock()

        @asynccontextmanager
        async def mock_expect_download(*args, **kwargs):
            mock_info = MagicMock()
            fut = asyncio.Future()
            fut.set_result(download_mock)
            mock_info.value = fut
            yield mock_info

        page.expect_download = mock_expect_download

        res = await exporter._export_and_download(page)
        assert res.name == "test.xlsx"
        page.keyboard.press.assert_awaited_with("Escape")
        exporter._wait_for_loading.assert_any_await(page, timeout_ms=10000)
        menu.click.assert_awaited_with(timeout=5000)
        option.click.assert_awaited_with(timeout=5000)

    @pytest.mark.asyncio
    async def test_export_and_download_menu_click_falls_back_to_force(
        self, exporter: UsageQueryExporter, tmp_path: Path
    ):
        page = AsyncMock()
        raw_download_path = tmp_path / "downloads" / "test2.xlsx"
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Date", "ICCID", "Total Data Usage"])
        wb.save(raw_download_path)

        menu = make_mock_locator()
        menu.click = AsyncMock(side_effect=[Exception("stalled"), None])
        option = make_mock_locator()
        download_btn = make_mock_locator()

        page.locator = MagicMock(
            side_effect=lambda sel: menu
            if ":has-text('Export')" in sel or "aria-label*='Export'" in sel
            else (
                option
                if "role='menuitem'" in sel or "v-menubar-popup" in sel or "Export to xlsx" in sel
                else download_btn
            )
        )
        exporter._wait_for_loading = AsyncMock()

        download_mock = AsyncMock()
        download_mock.suggested_filename = "test2.xlsx"
        download_mock.save_as = AsyncMock()

        @asynccontextmanager
        async def mock_expect_download(*args, **kwargs):
            mock_info = MagicMock()
            fut = asyncio.Future()
            fut.set_result(download_mock)
            mock_info.value = fut
            yield mock_info

        page.expect_download = mock_expect_download

        await exporter._export_and_download(page)
        assert menu.click.await_count == 2
        assert menu.click.await_args_list[1].kwargs == {"force": True, "timeout": 5000}

    @pytest.mark.asyncio
    async def test_export_and_download_option_click_falls_back_to_force(
        self, exporter: UsageQueryExporter, tmp_path: Path
    ):
        page = AsyncMock()
        raw_download_path = tmp_path / "downloads" / "test3.xlsx"
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Date", "ICCID", "Total Data Usage"])
        wb.save(raw_download_path)

        menu = make_mock_locator()
        option = make_mock_locator()
        option.click = AsyncMock(side_effect=[Exception("stalled"), None])
        download_btn = make_mock_locator()

        page.locator = MagicMock(
            side_effect=lambda sel: menu
            if ":has-text('Export')" in sel or "aria-label*='Export'" in sel
            else (
                option
                if "role='menuitem'" in sel or "v-menubar-popup" in sel or "Export to xlsx" in sel
                else download_btn
            )
        )
        exporter._wait_for_loading = AsyncMock()

        download_mock = AsyncMock()
        download_mock.suggested_filename = "test3.xlsx"
        download_mock.save_as = AsyncMock()

        @asynccontextmanager
        async def mock_expect_download(*args, **kwargs):
            mock_info = MagicMock()
            fut = asyncio.Future()
            fut.set_result(download_mock)
            mock_info.value = fut
            yield mock_info

        page.expect_download = mock_expect_download

        await exporter._export_and_download(page)
        assert option.click.await_count == 2
        assert option.click.await_args_list[1].kwargs == {"force": True, "timeout": 5000}

    @pytest.mark.asyncio
    async def test_export_and_download_fallbacks_for_menu_and_option(
        self, exporter: UsageQueryExporter, tmp_path: Path
    ):
        page = AsyncMock()
        raw_download_path = tmp_path / "downloads" / "test_fallback.xlsx"
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Date", "ICCID", "Total Data Usage"])
        wb.save(raw_download_path)

        missing_export_menu = make_mock_locator()
        missing_export_menu.count = AsyncMock(return_value=0)
        missing_export_menu.is_visible = AsyncMock(return_value=False)

        empty_content_items = make_mock_locator()
        empty_content_items.count = AsyncMock(return_value=0)

        all_items = make_mock_locator()
        all_items.count = AsyncMock(return_value=2)
        fallback_menu = make_mock_locator()
        all_items.nth = MagicMock(return_value=fallback_menu)

        missing_option = make_mock_locator()
        missing_option.count = AsyncMock(return_value=0)
        missing_option.is_visible = AsyncMock(return_value=False)

        fallback_option = make_mock_locator()
        download_btn = make_mock_locator()

        def locator_se(sel: str):
            if ":has-text('Export')" in sel or "aria-label*='Export'" in sel:
                return missing_export_menu
            elif ":not([class*='user']):not([class*='valo'])" in sel:
                return empty_content_items
            elif sel == "span.v-menubar-menuitem":
                return all_items
            elif "role='menuitem'" in sel or "v-menubar-popup" in sel:
                return missing_option
            elif "Export to xlsx" in sel or "v-menubar-menuitem-caption" in sel:
                return fallback_option
            return download_btn

        page.locator = MagicMock(side_effect=locator_se)
        exporter._wait_for_loading = AsyncMock()

        download_mock = AsyncMock()
        download_mock.suggested_filename = "test_fallback.xlsx"
        download_mock.save_as = AsyncMock()

        @asynccontextmanager
        async def mock_expect_download(*args, **kwargs):
            mock_info = MagicMock()
            fut = asyncio.Future()
            fut.set_result(download_mock)
            mock_info.value = fut
            yield mock_info

        page.expect_download = mock_expect_download

        res = await exporter._export_and_download(page)
        assert res.name == "test_fallback.xlsx"
        page.keyboard.press.assert_awaited_with("Escape")
        fallback_menu.click.assert_awaited_with(timeout=5000)
        fallback_option.click.assert_awaited_with(timeout=5000)

    @pytest.mark.asyncio
    async def test_export_and_download_failure_captures_screenshot(
        self, exporter: UsageQueryExporter
    ):
        page = AsyncMock()
        menu = make_mock_locator()
        menu.wait_for = AsyncMock(side_effect=PlaywrightTimeoutError("menu not visible"))
        page.locator = MagicMock(return_value=menu)
        exporter._wait_for_loading = AsyncMock()

        with pytest.raises(UsageQueryError, match="Failed to open the export menu"):
            await exporter._export_and_download(page)

        page.screenshot.assert_awaited_once()
        call_kwargs = page.screenshot.await_args.kwargs
        assert "error_export_failed.png" in call_kwargs.get("path", "")

    @pytest.mark.asyncio
    async def test_select_report_type_daily_already_daily(
        self, exporter: UsageQueryExporter, caplog: pytest.LogCaptureFixture
    ):
        page = AsyncMock()
        exporter._wait_for_loading = AsyncMock()

        input_mock = make_mock_locator()
        input_mock.input_value = AsyncMock(return_value=" Daily ")

        filter_inputs = make_mock_locator()
        filter_inputs.count = AsyncMock(return_value=1)
        filter_inputs.nth = MagicMock(return_value=input_mock)

        btn_mock = make_mock_locator()

        def locator_se(selector: str):
            if "input.v-filterselect-input" in selector:
                return filter_inputs
            elif "v-filterselect-button" in selector:
                return btn_mock
            return make_mock_locator()

        page.locator = MagicMock(side_effect=locator_se)

        await exporter._select_report_type_daily(page)

        assert "Report Type is already Daily" in caplog.text
        btn_mock.click.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_select_report_type_daily_monthly_selection(
        self, exporter: UsageQueryExporter
    ):
        page = AsyncMock()
        exporter._wait_for_loading = AsyncMock()

        btn_mock = make_mock_locator()
        ancestor_mock = make_mock_locator()
        ancestor_mock.locator = MagicMock(return_value=btn_mock)

        input_mock = make_mock_locator()
        input_mock.input_value = AsyncMock(return_value="Monthly")
        input_mock.locator = MagicMock(return_value=ancestor_mock)

        filter_inputs = make_mock_locator()
        filter_inputs.count = AsyncMock(return_value=1)
        filter_inputs.nth = MagicMock(return_value=input_mock)

        daily_option = make_mock_locator()

        captured_selectors: list[str] = []

        def locator_se(selector: str):
            captured_selectors.append(selector)
            if "input.v-filterselect-input" in selector:
                return filter_inputs
            elif "gwt-MenuItem" in selector or "Daily" in selector:
                return daily_option
            return make_mock_locator()

        page.locator = MagicMock(side_effect=locator_se)

        await exporter._select_report_type_daily(page)

        btn_mock.click.assert_awaited_once()
        daily_option.click.assert_awaited_once()
        expected_option_selector = (
            "td.gwt-MenuItem[role='listitem'], .gwt-MenuItem, "
            ".v-filterselect-suggestmenu span, div[role='option']"
        )
        assert expected_option_selector in captured_selectors

    @pytest.mark.asyncio
    async def test_select_report_type_daily_label_matching(
        self, exporter: UsageQueryExporter
    ):
        page = AsyncMock()
        exporter._wait_for_loading = AsyncMock()

        btn_mock = make_mock_locator()
        ancestor_mock = make_mock_locator()
        ancestor_mock.locator = MagicMock(return_value=btn_mock)

        container_mock = make_mock_locator()
        container_mock.inner_text = AsyncMock(return_value="Report Type:")

        input_mock = make_mock_locator()
        input_mock.input_value = AsyncMock(return_value="")

        def input_locator_se(sel: str):
            if "ancestor::div" in sel:
                return ancestor_mock
            elif "ancestor::*" in sel:
                return container_mock
            return make_mock_locator()

        input_mock.locator = MagicMock(side_effect=input_locator_se)

        filter_inputs = make_mock_locator()
        filter_inputs.count = AsyncMock(return_value=1)
        filter_inputs.nth = MagicMock(return_value=input_mock)

        daily_option = make_mock_locator()

        def locator_se(selector: str):
            if "input.v-filterselect-input" in selector:
                return filter_inputs
            elif "gwt-MenuItem" in selector or "Daily" in selector:
                return daily_option
            return make_mock_locator()

        page.locator = MagicMock(side_effect=locator_se)

        await exporter._select_report_type_daily(page)

        btn_mock.click.assert_awaited_once()
        daily_option.click.assert_awaited_once()
