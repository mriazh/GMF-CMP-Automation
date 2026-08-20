"""Custom exceptions for CMP Automation."""


class CMPAutomationError(Exception):
    """Base exception for CMP Automation errors."""

    def __init__(self, message: str, details: str | None = None):
        super().__init__(message)
        self.message = message
        self.details = details

    def __str__(self) -> str:
        if self.details:
            return f"{self.message}: {self.details}"
        return self.message


class ConfigurationError(CMPAutomationError):
    """Raised when configuration is invalid or missing."""


class BrowserError(CMPAutomationError):
    """Raised when browser operations fail."""


class AuthenticationError(CMPAutomationError):
    """Raised when authentication fails."""


class OTPError(CMPAutomationError):
    """Raised when OTP retrieval or validation fails."""


class OTPTimeoutError(OTPError):
    """Raised when OTP polling times out."""


class OTPNotFoundError(OTPError):
    """Raised when no valid OTP email is found."""


class OTPExpiredError(OTPError):
    """Raised when OTP is expired or invalid."""


class UsageQueryError(CMPAutomationError):
    """Raised when Usage Query execution or parsing fails."""


class ExportDialogCloseError(UsageQueryError):
    """Raised when the export dialog fails to close."""


class SortOrderError(UsageQueryError):
    """Raised when table sort order verification fails."""


ProductsExportError = UsageQueryError


class DashboardError(CMPAutomationError):
    """Raised when dashboard screenshot capture fails."""


class ExcelReportError(CMPAutomationError):
    """Raised when Excel report generation fails."""


class DownloadError(CMPAutomationError):
    """Raised when file download fails."""


class ValidationError(CMPAutomationError):
    """Raised when validation fails."""


class WorkflowError(CMPAutomationError):
    """Raised when workflow orchestration fails."""


class WorkbookError(CMPAutomationError):
    """Raised when monthly workbook operations fail."""
