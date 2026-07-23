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
from .utils import generate_export_filename, is_approved_portal_url, wait_for_portal_url

logger = logging.getLogger(__name__)


class ProductsExporter:
    """Handles products table export to XLSX."""

    # Selectors for products page (Vaadin 7 table, Vaadin 8 Grid, Vaadin 14+ grid)
    PRODUCTS_TABLE_SELECTORS = [
        "table.v-table",
        ".v-table-table",
        '[role="grid"]',
        ".v-grid",
        "vaadin-grid",
        ".v-grid-table",
        "div.v-table",
        ".product-table",
    ]

    # Safe structural DOM diagnostic: reports only tag names, ids, classes,
    # roles, and element counts. Never reads element text, so phone/SIM
    # numbers, credentials, OTPs, or cell content cannot leak into logs.
    _DOM_DIAGNOSTIC_JS = """() => {
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
    candidates: summarize('__TABLE_CANDIDATES__', 10),
    counts: {
      tables: count('table'),
      grids: count('[role="grid"]'),
      vGrids: count('.v-grid'),
      vaadinGrids: count('vaadin-grid'),
      vCsslayouts: count('[class*="v-csslayout"]'),
      windows: count('.v-window'),
      dialogs: count('[role="dialog"]')
    }
  };
}"""
    BILLING_STATUS_HEADER_SELECTORS = [
        '.v-grid-column-header-content:has-text("Billing Status")',
        'th:has-text("Billing Status")',
        'th:has-text("Billing")',
        '[data-column="billingStatus"]',
        'th[aria-label*="Billing" i]',
    ]
    EXPORT_MENU_SELECTORS = [
        "span.v-menubar-menuitem:has(span.v-icon.IcoMoon-Ultimate)",
        "#simExportMenu",
        '[id*="simExportMenu"]',
        'button:has-text("Export")',
        'button[aria-label*="Export" i]',
        ".export-button",
        '[data-testid="export-menu"]',
    ]
    EXPORT_XLSX_SELECTORS = [
        'span.v-menubar-menuitem-caption:has(span.v-icon.IcoMoon-Lindua):has-text("To xlsx")',
        'menuitem:has-text("To xlsx")',
        'menuitem:has-text("XLSX")',
        'menuitem:has-text("Excel")',
        '[role="menuitem"]:has-text("xlsx" i)',
        '[role="menuitem"]:has-text("Excel" i)',
    ]
    CONFIRM_DIALOG_SELECTORS = [
        "#confirmdialog-ok-button",
        '[role="button"]:has-text("Confirm")',
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
        '[role="button"]:has-text("Download")',
        'button:has-text("Download")',
        'a:has-text("Download")',
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

    def _is_products_url(self, url: str) -> bool:
        """Strict check that ``url`` is the exact configured Products URL.

        Requires HTTPS, the exact approved hostname (never substring-only),
        a root path, and the exact ``!products`` fragment. Mirrors the
        validation in ``CMPLogin`` so navigation and authentication agree.
        """
        return is_approved_portal_url(url, self.config.cmp_products_url, "!products")

    async def _navigate_to_products(self, page: Page) -> None:
        """Ensure the page is at the exact configured Products URL.

        The Vaadin SPA keeps background requests active, so ``networkidle``
        is not a valid readiness signal; ``domcontentloaded`` plus the
        products table wait that follows are the readiness conditions. When
        the page is already at the exact Products URL, navigation is skipped
        entirely (login ends there).
        """
        logger.debug("Navigating to products page")
        if self._is_products_url(page.url):
            logger.debug("Already at exact products URL; skipping navigation")
            return
        try:
            await page.goto(self.config.cmp_products_url, wait_until="domcontentloaded")
        except PlaywrightTimeoutError as e:
            raise ProductsExportError("Timeout navigating to products page", str(e)) from e
        if not await wait_for_portal_url(page, self._is_products_url):
            raise ProductsExportError(
                "Products URL was not reached after navigation",
                f"URL: {page.url}",
            )

    def _combined_table_selector(self) -> str:
        """Combine all table candidates into a single CSS selector list."""
        return ", ".join(self.PRODUCTS_TABLE_SELECTORS)

    def _dom_diagnostic_js(self) -> str:
        """Build the structural diagnostic script with the current candidates.

        The candidate list is injected at runtime so the diagnostic never
        drifts out of sync with ``PRODUCTS_TABLE_SELECTORS``.
        """
        return self._DOM_DIAGNOSTIC_JS.replace(
            "__TABLE_CANDIDATES__", self._combined_table_selector()
        )

    async def _find_visible_table_selector(self, page: Page) -> str | None:
        """Identify which candidate selector is currently visible, if any."""
        for selector in self.PRODUCTS_TABLE_SELECTORS:
            try:
                if await page.locator(selector).first.is_visible():
                    return selector
            except Exception:
                continue
        return None

    async def _wait_for_table(self, page: Page) -> None:
        """Wait for the products table to load (single 30s budget).

        All candidates are combined into one CSS selector list, so the whole
        table-wait costs at most 30 seconds regardless of candidate count
        (never 30s per selector). If no candidate becomes visible, a safe
        structural DOM diagnostic is attached to the raised error.
        """
        logger.debug("Waiting for products table")
        try:
            # Filter visible first so a hidden candidate earlier in DOM order
            # cannot shadow a visible one (e.g. Vaadin hidden template tables).
            await page.locator(self._combined_table_selector()).filter(visible=True).first.wait_for(
                state="visible", timeout=30000
            )
        except PlaywrightTimeoutError:
            dom_summary = await self._get_dom_summary(page)
            raise ProductsExportError(
                "Products table did not load",
                f"URL: {page.url}, DOM: {dom_summary}",
            )
        matched = await self._find_visible_table_selector(page)
        if matched:
            logger.debug("Products table found with selector: %s", matched)
        else:
            logger.debug("Products table found (matched combined selector list)")

    async def _get_dom_summary(self, page: Page) -> str:
        """Collect a bounded, structural-only DOM diagnostic.

        The in-page script reports only tag names, ids, classes, roles, and
        element counts for the table candidates and generic structural
        landmarks. Element text content is never read, so phone/SIM numbers,
        credentials, OTPs, or arbitrary body text cannot appear.
        """
        try:
            info = await page.evaluate(self._dom_diagnostic_js())
        except Exception:
            return "Unable to retrieve DOM summary"
        if not isinstance(info, dict):
            return "Unable to retrieve DOM summary"
        parts = []
        candidates = info.get("candidates")
        if isinstance(candidates, list):
            entries = [str(c) for c in candidates if isinstance(c, str)]
            if entries:
                parts.append(f"candidates[{len(entries)}]: " + " | ".join(entries[:10]))
            else:
                parts.append("candidates: none")
        counts = info.get("counts")
        if isinstance(counts, dict):
            parts.append("counts: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
        if not parts:
            return "Unable to retrieve DOM summary"
        summary = " | ".join(parts)
        return summary[:2000]

    async def _sort_by_billing_status(self, page: Page) -> None:
        """Sort Billing Status and verify a real sort-state change.

        The live Vaadin 8 grid expresses sort state on the header ``<th>``
        element as the ``sort-asc``/``sort-desc`` classes and never sets
        ``aria-sort``. Sort-state detection therefore reads the parent ``th``
        class (and tolerates ``aria-sort`` where another grid provides it).

        The click can occasionally be lost or queued while the grid is still
        serving its initial data push, so the click is retried (bounded) until
        a sort state is observed. Clicking again from any state still lands on
        a sorted state, so retries are idempotent for detection.
        """
        logger.debug("Sorting by Billing Status")
        for table_selector in self.PRODUCTS_TABLE_SELECTORS:
            table = page.locator(table_selector).first
            if not await table.count():
                continue
            for selector in self.BILLING_STATUS_HEADER_SELECTORS:
                header = table.locator(selector).first
                if not await header.count():
                    continue
                th = header.locator("xpath=..")
                if await self._th_is_sorted(th):
                    return
                for _ in range(3):
                    await header.click()
                    for _ in range(20):
                        if await self._th_is_sorted(th):
                            return
                        await page.wait_for_timeout(250)
                raise ProductsExportError("Billing Status sort did not complete")
        raise ProductsExportError("Could not find Billing Status column header")

    async def _th_is_sorted(self, th: Locator) -> bool:
        """Return True when the header ``<th>`` shows a sort state.

        Detects both the Vaadin 8 ``sort-asc``/``sort-desc`` classes and an
        ``aria-sort`` attribute where another grid provides one.
        """
        try:
            cls = await th.get_attribute("class") or ""
            aria = await th.get_attribute("aria-sort")
        except Exception:
            return False
        return (
            aria in {"ascending", "descending"}
            or "sort-asc" in cls
            or "sort-desc" in cls
        )

    async def _open_export_menu(self, page: Page) -> None:
        """Open the export dropdown menu.

        The primary target is the Vaadin menubar item whose submenu is an
        overlay, not a ``.v-window`` or ``[role="dialog"]``, so after the
        click we wait for the exact visible "To xlsx" caption instead.
        """
        logger.debug("Opening export menu")
        for selector in self.EXPORT_MENU_SELECTORS:
            try:
                button = page.locator(selector).first
                if await button.count() > 0:
                    await button.click()
                    logger.debug("Opened export menu using selector: %s", selector)
                    try:
                        await page.locator(self.EXPORT_XLSX_SELECTORS[0]).first.wait_for(
                            state="visible", timeout=10000
                        )
                    except PlaywrightTimeoutError as e:
                        raise ProductsExportError(
                            "Export submenu (To xlsx) did not become visible"
                        ) from e
                    return
            except ProductsExportError:
                # A real diagnostic (the exact submenu was clicked but never
                # appeared) must propagate - not be swallowed by the
                # per-selector fallback loop.
                raise
            except Exception:
                continue
        raise ProductsExportError("Could not find export menu button")

    async def _click_menu_item(self, item: Locator) -> None:
        """Click a Vaadin menubar submenu item robustly.

        The export submenu re-renders while the mouse is in motion: the
        caption never satisfies actionability stability, so a normal click
        times out (and by then the menu has usually closed). A force click
        lands immediately and is proven against the live submenu. A synthetic
        click is the last resort - it is immune to DOM re-rendering.
        """
        try:
            await item.click(force=True)
        except Exception:
            await item.evaluate("el => el.click()")

    async def _select_xlsx_export(self, page: Page) -> None:
        """Select 'To xlsx' from the export submenu.

        The primary selector targets the exact Vaadin menubar submenu caption
        (``IcoMoon-Lindua`` icon + "To xlsx" text). The submenu is an overlay,
        not a ``.v-window`` or ``[role="dialog"]``, so it is searched from the
        page and waited for until visible. Generic fallbacks are then searched
        from the visible popup as before.
        """
        logger.debug("Selecting XLSX export")
        primary = self.EXPORT_XLSX_SELECTORS[0]
        try:
            item = page.locator(primary).first
            await item.wait_for(state="visible", timeout=10000)
            await self._click_menu_item(item)
            logger.debug("Selected XLSX export using selector: %s", primary)
            return
        except Exception:
            pass
        for selector in self.EXPORT_XLSX_SELECTORS[1:]:
            try:
                item = self._popup(page).locator(selector).first
                if await item.count() > 0:
                    await self._click_menu_item(item)
                    logger.debug("Selected XLSX export using selector: %s", selector)
                    return
            except Exception:
                continue
        raise ProductsExportError("Could not find XLSX export option")

    async def _confirm_export(self, page: Page) -> None:
        """Handle the Vaadin ConfirmDialog.

        The dialog renders after a server round-trip following the "To xlsx"
        click, so the exact OK button is waited for (``count()`` alone does
        not wait for render). The primary selector is the exact live id
        ``#confirmdialog-ok-button`` (a ``div[role=button]``); generic
        fallbacks are searched from the visible popup.
        """
        logger.debug("Confirming export")
        primary = self.CONFIRM_DIALOG_SELECTORS[0]
        try:
            primary_button = page.locator(primary).first
            button: Locator | None = primary_button
            try:
                await primary_button.wait_for(state="visible", timeout=15000)
            except PlaywrightTimeoutError:
                button = None
            if button is not None:
                await button.click()
                logger.debug("Confirmed export using selector: %s", primary)
                return
            for selector in self.CONFIRM_DIALOG_SELECTORS[1:]:
                try:
                    item = self._popup(page).locator(selector).first
                    if await item.count() > 0:
                        await item.click()
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

        # Wait for processing popup. Each candidate carries its own
        # has-text marker (e.g. ``.v-window:has-text("Processing")``) and the
        # popup element itself matches it, so candidates are searched from the
        # page - not from a fixed ``_popup()`` locator.
        for selector in self.PROCESSING_POPUP_SELECTORS:
            try:
                await page.locator(selector).first.wait_for(
                    state="visible", timeout=30000
                )
                logger.debug("Processing popup detected with selector: %s", selector)
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
