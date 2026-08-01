"""Sanitized, opt-in diagnostic for the post-OTP authentication stage.

Purpose
-------
Investigate why the portal sometimes never reaches the approved Products URL
after the OTP is submitted (observed live on 2026-08-14: after 3 x 30-second
verification windows and 2 reloads the page stayed at the CAS login URL with
``passwords=1, buttons=1, grids=0``). The diagnostic records *metadata only*:

- relative timing (per event, from attach);
- URL state **category** only (``cas-login`` / ``portal-root`` / ``products`` /
  ``dashboard`` / ``other`` / ``unavailable``) - never the raw URL, query
  string, or service URL;
- navigation/frame-navigation event types (with the URL state category);
- network request/response metadata via :class:`NetworkDiag` (sanitized
  ``scheme://host/path``, method, resource type, status, finished/failed);
- structural DOM counts only (never element text or body HTML).

Safety guarantees
-----------------
- Never records: OTP values, credentials, mailbox content, request/response
  bodies, headers/cookies, POST data, query strings, raw service URLs, raw
  exception messages, page text, or body HTML.
- The diagnostic is **opt-in** (``--diagnose-auth``). It does not change
  workflow behavior: no retries, no reloads, no OTP resubmission.
- Bounded: capped URL states / navigation events / DOM samples, and a capped
  summary string.
"""

import logging
import time
from urllib.parse import urlsplit

from playwright.async_api import Frame, Page

from .network_diag import NetworkDiag

logger = logging.getLogger(__name__)

MAX_URL_STATES = 50
MAX_NAV_EVENTS = 50
MAX_DOM_SAMPLES = 10
MAX_SUMMARY_CHARS = 4000

# Structural DOM counts only: no textContent/innerText/innerHTML/.value, so
# credentials, OTPs, or arbitrary page body text can never leak.
DOM_COUNTS_JS = """() => ({
  inputs: document.querySelectorAll('input').length,
  passwords: document.querySelectorAll('input[type="password"]').length,
  buttons: document.querySelectorAll('button').length,
  forms: document.querySelectorAll('form').length,
  grids: document.querySelectorAll('[role="grid"], .v-grid').length,
  windows: document.querySelectorAll('.v-window').length,
  dialogs: document.querySelectorAll('[role="dialog"]').length
})"""


def classify_url_state(url: object, approved_host: str | None) -> str:
    """Classify a URL into a safe category - never the raw URL.

    Only the category is ever recorded; query strings, fragments, and service
    parameters are dropped entirely. Categories:

    - ``products`` / ``dashboard``: exact portal fragment reached;
    - ``cas-login``: the CAS login/token page (path contains ``cas`` or ends
      in ``login``) on the approved host;
    - ``portal-root``: approved host at the root path with no known fragment
      (the SPA before/after mount);
    - ``other``: approved host with an unrecognized path, a different host, or
      an unparseable URL;
    - ``unavailable``: empty/non-string input (e.g. a failed URL read).
    """
    if not isinstance(url, str) or not url:
        return "unavailable"
    try:
        parts = urlsplit(url)
    except ValueError:
        return "other"
    host = parts.hostname or ""
    if approved_host is None or host != approved_host:
        return "other"
    fragment = parts.fragment or ""
    if fragment == "!products":
        return "products"
    if fragment == "!dashboard":
        return "dashboard"
    path = parts.path or ""
    if "cas" in path.lower() or path.rstrip("/").endswith("login"):
        return "cas-login"
    if path in ("", "/"):
        return "portal-root"
    return "other"


