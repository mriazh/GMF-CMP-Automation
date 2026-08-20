"""Excel report generation with embedded dashboard screenshot for CMP Daily Usage."""

import logging
import zipfile
from copy import copy
from datetime import date, datetime
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.drawing.image import Image as XLImage
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .config import Config
from .exceptions import ExcelReportError
from .usage_query import UsageQueryExporter, UsageReportArtifact

logger = logging.getLogger(__name__)

DAILY_SHEETS = [f"{i:02d}" for i in range(1, 32)]

# Styles for lookup headers and cells
HEADER_FILL = PatternFill(start_color="FF00B0F0", end_color="FF00B0F0", fill_type="solid")
HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="000000")
HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center")

DATA_FONT = Font(name="Calibri", size=11, bold=False)
DATA_ALIGNMENT_LEFT = Alignment(horizontal="left", vertical="center")

THIN_SIDE = Side(border_style="thin", color="FF000000")
THIN_BORDER = Border(left=THIN_SIDE, right=THIN_SIDE, top=THIN_SIDE, bottom=THIN_SIDE)


class ExcelReportGenerator:
    """Generates and updates monthly Excel reports for daily data usage."""

    def __init__(self, config: Config) -> None:
        self.config = config

    def prepare_template_copy(
        self,
        template_path: Path,
        output_path: Path,
        query_month: date | None = None,
    ) -> Path:
        """Create a clean copy of the template workbook for a new month.

        Extracts and normalizes the ICCID -> LOCATION lookup table from all
        source sheets, writes it to O3:P140 across all sheets with matching styles,
        clears prior data values from daily sheets 01-31 while preserving titles,
        formatting, formulas, and dimensions.
        """
        if output_path.exists():
            logger.info("Monthly workbook already exists at: %s", output_path)
            return output_path

        if not template_path.exists():
            example_fallback = template_path.with_name(
                template_path.stem + ".example" + template_path.suffix
            )
            if example_fallback.exists() and example_fallback.is_file():
                template_path = example_fallback
            else:
                raise ExcelReportError(f"Source template XLSX not found: {template_path}")

        logger.info("Preparing new monthly workbook copy: %s -> %s", template_path, output_path)

        try:
            wb = openpyxl.load_workbook(template_path)
        except (zipfile.BadZipFile, Exception) as e:
            raise ExcelReportError(
                f"Source template is not a valid XLSX file: {template_path}"
            ) from e

        # 1. Extract and normalize lookup table from all source sheets
        lookup_map = self._extract_lookup_map(wb)

        # 2. Normalize every daily sheet (01-31) and Master
        for name in wb.sheetnames:
            ws = wb[name]
            self._write_lookup_table(ws, lookup_map)

            if name in DAILY_SHEETS:
                self._clear_daily_sheet_data(ws)
                if hasattr(ws, "_images"):
                    ws._images.clear()

        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            wb.save(output_path)
        except Exception as e:
            raise ExcelReportError(f"Failed to save prepared template copy to {output_path}") from e

        logger.info("Successfully created clean monthly workbook: %s", output_path)
        return output_path

    def update_daily_sheet(
        self,
        output_path: Path,
        query_date: date,
        records: list[dict[str, Any]],
        screenshot_path: Path | None = None,
    ) -> Path:
        """Update a specific calendar day sheet (01-31) in the monthly workbook."""
        if not output_path.exists():
            raise ExcelReportError(f"Target monthly workbook does not exist: {output_path}")

        sheet_name = f"{query_date.day:02d}"
        logger.info(
            "Updating day sheet '%s' for date %s in %s with %d records",
            sheet_name,
            query_date,
            output_path,
            len(records),
        )

        try:
            wb = openpyxl.load_workbook(output_path)
        except Exception as e:
            raise ExcelReportError(f"Failed to load workbook at {output_path}") from e

        if sheet_name not in wb.sheetnames:
            raise ExcelReportError(
                f"Worksheet '{sheet_name}' not found in monthly workbook {output_path}"
            )

        ws = wb[sheet_name]

        # Extract lookup mapping for LOCATION resolution
        lookup_map = self._extract_lookup_map(wb)

        # Clear managed data cells before writing (ensures rerun replaces old data cleanly)
        self._clear_daily_sheet_data(ws)

        # Sort records numerically descending by Total Data Usage
        sorted_records = sorted(
            records,
            key=lambda r: int(r.get("total_usage_bytes", 0)),
            reverse=True,
        )

        date_str = query_date.strftime("%Y-%m-%d")

        # 1. Populate Main Table (B5:E54, up to 50 rows)
        for idx, rec in enumerate(sorted_records[:50]):
            row = 5 + idx
            rec_date = rec.get("date") or date_str
            iccid = str(rec.get("iccid", "")).strip()
            usage_bytes = int(rec.get("total_usage_bytes", 0))

            ws.cell(row=row, column=2, value=idx + 1)
            ws.cell(row=row, column=3, value=rec_date)
            ws.cell(row=row, column=4, value=iccid)
            ws.cell(row=row, column=5, value=usage_bytes)

        # Total row 55
        ws.cell(row=55, column=4, value="TOTAL")
        ws.cell(row=55, column=5, value="=SUM(E5:E54)")

        # 2. Populate Top 5 Table (G5:L9, up to 5 rows)
        for idx, rec in enumerate(sorted_records[:5]):
            row = 5 + idx
            rec_date = rec.get("date") or date_str
            iccid = str(rec.get("iccid", "")).strip()
            location = lookup_map.get(iccid, "")
            usage_bytes = int(rec.get("total_usage_bytes", 0))

            ws.cell(row=row, column=7, value=idx + 1)
            ws.cell(row=row, column=8, value=rec_date)
            ws.cell(row=row, column=9, value=iccid)
            ws.cell(row=row, column=10, value=location)
            ws.cell(row=row, column=11, value=usage_bytes)
            ws.cell(row=row, column=12, value=f"=K{row}/(1024^3)")

        # 3. Populate / verify Summary Cards (H12:K13)
        ws.cell(row=12, column=8, value="TOTAL USAGE DAILY")
        ws.cell(row=12, column=9, value="Byte")
        ws.cell(row=12, column=10, value="Gigabyte")
        ws.cell(row=12, column=11, value="SESSION")

        ws.cell(row=13, column=9, value="=E55")
        ws.cell(row=13, column=10, value="=I13/(1024^3)")
        ws.cell(row=13, column=11, value="=COUNTA(D5:D54)")

        # 4. Embed dashboard screenshot at H15
        if screenshot_path is not None:
            if not screenshot_path.exists():
                raise ExcelReportError(f"Screenshot not found: {screenshot_path}")
            self._embed_screenshot(ws, screenshot_path)

        try:
            wb.save(output_path)
        except Exception as e:
            raise ExcelReportError(f"Failed to save updated workbook to {output_path}") from e

        logger.info("Successfully updated sheet '%s' in %s", sheet_name, output_path)
        return output_path

    def generate_report(
        self,
        artifact_or_path: Path | UsageReportArtifact | list[dict[str, Any]],
        screenshot_path: Path | None = None,
        query_date: date | None = None,
        output_path: Path | None = None,
    ) -> Path:
        """High-level entry point to generate/update the monthly Excel report."""
        records: list[dict[str, Any]]

        if isinstance(artifact_or_path, UsageReportArtifact):
            records = artifact_or_path.rows
            if query_date is None:
                query_date = artifact_or_path.query_date
        elif isinstance(artifact_or_path, list):
            records = artifact_or_path
        elif isinstance(artifact_or_path, (str, Path)):
            raw_path = Path(artifact_or_path)
            if not raw_path.exists():
                raise ExcelReportError(f"Source XLSX not found: {raw_path}")
            exporter = UsageQueryExporter(self.config)
            records = exporter._parse_usage_data(raw_path)
        else:
            raise ExcelReportError(f"Unsupported artifact type: {type(artifact_or_path)}")

        if query_date is None:
            if records and records[0].get("date"):
                try:
                    query_date = datetime.strptime(str(records[0]["date"]), "%Y-%m-%d").date()
                except ValueError:
                    query_date = datetime.now(self.config.get_timezone()).date()
            else:
                query_date = datetime.now(self.config.get_timezone()).date()

        if output_path is None:
            month_str = query_date.strftime("%Y%m")
            output_path = self.config.excel_output_dir / f"Daily-Data-Usage-M2M-{month_str}.xlsx"

        if not output_path.exists():
            self.prepare_template_copy(self.config.excel_template_path, output_path, query_date)

        return self.update_daily_sheet(output_path, query_date, records, screenshot_path)

    def _extract_lookup_map(self, wb: openpyxl.Workbook) -> dict[str, str]:
        """Extract all unique ICCID -> LOCATION pairs from O5:P140 across all sheets."""
        lookup_map: dict[str, str] = {}
        for name in wb.sheetnames:
            ws = wb[name]
            for row in range(4, 141):
                iccid_val = ws.cell(row=row, column=15).value
                loc_val = ws.cell(row=row, column=16).value
                if iccid_val is not None:
                    iccid_str = str(iccid_val).strip()
                    loc_str = str(loc_val).strip() if loc_val is not None else ""
                    if iccid_str:
                        if iccid_str not in lookup_map or (not lookup_map[iccid_str] and loc_str):
                            lookup_map[iccid_str] = loc_str
        return lookup_map

    def _write_lookup_table(self, ws: Any, lookup_map: dict[str, str]) -> None:
        """Write normalized lookup headers at O3:P3 and data at O5:P140 with styles."""
        cell_o3 = ws.cell(row=3, column=15, value="ICCID")
        cell_o3.font = copy(HEADER_FONT)
        cell_o3.fill = copy(HEADER_FILL)
        cell_o3.alignment = copy(HEADER_ALIGNMENT)
        cell_o3.border = copy(THIN_BORDER)

        cell_p3 = ws.cell(row=3, column=16, value="LOCATION")
        cell_p3.font = copy(HEADER_FONT)
        cell_p3.fill = copy(HEADER_FILL)
        cell_p3.alignment = copy(HEADER_ALIGNMENT)
        cell_p3.border = copy(THIN_BORDER)

        for r in range(4, 141):
            ws.cell(row=r, column=15, value=None)
            ws.cell(row=r, column=16, value=None)

        for idx, (iccid, loc) in enumerate(sorted(lookup_map.items())):
            r = 4 + idx
            if r > 140:
                break
            c_o = ws.cell(row=r, column=15, value=iccid)
            c_o.font = copy(DATA_FONT)
            c_o.border = copy(THIN_BORDER)
            c_o.alignment = copy(DATA_ALIGNMENT_LEFT)

            c_p = ws.cell(row=r, column=16, value=loc)
            c_p.font = copy(DATA_FONT)
            c_p.border = copy(THIN_BORDER)
            c_p.alignment = copy(DATA_ALIGNMENT_LEFT)

    def _clear_daily_sheet_data(self, ws: Any) -> None:
        """Clear data cells in main and top-5 tables, resetting formulas and index numbers."""
        for r in range(5, 55):
            ws.cell(row=r, column=2).value = r - 4  # NO: 1..50
            ws.cell(row=r, column=3).value = None  # Date
            ws.cell(row=r, column=4).value = None  # ICCID
            ws.cell(row=r, column=5).value = None  # Total Data Usage (bytes)

        ws.cell(row=55, column=4).value = "TOTAL"
        ws.cell(row=55, column=5).value = "=SUM(E5:E54)"

        for r in range(5, 10):
            ws.cell(row=r, column=7).value = r - 4  # NO: 1..5
            ws.cell(row=r, column=8).value = None  # Date
            ws.cell(row=r, column=9).value = None  # ICCID
            ws.cell(row=r, column=10).value = None  # LOCATION
            ws.cell(row=r, column=11).value = None  # Total Data Usage (bytes)
            ws.cell(row=r, column=12).value = f"=K{r}/(1024^3)"

        ws.cell(row=13, column=9).value = "=E55"
        ws.cell(row=13, column=10).value = "=I13/(1024^3)"
        ws.cell(row=13, column=11).value = "=COUNTA(D5:D54)"

    def _embed_screenshot(self, ws: Any, screenshot_path: Path) -> None:
        """Embed dashboard screenshot at H15, bounded to the H:L template area."""
        if hasattr(ws, "_images"):
            retained_images = []
            for img in ws._images:
                anchor = getattr(img, "anchor", None)
                if isinstance(anchor, str) and anchor.upper() == "H15":
                    continue
                if hasattr(anchor, "_from"):
                    from_anchor = getattr(anchor, "_from", None)
                    if from_anchor is not None:
                        from_col = getattr(from_anchor, "col", None)
                        from_row = getattr(from_anchor, "row", None)
                        if from_col == 7 and from_row == 14:
                            continue
                retained_images.append(img)
            ws._images = retained_images

        img = XLImage(str(screenshot_path))
        # Keep the image inside columns H:L. Preserve its aspect ratio while
        # fitting the available template width, and anchor at the exact H15 cell.
        width_units = sum(ws.column_dimensions[get_column_letter(col)].width or 8.43 for col in range(8, 13))
        target_width = max(1, int(width_units * 7))
        if img.width > target_width:
            scale = target_width / img.width
            img.width = target_width
            img.height = max(1, int(img.height * scale))
        img.anchor = OneCellAnchor(
            _from=AnchorMarker(col=7, row=14),
            ext=XDRPositiveSize2D(cx=target_width * 9525, cy=img.height * 9525),
        )
        ws.add_image(img)
