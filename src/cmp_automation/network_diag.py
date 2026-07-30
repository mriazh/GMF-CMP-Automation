"""Sanitized, opt-in network metadata diagnostic for the export-confirm flow.

Purpose
-------
Investigate why the Confirm dialog sometimes never appears after the "To xlsx"
click (observed live on 2026-08-14). The diagnostic records *metadata only*:
HTTP method, sanitized host/path (query, fragment, and userinfo stripped),
resource type, response status, relative timing, and the set of network
events observed per request.

Safety guarantees
-----------------
- Never reads or persists request/response *bodies*, headers, cookies, POST
  data, credentials, OTPs, or customer data. No raw HAR is produced.
- Failure text from ``requestfailed`` is reduced to a known error code
  (``net::*`` / ``NS_ERROR_*``) or the literal ``<failed>``; the raw message
  is never recorded.
- The diagnostic is **opt-in** (``--diagnose-export``). It does not change
  workflow behavior: no retries, no re-clicks, no extra requests.
- Bounded: at most ``MAX_REQUESTS`` records and ``MAX_SUMMARY_CHARS``
  characters in the summary.
"""

import logging
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from playwright.async_api import Page, Request, Response

logger = logging.getLogger(__name__)

MAX_REQUESTS = 100
MAX_SUMMARY_CHARS = 4000

# Recognized network error codes. Raw failure text (which can embed URLs,
# tokens, or credentials) is never recorded - only a matched code.
_FAILURE_CODE_RE = re.compile(r"(net::[A-Z0-9_]+|NS_ERROR_[A-Z0-9_]+)")


def _sanitize_url(url: str) -> str:
    """Return ``scheme://host/path`` with query, fragment, and userinfo removed.

    ``urlsplit`` lowercases the host and excludes userinfo automatically.
    Query strings and fragments can carry tokens/secrets and are dropped
    entirely; the path is preserved.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable>"
    scheme = parts.scheme
    host = parts.hostname or ""
    path = parts.path or "/"
    if scheme:
        return f"{scheme}://{host}{path}"
    return f"{host}{path}"


def _sanitize_failure(failure: str | None) -> str | None:
    """Reduce a request-failure message to a known error code.

    The raw message can embed the destination URL (and anything in it), so
    only a matched ``net::*`` / ``NS_ERROR_*`` code is kept; anything else
    becomes the literal ``<failed>``.
    """
    if not failure:
        return None
    match = _FAILURE_CODE_RE.search(failure)
    return match.group(1) if match else "<failed>"


@dataclass
class _RequestRecord:
    """Sanitized metadata for a single network request."""

    index: int
    method: str
    url: str
    resource_type: str
    events: list[str] = field(default_factory=list)
    status: int | None = None
    start_ms: float | None = None
    end_ms: float | None = None
    failure: str | None = None

    @property
    def duration_ms(self) -> float | None:
        if self.start_ms is None or self.end_ms is None:
            return None
        return self.end_ms - self.start_ms


class NetworkDiag:
    """Passive, sanitized listener for network metadata on a page.

    Attach before the action under investigation (e.g. the "To xlsx" click)
    and detach afterwards; ``summary()`` then describes what the server
    actually did - nothing fired, a request was sent but no dialog came back,
    the request failed, or the response was merely late.
    """

    def __init__(self) -> None:
        # Keyed by the request object itself (not ``id(request)``): Playwright
        # request objects can be short-lived, and Python reuses freed object
        # ids, which would let a later request silently overwrite an earlier
        # record. Keeping the object as the key also holds a strong reference
        # so the record survives until detach.
        self._records: dict[Request, _RequestRecord] = {}
        self._page: Page | None = None
        self._t0: float | None = None
        self._next_index = 1
        self._truncated = False

    # -- lifecycle --------------------------------------------------------

    def attach(self, page: Page) -> None:
        """Start listening on ``page``. Calling twice is a no-op."""
        if self._page is not None:
            return
        self._page = page
        self._t0 = time.monotonic()
        self._records = {}
        self._next_index = 1
        self._truncated = False
        page.on("request", self._on_request)
        page.on("response", self._on_response)
        page.on("requestfinished", self._on_requestfinished)
        page.on("requestfailed", self._on_requestfailed)
        logger.debug("Network diagnostic attached (export-click investigation)")

    def detach(self) -> None:
        """Stop listening. Safe to call when never attached."""
        page = self._page
        if page is None:
            return
        page.remove_listener("request", self._on_request)
        page.remove_listener("response", self._on_response)
        page.remove_listener("requestfinished", self._on_requestfinished)
        page.remove_listener("requestfailed", self._on_requestfailed)
        self._page = None
        logger.debug("Network diagnostic detached")

    # -- event handlers ---------------------------------------------------

    def _on_request(self, request: Request) -> None:
        if self._page is None:
            return
        if len(self._records) >= MAX_REQUESTS:
            self._truncated = True
            return
        record = _RequestRecord(
            index=self._next_index,
            method=request.method or "?",
            url=_sanitize_url(request.url),
            resource_type=request.resource_type or "?",
        )
        self._next_index += 1
        record.start_ms = self._relative_ms()
        record.events.append("request")
        self._records[request] = record

    def _on_response(self, response: Response) -> None:
        record = self._records.get(response.request)
        if record is None:
            return
        record.status = response.status
        record.events.append("response")

    def _on_requestfinished(self, request: Request) -> None:
        record = self._records.get(request)
        if record is None:
            return
        record.end_ms = self._relative_ms()
        record.events.append("finished")

    def _on_requestfailed(self, request: Request) -> None:
        record = self._records.get(request)
        if record is None:
            return
        record.end_ms = self._relative_ms()
        record.failure = _sanitize_failure(request.failure)
        record.events.append("failed")

    def _relative_ms(self) -> float:
        if self._t0 is None:
            return 0.0
        return (time.monotonic() - self._t0) * 1000.0

    # -- output -----------------------------------------------------------

    def summary(self) -> str:
        """Return a bounded, sanitized description of observed network events."""
        if not self._records:
            return "requests=0 (no network activity observed)"
        parts = [f"requests={len(self._records)}"]
        # Truncation note goes up front: the per-request lines below are
        # clipped by MAX_SUMMARY_CHARS, so a trailing note could be lost.
        if self._truncated:
            parts.append(f"(truncated at {MAX_REQUESTS} requests)")
        for record in self._records.values():
            timing = self._format_timing(record)
            status = f"status={record.status}" if record.status is not None else "status=none"
            failure = f"failure={record.failure}" if record.failure else ""
            parts.append(
                "#{index} {method} {url} ({resource_type}) "
                "events=[{events}] {status} {timing} {failure}".format(
                    index=record.index,
                    method=record.method,
                    url=record.url,
                    resource_type=record.resource_type,
                    events=",".join(record.events),
                    status=status,
                    timing=timing,
                    failure=failure,
                ).strip()
            )
        summary = " | ".join(parts)
        return summary[:MAX_SUMMARY_CHARS]

    @staticmethod
    def _format_timing(record: _RequestRecord) -> str:
        if record.start_ms is None:
            return "timing=?"
        start = f"start=+{record.start_ms:.0f}ms"
        if record.end_ms is None:
            return f"{start} (no completion event)"
        duration = record.duration_ms
        duration_text = f"{duration:.0f}ms" if duration is not None else "?"
        return f"{start} end=+{record.end_ms:.0f}ms dur={duration_text}"
