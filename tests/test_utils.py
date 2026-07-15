"""Tests for utility functions."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cmp_automation.utils import extract_token_from_email_body, generate_export_filename


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

    def test_with_dashboard_suffix_derivation(self):
        """Test the documented _with_dashboard suffix for the final report."""
        export = generate_export_filename(
            datetime(2024, 6, 1, 8, 5, 7, tzinfo=ZoneInfo("Asia/Jakarta")), Path("/tmp")
        )
        final = export.with_name(export.stem + "_with_dashboard.xlsx")
        assert final.name == "sim_export_20240601_080507_with_dashboard.xlsx"


class TestExtractTokenFromEmailBody:
    """Tests for token extraction (regression coverage)."""

    def test_numeric_token(self):
        """Test extraction of a plain numeric token."""
        assert extract_token_from_email_body("Your code is 123456") == "123456"

    def test_alphanumeric_token(self):
        """Test extraction of an alphanumeric token."""
        assert extract_token_from_email_body("Token: ABC123") == "ABC123"

    def test_no_token(self):
        """Test that None is returned when no token exists."""
        assert extract_token_from_email_body("No code here") is None
