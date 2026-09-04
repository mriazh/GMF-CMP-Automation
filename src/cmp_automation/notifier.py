"""Optional, best-effort WhatsApp lifecycle notifications via a GOWA gateway.

The adapter is deliberately small and isolated:

* Standard library ``urllib.request`` only, behind an injectable transport seam
  so unit tests never perform a real network call.
* No retries, no queue, and no durable store: one HTTP attempt per event.
* Every request is bounded by a short, configurable timeout.
* Notification failures never propagate to the caller and never change the
  pipeline result.

Logging is metadata-only (event name, outcome, HTTP status, redacted endpoint).
Message bodies, file paths, recipient JIDs, credentials, ICCIDs, OTPs, and raw
exception detail are never logged and never delivered. Messages are built from a
short allow-listed set of pipeline facts only, so a notification body is always
short and can never leak path-shaped data.
"""

import json
import logging
import re
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from .config import Config, notifications_configured

logger = logging.getLogger(__name__)

APP_NAME = "GMF CMP Automation"
SEND_PATH = "/send/message"
DEVICE_HEADER = "X-Device-Id"
MAX_MESSAGE_LENGTH = 400

EVENT_START = "START"
EVENT_SUCCESS = "SUCCESS"
EVENT_FAILED = "FAILED"

_LABELS = {
    EVENT_START: "started",
    EVENT_SUCCESS: "succeeded",
    EVENT_FAILED: "failed",
}
_ALLOWED_EVENTS = frozenset(_LABELS)
REDACTED = "[redacted]"

# Credential-bearing URL (``https://user:pass@host/path``).
_URL_CREDENTIALS_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*://[^\s/@]+(?::[^\s/@]*)?@")
# ``password=hunter2`` / ``otp: 482913`` style assignments.
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(pass(?:word|wd)?|pwd|secret|token|otp|api[_-]?key|apikey|credential|"
    r"authorization|bearer|device[_-]?id|cookie|session)"
    r"(\s*[=:]\s*|\s+)(\S+)"
)
# Standalone credential material (no key name in front of it).
_BEARER_TOKEN_RE = re.compile(r"(?i)\bbearer\s+\S+")
# ICCIDs (18-20 digits), OTPs (6 digits), and other long digit runs.
_LONG_DIGIT_RUN_RE = re.compile(r"\d{6,}")
# A generated workbook filename legitimately embeds a six-digit month stamp
# (``Daily-Data-Usage-M2M-202609.xlsx``, see ``excel_report.generate_report``).
# Only that exact ``-YYYYMM.`` shape is ever preserved, and only when a caller
# opts in via ``preserve_month_stamp``; every other digit run in the text is
# still redacted, so an ICCID or OTP can never ride along in a name.
_MONTH_STAMP_RE = re.compile(r"(?<=-)\d{6}(?=\.)")


class Transport(Protocol):
    """Injectable HTTP seam used by the notifier.

    Implementations receive the fully built endpoint, the UTF-8 JSON body, the
    request headers, and the timeout in seconds, and return the HTTP status
    code. Raising is allowed: the notifier converts any failure into a warning.
    """

    def __call__(
        self, url: str, body: bytes, headers: Mapping[str, str], timeout: float
    ) -> int: ...


@dataclass(frozen=True)
class NotifierConfig:
    """Resolved GOWA delivery settings (never contains secrets by design)."""

    base_url: str
    target_jid: str
    device_id: str | None = None
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        """Normalize the endpoint so join logic never doubles a separator."""
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))

    @classmethod
    def from_config(cls, config: Config) -> "NotifierConfig | None":
        """Build settings from application config, or None when not configured."""
        if not notifications_configured(config):
            logger.debug("WhatsApp notifications are not configured; skipping notification")
            return None
        assert config.gowa_base_url is not None  # narrowed by notifications_configured
        assert config.gowa_target_jid is not None
        return cls(
            base_url=config.gowa_base_url.rstrip("/"),
            target_jid=config.gowa_target_jid,
            device_id=config.gowa_device_id,
            timeout_seconds=config.gowa_timeout_seconds,
        )


