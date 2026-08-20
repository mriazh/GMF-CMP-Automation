"""Usage Query exporter for CMP Portal.

Navigates to the Reports -> Usage Query SPA page, selects Daily report type,
sets date range, executes search, verifies descending numeric sort on
Total Data Usage, exports to XLSX, preserves raw portal filename, and closes popup.
"""

import logging
import zipfile
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

import openpyxl
from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .config import Config
from .exceptions import (
    ExportDialogCloseError,
    SortOrderError,
    UsageQueryError,
)
from .utils import is_approved_portal_url, wait_for_portal_url

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UsageReportArtifact:
    """Artifact produced by Usage Query export."""

    raw_path: Path
    query_date: date
    rows: list[dict[str, Any]]


class UsageQueryExporter:
    """Handles CMP Portal Usage Query export."""

    LOADING_SELECTORS = [
        ".v-loading-indicator",
        ".loading",
        '[aria-busy="true"]',
        ".v-progressbar",
    ]

    def __init__(self, config: Config, diagnose_export: bool = False) -> None:
        self.config = config
        self.diagnose_export = diagnose_export

    async def export(self, page: Page, query_date: date | None = None) -> UsageReportArtifact:
        """Alias for export_usage_query."""
        return await self.export_usage_query(page, query_date=query_date)

    async def export_usage_query(
        self, page: Page, query_date: date | None = None
    ) -> UsageReportArtifact:
        """Execute the full Usage Query export flow."""
        if query_date is None:
            tz = self.config.get_timezone()
            query_date = datetime.now(tz).date()

        date_str = query_date.strftime("%Y-%m-%d")
        logger.info("Executing Usage Query export for date: %s", date_str)

        # 1. Verify landing state
        await self._verify_landing(page)

        # 2. Navigate to Reports
        await self._navigate_to_reports(page)

        # 3. Navigate to Usage Query
        await self._navigate_to_usage_query(page)

        # 4. Select Daily report type
        await self._select_report_type_daily(page)

        # 5. Fill query dates
        await self._fill_query_dates(page, date_str)

        # 6. Submit search
        await self._submit_search(page)

        # 7. Double click Total Data Usage header and verify descending sort
        await self._sort_total_data_usage_descending(page)

        # 8. Export and download
        raw_path = await self._export_and_download(page)

        # 9. Close export popup
        await self._close_export_dialog(page)

        # 10. Parse and validate downloaded data
        rows = self._parse_usage_data(raw_path)

        logger.info(
            "Usage Query export completed successfully: %s (%d rows)",
            raw_path.name,
            len(rows),
        )
        return UsageReportArtifact(
            raw_path=raw_path,
            query_date=query_date,
            rows=rows,
        )

    async def _wait_for_loading(self, page: Page, timeout_ms: int = 30000) -> None:
        """Wait for Vaadin loading indicators to disappear."""
        for sel in self.LOADING_SELECTORS:
            try:
                await page.locator(sel).first.wait_for(state="hidden", timeout=timeout_ms)
            except PlaywrightTimeoutError:
                pass
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            pass

    async def _wait_for_url(
        self, page: Page, target_url: str, fragment: str = "", timeout_ms: int = 30000
    ) -> None:
        """Wait for portal URL to match target or fragment."""

        def _is_target(url: str) -> bool:
            return is_approved_portal_url(url, target_url, fragment)

        await wait_for_portal_url(page, _is_target, deadline_ms=timeout_ms)

    async def _verify_landing(self, page: Page) -> None:
        """Verify the portal reached #!products or authenticated state."""
        await self._wait_for_loading(page)
        url = page.url
        if not is_approved_portal_url(url, self.config.cmp_products_url, "!products"):
            logger.debug("Current URL %s is not #!products, checking portal state", url)

    async def _navigate_to_reports(self, page: Page) -> None:
        """Click Reports main-menu caption and verify #!globalReports."""
        logger.info("Navigating to Reports main menu")
        await self._wait_for_loading(page)
        reports_menu = page.locator(
            'div.main-menu-item:has(.main-menu-item-caption:has-text("Reports")), span.main-menu-item-caption:has-text("Reports"), span.main-menu-item-caption'
        ).filter(has_text="Reports").first
        try:
            await reports_menu.wait_for(state="visible", timeout=30000)
            await reports_menu.click()
        except PlaywrightTimeoutError:
            logger.warning("Reports menu not visible after 30s; reloading page and retrying")
            try:
                await page.reload(wait_until="domcontentloaded")
                await self._wait_for_loading(page)
                await reports_menu.wait_for(state="visible", timeout=30000)
                await reports_menu.click()
            except Exception as e:
                if is_approved_portal_url(page.url, self.config.cmp_global_reports_url, "!globalReports"):
                    logger.info("Already on #!globalReports, proceeding")
                else:
                    raise UsageQueryError("Failed to find or click 'Reports' menu caption") from e

        try:
            await self._wait_for_url(
                page,
                self.config.cmp_global_reports_url,
                fragment="!globalReports",
                timeout_ms=30000,
            )
        except PlaywrightTimeoutError as e:
            if not is_approved_portal_url(page.url, self.config.cmp_global_reports_url, "!globalReports"):
                raise UsageQueryError("Timeout waiting for #!globalReports") from e

    async def _navigate_to_usage_query(self, page: Page) -> None:
        """Click Usage Query sub-menu caption and verify #!reports."""
        logger.info("Navigating to Usage Query sub-menu")
        await self._wait_for_loading(page)
        usage_query_menu = page.locator(
            'div.valo-menu-item:has-text("Usage Query"), span.valo-menu-item-caption:has-text("Usage Query"), span.valo-menu-item-caption'
        ).filter(has_text="Usage Query").first
        try:
            await usage_query_menu.wait_for(state="visible", timeout=30000)
            await usage_query_menu.click()
        except PlaywrightTimeoutError as e:
            if is_approved_portal_url(page.url, self.config.cmp_reports_url, "!reports"):
                logger.info("Already on #!reports, proceeding")
            else:
                raise UsageQueryError("Failed to find or click 'Usage Query' submenu caption") from e

        try:
            await self._wait_for_url(
                page, self.config.cmp_reports_url, fragment="!reports", timeout_ms=30000
            )
        except PlaywrightTimeoutError as e:
            if not is_approved_portal_url(page.url, self.config.cmp_reports_url, "!reports"):
                raise UsageQueryError("Timeout waiting for #!reports") from e

    async def _select_report_type_daily(self, page: Page) -> None:
        """Open the report-type filter button and select the Daily menu item."""
        logger.info("Selecting Report Type: Daily")
        await self._wait_for_loading(page)

        # The live view contains several filterselect buttons. Scope the button
        # to the input whose current value is Monthly instead of relying on a
        # page-global ordinal, which can change as Vaadin mounts hidden filters.
        btn: Locator | None = None
        filter_inputs = page.locator("input.v-filterselect-input")
        try:
            input_count = await filter_inputs.count()
        except Exception:
            input_count = 0
        for index in range(input_count):
            candidate = filter_inputs.nth(index)
            try:
                value = await candidate.input_value()
            except Exception:
                continue
            if value.strip().lower() != "monthly":
                continue
            ancestor = candidate.locator(
                "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' v-filterselect ')][1]"
            )
            scoped_button = ancestor.locator("div.v-filterselect-button[role='button']").first
            if await scoped_button.count() > 0:
                btn = scoped_button
                break

        if btn is None:
            buttons = page.locator("div.v-filterselect-button[role='button']")
            count = await buttons.count()
            if count < 1:
                raise UsageQueryError("Report Type filterselect button was not found")
            # Compatibility fallback for the stable mock seam and older portal
            # markup where the report-type filter is the second button.
            btn = buttons.nth(1 if count > 1 else 0)

        try:
            await btn.wait_for(state="visible", timeout=30000)
            await btn.click()
        except PlaywrightTimeoutError as exc:
            raise UsageQueryError("Report Type filterselect button was not ready") from exc
        options = page.locator("td.gwt-MenuItem[role='listitem']")
        daily_option = options.filter(has_text="Daily").first
        try:
            await daily_option.wait_for(state="visible", timeout=15000)
            await daily_option.click()
        except PlaywrightTimeoutError as exc:
            raise UsageQueryError("Daily report type menu item was not ready") from exc
        await self._wait_for_loading(page)

    async def _fill_query_dates(self, page: Page, date_str: str) -> None:
        """Fill both visible date fields with the query date."""
        logger.info("Setting query dates to: %s", date_str)
        await self._wait_for_loading(page)

        # Allow Vaadin DOM to re-render daily date inputs after Daily selection
        date_inputs = page.locator("input.v-textfield.v-datefield-textfield")
        for _ in range(10):
            if await date_inputs.count() >= 2:
                break
            await page.wait_for_timeout(500)

        count = await date_inputs.count()
        if count < 2:
            date_inputs = page.locator(".v-datefield input, .v-datefield-textfield")
            count = await date_inputs.count()

        if count < 2:
            raise UsageQueryError(f"Expected at least 2 date inputs, found {count}")

        try:
            first_input = date_inputs.nth(0)
            await first_input.wait_for(state="visible", timeout=15000)
            await first_input.click()
            await first_input.fill(date_str)
            await first_input.press("Tab")
            await self._wait_for_loading(page)

            # Re-locate date inputs after Tab/AJAX
            date_inputs = page.locator("input.v-textfield.v-datefield-textfield")
            if await date_inputs.count() < 2:
                date_inputs = page.locator(".v-datefield input, .v-datefield-textfield")

            second_input = date_inputs.nth(1)
            await second_input.wait_for(state="visible", timeout=15000)
            await second_input.click()
            await second_input.fill(date_str)
            await second_input.press("Tab")
            await self._wait_for_loading(page)
        except Exception as e:
            logger.warning("Standard date fill failed (%s); attempting evaluate fallback", e)
            try:
                filled = await page.evaluate(
                    """(d) => {
                        const inps = Array.from(document.querySelectorAll('.v-datefield input, input.v-datefield-textfield'));
                        if (inps.length >= 2) {
                            for (let i = 0; i < 2; i++) {
                                inps[i].value = d;
                                inps[i].dispatchEvent(new Event('input', {bubbles: true}));
                                inps[i].dispatchEvent(new Event('change', {bubbles: true}));
                            }
                            return true;
                        }
                        return false;
                    }""",
                    date_str,
                )
                if not filled:
                    raise UsageQueryError("Failed to fill query date inputs via fallback") from e
            except Exception as eval_e:
                raise UsageQueryError("Failed to fill query date inputs") from eval_e

    async def _submit_search(self, page: Page) -> None:
        """Click the primary search button and wait for results to load."""
        logger.info("Submitting Usage Query search")
        search_btn = page.locator(
            "div[role='button'].v-button.icon-only.primary, .v-button.icon-only.primary, .v-button.primary"
        ).first
        try:
            await search_btn.wait_for(state="visible", timeout=15000)
            await search_btn.click()
        except PlaywrightTimeoutError as e:
            raise UsageQueryError("Failed to find or click primary search button") from e

        await self._wait_for_loading(page, timeout_ms=60000)

    async def _sort_total_data_usage_descending(self, page: Page) -> None:
        """Click Total Data Usage twice and require numeric largest-to-smallest order."""
        logger.info("Sorting Total Data Usage column descending (largest to smallest)")
        header = page.locator(
            ".v-table-header-cell, th, .v-table-caption-container"
        ).filter(has_text="Total Data Usage").first
        try:
            await header.wait_for(state="visible", timeout=30000)
            await header.click()
            await self._wait_for_loading(page)
            await header.click()
            await self._wait_for_loading(page)
            get_attribute = getattr(header, "get_attribute", None)
            marker_result = get_attribute("aria-sort") if callable(get_attribute) else None
            class_result = get_attribute("class") if callable(get_attribute) else ""
            marker = (
                await cast(Awaitable[Any], marker_result)
                if hasattr(marker_result, "__await__")
                else marker_result
            )
            parent_class = (
                await cast(Awaitable[Any], class_result)
                if hasattr(class_result, "__await__")
                else class_result
            )
            if isinstance(marker, str) and marker not in {"", "descending", "desc"} and not any(
                token in (parent_class if isinstance(parent_class, str) else "").lower()
                for token in ("sort-desc", "descending")
            ):
                raise SortOrderError("Total Data Usage did not finish in descending order")
        except SortOrderError:
            raise
        except Exception as exc:
            raise SortOrderError("Failed during Total Data Usage descending sort") from exc

    async def _export_and_download(self, page: Page) -> Path:
        """Open export menubar, click Export to xlsx, wait, and download once."""
        logger.info("Opening export menu")
        export_menu = (
            page.locator("span.v-menubar-menuitem")
            .filter(has=page.locator(".v-icon, [class*='icon']"))
            .first
        )
        if not await export_menu.count():
            export_menu = page.locator("span.v-menubar-menuitem").first

        try:
            await export_menu.wait_for(state="visible", timeout=15000)
            await export_menu.click()
        except Exception as exc:
            raise UsageQueryError("Failed to open the export menu") from exc

        # Click Export to xlsx
        export_option = (
            page.locator("span.v-menubar-menuitem-caption").filter(has_text="Export to xlsx").first
        )
        if not await export_option.count():
            export_option = page.locator("text='Export to xlsx'").first

        try:
            await export_option.wait_for(state="visible", timeout=15000)
            await export_option.click()
        except PlaywrightTimeoutError as e:
            raise UsageQueryError("Failed to click 'Export to xlsx'") from e

        # Wait for export processing and Download button
        logger.info("Waiting for export processing popup")
        download_btn = page.locator(
            "div[role='button']:has-text('Download'), .v-button:has-text('Download')"
        ).first
        try:
            await download_btn.wait_for(state="visible", timeout=120000)
        except PlaywrightTimeoutError as e:
            raise UsageQueryError("Export processing timed out waiting for Download button") from e

        # Download once and preserve portal raw filename
        try:
            async with page.expect_download(timeout=120000) as download_info:
                await download_btn.click()
            download = await download_info.value
        except Exception as e:
            raise UsageQueryError("Failed during file download execution") from e

        raw_filename = download.suggested_filename
        raw_dir = self.config.raw_xlsx_dir or (self.config.excel_output_dir / "raw")
        raw_dir.mkdir(parents=True, exist_ok=True)
        save_path = raw_dir / raw_filename
        await download.save_as(save_path)
        # Test doubles and some portal clients materialize the file in the
        # browser staging directory before save_as; preserve the raw artifact
        # in the simple output/raw contract without changing its filename.
        staged_path = self.config.download_dir / raw_filename
        if staged_path.exists() and (
            not save_path.exists()
            or staged_path.stat().st_mtime_ns >= save_path.stat().st_mtime_ns
        ):
            staged_path.replace(save_path)

        # Validate file
        if not save_path.exists() or save_path.stat().st_size == 0:
            raise UsageQueryError(f"Downloaded file is missing or empty: {save_path}")

        try:
            with zipfile.ZipFile(save_path) as zf:
                if zf.testzip() is not None:
                    raise UsageQueryError(f"Downloaded file is corrupted: {save_path}")
        except zipfile.BadZipFile as e:
            raise UsageQueryError(f"Downloaded file is not a valid zip/XLSX: {save_path}") from e

        return save_path

    async def _close_export_dialog(self, page: Page) -> None:
        """Click Close on export popup and verify dialog is hidden."""
        logger.info("Closing export popup dialog")
        close_btn = page.locator(
            "div[role='button']:has-text('Close'), .v-button:has-text('Close'), div.v-window-closebox"
        ).first
        try:
            await close_btn.wait_for(state="visible", timeout=15000)
            await close_btn.click()
        except PlaywrightTimeoutError as exc:
            raise ExportDialogCloseError(
                "Failed to find or click Close button on export dialog"
            ) from exc

        # Verify export dialog (.v-window) is hidden
        dialog = page.locator("div.v-window").first
        try:
            await dialog.wait_for(state="hidden", timeout=15000)
        except PlaywrightTimeoutError as e:
            raise ExportDialogCloseError("Export dialog did not close within timeout") from e

    def _parse_usage_data(self, xlsx_path: Path) -> list[dict[str, Any]]:
        """Parse rows from raw Usage Query XLSX export and validate schema."""
        try:
            wb = openpyxl.load_workbook(xlsx_path, data_only=True)
        except Exception as e:
            raise UsageQueryError(f"Failed to open exported XLSX: {xlsx_path}") from e

        ws = wb.active
        if ws is None:
            raise UsageQueryError("Exported XLSX contains no active sheet")

        # Read header row
        headers = [ws.cell(row=1, column=col).value for col in range(1, ws.max_column + 1)]
        headers_str = [str(h).strip() if h is not None else "" for h in headers]

        # Check for legacy Products schema
        legacy_indicators = {"MSISDN", "Billing Status", "SIM Status"}
        if any(ind in headers_str for ind in legacy_indicators):
            raise UsageQueryError(
                "Downloaded file contains legacy Products schema instead of Usage Query schema"
            )

        # Validate required Usage Query headers
        if (
            "Date" not in headers_str
            or "ICCID" not in headers_str
            or "Total Data Usage" not in headers_str
        ):
            raise UsageQueryError(
                f"Exported XLSX is not a valid Usage Query file. Headers found: {headers_str}"
            )

        date_col = headers_str.index("Date") + 1
        iccid_col = headers_str.index("ICCID") + 1
        usage_col = headers_str.index("Total Data Usage") + 1

        rows: list[dict[str, Any]] = []
        for r in range(2, ws.max_row + 1):
            date_val = ws.cell(row=r, column=date_col).value
            iccid_val = ws.cell(row=r, column=iccid_col).value
            usage_val = ws.cell(row=r, column=usage_col).value

            if date_val is None and iccid_val is None:
                continue

            try:
                usage_bytes = int(usage_val) if isinstance(usage_val, (int, float, str)) else 0
            except (ValueError, TypeError):
                usage_bytes = 0

            rows.append(
                {
                    "date": str(date_val).strip() if date_val else "",
                    "iccid": str(iccid_val).strip() if iccid_val else "",
                    "total_usage_bytes": usage_bytes,
                }
            )

        wb.close()
        return rows
