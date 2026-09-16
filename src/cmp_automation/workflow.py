"""Workflow orchestration for CMP Daily Data Usage automation."""

import asyncio
import logging
import shutil
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from pathlib import Path

from playwright.async_api import Page

from .browser import browser_context
from .cmp_login import CMPLogin
from .config import Config, validate_paths
from .connectivity import ConnectivityController
from .dashboard import DashboardCapture
from .excel_report import ExcelReportGenerator
from .exceptions import WorkflowError
from .mailbox import MailboxClient
from .usage_query import UsageQueryExporter, UsageReportArtifact

logger = logging.getLogger(__name__)


def _newest_first(candidates: Iterable[Path]) -> list[Path]:
    """Sort glob results by modification time, newest first."""
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)


def _find_matching_raw_file(
    raw_dir: Path,
    download_dir: Path,
    target_date: date,
    exporter: UsageQueryExporter,
) -> Path | None:
    """Find the newest raw export whose rows actually carry ``target_date``.

    Daily automation runs on day H at 00:30 to export H-1 usage data, so the
    filename timestamp is one day ahead of the data it holds. The filename
    alone cannot separate the two days, hence the content check on the parsed
    rows. Candidate ordering: next-day timestamp (the 00:30 run), same-day
    timestamp, then any raw export, each newest first.
    """
    next_day = target_date + timedelta(days=1)
    target_str = target_date.strftime("%Y-%m-%d")
    patterns = (
        f"report_{next_day:%Y%m%d}_*_DAILY_USAGE_by_SIM.xlsx",
        f"report_{target_date:%Y%m%d}_*_DAILY_USAGE_by_SIM.xlsx",
        "report_*_DAILY_USAGE_by_SIM.xlsx",
    )

    seen: set[Path] = set()
    for directory in (raw_dir, download_dir):
        for pattern in patterns:
            for candidate in _newest_first(directory.glob(pattern)):
                if candidate in seen:
                    continue
                seen.add(candidate)
                try:
                    records = exporter.parse_usage_data(candidate)
                except Exception as exc:
                    # Unreadable or wrong-schema file: skip it, never fail discovery.
                    logger.warning(
                        "Skipped unusable raw candidate %s: %s", candidate.name, type(exc).__name__
                    )
                    continue
                if any(r.get("date") == target_str for r in records):
                    return candidate
    return None


def _find_matching_image(image_dir: Path, target_date: date) -> Path | None:
    """Find the dashboard screenshot matching ``target_date``.

    Prioritizes the next-day timestamp (the 00:30 run), then the same-day
    timestamp, then the newest screenshot of any date.
    """
    next_day = target_date + timedelta(days=1)
    patterns = (
        f"dashboard_{next_day:%Y%m%d}_*.png",
        f"dashboard_{target_date:%Y%m%d}_*.png",
        "dashboard_*.png",
    )
    for pattern in patterns:
        candidates = _newest_first(image_dir.glob(pattern))
        if candidates:
            return candidates[0]
    return None


