"""Tests for the sanitized, opt-in AuthDiag diagnostic.

The diagnostic must record metadata only: URL state categories, navigation
event types, structural DOM counts, and sanitized network metadata. It must
never record OTP values, credentials, query strings, raw service URLs, raw
exception messages, request/response bodies, headers/cookies, page text, or
body HTML.
"""

from unittest.mock import Mock

import pytest

from cmp_automation.auth_diag import AuthDiag, classify_url_state
from cmp_automation.network_diag import MAX_REQUESTS

APPROVED_HOST = "ep.iotcc.telkomsel.com"


class FakePage:
    """Minimal page double exposing on/remove_listener/fire + url/evaluate."""

    def __init__(self, url="https://ep.iotcc.telkomsel.com/", dom_counts=None):
        self._url = url
        self.dom_counts = dom_counts
        self.listeners = {}
        self.main_frame = FakeFrame(url, main=True)

    @property
    def url(self):
        return self._url

    def on(self, event, handler):
        self.listeners.setdefault(event, []).append(handler)

    def remove_listener(self, event, handler):
        self.listeners[event].remove(handler)

    def fire(self, event, obj):
        for handler in self.listeners.get(event, []):
            handler(obj)

    async def evaluate(self, _js):
        if self.dom_counts is None:
            raise RuntimeError("evaluate called with no fixture")
        return self.dom_counts


class FakeFrame:
    def __init__(self, url, main=False):
        self.url = url
        self._main = main

    def __eq__(self, other):
        return isinstance(other, FakeFrame) and other._main == self._main


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


class TestClassifyUrlState:
    """URLs are reduced to safe categories, never recorded raw."""

    def test_products_fragment(self):
        assert (
            classify_url_state("https://ep.iotcc.telkomsel.com/#!products", APPROVED_HOST)
            == "products"
        )

    def test_dashboard_fragment(self):
        assert (
            classify_url_state(
                "https://ep.iotcc.telkomsel.com/#!dashboard", APPROVED_HOST
            )
            == "dashboard"
        )

    def test_cas_login_path(self):
        assert (
            classify_url_state(
                "https://ep.iotcc.telkomsel.com/cas/login?service=https%3A%2F%2Fep.iotcc.telkomsel.com",
                APPROVED_HOST,
            )
            == "cas-login"
        )

    def test_portal_root(self):
        assert (
            classify_url_state("https://ep.iotcc.telkomsel.com/", APPROVED_HOST)
            == "portal-root"
        )
        assert (
            classify_url_state("https://ep.iotcc.telkomsel.com", APPROVED_HOST)
            == "portal-root"
        )

    def test_other_host_is_other(self):
        assert (
            classify_url_state("https://evil.com/#!products", APPROVED_HOST) == "other"
        )
        assert (
            classify_url_state("https://ep.iotcc.telkomsel.com.evil.com/#!products", APPROVED_HOST)
            == "other"
        )

    def test_unknown_path_is_other(self):
        assert (
            classify_url_state("https://ep.iotcc.telkomsel.com/app/settings", APPROVED_HOST)
            == "other"
        )

    def test_unparseable_or_empty_is_safe(self):
        assert classify_url_state("http://[unclosed", APPROVED_HOST) == "other"
        assert classify_url_state("", APPROVED_HOST) == "unavailable"
        assert classify_url_state(None, APPROVED_HOST) == "unavailable"

    def test_none_approved_host_is_strict(self):
        assert classify_url_state("https://ep.iotcc.telkomsel.com/#!products", None) == "other"


