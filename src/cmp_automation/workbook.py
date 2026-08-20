"""Monthly workbook service with day sheet management, lookups, summaries, top5, and image placement."""

import logging
import re
import zipfile
from copy import copy as shallow_copy
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.worksheet import Worksheet

from .config import Config
from .exceptions import WorkbookError

logger = logging.getLogger(__name__)

# Constants for standard workbook structure
LOOKUP_START_ROW = 3
LOOKUP_END_ROW = 140
LOOKUP_KEY_COL = 15  # Column O
LOOKUP_VAL_COL = 16  # Column P
LOOKUP_RANGE = "O3:P140"
DEFAULT_IMAGE_CELL = "H15"

MAIN_DATA_START_ROW = 5
MAIN_DATA_END_ROW = 54
MAIN_TOTAL_ROW = 55

TOP5_START_ROW = 5
TOP5_END_ROW = 9

SUMMARY_HEADER_ROW = 12
SUMMARY_DATA_ROW = 13

BLUE_HEADER_FILL = "00B0F0"
BLUE_HEADER_FONT_NAME = "Calibri"
BLUE_HEADER_FONT_SIZE = 11


def get_monthly_workbook_path(
    base_dir: Path,
    target_date: date | datetime | None = None,
    filename_prefix: str = "Daily-Data-Usage-M2M",
) -> Path:
    """Generate standardized monthly workbook path for given date/month.

    Format: {filename_prefix}-{YYYYMM}.xlsx
    Example: Daily-Data-Usage-M2M-202609.xlsx
    """
    if target_date is None:
        target_date = date.today()
    elif isinstance(target_date, datetime):
        target_date = target_date.date()

    filename = f"{filename_prefix}-{target_date.year:04d}{target_date.month:02d}.xlsx"
    return base_dir / filename


def format_day_sheet_name(target_day: int | str | date | datetime) -> str:
    """Format day representation into standard 2-digit day sheet name (e.g. '01', '15')."""
    if isinstance(target_day, (datetime, date)):
        return f"{target_day.day:02d}"
    if isinstance(target_day, int):
        if not 1 <= target_day <= 31:
            raise ValueError(f"Invalid day number: {target_day}. Must be between 1 and 31.")
        return f"{target_day:02d}"
    if isinstance(target_day, str):
        cleaned = target_day.strip()
        if cleaned.isdigit():
            day_num = int(cleaned)
            if not 1 <= day_num <= 31:
                raise ValueError(f"Invalid day number: {target_day}. Must be between 1 and 31.")
            return f"{day_num:02d}"
        raise ValueError(f"Invalid day string: {target_day}")
    raise TypeError(f"Unsupported day type: {type(target_day)}")