class AuthDiag:
    """Passive, sanitized listener for the post-OTP authentication stage.

    Attach immediately before ``_submit_token()`` and detach after
    ``_verify_authentication()`` completes or fails. ``summary()`` then
    describes the URL-state transitions, navigation events, structural DOM
    counts, and network metadata observed during the wait - enough to tell
    whether the OTP submission fired a request, whether the redirect happened,
    and where the SPA stopped.
    """

    def __init__(self, approved_host: str | None) -> None:
        self._approved_host = approved_host
        self._network = NetworkDiag()
        self._page: Page | None = None
        self._t0: float | None = None
        self._url_states: list[str] = []
        self._nav_events: list[str] = []
        self._dom_samples: list[str] = []
        self._truncated = False

    # -- lifecycle --------------------------------------------------------

    async def attach(self, page: Page) -> None:
        """Start listening on ``page``. Calling twice is a no-op."""
        if self._page is not None:
            return
        self._page = page
        self._t0 = time.monotonic()
        self._network.attach(page)
        page.on("framenavigated", self._on_framenavigated)
        self._record_url_state(page)
        await self._sample_dom(page)
        logger.debug("Auth diagnostic attached (post-OTP investigation)")

    async def detach(self) -> None:
        """Stop listening and capture the final state. Safe when not attached.

        Each cleanup step is independently guarded so that a failure in
        one (e.g. page closing mid-cleanup) does not prevent the others
        from executing.  Cleanup exceptions are silently ignored — they
        cannot fix a broken page and must not mask the original workflow
        exception from ``CMPLogin.login()``'s finally block.
        """
        page = self._page
        if page is None:
            return
        self._record_url_state(page)
        await self._sample_dom(page)
        try:
            page.remove_listener("framenavigated", self._on_framenavigated)
        except Exception:
            pass
        try:
            self._network.detach()
        except Exception:
            pass
        self._page = None
        logger.debug("Auth diagnostic detached")

    # -- event handlers ---------------------------------------------------

    def _on_framenavigated(self, frame: Frame) -> None:
        if self._page is None:
            return
        if len(self._nav_events) >= MAX_NAV_EVENTS:
            self._truncated = True
            return
        category = classify_url_state(frame.url, self._approved_host)
        is_main = frame is self._page.main_frame
        self._nav_events.append(
            f"framenavigated:{'main' if is_main else 'frame'}:{category}"
        )
        self._append_url_state(category)

    # -- sampling ---------------------------------------------------------

    def _record_url_state(self, page: Page) -> None:
        try:
            url = page.url
        except Exception:
            # A closed/crashed page yields the safe category only.
            state = "unavailable"
        else:
            state = classify_url_state(url, self._approved_host)
        self._append_url_state(state)

    def _append_url_state(self, state: str) -> None:
        if len(self._url_states) >= MAX_URL_STATES:
            self._truncated = True
            return
        # Deduplicate consecutive identical states to keep the timeline terse.
        if not self._url_states or self._url_states[-1] != state:
            self._url_states.append(state)

    async def _sample_dom(self, page: Page) -> None:
        if len(self._dom_samples) >= MAX_DOM_SAMPLES:
            self._truncated = True
            return
        try:
            counts = await page.evaluate(DOM_COUNTS_JS)
        except Exception:
            counts = None
        if not isinstance(counts, dict):
            self._dom_samples.append("<unavailable>")
            return
        parts = [f"{k}={v}" for k, v in counts.items() if isinstance(v, int)]
        self._dom_samples.append("{" + " ".join(parts) + "}" if parts else "<unavailable>")

    # -- output -----------------------------------------------------------

    def summary(self) -> str:
        """Return a bounded, sanitized description of the auth stage."""
        parts = []
        final_url = self._url_states[-1] if self._url_states else "unavailable"
        parts.append(f"final_url={final_url}")
        if self._truncated:
            parts.append("(truncated)")
        if self._url_states:
            parts.append("urls=[" + ",".join(self._url_states) + "]")
        if self._nav_events:
            parts.append("nav=[" + ",".join(self._nav_events) + "]")
        for i, sample in enumerate(self._dom_samples):
            parts.append(f"dom{i}={sample}")
        parts.append(self._network.summary())
        summary = " | ".join(parts)
        return summary[:MAX_SUMMARY_CHARS]