class TestAuthDiagLifecycle:
    """attach/detach wiring and listener hygiene."""

    @pytest.mark.asyncio
    async def test_attach_registers_and_detach_removes_listeners(self):
        page = FakePage()
        diag = AuthDiag(APPROVED_HOST)
        await diag.attach(page)
        assert len(page.listeners.get("framenavigated", [])) == 1
        await diag.detach()
        assert page.listeners.get("framenavigated", []) == []

    @pytest.mark.asyncio
    async def test_attach_twice_is_noop(self):
        page = FakePage()
        diag = AuthDiag(APPROVED_HOST)
        await diag.attach(page)
        await diag.attach(page)
        assert len(page.listeners.get("framenavigated", [])) == 1
        await diag.detach()

    @pytest.mark.asyncio
    async def test_detach_when_never_attached_is_safe(self):
        diag = AuthDiag(APPROVED_HOST)
        await diag.detach()
        assert diag.summary().startswith("final_url=unavailable")


class TestAuthDiagSummary:
    """Summary reports safe metadata: categories, nav events, DOM counts, network."""

    @pytest.mark.asyncio
    async def test_summary_reports_url_states_and_nav_events(self):
        page = FakePage(url="https://ep.iotcc.telkomsel.com/cas/login?service=x")
        diag = AuthDiag(APPROVED_HOST)
        await diag.attach(page)
        # Simulate the CAS redirect: portal root, then the Products SPA.
        # Fire with the page's own main_frame object so frame identity holds.
        page._url = "https://ep.iotcc.telkomsel.com/"
        page.main_frame = FakeFrame("https://ep.iotcc.telkomsel.com/", main=True)
        page.fire("framenavigated", page.main_frame)
        page._url = "https://ep.iotcc.telkomsel.com/#!products"
        page.main_frame = FakeFrame("https://ep.iotcc.telkomsel.com/#!products", main=True)
        page.fire("framenavigated", page.main_frame)
        await diag.detach()

        summary = diag.summary()
        assert "final_url=products" in summary
        assert "urls=[cas-login,portal-root,products]" in summary
        assert "framenavigated:main:portal-root" in summary
        assert "framenavigated:main:products" in summary

    @pytest.mark.asyncio
    async def test_summary_reports_structural_dom_counts_only(self):
        page = FakePage(
            dom_counts={
                "inputs": 2,
                "passwords": 1,
                "buttons": 1,
                "forms": 1,
                "grids": 0,
                "windows": 0,
                "dialogs": 0,
            }
        )
        diag = AuthDiag(APPROVED_HOST)
        await diag.attach(page)
        await diag.detach()
        summary = diag.summary()
        assert "passwords=1" in summary
        assert "grids=0" in summary

    @pytest.mark.asyncio
    async def test_dom_sample_failure_is_normalized(self):
        page = FakePage(dom_counts=None)  # evaluate raises
        diag = AuthDiag(APPROVED_HOST)
        await diag.attach(page)
        await diag.detach()
        assert "dom0=<unavailable>" in diag.summary()

    @pytest.mark.asyncio
    async def test_dom_counts_never_include_text(self):
        page = FakePage(dom_counts={"passwords": 1, "leak": "SECRET123"})
        diag = AuthDiag(APPROVED_HOST)
        await diag.attach(page)
        await diag.detach()
        # Only integer counts are ever rendered - string values are dropped.
        summary = diag.summary()
        assert "passwords=1" in summary
        assert "SECRET123" not in summary
        assert "leak" not in summary