def parse_numeric_value(val: Any) -> float:
    """Extract float numeric value from strings like '1,024.50 MB', '500 GB', '123', or numeric types."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        cleaned = val.replace(",", "").strip()
        match = re.search(r"[-+]?\d*\.?\d+", cleaned)
        if match:
            try:
                return float(match.group())
            except ValueError:
                return 0.0
    return 0.0


def parse_usage_bytes(record: dict[str, Any] | list[Any] | tuple[Any, ...]) -> int:
    """Extract integer total data usage in bytes from record dict or row sequence."""
    if isinstance(record, dict):
        for k, v in record.items():
            k_norm = str(k).lower().replace("_", " ").replace("-", " ").strip()
            if "total data usage" in k_norm or "data usage" in k_norm or k_norm in ("usage", "bytes", "usage_bytes"):
                if v is not None:
                    return int(parse_numeric_value(v))
        for k, v in record.items():
            if "byte" in str(k).lower() and v is not None:
                return int(parse_numeric_value(v))
    elif isinstance(record, (list, tuple)):
        for item in reversed(record):
            if isinstance(item, (int, float)):
                return int(item)
            if isinstance(item, str) and item.replace(",", "").strip().isdigit():
                return int(item.replace(",", "").strip())
    return 0


def extract_data_from_xlsx(file_path: Path | str) -> list[dict[str, Any]]:
    """Extract records from raw CMP Daily Usage export XLSX file."""
    p = Path(file_path)
    if not p.exists():
        raise WorkbookError(f"Raw XLSX file not found: {p}")

    try:
        wb = load_workbook(p, data_only=True)
    except zipfile.BadZipFile as e:
        raise WorkbookError(f"Raw XLSX file is not a valid zip/xlsx: {p}") from e
    except Exception as e:
        raise WorkbookError(f"Failed to load raw XLSX file {p}: {e}") from e

    try:
        ws = wb.active
        if ws is None:
            return []

        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []

        header_idx = -1
        headers: list[str] = []
        for idx, row in enumerate(rows):
            row_strs = [str(c).lower().strip() for c in row if c is not None]
            if any("iccid" in s for s in row_strs) or any("usage" in s for s in row_strs):
                header_idx = idx
                headers = [str(c).strip() if c is not None else f"col_{i}" for i, c in enumerate(row)]
                break

        if header_idx == -1:
            headers = [str(c).strip() if c is not None else f"col_{i}" for i, c in enumerate(rows[0])]
            data_rows = rows[1:]
        else:
            data_rows = rows[header_idx + 1 :]

        records: list[dict[str, Any]] = []
        for row in data_rows:
            if not any(c is not None for c in row):
                continue
            row_dict: dict[str, Any] = {}
            for col_idx, val in enumerate(row):
                if col_idx < len(headers):
                    col_name = headers[col_idx]
                    row_dict[col_name] = val
            records.append(row_dict)

        return records
    finally:
        wb.close()

    """Extract float numeric value from strings like '1,024.50 MB', '500 GB', '123', or numeric types."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        cleaned = val.replace(",", "").strip()
        match = re.search(r"[-+]?\d*\.?\d+", cleaned)
        if match:
            try:
                return float(match.group())
            except ValueError:
                return 0.0
    return 0.0


def read_lookup_table(
    ws: Worksheet,
    start_row: int = LOOKUP_START_ROW,
    end_row: int = LOOKUP_END_ROW,
    key_col: int = LOOKUP_KEY_COL,
    val_col: int = LOOKUP_VAL_COL,
) -> dict[str, str]:
    """Read key-value lookup pairs from specified worksheet range."""
    lookup: dict[str, str] = {}
    for row in range(start_row, end_row + 1):
        k_cell = ws.cell(row=row, column=key_col).value
        v_cell = ws.cell(row=row, column=val_col).value

        if k_cell is not None:
            k_str = str(k_cell).strip()
            v_str = str(v_cell).strip() if v_cell is not None else ""

            # Skip header names
            if k_str.upper() in ("ICCID", "KEY", "NO") or not k_str:
                continue

            if k_str not in lookup:
                lookup[k_str] = v_str
    return lookup


def _get_blue_header_style() -> tuple[Font, PatternFill, Alignment, Border]:
    """Construct standardized blue header styling matching adjacent blue table header."""
    font = Font(name=BLUE_HEADER_FONT_NAME, size=BLUE_HEADER_FONT_SIZE, bold=True, color="000000")
    fill = PatternFill(start_color=f"FF{BLUE_HEADER_FILL}", end_color=f"FF{BLUE_HEADER_FILL}", fill_type="solid")
    align = Alignment(horizontal="center", vertical="center")
    thin_side = Side(style="thin", color="000000")
    border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)
    return font, fill, align, border


def _get_data_cell_style() -> tuple[Font, Alignment, Border]:
    """Construct standardized data cell styling for lookup table."""
    font = Font(name="Calibri", size=11, bold=False, color="000000")
    align = Alignment(horizontal="center", vertical="center")
    thin_side = Side(style="thin", color="000000")
    border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)
    return font, align, border


