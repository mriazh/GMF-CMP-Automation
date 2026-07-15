"""Tests for exception classes."""


from cmp_automation.exceptions import (
    AuthenticationError,
    BrowserError,
    CMPAutomationError,
    ConfigurationError,
    DashboardError,
    DownloadError,
    ExcelReportError,
    OTPError,
    OTPExpiredError,
    OTPNotFoundError,
    OTPTimeoutError,
    ProductsExportError,
    ValidationError,
)


class TestExceptions:
    """Tests for exception hierarchy and behavior."""

    def test_base_exception(self):
        """Test base exception."""
        exc = CMPAutomationError("Base error")
        assert str(exc) == "Base error"
        assert exc.message == "Base error"
        assert exc.details is None

    def test_base_exception_with_details(self):
        """Test base exception with details."""
        exc = CMPAutomationError("Base error", "Additional details")
        assert str(exc) == "Base error: Additional details"
        assert exc.message == "Base error"
        assert exc.details == "Additional details"

    def test_configuration_error(self):
        """Test ConfigurationError."""
        exc = ConfigurationError("Config invalid", "missing field")
        assert isinstance(exc, CMPAutomationError)
        assert str(exc) == "Config invalid: missing field"

    def test_browser_error(self):
        """Test BrowserError."""
        exc = BrowserError("Browser failed", "timeout")
        assert isinstance(exc, CMPAutomationError)
        assert str(exc) == "Browser failed: timeout"

    def test_authentication_error(self):
        """Test AuthenticationError."""
        exc = AuthenticationError("Auth failed", "invalid credentials")
        assert isinstance(exc, CMPAutomationError)
        assert str(exc) == "Auth failed: invalid credentials"

    def test_otp_error_hierarchy(self):
        """Test OTP error hierarchy."""
        base = OTPError("OTP error")
        assert isinstance(base, CMPAutomationError)

        timeout = OTPTimeoutError("Timed out", "120s")
        assert isinstance(timeout, OTPError)
        assert isinstance(timeout, CMPAutomationError)
        assert str(timeout) == "Timed out: 120s"

        not_found = OTPNotFoundError("Not found", "subject: X")
        assert isinstance(not_found, OTPError)
        assert str(not_found) == "Not found: subject: X"

        expired = OTPExpiredError("Expired", "old token")
        assert isinstance(expired, OTPError)
        assert str(expired) == "Expired: old token"

    def test_products_export_error(self):
        """Test ProductsExportError."""
        exc = ProductsExportError("Export failed", "no table")
        assert isinstance(exc, CMPAutomationError)
        assert str(exc) == "Export failed: no table"

    def test_dashboard_error(self):
        """Test DashboardError."""
        exc = DashboardError("Dashboard failed", "no container")
        assert isinstance(exc, CMPAutomationError)
        assert str(exc) == "Dashboard failed: no container"

    def test_excel_report_error(self):
        """Test ExcelReportError."""
        exc = ExcelReportError("Excel failed", "corrupt file")
        assert isinstance(exc, CMPAutomationError)
        assert str(exc) == "Excel failed: corrupt file"

    def test_download_error(self):
        """Test DownloadError."""
        exc = DownloadError("Download failed", "empty file")
        assert isinstance(exc, CMPAutomationError)
        assert str(exc) == "Download failed: empty file"

    def test_validation_error(self):
        """Test ValidationError."""
        exc = ValidationError("Validation failed", "invalid input")
        assert isinstance(exc, CMPAutomationError)
        assert str(exc) == "Validation failed: invalid input"

    def test_all_exceptions_inherit_from_base(self):
        """Test that all custom exceptions inherit from CMPAutomationError."""
        exceptions = [
            ConfigurationError("test"),
            BrowserError("test"),
            AuthenticationError("test"),
            OTPError("test"),
            OTPTimeoutError("test"),
            OTPNotFoundError("test"),
            OTPExpiredError("test"),
            ProductsExportError("test"),
            DashboardError("test"),
            ExcelReportError("test"),
            DownloadError("test"),
            ValidationError("test"),
        ]

        for exc in exceptions:
            assert isinstance(exc, CMPAutomationError)
            assert isinstance(exc, Exception)