class TestAuthDiagNoLeak:
    """Secrets embedded in URLs/events never reach the summary."""

    @pytest.mark.asyncio
    async def test_query_strings_and_secrets_never_leak(self):
        page = FakePage(
            url=(
                "https://ep.iotcc.telkomsel.com/cas/login"
                "?token=SECRET123&service=https%3A%2F%2Fep.iotcc.telkomsel.com"
            )
        )
        diag = AuthDiag(APPROVED_HOST)
        await diag.attach(page)
        page.fire(
            "framenavigated",
            FakeFrame(
                "https://ep.iotcc.telkomsel.com/#!products?token=SECRET123", main=True
            ),
        )
        await diag.detach()
        summary = diag.summary()
        assert "SECRET123" not in summary
        assert "?token" not in summary
        assert "?service" not in summary

    @pytest.mark.asyncio
    async def test_network_metadata_is_sanitized(self):
        page = FakePage()
        diag = AuthDiag(APPROVED_HOST)
        await diag.attach(page)
        req = _request(
            url=(
                "https://user:pass@ep.iotcc.telkomsel.com/export"
                "?token=SECRET123&id=42#frag"
            )
        )
        page.fire("request", req)
        page.fire("response", _response(req, status=200))
        page.fire("requestfinished", req)
        await diag.detach()
        summary = diag.summary()
        assert "SECRET123" not in summary
        assert "user:pass" not in summary
        assert "?token" not in summary
        assert "https://ep.iotcc.telkomsel.com/export" in summary
        assert "status=200" in summary

    @pytest.mark.asyncio
    async def test_raw_failure_text_never_leaks(self):
        page = FakePage()
        diag = AuthDiag(APPROVED_HOST)
        await diag.attach(page)
        req = _request()
        req.failure = (
            "net::ERR_CONNECTION_RESET https://ep.iotcc.telkomsel.com/export?token=SECRET123"
        )
        page.fire("request", req)
        page.fire("requestfailed", req)
        await diag.detach()
        summary = diag.summary()
        assert "net::ERR_CONNECTION_RESET" in summary
        assert "SECRET123" not in summary
        assert "ERR_CONNECTION_RESET https" not in summary

    def test_dom_js_is_structural_only(self):
        from cmp_automation.auth_diag import DOM_COUNTS_JS

        for banned in ("textContent", "innerText", "innerHTML", ".value"):
            assert banned not in DOM_COUNTS_JS

    @pytest.mark.asyncio
    async def test_url_state_from_closed_page_is_unavailable(self):
        class _ClosedPage:
            @property
            def url(self):
                raise RuntimeError("Target page closed")

            def on(self, event, handler):
                self.listeners.setdefault(event, []).append(handler)

            def remove_listener(self, event, handler):
                self.listeners[event].remove(handler)

            async def evaluate(self, _js):
                raise RuntimeError("closed")

        page = _ClosedPage()
        page.listeners = {}
        page.main_frame = FakeFrame("", main=True)
        diag = AuthDiag(APPROVED_HOST)
        await diag.attach(page)
        await diag.detach()
        assert "final_url=unavailable" in diag.summary()


class TestAuthDiagBounded:
    """The diagnostic is bounded: capped events and summary length."""

    @pytest.mark.asyncio
    async def test_nav_events_are_capped(self):
        page = FakePage()
        diag = AuthDiag(APPROVED_HOST)
        await diag.attach(page)
        for i in range(80):
            page.fire(
                "framenavigated",
                FakeFrame(f"https://ep.iotcc.telkomsel.com/#!products?x={i}", main=True),
            )
        await diag.detach()
        assert "(truncated)" in diag.summary()

    @pytest.mark.asyncio
    async def test_network_records_are_capped(self):
        page = FakePage()
        diag = AuthDiag(APPROVED_HOST)
        await diag.attach(page)
        for i in range(MAX_REQUESTS + 5):
            req = _request(url=f"https://ep.iotcc.telkomsel.com/export/{i}")
            page.fire("request", req)
        await diag.detach()
        summary = diag.summary()
        assert f"requests={MAX_REQUESTS}" in summary
        assert f"(truncated at {MAX_REQUESTS} requests)" in summary

    @pytest.mark.asyncio
    async def test_summary_length_is_capped(self):
        page = FakePage()
        diag = AuthDiag(APPROVED_HOST)
        await diag.attach(page)
        for i in range(40):
            page.fire(
                "framenavigated",
                FakeFrame(f"https://ep.iotcc.telkomsel.com/#!products?x={i}", main=True),
            )
        await diag.detach()
        assert len(diag.summary()) <= 4000