def write_lookup_table(
    ws: Worksheet,
    lookup_dict: dict[str, str] | list[tuple[str, str]],
    start_row: int = LOOKUP_START_ROW,
    end_row: int = LOOKUP_END_ROW,
    key_col: int = LOOKUP_KEY_COL,
    val_col: int = LOOKUP_VAL_COL,
    apply_styles: bool = True,
) -> None:
    """Write normalized lookup table into O3:P140 with headers O3='ICCID', P3='LOCATION' and styled cells."""
    if isinstance(lookup_dict, dict):
        pairs = list(lookup_dict.items())
    else:
        pairs = list(lookup_dict)

    h_font, h_fill, h_align, h_border = _get_blue_header_style()
    d_font, d_align, d_border = _get_data_cell_style()

    # Write Header at start_row (Row 3)
    o3 = ws.cell(row=start_row, column=key_col)
    p3 = ws.cell(row=start_row, column=val_col)
    o3.value = "ICCID"
    p3.value = "LOCATION"

    if apply_styles:
        for cell in (o3, p3):
            cell.font = shallow_copy(h_font)
            cell.fill = shallow_copy(h_fill)
            cell.alignment = shallow_copy(h_align)
            cell.border = shallow_copy(h_border)

    # Write Data at start_row + 1 through end_row (Rows 4 to 140)
    data_start = start_row + 1
    for idx, row in enumerate(range(data_start, end_row + 1)):
        o_cell = ws.cell(row=row, column=key_col)
        p_cell = ws.cell(row=row, column=val_col)

        if idx < len(pairs):
            k, v = pairs[idx]
            o_cell.value = str(k).strip()
            p_cell.value = str(v).strip()
        else:
            o_cell.value = None
            p_cell.value = None

        if apply_styles:
            for cell in (o_cell, p_cell):
                cell.font = shallow_copy(d_font)
                cell.alignment = shallow_copy(d_align)
                cell.border = shallow_copy(d_border)



def clean_day_sheet(
    ws: Worksheet,
    preserve_styles: bool = True,
    preserve_lookup: bool = True,
    clear_images: bool = True,
) -> None:
    """Clean daily report area while strictly preserving layout, merges, fonts, borders, fills, and lookups."""
    # Main table data area: B5:E54
    for row in range(MAIN_DATA_START_ROW, MAIN_DATA_END_ROW + 1):
        b_cell = ws.cell(row=row, column=2)  # NO
        b_cell.value = row - MAIN_DATA_START_ROW + 1
        ws.cell(row=row, column=3).value = None  # Date
        ws.cell(row=row, column=4).value = None  # ICCID
        ws.cell(row=row, column=5).value = None  # Usage

    # Main total row 55
    ws.cell(row=MAIN_TOTAL_ROW, column=4).value = "TOTAL"
    ws.cell(row=MAIN_TOTAL_ROW, column=5).value = "=SUM(E5:E54)"

    # Top 5 table: G5:L9
    for row in range(TOP5_START_ROW, TOP5_END_ROW + 1):
        rank = row - TOP5_START_ROW + 1
        ws.cell(row=row, column=7).value = rank  # G: NO
        ws.cell(row=row, column=8).value = None  # H: Date
        ws.cell(row=row, column=9).value = None  # I: ICCID
        ws.cell(row=row, column=10).value = None  # J: LOCATION
        ws.cell(row=row, column=11).value = None  # K: Bytes
        ws.cell(row=row, column=12).value = f"=K{row}/(1024^3)"  # L: GB formula

    # Summary table: H12:K13
    ws.cell(row=SUMMARY_HEADER_ROW, column=8).value = "TOTAL USAGE DAILY"
    ws.cell(row=SUMMARY_HEADER_ROW, column=9).value = "Byte"
    ws.cell(row=SUMMARY_HEADER_ROW, column=10).value = "Gigabyte"
    ws.cell(row=SUMMARY_HEADER_ROW, column=11).value = "SESSION"

    ws.cell(row=SUMMARY_DATA_ROW, column=8).value = None
    ws.cell(row=SUMMARY_DATA_ROW, column=9).value = "=E55"
    ws.cell(row=SUMMARY_DATA_ROW, column=10).value = "=I13/(1024^3)"
    ws.cell(row=SUMMARY_DATA_ROW, column=11).value = "=COUNTA(D5:D54)"

    # Clear lookup if requested (by default preserved)
    if not preserve_lookup:
        for row in range(LOOKUP_START_ROW, LOOKUP_END_ROW + 1):
            ws.cell(row=row, column=LOOKUP_KEY_COL).value = None
            ws.cell(row=row, column=LOOKUP_VAL_COL).value = None

    # Clear images
    if clear_images and hasattr(ws, "_images"):
        ws._images.clear()


