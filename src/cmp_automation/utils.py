"""Utility functions for CMP Automation."""

import logging
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import Page

logger = logging.getLogger(__name__)


def extract_token_from_email_body(body: str, sender: str | None = None) -> str | None:
    r"""Extract the six-digit numeric CMP OTP from an email body.

    Aligned with the proven extraction logic in ``GMF-CMP-Monitor/otp.py``:
    CMP OTPs are exactly six numeric digits, so 8-digit and alphanumeric
    candidates are never accepted.

    Priority:
    1. A token explicitly labeled with the word "token" (case-insensitive),
       tolerating any non-digit markup (HTML tags, colons, whitespace) between
       the label and the digits, and requiring the six digits are not a prefix
       of a longer number: ``token[^\d]*(\d{6})(?!\d)``.
    2. Otherwise, a bare six-digit number, accepted only when exactly one such
       number exists in the body (fail closed on ambiguity).

    CSS values (e.g. ``300italic``), hex colors (e.g. ``0563C1``), dates, and
    alphanumeric strings never match because they are not six consecutive
    digits.

    Args:
        body: Email body text (plain text or HTML)
        sender: Optional sender email/name (kept for API compatibility)

    Returns:
        The six-digit token string if found, None otherwise.

    Security: token values or candidate values are never logged, even at
    DEBUG level; ambiguity is logged only as a safe candidate count.
    """
    if not body or not body.strip():
        return None

    # Labeled token: the word "token" followed by any non-digit markup, then
    # exactly six digits that are not part of a longer number (negative
    # lookahead). HTML tags/entities between label and digits are fine.
    token_match = re.search(r"token[^\d]*(\d{6})(?!\d)", body, re.IGNORECASE)
    if token_match:
        return token_match.group(1)

    # Bare six-digit numbers: accept only when exactly one exists in the body.
    matches: list[str] = re.findall(r"\b\d{6}\b", body)
    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        logger.debug(
            "Ambiguous token candidates in email body (candidate count=%d)",
            len(matches),
        )
    return None


def is_approved_portal_url(url: str, approved_url: str, fragment: str) -> bool:
    """Strict portal URL validation shared by the SPA navigation helpers.

    Requires HTTPS, the exact approved hostname (never a substring-only or
    untrusted-host check), a root path, and the exact ``!fragment``. Mirrors
    ``CMPLogin._is_portal_page`` so navigation and authentication agree on
    what counts as the approved portal page.
    """
    try:
        if not isinstance(url, str):
            return False
        parsed = urlparse(url)
        approved = urlparse(approved_url)
        if parsed.scheme != "https" or approved.scheme != "https":
            return False
        if approved.hostname is None or parsed.hostname != approved.hostname:
            return False
        # hostname alone would accept explicit-port tricks like :443/:8443.
        if parsed.port is not None:
            return False
        if parsed.path not in ("", "/"):
            return False
        return parsed.fragment == fragment
    except Exception:
        return False


async def window_location_href(page: Page) -> str | None:
    """Read the current SPA URL via ``window.location.href``, or None.

    SPA hash navigation may update ``window.location`` before ``page.url``;
    navigation and authentication checks use this as a fallback.
    """
    try:
        href = await page.evaluate("window.location.href")
        return href if isinstance(href, str) else None
    except Exception:
        return None


async def wait_for_portal_url(
    page: Page,
    is_target: Callable[[str], bool],
    deadline_ms: int = 30000,
    interval_ms: int = 2000,
) -> bool:
    """Short-poll until ``is_target`` accepts the page URL or SPA URL.

    The Vaadin SPA may settle on its hash slightly after ``domcontentloaded``;
    ``window.location.href`` is the fallback when ``page.url`` lags behind
    SPA hash navigation. Shared by the Products and Dashboard navigation
    paths, with the same poll budget as the authentication verification.
    """
    for _ in range(deadline_ms // interval_ms):
        if is_target(page.url):
            return True
        href = await window_location_href(page)
        if href is not None and is_target(href):
            return True
        await page.wait_for_timeout(interval_ms)
    return False


def generate_export_filename(timestamp: datetime, download_dir: Path) -> Path:
    """Generate the timestamped products export filename."""
    return download_dir / f"sim_export_{timestamp.strftime('%Y%m%d_%H%M%S')}.xlsx"
