"""Products export functionality for CMP Portal."""

import logging
import zipfile
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook
from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .config import Config
from .exceptions import DownloadError, ProductsExportError
from .utils import generate_export_filename

logger = logging.getLogger(__name__)


class ProductsExporter:
    """Handles products table export to XLSX."""

    # Selectors for products page
    PRODUCTS_TABLE_SELECTORS = [
        "table.v-table",
        ".v-table-table",
        '[role="grid"]',
        ".product-table",
    ]
    BILLING_STATUS_HEADER_SELECTORS = [
        'th:has-text("Billing Status")',
        'th:has-text("Billing")',
        '[data-column="billingStatus"]',
        'th[aria-label*="Billing" i]',
    ]
    EXPORT_MENU_SELECTORS = [
        "#simExportMenu",
        '[id*="simExportMenu"]',
        'button:has-text("Export")',
        'button[aria-label*="Export" i]',
        ".export-button",
        '[data-testid="export-menu"]',
    ]
    EXPORT_XLSX_SELECTORS = [
        'menuitem:has-text("To xlsx")',
        'menuitem:has-text("XLSX")',
        'menuitem:has-text("Excel")',
        '[role="menuitem"]:has-text("xlsx" i)',
        '[role="menuitem"]:has-text("Excel" i)',
    ]
    CONFIRM_DIALOG_SELECTORS = [
        'button:has-text("Confirm")',
        'button:has-text("OK")',
        'button:has-text("Yes")',
        '[role="dialog"] button:has-text("Confirm")',
        '.v-confirm-dialog button:has-text("Confirm")',
    ]
    PROCESSING_POPUP_SELECTORS = [
        '.v-window:has-text("Processing")',
        '.v-window:has-text("Export")',
        '[role="dialog"]:has-text("Processing")',
        ".export-processing",
    ]
    DOWNLOAD_BUTTON_SELECTORS = [
        'button:has-text("Download")',
        'a:has-text("Download")',
        '[role="button"]:has-text("Download")',
        '.v-button:has-text("Download")',
    ]
    CLOSE_BUTTON_SELECTORS = [
        'button:has-text("Close")',
        'button:has-text("Tutup")',
        '[role="dialog"] button:has-text("Close")',
        ".v-window-closebutton",
    ]

    def __init__(self, config: Config):
        self.config = config

    def _popup(self, page: Page) -> Locator:
        """Return visible export dialogs/windows only."""
        return page.locator('.v-window:visible, [role="dialog"]:visible').last

    async def export_products(self, page: Page) -> Path:
        """Export products table to XLSX and return the downloaded file path."""
        logger.info("Starting products export")

        await self._navigate_to_products(page)
        await self._wait_for_table(page)
        await self._sort_by_billing_status(page)
        await self._open_export_menu(page)
        await self._select_xlsx_export(page)
        await self._confirm_export(page)
        download_path = await self._wait_for_download(page)
        await self._close_export_popup(page)

        logger.info("Products export completed: %s", download_path)
        return download_path

    async def _navigate_to_products(self, page: Page) -> None:
        """Navigate to products page."""
        logger.debug("Navigating to products page")
        try:
            await page.goto(self.config.cmp_products_url, wait_until="networkidle")
        except PlaywrightTimeoutError as e:
            raise ProductsExportError("Timeout navigating to products page", str(e)) from e

    async def _wait_for_table(self, page: Page) -> None:
        """Wait for products table to load."""
        logger.debug("Waiting for products table")
        for selector in self.PRODUCTS_TABLE_SELECTORS:
            try:
                await page.locator(selector).first.wait_for(state="visible", timeout=30000)
                logger.debug("Products table found with selector: %s", selector)
                return
            except PlaywrightTimeoutError:
                continue
        raise ProductsExportError("Products table did not load")

    async def _sort_by_billing_status(self, page: Page) -> None:
        """Sort Billing Status once and verify a real sort-state change."""
        logger.debug("Sorting by Billing Status")
        for table_selector in self.PRODUCTS_TABLE_SELECTORS:
            table = page.locator(table_selector).first
            if not await table.count():
                continue
            for selector in self.BILLING_STATUS_HEADER_SELECTORS:
                header = table.locator(selector).first
                if not await header.count():
                    continue
                before = await header.get_attribute("aria-sort")
                before_class = await header.get_attribute("class") or ""
                if before in {"ascending", "descending"} or "sort-asc" in before_class or "sort-desc" in before_class:
                    return
                await header.click()
                for _ in range(40):
                    after = await header.get_attribute("aria-sort")
                    after_class = await header.get_attribute("class") or ""
                    if (after and after != before) or after in {"ascending", "descending"} or "sort-asc" in after_class or "sort-desc" in after_class:
                        return
                    await page.wait_for_timeout(250)
                raise ProductsExportError("Billing Status sort did not complete")
        raise ProductsExportError("Could not find Billing Status column header")

    async def _open_export_menu(self, page: Page) -> None:
        """Open the export dropdown menu."""
        logger.debug("Opening export menu")
        for selector in self.EXPORT_MENU_SELECTORS:
            try:
                button = page.locator(selector).first
                if await button.count() > 0:
                    await button.click()
                    try:
                        await page.locator('.v-window:visible, [role="dialog"]:visible').last.wait_for(state="visible", timeout=10000)
                    except PlaywrightTimeoutError as e:
                        raise ProductsExportError("Export popup did not become visible") from e
                    logger.debug("Opened export menu using selector: %s", selector)
                    return
            except Exception:
                continue
        raise ProductsExportError("Could not find export menu button")

    async def _select_xlsx_export(self, page: Page) -> None:
        """Select 'To xlsx' from export menu."""
        logger.debug("Selecting XLSX export")
        for selector in self.EXPORT_XLSX_SELECTORS:
            try:
                item = self._popup(page).locator(selector).first
                if await item.count() > 0:
                    await item.click()
                    logger.debug("Selected XLSX export using selector: %s", selector)
                    return
            except Exception:
                continue
        raise ProductsExportError("Could not find XLSX export option")

    async def _confirm_export(self, page: Page) -> None:
        """Handle confirmation dialog."""
        logger.debug("Confirming export")
        try:
            for selector in self.CONFIRM_DIALOG_SELECTORS:
                try:
                    button = self._popup(page).locator(selector).first
                    if await button.count() > 0:
                        await button.click()
                        logger.debug("Confirmed export using selector: %s", selector)
                        return
                except Exception:
                    continue
            raise ProductsExportError("Export confirmation dialog or Confirm control was not found")
        except ProductsExportError:
            raise
        except Exception as e:
            raise ProductsExportError("Failed to confirm export", str(e)) from e

    async def _wait_for_download(self, page: Page) -> Path:
        """Wait for processing to complete and download the file."""
        logger.debug("Waiting for export processing and download")

        # Wait for processing popup
        for selector in self.PROCESSING_POPUP_SELECTORS:
            try:
                await self._popup(page).wait_for(state="visible", timeout=30000)
                logger.debug("Processing popup detected")
                break
            except PlaywrightTimeoutError:
                continue

        # Wait for download button to become available
        download_button = None
        for selector in self.DOWNLOAD_BUTTON_SELECTORS:
            try:
                btn = self._popup(page).locator(selector).first
                await btn.wait_for(state="visible", timeout=60000)
                download_button = btn
                logger.debug("Download button available with selector: %s", selector)
                break
            except PlaywrightTimeoutError:
                continue

        if not download_button:
            # Fallback: wait a bit and try again
            await page.wait_for_timeout(10000)
            for selector in self.DOWNLOAD_BUTTON_SELECTORS:
                try:
                    btn = self._popup(page).locator(selector).first
                    if await btn.count() > 0:
                        download_button = btn
                        break
                except Exception:
                    continue

        if not download_button:
            raise DownloadError("Download button never became available")

        # Handle download
        async with page.expect_download(timeout=60000) as download_info:
            await download_button.click()

        download = await download_info.value
        suggested_filename = download.suggested_filename

        if not suggested_filename.lower().endswith(".xlsx"):
            logger.warning("Unexpected download filename: %s", suggested_filename)

        # Generate timestamp-based filename
        final_path = generate_export_filename(
            datetime.now(self.config.get_timezone()), self.config.download_dir
        )
        suffix = 1
        while final_path.exists():
            final_path = final_path.with_name(f"{final_path.stem}_{suffix}{final_path.suffix}")
            suffix += 1

        await download.save_as(final_path)
        if not final_path.exists() or final_path.stat().st_size == 0:
            raise DownloadError("Downloaded file is empty or missing")
        if not zipfile.is_zipfile(final_path):
            raise DownloadError("Downloaded file is not a valid XLSX archive")
        try:
            load_workbook(final_path, read_only=True).close()
        except Exception as e:
            raise DownloadError("Downloaded XLSX could not be opened") from e

        logger.info("File downloaded to: %s", final_path)
        return final_path

    async def _close_export_popup(self, page: Page) -> None:
        """Close the export popup after download."""
        logger.debug("Closing export popup")
        for selector in self.CLOSE_BUTTON_SELECTORS:
            try:
                button = self._popup(page).locator(selector).first
                if await button.count() > 0:
                    await button.click()
                    logger.debug("Closed export popup using selector: %s", selector)
                    return
            except Exception:
                continue
        logger.warning("Could not find close button for export popup")