def insert_dashboard_image(
    ws: Worksheet,
    screenshot_path: Path | str,
    cell: str = DEFAULT_IMAGE_CELL,
) -> None:
    """Insert dashboard screenshot anchored exactly at specified cell (default H15)."""
    p = Path(screenshot_path)
    if not p.exists():
        raise WorkbookError(f"Screenshot image not found: {p}")

    try:
        # On rerun or update, remove any existing images on worksheet
        if hasattr(ws, "_images"):
            ws._images.clear()

        img = XLImage(str(p))
        img.anchor = cell
        ws.add_image(img, cell)
    except Exception as e:
        raise WorkbookError(f"Failed to insert dashboard image into sheet: {e}") from e


def calculate_top5(
    records: list[dict[str, Any]],
    lookup_dict: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Sort records descending by usage and extract Top 5 with resolved LOCATION."""
    if lookup_dict is None:
        lookup_dict = {}

    sorted_records = sorted(records, key=parse_usage_bytes, reverse=True)
    top5: list[dict[str, Any]] = []

    for rank, rec in enumerate(sorted_records[:5], start=1):
        iccid = ""
        for k, v in rec.items():
            if "iccid" in str(k).lower():
                iccid = str(v).strip() if v is not None else ""
                break

        usage = parse_usage_bytes(rec)
        loc = lookup_dict.get(iccid, "")
        if not loc and iccid:
            logger.warning("Unmatched location for ICCID in top 5 lookup")

        top5.append({
            "rank": rank,
            "iccid": iccid,
            "location": loc,
            "usage_bytes": usage,
            "raw_record": rec,
        })

    return top5


def calculate_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate summary metrics (total bytes, total GB, session count) from records."""
    total_bytes = sum(parse_usage_bytes(r) for r in records)
    total_gb = total_bytes / (1024**3)
    session_count = len(records)
    return {
        "total_bytes": total_bytes,
        "total_gb": total_gb,
        "session_count": session_count,
    }



def write_data_section(
    ws: Worksheet,
    records: list[dict[str, Any]],
    target_date: date | datetime | str,
) -> None:
    """Populate main daily usage table into B5:E54 sorted descending by usage."""
    if isinstance(target_date, (date, datetime)):
        date_str = target_date.strftime("%Y-%m-%d")
    else:
        date_str = str(target_date).strip()

    sorted_records = sorted(records, key=parse_usage_bytes, reverse=True)

    for idx, row in enumerate(range(MAIN_DATA_START_ROW, MAIN_DATA_END_ROW + 1)):
        no_cell = ws.cell(row=row, column=2)
        date_cell = ws.cell(row=row, column=3)
        iccid_cell = ws.cell(row=row, column=4)
        usage_cell = ws.cell(row=row, column=5)

        no_cell.value = idx + 1

        if idx < len(sorted_records):
            rec = sorted_records[idx]
            iccid = ""
            rec_date = date_str
            for k, v in rec.items():
                k_lower = str(k).lower()
                if "iccid" in k_lower:
                    iccid = str(v).strip() if v is not None else ""
                elif "date" in k_lower and v is not None:
                    rec_date = str(v).strip()

            usage_val = parse_usage_bytes(rec)
            date_cell.value = rec_date
            iccid_cell.value = iccid
            usage_cell.value = usage_val
        else:
            date_cell.value = None
            iccid_cell.value = None
            usage_cell.value = None

    # Total row 55
    ws.cell(row=MAIN_TOTAL_ROW, column=4).value = "TOTAL"
    ws.cell(row=MAIN_TOTAL_ROW, column=5).value = "=SUM(E5:E54)"


def write_top5_section(
    ws: Worksheet,
    top5_records: list[dict[str, Any]],
    target_date: date | datetime | str,
    lookup_dict: dict[str, str] | None = None,
) -> None:
    """Populate Top 5 daily table into G5:L9 with resolved LOCATION."""
    if lookup_dict is None:
        lookup_dict = {}

    if isinstance(target_date, (date, datetime)):
        date_str = target_date.strftime("%Y-%m-%d")
    else:
        date_str = str(target_date).strip()

    for idx, row in enumerate(range(TOP5_START_ROW, TOP5_END_ROW + 1)):
        rank = idx + 1
        ws.cell(row=row, column=7).value = rank  # G: NO

        if idx < len(top5_records):
            item = top5_records[idx]
            if "iccid" in item and "usage_bytes" in item:
                iccid = str(item.get("iccid", "")).strip()
                loc = str(item.get("location", "")).strip() or lookup_dict.get(iccid, "")
                usage = int(item.get("usage_bytes", 0))
            else:
                iccid = ""
                for k, v in item.items():
                    if "iccid" in str(k).lower():
                        iccid = str(v).strip() if v is not None else ""
                        break
                loc = lookup_dict.get(iccid, "")
                usage = parse_usage_bytes(item)

            if not loc and iccid:
                logger.warning("Top 5 record has unmatched location for ICCID")

            ws.cell(row=row, column=8).value = date_str  # H: Date
            ws.cell(row=row, column=9).value = iccid  # I: ICCID
            ws.cell(row=row, column=10).value = loc  # J: LOCATION
            ws.cell(row=row, column=11).value = usage  # K: Bytes
            ws.cell(row=row, column=12).value = f"=K{row}/(1024^3)"  # L: GB formula
        else:
            ws.cell(row=row, column=8).value = None
            ws.cell(row=row, column=9).value = None
            ws.cell(row=row, column=10).value = None
            ws.cell(row=row, column=11).value = None
            ws.cell(row=row, column=12).value = f"=K{row}/(1024^3)"


def write_summary_section(
    ws: Worksheet,
    summary_data: dict[str, Any] | None = None,
) -> None:
    """Populate Daily Summary section into H12:K13."""
    ws.cell(row=SUMMARY_HEADER_ROW, column=8).value = "TOTAL USAGE DAILY"
    ws.cell(row=SUMMARY_HEADER_ROW, column=9).value = "Byte"
    ws.cell(row=SUMMARY_HEADER_ROW, column=10).value = "Gigabyte"
    ws.cell(row=SUMMARY_HEADER_ROW, column=11).value = "SESSION"

    ws.cell(row=SUMMARY_DATA_ROW, column=8).value = None
    ws.cell(row=SUMMARY_DATA_ROW, column=9).value = "=E55"
    ws.cell(row=SUMMARY_DATA_ROW, column=10).value = "=I13/(1024^3)"
    ws.cell(row=SUMMARY_DATA_ROW, column=11).value = "=COUNTA(D5:D54)"



class MonthlyWorkbookService:
    """Production service managing monthly Excel workbook, daily sheets, lookups, summaries, top5, and image placement."""

    def __init__(
        self,
        config: Config | None = None,
        template_path: Path | str | None = None,
        output_dir: Path | str | None = None,
    ) -> None:
        self.config = config
        self.template_path = Path(template_path) if template_path else Path("config/Daily-Data-Usage-M2M.xlsx")
        self.output_dir = Path(output_dir) if output_dir else Path("output")

    def get_monthly_workbook_path(self, target_date: date | datetime | None = None) -> Path:
        """Generate standardized monthly workbook path for given target date."""
        return get_monthly_workbook_path(self.output_dir, target_date)

    def extract_lookup_from_workbook(self, wb: Workbook) -> dict[str, str]:
        """Scan workbook sheets for unique ICCID-to-LOCATION pairs from O5:P140 or O3:P140."""
        lookup: dict[str, str] = {}
        for sname in wb.sheetnames:
            ws = wb[sname]
            sheet_lookup = read_lookup_table(ws, start_row=3, end_row=LOOKUP_END_ROW)
            for k, v in sheet_lookup.items():
                if k and k not in lookup:
                    lookup[k] = v
        return lookup

    def normalize_lookup_table(
        self,
        wb: Workbook,
        lookup_dict: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Normalize lookup table O3:P140 across all day sheets and return lookup dict."""
        if lookup_dict is None or not lookup_dict:
            lookup_dict = self.extract_lookup_from_workbook(wb)

        for sname in wb.sheetnames:
            if sname == "Master" or (sname.isdigit() and 1 <= int(sname) <= 31):
                ws = wb[sname]
                write_lookup_table(ws, lookup_dict)

        return lookup_dict

    def clean_all_day_sheets(self, wb: Workbook) -> None:
        """Clean data areas on all day sheets '01'..'31' while preserving layout and lookups."""
        for sname in wb.sheetnames:
            if sname.isdigit() and 1 <= int(sname) <= 31:
                ws = wb[sname]
                clean_day_sheet(ws, preserve_styles=True, preserve_lookup=True, clear_images=True)

    def load_or_create_monthly_workbook(
        self,
        target_date: date | datetime,
    ) -> tuple[Workbook, bool]:
        """Load existing monthly workbook or initialize new one from template.

        Returns:
            tuple of (Workbook, is_newly_created)
        """
        output_path = self.get_monthly_workbook_path(target_date)

        if output_path.exists():
            try:
                wb = load_workbook(output_path)
                return wb, False
            except Exception as e:
                raise WorkbookError(f"Failed to load existing monthly workbook {output_path}: {e}") from e

        if not self.template_path.exists():
            example_fallback = self.template_path.with_name(
                self.template_path.stem + ".example" + self.template_path.suffix
            )
            if example_fallback.exists() and example_fallback.is_file():
                self.template_path = example_fallback
            else:
                raise WorkbookError(f"Template workbook not found: {self.template_path}")

        try:
            wb = load_workbook(self.template_path)
        except Exception as e:
            raise WorkbookError(f"Failed to load template workbook {self.template_path}: {e}") from e

        lookup = self.extract_lookup_from_workbook(wb)
        self.clean_all_day_sheets(wb)
        self.normalize_lookup_table(wb, lookup)

        return wb, True


    def process_daily_data(
        self,
        target_date: date | datetime,
        records_or_file: list[dict[str, Any]] | Path | str,
        screenshot_path: Path | str | None = None,
        output_path: Path | str | None = None,
    ) -> Path:
        """Process daily usage data and update/create the monthly workbook.

        - Descending sort of records
        - Populates B5:E54 main table
        - Populates G5:L9 top 5 table with looked up location
        - Populates H12:K13 summary table
        - Places dashboard screenshot at H15
        - Preserves all other day sheets and lookup tables
        """
        if isinstance(records_or_file, (str, Path)):
            records = extract_data_from_xlsx(records_or_file)
        else:
            records = records_or_file

        if output_path is None:
            dest_path = self.get_monthly_workbook_path(target_date)
        else:
            dest_path = Path(output_path)

        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # Load or create monthly workbook
        wb, is_new = self.load_or_create_monthly_workbook(target_date)

        try:
            day_sheet_name = format_day_sheet_name(target_date)
            if day_sheet_name not in wb.sheetnames:
                raise WorkbookError(
                    f"Day sheet '{day_sheet_name}' not found in workbook. Available sheets: {wb.sheetnames}"
                )

            ws = wb[day_sheet_name]

            # Get lookup mapping
            lookup_dict = self.extract_lookup_from_workbook(wb)

            # Clean target day sheet for rerun / new write
            clean_day_sheet(ws, preserve_styles=True, preserve_lookup=True, clear_images=True)

            # Ensure lookup is normalized
            write_lookup_table(ws, lookup_dict)

            # Write main data section (descending)
            write_data_section(ws, records, target_date)

            # Calculate and write Top 5
            top5 = calculate_top5(records, lookup_dict)
            write_top5_section(ws, top5, target_date, lookup_dict)

            # Write summary
            write_summary_section(ws)

            # Insert screenshot if provided
            if screenshot_path is not None:
                insert_dashboard_image(ws, screenshot_path, cell=DEFAULT_IMAGE_CELL)

            # Save workbook
            wb.save(dest_path)
            return dest_path
        finally:
            wb.close()

    def process_daily_run(
        self,
        target_date: date | datetime,
        records_or_file: list[dict[str, Any]] | Path | str,
        screenshot_path: Path | str | None = None,
        output_path: Path | str | None = None,
    ) -> Path:
        """Alias for process_daily_data."""
        return self.process_daily_data(
            target_date=target_date,
            records_or_file=records_or_file,
            screenshot_path=screenshot_path,
            output_path=output_path,
        )

