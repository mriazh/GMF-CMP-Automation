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
    loc.fill = AsyncMock()
    loc.press = AsyncMock()
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
