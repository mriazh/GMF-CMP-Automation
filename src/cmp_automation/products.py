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
from .network_diag import NetworkDiag
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
    # Safe structural diagnostic for the confirm dialog state: reports only
    # element counts and visibility flags - never element text.
    _CONFIRM_DIAGNOSTIC_JS = """() => {
  const isVisible = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const count = (sel) => document.querySelectorAll(sel).length;
  const visibleCount = (sel) =>
    Array.from(document.querySelectorAll(sel)).filter(isVisible).length;
  return {
    confirmButtonTotal: count('#confirmdialog-ok-button'),
    confirmButtonVisible: visibleCount('#confirmdialog-ok-button'),
    windows: count('.v-window'),
    windowsVisible: visibleCount('.v-window'),
    dialogs: count('[role="dialog"]'),
    dialogsVisible: visibleCount('[role="dialog"]'),
    confirmDialogs: count('.v-confirm-dialog, .confirmdialog'),
    confirmDialogsVisible: visibleCount('.v-confirm-dialog, .confirmdialog')
  };
}"""

    # Safe structural diagnostic for the export submenu state: reports only
    # element counts and visibility flags - never element text. Uses a
    # real-CSS variant of the caption selector (no Playwright-only
    # ``:has-text()``) so it can run inside the page.
    #
    # The ``popupContainer`` field is the key false-positive detector: a
    # visible caption that belongs to a static/hidden Vaadin template
    # satisfies the caption check while the actual interactive submenu
    # popup (``.v-menubar-popup``) never opened. When
    # ``captionVisible>0`` but ``popupContainer=0``, the submenu is
    # definitely not open and any click on the caption is a no-op.
    _MENU_DIAGNOSTIC_JS = """() => {
  const isVisible = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const count = (sel) => document.querySelectorAll(sel).length;
  const visibleCount = (sel) =>
    Array.from(document.querySelectorAll(sel)).filter(isVisible).length;
  const captionSel = 'span.v-menubar-menuitem-caption:has(span.v-icon.IcoMoon-Lindua)';
  const popupSel = '.v-menubar-popup, .v-menubar-submenu, [role="menu"]';
  return {
    captionTotal: count(captionSel),
    captionVisible: visibleCount(captionSel),
    popupContainer: count(popupSel),
    popupContainerVisible: visibleCount(popupSel),
    menus: count('.v-menubar-popup, .v-menubar-submenu, [role="menu"]'),
    menusVisible: visibleCount('.v-menubar-popup, .v-menubar-submenu, [role="menu"]'),
    windows: count('.v-window'),
    windowsVisible: visibleCount('.v-window'),
    dialogs: count('[role="dialog"]'),
    dialogsVisible: visibleCount('[role="dialog"]')
  };
}"""

    DOWNLOAD_BUTTON_SELECTORS = [
        '[role="button"]:has-text("Download")',
        'button:has-text("Download")',
        'a:has-text("Download")',
        '.v-button:has-text("Download")',
    ]
    # Bounded single window for the actual file download. The 15-second
    # visibility bound above stays untouched; this covers only the ``download``
    # event itself, which can lag well beyond 60s on a slow portal (observed
    # live). The Download control is clicked exactly once: a re-click while the
    # first server-side export may still be running would start a second
    # export and produce a duplicate download, so the click is never repeated.
    DOWNLOAD_TIMEOUT_MS = 120000
    # Bounded retries for the exact "To xlsx" submenu selection. Vaadin
    # re-renders the export submenu while it is being interacted with (the
    # exact selector can be visible one moment and detached the next), so the
    # primary selection is retried in bounded attempts before any fallback.
    XLSX_SELECT_ATTEMPTS = 3
    # Bounded re-wait budget for the confirm dialog. The dialog is a pure
    # server response to the "To xlsx" click - no re-click happens, so
    # waiting again cannot double-submit anything. On a degraded portal the
    # dialog can take well over 30s to render (observed live).
    CONFIRM_WAIT_TIMEOUT_MS = 30000
    CONFIRM_WAIT_ATTEMPTS = 2
    CLOSE_BUTTON_SELECTORS = [
        'button:has-text("Close")',
        'button:has-text("Tutup")',
        '[role="dialog"] button:has-text("Close")',
        ".v-window-closebutton",
    ]
    # Bounded retries for closing the export popup. The dialog can take a
    # moment to become closable after the download completes (observed as the
    # "Could not find close button for export popup" warning), so the close
    # is retried with a short delay. Closing is best-effort: the export
    # itself already succeeded, so failure is only a warning.
    CLOSE_MAX_ATTEMPTS = 3
    CLOSE_RETRY_DELAY_MS = 1500

    def __init__(self, config: Config, diagnose_export: bool = False):
        self.config = config
        # Opt-in diagnostic only: passively records sanitized network metadata
        # across the export-menu open → "To xlsx" click → Confirm wait
        # (see ``NetworkDiag``).  It never changes workflow behavior - no
        # retries, no re-clicks, no extra requests - and is inactive unless
        # ``--diagnose-export`` is passed.
        self.diagnose_export = diagnose_export
        self._network_diag: NetworkDiag | None = None

    def _popup(self, page: Page) -> Locator:
        """Return visible export dialogs/windows only."""
        return page.locator('.v-window:visible, [role="dialog"]:visible').last

    async def export_products(self, page: Page) -> Path:
        """Export products table to XLSX and return the downloaded file path."""
        logger.info("Starting products export")

        await self._navigate_to_products(page)
        await self._wait_for_table(page)
        await self._sort_by_billing_status(page)
        # Opt-in: attach the sanitized network listener before the export
        # menu is opened so network traffic across the full export-menu →
        # "To xlsx" → Confirm sequence is captured — including the menu-
        # opening click itself, which can fail when the server does not
        # respond.  Detached and summarized after the Confirm stage —
        # including on failure — so the diagnostic can distinguish
        # no-request / request-but-no-dialog / failed / late-response.
        if self.diagnose_export:
            self._network_diag = NetworkDiag()
            self._network_diag.attach(page)
        try:
            await self._open_export_menu(page)
            await self._select_xlsx_export(page)
            await self._confirm_export(page)
        finally:
            if self._network_diag is not None:
                self._network_diag.detach()
                logger.info(
                    "Export-click network diagnostic: %s",
                    self._network_diag.summary(),
                )
                self._network_diag = None
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

        Vaadin renders hidden template/duplicate menu items, so the visible
        filter must come before ``.first``: otherwise ``.first`` can select a
        hidden match while another matching control is the visible one.

        After the caption wait succeeds, a *visible* popup-container check
        verifies the submenu is genuinely open.  A static Vaadin template
        caption can satisfy ``wait_for(visible=True)`` while no interactive
        popup exists — the check rejects this false positive early with a
        clear ``ProductsExportError`` before any XLSX selection is attempted.
        """
        logger.debug("Opening export menu")
        for selector in self.EXPORT_MENU_SELECTORS:
            try:
                menu_item = page.locator(selector).filter(visible=True).first
                await menu_item.wait_for(state="visible", timeout=10000)
                await menu_item.click()
                logger.debug("Opened export menu using selector: %s", selector)
                try:
                    submenu = (
                        page.locator(self.EXPORT_XLSX_SELECTORS[0])
                        .filter(visible=True)
                        .first
                    )
                    # The submenu renders after a server round-trip; on a slow
                    # portal (observed live at > 10s) this needs the same
                    # bounded 30s budget as the confirm dialog.
                    await submenu.wait_for(state="visible", timeout=30000)
                except PlaywrightTimeoutError as e:
                    raise ProductsExportError(
                        "Export submenu (To xlsx) did not become visible"
                    ) from e
                # Verify a *visible* submenu container is actually open,
                # not just a static caption element.  Vaadin renders hidden
                # template menu items whose captions pass the CSS visibility
                # check but belong to no interactive popup — clicking them
                # is a no-op.  We require at least one *visible* popup
                # container (``.v-menubar-popup``) so a hidden/off-screen
                # template cannot satisfy the guard.
                popup_count = await page.locator(
                    '.v-menubar-popup, .v-menubar-submenu, [role="menu"]'
                ).filter(visible=True).count()
                if popup_count == 0:
                    diag = await self._menu_dom_diagnostic(page)
                    raise ProductsExportError(
                        "Export submenu (To xlsx) caption is visible but "
                        "no submenu container is open",
                        diag,
                    )
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

    async def _run_dom_diagnostic(self, page: Page, js: str) -> str:
        """Run a bounded structural diagnostic script and format its counts.

        Reports only counts and visibility flags - never element text - so
        SIM numbers, credentials, or menu labels cannot leak into logs.
        """
        try:
            info = await page.evaluate(js)
        except Exception:
            return "Unable to retrieve DOM diagnostic"
        if not isinstance(info, dict):
            return "Unable to retrieve DOM diagnostic"
        parts = [f"{k}={v}" for k, v in info.items()]
        return " | ".join(parts) if parts else "Unable to retrieve DOM diagnostic"

    async def _menu_dom_diagnostic(self, page: Page) -> str:
        """Collect a bounded structural diagnostic of the export submenu state."""
        return await self._run_dom_diagnostic(page, self._MENU_DIAGNOSTIC_JS)

    async def _confirm_dom_diagnostic(self, page: Page) -> str:
        """Collect a bounded structural diagnostic of the confirm dialog state."""
        return await self._run_dom_diagnostic(page, self._CONFIRM_DIAGNOSTIC_JS)

    async def _select_xlsx_export(self, page: Page) -> None:
        """Select 'To xlsx' from the export submenu.

        The primary selector targets the exact Vaadin menubar submenu caption
        (``IcoMoon-Lindua`` icon + "To xlsx" text). The submenu is an overlay,
        not a ``.v-window`` or ``[role="dialog"]``, so it is searched from the
        page and waited for until visible. Vaadin re-renders the submenu while
        it is being interacted with, so the primary selection is retried in
        ``XLSX_SELECT_ATTEMPTS`` bounded attempts (fresh locator each time)
        before generic fallbacks (searched from the visible popup) run.
        """
        logger.debug("Selecting XLSX export")
        primary = self.EXPORT_XLSX_SELECTORS[0]
        for attempt in range(1, self.XLSX_SELECT_ATTEMPTS + 1):
            try:
                item = (
                    page.locator(primary)
                    .filter(visible=True)
                    .first
                )
                await item.wait_for(state="visible", timeout=10000)
                await self._click_menu_item(item)
                logger.debug(
                    "Selected XLSX export using selector: %s (attempt %d/%d)",
                    primary,
                    attempt,
                    self.XLSX_SELECT_ATTEMPTS,
                )
                return
            except Exception:
                logger.debug(
                    "Primary XLSX selector not usable (attempt %d/%d); menu diagnostic: %s",
                    attempt,
                    self.XLSX_SELECT_ATTEMPTS,
                    await self._menu_dom_diagnostic(page),
                )
        for selector in self.EXPORT_XLSX_SELECTORS[1:]:
            try:
                item = (
                    self._popup(page)
                    .locator(selector)
                    .filter(visible=True)
                    .first
                )
                if await item.count() > 0:
                    await self._click_menu_item(item)
                    logger.debug("Selected XLSX export using selector: %s", selector)
                    return
            except Exception:
                continue
        diag = await self._menu_dom_diagnostic(page)
        raise ProductsExportError("Could not find XLSX export option", diag)

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
            # The dialog renders after a server round-trip; on a slow portal
            # this can exceed a single window (observed live), so the wait is
            # re-run in bounded attempts - waiting again is safe because no
            # click happens while waiting.
            for wait_attempt in range(1, self.CONFIRM_WAIT_ATTEMPTS + 1):
                try:
                    await primary_button.wait_for(
                        state="visible", timeout=self.CONFIRM_WAIT_TIMEOUT_MS
                    )
                    break
                except PlaywrightTimeoutError:
                    if wait_attempt < self.CONFIRM_WAIT_ATTEMPTS:
                        logger.debug(
                            "Confirm button not visible in %d ms (attempt %d/%d); dialog diagnostic: %s",
                            self.CONFIRM_WAIT_TIMEOUT_MS,
                            wait_attempt,
                            self.CONFIRM_WAIT_ATTEMPTS,
                            await self._confirm_dom_diagnostic(page),
                        )
                    else:
                        logger.debug(
                            "Confirm button not visible after %d attempts; dialog diagnostic: %s",
                            self.CONFIRM_WAIT_ATTEMPTS,
                            await self._confirm_dom_diagnostic(page),
                        )
                        button = None
            if button is not None:
                await button.click()
                logger.debug("Confirmed export using selector: %s", primary)
                return
            for selector in self.CONFIRM_DIALOG_SELECTORS[1:]:
                try:
                    item = (
                        self._popup(page)
                        .locator(selector)
                        .filter(visible=True)
                        .first
                    )
                    if await item.count() > 0:
                        await item.click()
                        logger.debug("Confirmed export using selector: %s", selector)
                        return
                except Exception:
                    continue
            diag = await self._confirm_dom_diagnostic(page)
            raise ProductsExportError(
                "Export confirmation dialog or Confirm control was not found", diag
            )
        except ProductsExportError:
            raise
        except Exception as e:
            raise ProductsExportError("Failed to confirm export", str(e)) from e

    async def _wait_for_download(self, page: Page) -> Path:
        """Wait for the live Download control and download the file once.

        The control wait is bounded to 15 seconds: the exact role-based
        Download selector is awaited directly with a single 15000 ms
        visibility wait. No sequential processing-popup or per-selector
        waits run first, so the post-Confirm wait can never balloon toward
        two minutes. If the button does not appear in time, ``DownloadError``
        is raised.

        The actual download operation is excluded from that bound: the
        ``download`` event is awaited within one bounded
        ``DOWNLOAD_TIMEOUT_MS`` window. The Download control is clicked
        exactly once - a re-click while the first server-side export may
        still be running would start a second export (duplicate download),
        so the click is never repeated. If the event does not arrive in
        time, ``DownloadError`` is raised instead.
        """
        logger.debug("Waiting for Download control and file download")

        download_button = (
            page.locator(self.DOWNLOAD_BUTTON_SELECTORS[0])
            .filter(visible=True)
            .first
        )
        try:
            await download_button.wait_for(state="visible", timeout=15000)
        except PlaywrightTimeoutError as e:
            raise DownloadError(
                "Download button did not appear within 15 seconds after Confirm"
            ) from e
        logger.debug("Download control visible after processing")

        # Exactly one click. A second click while the first server-side
        # export may still be running would trigger a duplicate download,
        # so the click is never retried - the single bounded wait below is
        # the only window the download event gets.
        try:
            async with page.expect_download(
                timeout=self.DOWNLOAD_TIMEOUT_MS
            ) as download_info:
                await download_button.click()
        except PlaywrightTimeoutError as e:
            raise DownloadError(
                "Download did not complete within the bounded window",
                f"timeout_ms={self.DOWNLOAD_TIMEOUT_MS}",
            ) from e

        download = await download_info.value
        suggested_filename = download.suggested_filename

        if not suggested_filename.lower().endswith(".xlsx"):
            logger.warning(
                "Unexpected download filename: %s", suggested_filename
            )

        # Generate timestamp-based filename
        final_path = generate_export_filename(
            datetime.now(self.config.get_timezone()), self.config.download_dir
        )
        suffix = 1
        while final_path.exists():
            final_path = final_path.with_name(
                f"{final_path.stem}_{suffix}{final_path.suffix}"
            )
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
        """Close the export popup after download (best-effort).

        The dialog may not be closable yet immediately after the download
        completes, so the close is retried in ``CLOSE_MAX_ATTEMPTS`` bounded
        attempts with a short delay. Only visible controls are considered -
        Vaadin renders hidden template duplicates (the same re-render
        behavior as the export menu), so ``filter(visible=True)`` precedes
        ``.first``. Failure is only a warning: the export already succeeded.
        """
        logger.debug("Closing export popup")
        for attempt in range(1, self.CLOSE_MAX_ATTEMPTS + 1):
            for selector in self.CLOSE_BUTTON_SELECTORS:
                try:
                    button = (
                        self._popup(page)
                        .locator(selector)
                        .filter(visible=True)
                        .first
                    )
                    if await button.count() > 0:
                        await button.click()
                        logger.debug(
                            "Closed export popup using selector: %s (attempt %d/%d)",
                            selector,
                            attempt,
                            self.CLOSE_MAX_ATTEMPTS,
                        )
                        return
                except Exception:
                    continue
            if attempt < self.CLOSE_MAX_ATTEMPTS:
                logger.debug(
                    "Close button not found (attempt %d/%d); retrying",
                    attempt,
                    self.CLOSE_MAX_ATTEMPTS,
                )
                await page.wait_for_timeout(self.CLOSE_RETRY_DELAY_MS)
        logger.warning("Could not find close button for export popup")
