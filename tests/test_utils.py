"""Tests for utility functions."""

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from cmp_automation.utils import (
    extract_token_from_email_body,
    generate_export_filename,
    is_approved_portal_url,
    wait_for_portal_url,
    window_location_href,
)


class TestGenerateExportFilename:
    """Tests for export filename generation."""

    def test_timestamped_filename_format(self):
        """Test the sim_export_YYYYMMDD_HHMMSS.xlsx format."""
        dt = datetime(2024, 1, 15, 12, 34, 56, tzinfo=ZoneInfo("Asia/Jakarta"))
        path = generate_export_filename(dt, Path("/tmp/downloads"))
        assert path.name == "sim_export_20240115_123456.xlsx"
        assert path.parent == Path("/tmp/downloads")

    def test_end_of_year_rollover(self):
        """Test filename around a year boundary."""
        dt = datetime(2024, 12, 31, 23, 59, 59, tzinfo=ZoneInfo("Asia/Jakarta"))
        assert generate_export_filename(dt, Path(".")).name == "sim_export_20241231_235959.xlsx"

    def test_single_digit_components_padded(self):
        """Test that month/day/hour components are zero-padded."""
        dt = datetime(2024, 6, 1, 8, 5, 7, tzinfo=ZoneInfo("Asia/Jakarta"))
        assert generate_export_filename(dt, Path(".")).name == "sim_export_20240601_080507.xlsx"

    def test_edited_suffix_derivation(self):
        """Test the documented ' - Edited' suffix for the final report."""
        export = generate_export_filename(
            datetime(2024, 6, 1, 8, 5, 7, tzinfo=ZoneInfo("Asia/Jakarta")), Path("/tmp")
        )
        final = export.with_name(export.stem + " - Edited.xlsx")
        assert final.name == "sim_export_20240601_080507 - Edited.xlsx"


class TestExtractTokenFromEmailBody:
    """Tests for token extraction (regression coverage)."""

    def test_numeric_token(self):
        """Test extraction of a plain numeric token."""
        assert extract_token_from_email_body("Your code is 123456") == "123456"

    def test_alphanumeric_token_rejected(self):
        """Test that alphanumeric tokens are rejected (CMP OTPs are 6 digits)."""
        assert extract_token_from_email_body("Token: ABC123") is None

    def test_no_token(self):
        """Test that None is returned when no token exists."""
        assert extract_token_from_email_body("No code here") is None


class TestIsApprovedPortalUrl:
    """Tests for the shared strict portal URL validation."""

    APPROVED = "https://ep.iotcc.telkomsel.com/#!products"

    def test_exact_url_accepted(self):
        assert is_approved_portal_url(self.APPROVED, self.APPROVED, "!products")

    def test_http_rejected(self):
        assert not is_approved_portal_url(
            "http://ep.iotcc.telkomsel.com/#!products", self.APPROVED, "!products"
        )

    def test_lookalike_host_rejected(self):
        assert not is_approved_portal_url(
            "https://ep.iotcc.telkomsel.com.evil.com/#!products",
            self.APPROVED,
            "!products",
        )

    def test_subdomain_host_rejected(self):
        assert not is_approved_portal_url(
            "https://sub.ep.iotcc.telkomsel.com/#!products", self.APPROVED, "!products"
        )

    def test_explicit_port_rejected(self):
        assert not is_approved_portal_url(
            "https://ep.iotcc.telkomsel.com:8443/#!products", self.APPROVED, "!products"
        )

    def test_wrong_fragment_rejected(self):
        assert not is_approved_portal_url(
            "https://ep.iotcc.telkomsel.com/#!dashboard", self.APPROVED, "!products"
        )

    def test_non_root_path_rejected(self):
        assert not is_approved_portal_url(
            "https://ep.iotcc.telkomsel.com/app/#!products", self.APPROVED, "!products"
        )

    def test_non_string_rejected(self):
        assert not is_approved_portal_url(12345, self.APPROVED, "!products")


class TestWindowLocationHref:
    """Tests for the SPA URL fallback helper."""

    @pytest.mark.asyncio
    async def test_returns_href_string(self):
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value="https://ep.iotcc.telkomsel.com/#!products")
        assert await window_location_href(page) == "https://ep.iotcc.telkomsel.com/#!products"

    @pytest.mark.asyncio
    async def test_returns_none_for_non_string(self):
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value=42)
        assert await window_location_href(page) is None

    @pytest.mark.asyncio
    async def test_returns_none_on_evaluate_failure(self):
        page = AsyncMock()
        page.evaluate = AsyncMock(side_effect=Exception("evaluate failed"))
        assert await window_location_href(page) is None


class TestWaitForPortalUrl:
    """Tests for the shared SPA URL poll helper."""

    @pytest.mark.asyncio
    async def test_returns_true_when_page_url_matches(self):
        page = AsyncMock()
        page.url = "https://ep.iotcc.telkomsel.com/#!products"
        assert await wait_for_portal_url(page, lambda u: u.endswith("!products")) is True

    @pytest.mark.asyncio
    async def test_uses_window_location_fallback(self):
        page = AsyncMock()
        page.url = "https://ep.iotcc.telkomsel.com/"
        page.evaluate = AsyncMock(return_value="https://ep.iotcc.telkomsel.com/#!products")
        assert await wait_for_portal_url(page, lambda u: u.endswith("!products")) is True

    @pytest.mark.asyncio
    async def test_returns_false_when_never_matches(self):
        page = AsyncMock()
        page.url = "https://ep.iotcc.telkomsel.com/"
        page.evaluate = AsyncMock(return_value="https://ep.iotcc.telkomsel.com/")
        assert await wait_for_portal_url(page, lambda u: u.endswith("!products")) is False