def redact_url(url: str) -> str:
    """Return a loggable URL with userinfo, query, and fragment removed."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return REDACTED
    host = parsed.hostname or ""
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _redact_digit_runs(text: str, *, preserve_month_stamp: bool = False) -> str:
    """Redact long digit runs, optionally protecting ``-YYYYMM.`` month stamps.

    Allow-listed stamps are swapped for a placeholder first, so they survive
    even though every other digit run is redacted. The placeholder is restored
    afterwards and cannot be forged from user data because it contains no
    digits, so it is never itself matched by ``_LONG_DIGIT_RUN_RE``.
    """
    if not preserve_month_stamp:
        return _LONG_DIGIT_RUN_RE.sub(REDACTED, text)

    preserved: list[str] = []

    def _protect(match: re.Match[str]) -> str:
        preserved.append(match.group(0))
        return f"\x00stamp{len(preserved) - 1}\x00"

    guarded = _MONTH_STAMP_RE.sub(_protect, text)
    redacted = _LONG_DIGIT_RUN_RE.sub(REDACTED, guarded)
    for index, stamp in enumerate(preserved):
        redacted = redacted.replace(f"\x00stamp{index}\x00", stamp)
    return redacted


def sanitize_message_text(
    text: str,
    max_length: int = MAX_MESSAGE_LENGTH,
    *,
    redact_digit_runs: bool = True,
    preserve_month_stamp: bool = False,
) -> str:
    """Redact and bound a single message fragment.

    Removes credential-bearing URLs, secret-looking assignments, and bearer
    tokens, then (unless ``redact_digit_runs`` is disabled) long digit runs
    (ICCID/OTP shaped) so notification text can never carry sensitive material.
    Whitespace is collapsed and the result is truncated to ``max_length``.

    ``preserve_month_stamp`` keeps an allow-listed ``-YYYYMM.`` month stamp
    intact while redacting every *other* digit run. It is opt-in for callers
    that must display a generated workbook filename, whose month stamp
    identifies the workbook. Notification bodies never need it, because file
    paths are deliberately excluded from messages. It never disables redaction
    for the surrounding text.

    ``redact_digit_runs=False`` remains available for callers that have already
    redacted their fragment and only want credential redaction plus bounding.
    """
    if not text:
        return ""
    cleaned = _URL_CREDENTIALS_RE.sub(REDACTED, text)
    cleaned = _BEARER_TOKEN_RE.sub(REDACTED, cleaned)
    cleaned = _SECRET_ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}{REDACTED}", cleaned)
    if redact_digit_runs:
        cleaned = _redact_digit_runs(cleaned, preserve_month_stamp=preserve_month_stamp)
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > max_length:
        cleaned = cleaned[: max_length - 3].rstrip() + "..."
    return cleaned


def safe_error_category(error: BaseException) -> str:
    """Return a sanitized failure category: the exception class name only."""
    category = sanitize_message_text(type(error).__name__, max_length=60)
    return category or "UnknownError"


def _format_elapsed(elapsed_seconds: float) -> str:
    """Render an elapsed duration in whole seconds."""
    return f"{int(elapsed_seconds)}s"


def format_event_message(
    event: str,
    *,
    mode: str | None = None,
    dates: Sequence[str] | None = None,
    elapsed_seconds: float | None = None,
    record_count: int | None = None,
    error_category: str | None = None,
) -> str:
    """Compose a bounded, redacted notification body from allow-listed fields.

    Only the application name, event, mode, target date(s), elapsed duration,
    record count, and a sanitized failure category are ever included. File
    paths are deliberately excluded so every message stays short and readable.
    Everything else is dropped.
    """
    if event not in _ALLOWED_EVENTS:
        raise ValueError(f"Unsupported notification event: {event}")

    parts = [f"[{APP_NAME}] {event}"]
    if mode:
        parts.append(f"mode={sanitize_message_text(mode, max_length=32)}")

    target_dates = [d for d in (dates or []) if d]
    if len(target_dates) == 1:
        parts.append(f"date={sanitize_message_text(target_dates[0], max_length=32)}")
    elif len(target_dates) > 1:
        first = sanitize_message_text(target_dates[0], max_length=32)
        last = sanitize_message_text(target_dates[-1], max_length=32)
        parts.append(f"dates={first}..{last}")

    if elapsed_seconds is not None:
        parts.append(f"elapsed={_format_elapsed(elapsed_seconds)}")
    if record_count is not None:
        parts.append(f"records={int(record_count)}")
    if error_category:
        parts.append(f"error={sanitize_message_text(error_category, max_length=60)}")

    # Every fragment above is individually redacted. The final assembly pass
    # re-applies credential redaction and digit-run redaction as a
    # defence-in-depth backstop, and bounds the final length.
    return sanitize_message_text(" | ".join(parts))


def http_post_json(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> int:
    """Default transport: a single bounded ``POST`` through ``urllib.request``."""
    request = urllib.request.Request(url, data=body, method="POST", headers=dict(headers))
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return int(getattr(response, "status", 0) or 0)


class Notifier:
    """Dispatch bounded, redacted lifecycle notifications to a GOWA gateway."""

    def __init__(self, config: NotifierConfig, *, transport: Transport | None = None) -> None:
        self._config = config
        self._transport = transport

    @property
    def target_jid(self) -> str:
        """Recipient JID (never logged and never included in message bodies)."""
        return self._config.target_jid

    def send_event(
        self,
        event: str,
        *,
        mode: str | None = None,
        dates: Sequence[str] | None = None,
        elapsed_seconds: float | None = None,
        record_count: int | None = None,
        error_category: str | None = None,
    ) -> bool:
        """Send one notification. Returns whether delivery was accepted.

        This never raises: transport errors and non-2xx responses are warned
        about and reported as ``False`` so the caller keeps its own outcome.
        """
        message = format_event_message(
            event,
            mode=mode,
            dates=dates,
            elapsed_seconds=elapsed_seconds,
            record_count=record_count,
            error_category=error_category,
        )
        return self.send_message(message, event=event)

    def send_message(self, message: str, *, event: str = "MESSAGE") -> bool:
        """Deliver an already-formatted message; failures stay non-fatal."""
        url = f"{self._config.base_url}{SEND_PATH}"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._config.device_id:
            headers[DEVICE_HEADER] = self._config.device_id
        body = json.dumps({"phone": self._config.target_jid, "message": message}).encode("utf-8")

        transport = self._transport if self._transport is not None else http_post_json
        try:
            status = transport(url, body, headers, self._config.timeout_seconds)
        except urllib.error.HTTPError as exc:
            logger.warning(
                "Notification event=%s outcome=rejected status=%s endpoint=%s",
                event,
                exc.code,
                redact_url(url),
            )
            return False
        except Exception as exc:
            logger.warning(
                "Notification event=%s outcome=error error=%s endpoint=%s",
                event,
                type(exc).__name__,
                redact_url(url),
            )
            return False

        if not 200 <= status < 300:
            logger.warning(
                "Notification event=%s outcome=rejected status=%s endpoint=%s",
                event,
                status,
                redact_url(url),
            )
            return False

        logger.info(
            "Notification event=%s outcome=sent status=%s endpoint=%s",
            event,
            status,
            redact_url(url),
        )
        return True


def build_notifier(config: Config, *, transport: Transport | None = None) -> Notifier | None:
    """Create a notifier, or return None when notifications are not configured."""
    notifier_config = NotifierConfig.from_config(config)
    if notifier_config is None:
        return None
    return Notifier(notifier_config, transport=transport)
