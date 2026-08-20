"""CMP Automation - Production-ready automation for Telkomsel CMP Portal."""

from .config import Config, load_config, validate_paths
from .connectivity import ConnectivityController, ConnectivityError
from .exceptions import (
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
    WorkbookError,
)
from .workbook import MonthlyWorkbookService
from .workflow import CMPAutomationWorkflow, run_workflow

__version__ = "1.0.0"

__all__ = [
    "Config",
    "ConnectivityController",
    "ConnectivityError",
    "load_config",
    "validate_paths",
    "CMPAutomationWorkflow",
    "run_workflow",
    "MonthlyWorkbookService",
    "CMPAutomationError",
    "ConfigurationError",
    "BrowserError",
    "AuthenticationError",
    "OTPError",
    "OTPTimeoutError",
    "OTPNotFoundError",
    "OTPExpiredError",
    "ProductsExportError",
    "DashboardError",
    "ExcelReportError",
    "DownloadError",
    "ValidationError",
    "WorkbookError",
]