class UsageWorkflowRunner:
    """Runner for CMP daily data usage export and Excel reporting workflow."""

    def __init__(
        self,
        config: Config,
        headed: bool = False,
        dry_run: bool = False,
        diagnose_export: bool = False,
        diagnose_auth: bool = False,
        query_date: date | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        monitor_cycles: int | None = None,
        connectivity_enabled: bool | None = None,
        allow_connectivity_mutation: bool | None = None,
        mode: str = "full",
        raw_xlsx: Path | None = None,
        image_path: Path | None = None,
        skip_screenshot: bool = False,
    ) -> None:
        self.config = config
        self.headed = headed
        self.dry_run = dry_run
        self.diagnose_export = diagnose_export
        self.diagnose_auth = diagnose_auth
        self.query_date = query_date
        self.start_date = start_date
        self.end_date = end_date
        self.monitor_cycles = monitor_cycles if monitor_cycles is not None else config.monitor_max_cycles
        self.connectivity_enabled = (
            config.connectivity_enabled if connectivity_enabled is None else connectivity_enabled
        )
        self.allow_connectivity_mutation = (
            config.connectivity_allow_connect
            if allow_connectivity_mutation is None
            else allow_connectivity_mutation
        )
        self.mode = mode
        self.raw_xlsx = raw_xlsx
        self.image_path = image_path
        self.skip_screenshot = skip_screenshot
        self.connectivity = ConnectivityController(config)

        self.mailbox = MailboxClient(config)
        self.login = CMPLogin(config, self.mailbox, diagnose_auth=diagnose_auth)
        self.usage_exporter = UsageQueryExporter(config, diagnose_export=diagnose_export)
        self.dashboard_capture = DashboardCapture(config)
        self.excel_report = ExcelReportGenerator(config)

        self.page: Page | None = None

    def _get_target_dates(self) -> list[date]:
        """Resolve list of dates to process."""
        if self.start_date and self.end_date:
            target_dates = []
            curr = self.start_date
            while curr <= self.end_date:
                target_dates.append(curr)
                curr += timedelta(days=1)
            return target_dates

        if self.query_date:
            return [self.query_date]

        # Default to today in configured timezone
        tz = self.config.get_timezone()
        return [datetime.now(tz).date()]

    async def _ensure_proxy_ready(self) -> None:
        """Ensure proxy is configured and ready before browser launch."""
        has_full_tunnel_mutation = self.connectivity_enabled and self.allow_connectivity_mutation
        warp_mode = self.config.warp_mode.lower()
        if self.config.warp_auto_connect and warp_mode in ("proxy", "warp"):
            if warp_mode == "warp" and not has_full_tunnel_mutation:
                logger.info(
                    "WARP auto-connect utilizing isolated SOCKS5 proxy mode on port %d",
                    self.config.warp_proxy_port,
                )

            if warp_mode == "proxy" or not has_full_tunnel_mutation:
                warp_available = (
                    (self.config.warp_cli_path and self.config.warp_cli_path.exists())
                    or bool(shutil.which(str(self.config.warp_cli_path)))
                    or bool(shutil.which("warp-cli"))
                    or bool(shutil.which("warp-cli.exe"))
                )
                if warp_available:
                    try:
                        success = await asyncio.to_thread(
                            self.connectivity.warp.prepare_proxy, self.config.warp_proxy_port
                        )
                    except Exception as exc:
                        logger.warning(
                            "WARP auto-connect could not be established; continuing with configured proxy or direct: %s",
                            exc,
                        )
                        success = False

                    if success:
                        if self.config.cmp_proxy_server is None:
                            self.config.cmp_proxy_server = f"socks5://127.0.0.1:{self.config.warp_proxy_port}"
                            logger.info("Auto-configured WARP SOCKS5 proxy: %s", self.config.cmp_proxy_server)
                    else:
                        logger.warning(
                            "WARP auto-connect could not be established; continuing with configured proxy or direct"
                        )

    async def run(self) -> Path:
        """Execute the complete workflow and return final report path."""
        logger.info("Starting CMP Daily Usage Automation workflow")

        if self.dry_run:
            logger.info("Running in dry-run mode: validating configuration and browser launch")
            validate_paths(self.config)
            await self._ensure_proxy_ready()
            async with browser_context(self.config, headed=self.headed) as browser:
                await browser.new_page()
                logger.info("Dry run: browser launched successfully")
            return Path("dry-run-success")

        target_dates = self._get_target_dates()
        logger.info("Pipeline mode=%s", self.mode)
        logger.info("Target dates to process: %s", [d.strftime("%Y-%m-%d") for d in target_dates])

        if self.mode == "generate":
            if len(target_dates) != 1:
                raise WorkflowError("Generate mode requires exactly one target date")
            target_date = target_dates[0]
            raw_xlsx = self.raw_xlsx
            image_path = self.image_path

            if raw_xlsx is None:
                raw_dir = self.config.raw_xlsx_dir or (self.config.excel_output_dir / "raw")
                match = _find_matching_raw_file(
                    raw_dir, self.config.download_dir, target_date, self.usage_exporter
                )
                if match is None:
                    raise WorkflowError(
                        f"Generate mode requires --raw-xlsx or a matching raw file in {raw_dir}"
                    )
                raw_xlsx = match
                logger.info("Auto-discovered raw export: %s", raw_xlsx)

            if not raw_xlsx.exists():
                raise WorkflowError(f"Raw XLSX input does not exist: {raw_xlsx}")

            if self.skip_screenshot:
                image_path = None
                logger.info("Screenshot embedding disabled (--skip-screenshot)")
            elif image_path is None:
                image_dir = self.config.image_dir or (self.config.excel_output_dir / "images")
                match = _find_matching_image(image_dir, target_date)
                if match is not None:
                    image_path = match
                    logger.info("Auto-discovered dashboard image: %s", image_path)

            report_path = self.excel_report.generate_report(
                artifact_or_path=raw_xlsx,
                screenshot_path=image_path,
                query_date=target_date,
            )
            logger.info("Generate stage completed: %s", report_path)
            return report_path

        last_report_path: Path | None = None

        try:
            if self.connectivity_enabled:
                await self.connectivity.ensure_authentication_connectivity(
                    allow_connect=self.allow_connectivity_mutation
                )

            await self._ensure_proxy_ready()

            async with browser_context(self.config, headed=self.headed) as browser:
                page = await browser.new_page()
                self.page = page

                # Step 1: Perform CMP Login
                logger.info("Step 1: Logging in to CMP Portal")
                await self.login.login(page)
                logger.info("Login successful")
                if self.connectivity_enabled:
                    await self.connectivity.release_authentication_connectivity(
                        allow_disconnect=self.config.connectivity_allow_disconnect
                    )
                    await self.connectivity.prepare_monitoring_connectivity(
                        allow_connect=self.allow_connectivity_mutation
                    )

                for target_date in target_dates:
                    date_str = target_date.strftime("%Y-%m-%d")
                    logger.info("Processing usage workflow for date: %s", date_str)

                    # Step 2: Export Usage Query
                    logger.info("Step 2: Exporting Usage Query data for %s", date_str)
                    artifact: UsageReportArtifact = await self.usage_exporter.export(
                        page, query_date=target_date
                    )
                    logger.info(
                        "Export successful: %d rows from %s",
                        len(artifact.rows),
                        artifact.raw_path,
                    )

                    # Step 3: Capture Dashboard Screenshot
                    screenshot_path: Path | None = None
                    if self.skip_screenshot:
                        logger.info("Step 3: Skipped dashboard screenshot (--skip-screenshot)")
                    else:
                        logger.info("Step 3: Capturing dashboard screenshot")
                        image_dir = self.config.image_dir or (self.config.excel_output_dir / "images")
                        image_dir.mkdir(parents=True, exist_ok=True)
                        ts = datetime.now(self.config.get_timezone()).strftime("%Y%m%d_%H%M%S")
                        routed_path = image_dir / f"dashboard_{ts}.png"
                        screenshot_path = await self.dashboard_capture.capture(page)
                        if screenshot_path.parent != image_dir and screenshot_path.exists():
                            screenshot_path.replace(routed_path)
                            screenshot_path = routed_path

                        logger.info("Dashboard screenshot captured: %s", screenshot_path)

                    if self.mode == "scrape":
                        logger.info(
                            "Scrape stage completed for %s: raw=%s image=%s",
                            date_str,
                            artifact.raw_path,
                            screenshot_path,
                        )
                        last_report_path = artifact.raw_path
                        continue

                    # Step 4: Update/Generate Monthly Excel Report
                    logger.info("Step 4: Updating Excel monthly report with day %s data", date_str)
                    last_report_path = self.excel_report.generate_report(
                        artifact_or_path=artifact,
                        screenshot_path=screenshot_path,
                        query_date=target_date,
                    )
                    logger.info("Excel report generated successfully at: %s", last_report_path)

            if last_report_path is None:
                raise WorkflowError("No reports were generated during workflow execution")

            return last_report_path
        finally:
            await self.mailbox.disconnect()
            if self.connectivity_enabled:
                await self.connectivity.cleanup(
                    allow_disconnect=self.config.connectivity_allow_disconnect
                )


# Backward compatibility alias
CMPAutomationWorkflow = UsageWorkflowRunner


async def run_workflow(
    config: Config,
    headed: bool = False,
    dry_run: bool = False,
    diagnose_export: bool = False,
    diagnose_auth: bool = False,
    query_date: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    monitor_cycles: int | None = None,
    connectivity_enabled: bool | None = None,
    allow_connectivity_mutation: bool | None = None,
    mode: str = "full",
    raw_xlsx: Path | None = None,
    image_path: Path | None = None,
    skip_screenshot: bool = False,
) -> Path:
    """Convenience function to run the full workflow."""
    workflow = UsageWorkflowRunner(
        config=config,
        headed=headed,
        dry_run=dry_run,
        diagnose_export=diagnose_export,
        diagnose_auth=diagnose_auth,
        query_date=query_date,
        start_date=start_date,
        end_date=end_date,
        monitor_cycles=monitor_cycles,
        connectivity_enabled=connectivity_enabled,
        allow_connectivity_mutation=allow_connectivity_mutation,
        mode=mode,
        raw_xlsx=raw_xlsx,
        image_path=image_path,
        skip_screenshot=skip_screenshot,
    )
    return await workflow.run()
