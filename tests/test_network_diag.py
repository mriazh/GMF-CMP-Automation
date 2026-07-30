"""Tests for the sanitized, opt-in NetworkDiag diagnostic.

The diagnostic must record metadata only (method, sanitized host/path,
resource type, status, timing, event types) and must never leak query
strings, userinfo, raw failure text, request/response bodies, or headers.
"""

from unittest.mock import Mock

import pytest

from cmp_automation.network_diag import MAX_REQUESTS, NetworkDiag, _sanitize_failure, _sanitize_url


class FakePage:
    """Minimal page double exposing only on/remove_listener/fire."""

    def __init__(self):
        self.listeners = {}

    def on(self, event, handler):
        self.listeners.setdefault(event, []).append(handler)

    def remove_listener(self, event, handler):
        self.listeners[event].remove(handler)

    def fire(self, event, obj):
        for handler in self.listeners.get(event, []):
            handler(obj)


def _request(url="https://ep.iotcc.telkomsel.com/export", method="POST", rtype="xhr"):
    req = Mock()
    req.url = url
    req.method = method
    req.resource_type = rtype
    return req


def _response(req, status=200):
    resp = Mock()
    resp.request = req
    resp.status = status
    return resp


class TestSanitizeUrl:
    """Query, fragment, and userinfo are stripped from reported URLs."""

    def test_query_fragment_userinfo_stripped(self):
        url = (
            "https://user:pass@ep.iotcc.telkomsel.com/export"
            "?token=SECRET123&id=42#frag"
        )
        assert _sanitize_url(url) == "https://ep.iotcc.telkomsel.com/export"

    def test_no_query_no_change(self):
        assert (
            _sanitize_url("https://ep.iotcc.telkomsel.com/export")
            == "https://ep.iotcc.telkomsel.com/export"
        )

    def test_unparseable_becomes_literal(self):
        assert _sanitize_url("http://[unclosed") == "<unparseable>"


class TestSanitizeFailure:
    """Raw failure text is reduced to a known error code or a literal."""

    def test_known_error_code_kept(self):
        failure = (
            "net::ERR_ABORTED navigating to "
            "https://ep.iotcc.telkomsel.com/export?token=SECRET123"
        )
        assert _sanitize_failure(failure) == "net::ERR_ABORTED"

    def test_ns_error_code_kept(self):
        assert _sanitize_failure("NS_ERROR_NET_TIMEOUT at https://host/") == (
            "NS_ERROR_NET_TIMEOUT"
        )

    def test_unknown_failure_becomes_literal(self):
        assert _sanitize_failure("connection reset by peer") == "<failed>"

    def test_empty_failure_is_none(self):
        assert _sanitize_failure(None) is None
        assert _sanitize_failure("") is None


class TestSummarySanitization:
    """End-to-end: the summary never leaks secrets embedded in metadata."""

    @pytest.fixture
    def page(self):
        return FakePage()

    def test_summary_contains_no_query_userinfo_or_fragment(self, page):
        diag = NetworkDiag()
        diag.attach(page)
        req = _request(
            url=(
                "https://user:pass@ep.iotcc.telkomsel.com/export"
                "?token=SECRET123&id=42#frag"
            )
        )
        page.fire("request", req)
        page.fire("response", _response(req, status=200))
        page.fire("requestfinished", req)
        diag.detach()

        summary = diag.summary()
        assert "SECRET123" not in summary
        assert "user:pass" not in summary
        assert "?token" not in summary
        assert "#frag" not in summary
        assert "https://ep.iotcc.telkomsel.com/export" in summary

    def test_summary_reports_metadata(self, page):
        diag = NetworkDiag()
        diag.attach(page)
        req = _request(method="POST")
        page.fire("request", req)
        page.fire("response", _response(req, status=500))
        page.fire("requestfinished", req)
        diag.detach()

        summary = diag.summary()
        assert "requests=1" in summary
        assert "POST" in summary
        assert "status=500" in summary
        assert "events=[request,response,finished]" in summary
        assert "start=+" in summary
        assert "dur=" in summary

    def test_failed_request_reports_sanitized_code_only(self, page):
        diag = NetworkDiag()
        diag.attach(page)
        req = _request()
        req.failure = (
            "net::ERR_CONNECTION_RESET "
            "https://ep.iotcc.telkomsel.com/export?token=SECRET123"
        )
        page.fire("request", req)
        page.fire("requestfailed", req)
        diag.detach()

        summary = diag.summary()
        assert "net::ERR_CONNECTION_RESET" in summary
        assert "SECRET123" not in summary
        assert "ERR_CONNECTION_RESET https" not in summary

    def test_bodies_and_headers_never_touched(self, page):
        """The diagnostic must never read bodies, headers, or post data."""
        diag = NetworkDiag()
        diag.attach(page)
        req = _request()
        page.fire("request", req)
        page.fire("response", _response(req, status=200))
        page.fire("requestfinished", req)
        page.fire("requestfailed", _request())  # unknown request: ignored
        diag.detach()

        for attr in ("post_data", "headers", "body", "text", "json"):
            getattr(req, attr).assert_not_called()
            assert getattr(req, attr).call_count == 0

    def test_no_activity_summary(self, page):
        diag = NetworkDiag()
        diag.attach(page)
        diag.detach()
        assert diag.summary() == "requests=0 (no network activity observed)"

    def test_detach_removes_all_listeners(self, page):
        diag = NetworkDiag()
        diag.attach(page)
        diag.detach()
        assert all(handlers == [] for handlers in page.listeners.values())

    def test_attach_twice_is_noop(self, page):
        diag = NetworkDiag()
        diag.attach(page)
        diag.attach(page)
        assert len(page.listeners.get("request", [])) == 1


class TestBounding:
    """The diagnostic is bounded: capped records, truncated summary."""

    def test_request_cap_truncates(self):
        page = FakePage()
        diag = NetworkDiag()
        diag.attach(page)
        for i in range(MAX_REQUESTS + 5):
            req = _request(url=f"https://ep.iotcc.telkomsel.com/export/{i}")
            page.fire("request", req)
        diag.detach()

        summary = diag.summary()
        assert f"requests={MAX_REQUESTS}" in summary
        assert f"(truncated at {MAX_REQUESTS} requests)" in summary


class TestModuleSourceSafety:
    """Source-level guarantee: no body/header API is ever referenced."""

    def test_no_body_or_header_access_in_source(self):
        import inspect

        source = inspect.getsource(NetworkDiag)
        for banned in ("post_data", ".headers", ".body(", ".text(", ".json(", "body_bytes"):
            assert banned not in source
