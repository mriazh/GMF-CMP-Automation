"""Tests for the best-effort WhatsApp lifecycle notifier.

Every test mocks the HTTP transport seam; no test may perform a real network call.
"""

import inspect
import json
import logging
import urllib.error
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from cmp_automation import notifier
from cmp_automation.config import Config, notifications_configured
from cmp_automation.exceptions import AuthenticationError, ConfigurationError
from cmp_automation.notifier import (
    APP_NAME,
    EVENT_FAILED,
    EVENT_START,
    EVENT_SUCCESS,
    MAX_MESSAGE_LENGTH,
    Notifier,
    NotifierConfig,
    _format_elapsed,
    build_notifier,
    format_event_message,
    http_post_json,
    redact_url,
    safe_error_category,
    sanitize_message_text,
)

DUMMY_BASE_URL = "https://gowa.example.invalid"
DUMMY_JID = "6280000000000@s.whatsapp.net"
DUMMY_DEVICE_ID = "dummy-device"


@dataclass
class RecordedCall:
    """A single captured transport invocation."""

    url: str
    payload: dict[str, object]
    headers: dict[str, str]
    timeout: float


@dataclass
class RecordingTransport:
    """Injected HTTP seam: records calls instead of performing HTTP."""

    status: int = 200
    error: BaseException | None = None
    calls: list[RecordedCall] = field(default_factory=list)

    def __call__(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> int:
        self.calls.append(
            RecordedCall(
                url=url,
                payload=json.loads(body.decode("utf-8")),
                headers=dict(headers),
                timeout=timeout,
            )
        )
        if self.error is not None:
            raise self.error
        return self.status


def make_config(tmp_path: Path, **overrides: object) -> Config:
    """Build a fully offline Config with notification defaults applied."""
    values: dict[str, object] = {
        "cmp_username": "user",
        "cmp_password": "pw",
        "gmf_email": "email@example.com",
        "gmf_password": "pw",
        "firefox_profile_dir": tmp_path / "profile",
        "download_dir": tmp_path / "downloads",
        "excel_output_dir": tmp_path / "reports",
        "excel_template_path": tmp_path / "template.xlsx",
    }
    values.update(overrides)
    return Config(**values)  # type: ignore[arg-type]


def make_notifier_config(**overrides: object) -> NotifierConfig:
    """Build a notifier config with dummy (non-routable) values."""
    values: dict[str, object] = {
        "base_url": DUMMY_BASE_URL,
        "target_jid": DUMMY_JID,
        "device_id": None,
        "timeout_seconds": 5.0,
    }
    values.update(overrides)
    return NotifierConfig(**values)  # type: ignore[arg-type]


class TestNotificationConfiguration:
    """Notifications stay off unless explicitly enabled and fully configured."""

    def test_disabled_by_default(self, tmp_path: Path) -> None:
        """A default config performs no notification dispatch."""
        config = make_config(tmp_path)
        assert config.whatsapp_notifications_enabled is False
        assert config.gowa_base_url is None
        assert config.gowa_target_jid is None
        assert config.gowa_device_id is None
        assert config.gowa_timeout_seconds == 5.0
        assert notifications_configured(config) is False

        transport = RecordingTransport()
        assert build_notifier(config, transport=transport) is None
        assert transport.calls == []

    def test_enabled_but_unconfigured_is_noop(self, tmp_path: Path) -> None:
        """Partial configuration never triggers a request."""
        partials = [
            {"gowa_base_url": DUMMY_BASE_URL},
            {"gowa_target_jid": DUMMY_JID},
            {"gowa_base_url": "", "gowa_target_jid": DUMMY_JID},
            {"gowa_base_url": DUMMY_BASE_URL, "gowa_target_jid": "   "},
        ]
        for overrides in partials:
            config = make_config(tmp_path, whatsapp_notifications_enabled=True, **overrides)
            assert notifications_configured(config) is False
            transport = RecordingTransport()
            assert build_notifier(config, transport=transport) is None
            assert transport.calls == []

    def test_enabled_and_configured_builds_notifier(self, tmp_path: Path) -> None:
        """Enabled plus base_url plus target_jid yields a notifier."""
        config = make_config(
            tmp_path,
            whatsapp_notifications_enabled=True,
            gowa_base_url=DUMMY_BASE_URL,
            gowa_target_jid=DUMMY_JID,
        )
        assert notifications_configured(config) is True
        built = build_notifier(config, transport=RecordingTransport())
        assert isinstance(built, Notifier)
        assert built.target_jid == DUMMY_JID


class TestNotConfiguredNotifierIsSilent:
    """An unconfigured notifier swallows everything without logging at INFO."""

    def test_missing_configuration_logs_debug_only(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Missing configuration is a debug-level no-op, never an error."""
        config = make_config(
            tmp_path, whatsapp_notifications_enabled=True, gowa_base_url=DUMMY_BASE_URL
        )
        transport = RecordingTransport()
        with caplog.at_level(logging.DEBUG, logger="cmp_automation.notifier"):
            assert build_notifier(config, transport=transport) is None
        assert "ERROR" not in caplog.text
        assert transport.calls == []


class TestSendEventRequestContract:
    """The GOWA request shape: URL, method, payload, headers, bounded timeout."""

    def test_single_post_request_with_expected_shape(self) -> None:
        """Exactly one POST to /send/message with a {phone, message} JSON body."""
        transport = RecordingTransport()
        client = Notifier(make_notifier_config(), transport=transport)

        sent = client.send_event(EVENT_START, mode="full", dates=["2026-09-16"])

        assert sent is True
        assert len(transport.calls) == 1
        call = transport.calls[0]
        assert call.url == f"{DUMMY_BASE_URL}/send/message"
        assert set(call.payload) == {"phone", "message"}
        assert call.payload["phone"] == DUMMY_JID
        assert isinstance(call.payload["message"], str)
        assert "START" in str(call.payload["message"])
        assert 0 < call.timeout <= 5.0

    def test_trailing_slash_in_base_url_is_normalized(self) -> None:
        """A trailing slash in the configured base URL is not doubled."""
        transport = RecordingTransport()
        client = Notifier(make_notifier_config(base_url=f"{DUMMY_BASE_URL}/"), transport=transport)
        client.send_event(EVENT_START, mode="scrape")
        assert transport.calls[0].url == f"{DUMMY_BASE_URL}/send/message"

    def test_device_id_header_present_when_configured(self) -> None:
        """X-Device-Id is sent when a device id is configured."""
        transport = RecordingTransport()
        client = Notifier(make_notifier_config(device_id=DUMMY_DEVICE_ID), transport=transport)
        client.send_event(EVENT_SUCCESS, mode="full")
        headers = {key.lower(): value for key, value in transport.calls[0].headers.items()}
        assert headers["x-device-id"] == DUMMY_DEVICE_ID

    def test_device_id_header_absent_when_not_configured(self) -> None:
        """X-Device-Id is omitted when no device id is configured."""
        transport = RecordingTransport()
        client = Notifier(make_notifier_config(), transport=transport)
        client.send_event(EVENT_SUCCESS, mode="full")
        headers = {key.lower(): value for key, value in transport.calls[0].headers.items()}
        assert "x-device-id" not in headers

    def test_timeout_is_configurable_and_bounded(self) -> None:
        """Every request carries the configured short timeout."""
        transport = RecordingTransport()
        client = Notifier(make_notifier_config(timeout_seconds=2.5), transport=transport)
        client.send_event(EVENT_START, mode="full")
        assert transport.calls[0].timeout == 2.5

    def test_notifier_from_config_uses_configured_timeout(self, tmp_path: Path) -> None:
        """NotifierConfig mirrors the application configuration."""
        config = make_config(
            tmp_path,
            whatsapp_notifications_enabled=True,
            gowa_base_url=DUMMY_BASE_URL,
            gowa_target_jid=DUMMY_JID,
            gowa_device_id=DUMMY_DEVICE_ID,
            gowa_timeout_seconds=3.0,
        )
        transport = RecordingTransport()
        client = build_notifier(config, transport=transport)
        assert client is not None
        client.send_event(EVENT_START, mode="full")
        assert transport.calls[0].timeout == 3.0


class TestDefaultTransport:
    """The stdlib transport uses urllib.request and is itself mockable."""

    def test_http_post_json_uses_post_with_timeout_and_body(self, monkeypatch) -> None:
        """The default transport issues a bounded POST through urllib.request."""
        captured: dict[str, object] = {}

        class FakeResponse:
            status = 200

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *exc: object) -> bool:
                return False

        def fake_urlopen(request, timeout=None):  # type: ignore[no-untyped-def]
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse()

        monkeypatch.setattr(notifier.urllib.request, "urlopen", fake_urlopen)

        status = http_post_json(
            f"{DUMMY_BASE_URL}/send/message",
            b'{"phone": "x", "message": "y"}',
            {"Content-Type": "application/json"},
            4.0,
        )

        request = captured["request"]
        assert status == 200
        assert captured["timeout"] == 4.0
        assert request.get_method() == "POST"
        assert request.full_url == f"{DUMMY_BASE_URL}/send/message"
        assert request.data == b'{"phone": "x", "message": "y"}'

    def test_send_event_uses_module_default_transport(self, monkeypatch) -> None:
        """Without an injected transport the module-level stdlib seam is used."""
        calls: list[tuple[str, bytes, dict[str, str], float]] = []

        def fake_post(url, body, headers, timeout):  # type: ignore[no-untyped-def]
            calls.append((url, body, dict(headers), timeout))
            return 200

        monkeypatch.setattr(notifier, "http_post_json", fake_post)
        client = Notifier(make_notifier_config())

        assert client.send_event(EVENT_START, mode="full") is True
        assert len(calls) == 1
        assert calls[0][0] == f"{DUMMY_BASE_URL}/send/message"


class TestTransportFailuresAreNonFatal:
    """Transport errors are warned about and swallowed."""

    def test_transport_exception_is_swallowed(self, caplog: pytest.LogCaptureFixture) -> None:
        """A raising transport returns False and never propagates."""
        transport = RecordingTransport(error=OSError("connection refused"))
        client = Notifier(make_notifier_config(), transport=transport)

        with caplog.at_level(logging.WARNING, logger="cmp_automation.notifier"):
            sent = client.send_event(EVENT_START, mode="full")

        assert sent is False
        assert "WARNING" in caplog.text
        assert len(transport.calls) == 1

    def test_non_2xx_response_is_swallowed(self, caplog: pytest.LogCaptureFixture) -> None:
        """A non-2xx status is warned about and treated as a failed send."""
        transport = RecordingTransport(status=500)
        client = Notifier(make_notifier_config(), transport=transport)

        with caplog.at_level(logging.WARNING, logger="cmp_automation.notifier"):
            sent = client.send_event(EVENT_START, mode="full")

        assert sent is False
        assert "500" in caplog.text

    def test_http_error_response_is_swallowed(self, caplog: pytest.LogCaptureFixture) -> None:
        """urllib HTTPError (4xx/5xx) never escapes the adapter."""
        transport = RecordingTransport(
            error=urllib.error.HTTPError(  # type: ignore[arg-type]
                url=f"{DUMMY_BASE_URL}/send/message",
                code=503,
                msg="Service Unavailable",
                hdrs=None,
                fp=None,
            )
        )
        client = Notifier(make_notifier_config(), transport=transport)

        with caplog.at_level(logging.WARNING, logger="cmp_automation.notifier"):
            sent = client.send_event(EVENT_START, mode="full")

        assert sent is False

    def test_logs_never_contain_message_body_or_recipient(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Failure logs carry metadata only, never body text or the recipient."""
        transport = RecordingTransport(error=OSError("connection refused"))
        client = Notifier(make_notifier_config(), transport=transport)

        with caplog.at_level(logging.DEBUG, logger="cmp_automation.notifier"):
            client.send_event(
                EVENT_SUCCESS,
                mode="full",
                dates=["2026-09-16"],
                record_count=34,
            )

        assert DUMMY_JID not in caplog.text
        assert "Daily-Data-Usage-M2M" not in caplog.text
        assert "records=34" not in caplog.text
        assert "output=" not in caplog.text


class TestMessageRedaction:
    """Notification text is bounded and redacted."""

    def test_start_message_contains_only_allow_listed_fields(self) -> None:
        """START messages carry app, event, mode, and target date."""
        message = format_event_message(EVENT_START, mode="full", dates=["2026-09-16"])
        assert message.startswith(f"[{APP_NAME}] START")
        assert "mode=full" in message
        assert "date=2026-09-16" in message

    def test_date_range_is_rendered_as_a_range(self) -> None:
        """A start/end range renders both boundaries."""
        message = format_event_message(EVENT_START, mode="full", dates=["2026-09-07", "2026-09-08"])
        assert "dates=2026-09-07..2026-09-08" in message

    def test_success_message_includes_elapsed_and_records_only(self) -> None:
        """SUCCESS messages carry elapsed time and record count, never a path."""
        message = format_event_message(
            EVENT_SUCCESS,
            mode="full",
            dates=["2026-09-16"],
            elapsed_seconds=95.4,
            record_count=34,
        )
        assert "SUCCESS" in message
        assert "elapsed=1m 35s" in message
        assert "records=34" in message
        assert "34" in message  # record count survives: it is allow-listed
        assert "output=" not in message
        assert ".xlsx" not in message
        assert "Daily-Data-Usage-M2M" not in message
        # Concise: one allow-listed fragment per pipeline fact, nothing else.
        assert message == (
            f"[{APP_NAME}] SUCCESS | mode=full | date=2026-09-16 | elapsed=1m 35s | records=34"
        )

    def test_failure_message_carries_only_a_safe_category(self) -> None:
        """FAILED messages never include raw exception text."""
        exc = AuthenticationError(
            "Login failed for ICCID 8991122334455667788 with OTP 482913 password=hunter2"
        )
        message = format_event_message(
            EVENT_FAILED,
            mode="scrape",
            dates=["2026-09-16"],
            elapsed_seconds=12.0,
            error_category=safe_error_category(exc),
        )
        assert "FAILED" in message
        assert "error=AuthenticationError" in message
        assert "8991122334455667788" not in message
        assert "482913" not in message
        assert "hunter2" not in message
        assert "Login failed" not in message

    def test_message_excludes_operational_secrets_even_if_supplied(self) -> None:
        """Hostile values are redacted before they reach the transport."""
        message = format_event_message(
            EVENT_FAILED,
            mode="full",
            dates=["2026-09-16"],
            error_category="ConfigurationError token=abc123456789 iccid=8991122334455667788",
        )
        assert "abc123456789" not in message
        assert "8991122334455667788" not in message
        assert "error=" in message

    def test_message_is_length_bounded(self) -> None:
        """Messages are truncated to a sane maximum length."""
        message = format_event_message(
            EVENT_FAILED,
            mode="full",
            dates=["2026-09-16"],
            error_category="E" * 5000,
        )
        assert len(message) <= MAX_MESSAGE_LENGTH

    def test_transport_payload_is_bounded(self) -> None:
        """The delivered payload respects the maximum length."""
        transport = RecordingTransport()
        client = Notifier(make_notifier_config(), transport=transport)
        client.send_event(EVENT_FAILED, mode="full", error_category="E" * 5000)
        assert len(str(transport.calls[0].payload["message"])) <= MAX_MESSAGE_LENGTH


class TestSanitizeHelpers:
    """Redaction helpers are directly provable."""

    @pytest.mark.parametrize(
        "text",
        [
            "ICCID 8991122334455667788",
            "otp 482913",
            "token=abc1234567890",
            "password: hunter2",
            "https://user:secret@gowa.example.invalid/send/message?apikey=zzz",
            "Bearer eyJhbGciOiJIUzI1NiJ9",
        ],
    )
    def test_sanitize_removes_sensitive_material(self, text: str) -> None:
        """Sensitive sequences never survive sanitisation."""
        sanitized = sanitize_message_text(text)
        assert "8991122334455667788" not in sanitized
        assert "482913" not in sanitized
        assert "abc1234567890" not in sanitized
        assert "hunter2" not in sanitized
        assert "secret@" not in sanitized
        assert "eyJhbGciOiJIUzI1NiJ9" not in sanitized
        assert "[redacted]" in sanitized

    def test_sanitize_keeps_allow_listed_dates(self) -> None:
        """Dates and small counts survive sanitisation."""
        sanitized = sanitize_message_text("date=2026-09-16 records=34 elapsed=95s")
        assert sanitized == "date=2026-09-16 records=34 elapsed=95s"

    def test_sanitize_keeps_allow_listed_month_stamp(self) -> None:
        """The generated workbook filename keeps its ``-YYYYMM.`` month stamp."""
        rendered = "output/reports/Daily-Data-Usage-M2M-202609.xlsx"
        assert sanitize_message_text(rendered, preserve_month_stamp=True) == rendered

    @pytest.mark.parametrize(
        "path",
        [
            "out/8991122334455667788.xlsx",
            "out/482913.xlsx",
            "out/6281234567890/report.xlsx",
            "out/report-20260915.xlsx",
            "output/Daily-Data-Usage-M2M-202609.xlsx",
            "out/8991122334455667788-Daily-Data-Usage-M2M-202609.xlsx",
        ],
    )
    def test_success_message_never_carries_an_output_path(self, path: str) -> None:
        """No output path, and nothing path-shaped, can reach a SUCCESS message.

        Regression test: output paths were once appended as ``output=...``, which
        could smuggle an ICCID, OTP or phone number into a notification even when
        digit redaction was misconfigured. ``format_event_message`` no longer
        accepts an output path at all, so every variant renders identically.
        """
        message = format_event_message(
            EVENT_SUCCESS,
            mode="full",
            dates=["2026-09-16"],
            elapsed_seconds=95.4,
            record_count=34,
        )
        assert "output=" not in message
        assert ".xlsx" not in message
        assert "/" not in message
        assert path not in message
        assert "202609" not in message
        for secret in ("8991122334455667788", "482913", "6281234567890", "20260915"):
            assert secret not in message

    def test_send_event_signature_has_no_output_path(self) -> None:
        """The notifier API no longer offers an output path, and sends a clean body."""
        assert "output_path" not in inspect.signature(Notifier.send_event).parameters
        assert "output_path" not in inspect.signature(format_event_message).parameters

        transport = RecordingTransport()
        client = Notifier(make_notifier_config(), transport=transport)
        client.send_event(
            EVENT_SUCCESS,
            mode="full",
            dates=["2026-09-16"],
            record_count=34,
        )

        delivered = str(transport.calls[0].payload["message"])
        assert "output=" not in delivered
        assert delivered.endswith("records=34")

    def test_sanitize_still_redacts_secrets_in_filenames(self) -> None:
        """Preserving a month stamp does not also skip credential redaction."""
        sanitized = sanitize_message_text(
            "output/token=abc1234567890/report.xlsx", preserve_month_stamp=True
        )
        assert "abc1234567890" not in sanitized

    def test_sanitize_truncates_to_max_length(self) -> None:
        """Sanitised text is bounded."""
        assert len(sanitize_message_text("a" * 5000)) <= MAX_MESSAGE_LENGTH

    def test_redact_url_drops_query_and_credentials(self) -> None:
        """Logged URLs never carry credentials, query, or fragment."""
        redacted = redact_url(
            "https://user:secret@gowa.example.invalid:8443/send/message?token=abc#frag"
        )
        assert redacted == "https://gowa.example.invalid:8443/send/message"
        assert "secret" not in redacted
        assert "token" not in redacted
        assert "frag" not in redacted

    def test_redact_url_tolerates_unparsable_input(self) -> None:
        """Malformed URLs never raise while being redacted."""
        assert isinstance(redact_url("not a url at all"), str)

    def test_safe_error_category_returns_class_name_only(self) -> None:
        """Error categories are the exception class name."""
        assert safe_error_category(ConfigurationError("secret detail")) == "ConfigurationError"
        assert safe_error_category(ValueError("boom")) == "ValueError"

    def test_safe_error_category_is_bounded(self) -> None:
        """Pathological class names stay bounded."""
        message = format_event_message(
            EVENT_FAILED,
            mode="full",
            error_category=safe_error_category(type("E" * 5000, (Exception,), {})("x")),
        )
        assert len(message) <= MAX_MESSAGE_LENGTH


class TestFormatElapsed:
    """Tests for _format_elapsed function covering MRTG-TelkomCare format requirements."""

    def test_format_seconds_only(self) -> None:
        """Test format for durations < 60s: Xs (e.g., 45s, 0s)."""
        assert _format_elapsed(0.0) == "0s"
        assert _format_elapsed(45.0) == "45s"
        assert _format_elapsed(59.9) == "59s"

    def test_format_minutes_and_seconds(self) -> None:
        """Test format for durations < 3600s: Xm Ys (e.g., 4m 17s, 10m 23s)."""
        assert _format_elapsed(60.0) == "1m 0s"
        assert _format_elapsed(77.0) == "1m 17s"
        assert _format_elapsed(3599.9) == "59m 59s"
        assert _format_elapsed(480.0) == "8m 0s"  # 8 minutes
        assert _format_elapsed(583.0) == "9m 43s"  # 9 minutes 43 seconds

    def test_format_hours_minutes_seconds(self) -> None:
        """Test format for durations >= 3600s: Xh Ym Zs (e.g., 1h 2m 3s)."""
        assert _format_elapsed(3600.0) == "1h 0m 0s"
        assert _format_elapsed(3723.0) == "1h 2m 3s"
        assert _format_elapsed(7200.0) == "2h 0m 0s"
        assert _format_elapsed(3661.0) == "1h 1m 1s"
        assert _format_elapsed(4800.0) == "1h 20m 0s"

    def test_format_elapsed_used_in_success_message(self) -> None:
        """Integration test: verify elapsed formatting appears correctly in SUCCESS messages."""
        message = format_event_message(
            EVENT_SUCCESS,
            mode="full",
            dates=["2026-09-16"],
            elapsed_seconds=95.4,
            record_count=34,
        )
        # 95.4 seconds should format as "1m 35s"
        assert "elapsed=1m 35s" in message
        assert "records=34" in message

        message = format_event_message(
            EVENT_SUCCESS,
            mode="full",
            dates=["2026-09-16"],
            elapsed_seconds=3723.0,
            record_count=42,
        )
        # 3723 seconds should format as "1h 2m 3s"
        assert "elapsed=1h 2m 3s" in message
        assert "records=42" in message


class TestLoggingIsMetadataOnly:
    """Success logging records metadata only."""

    def test_success_log_has_event_and_redacted_endpoint(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A successful send logs the event name and a redacted endpoint."""
        transport = RecordingTransport()
        client = Notifier(make_notifier_config(), transport=transport)

        with caplog.at_level(logging.INFO, logger="cmp_automation.notifier"):
            client.send_event(EVENT_SUCCESS, mode="full", dates=["2026-09-16"])

        assert "success" in caplog.text.lower()
        assert "gowa.example.invalid" in caplog.text
        assert DUMMY_JID not in caplog.text
        assert "2026-09-16" not in caplog.text
