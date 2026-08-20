"""Tests for Excel report generation and template management."""

from datetime import date
from pathlib import Path

import openpyxl
import pytest
from PIL import Image as PILImage

from cmp_automation.config import Config
from cmp_automation.excel_report import ExcelReportGenerator
from cmp_automation.exceptions import ExcelReportError
from cmp_automation.usage_query import UsageReportArtifact


@pytest.fixture
def config(tmp_path: Path) -> Config:
    """Create a test config."""
    template_path = tmp_path / "template.xlsx"
    wb = openpyxl.Workbook()
    for i in range(1, 6):
        wb.create_sheet(f"{i:02d}")
    wb.create_sheet("Master")
    if "Sheet" in wb.sheetnames:
        del wb["Sheet"]

    ws01 = wb["01"]
    ws01.cell(row=5, column=15, value="8962000000000001")
    ws01.cell(row=5, column=16, value="Jakarta")
    ws01.cell(row=6, column=15, value="8962000000000002")
    ws01.cell(row=6, column=16, value="Surabaya")

    ws_master = wb["Master"]
    ws_master.cell(row=5, column=15, value="8962000000000003")
    ws_master.cell(row=5, column=16, value="Bandung")

    wb.save(template_path)

    return Config(
        cmp_username="test",
        cmp_password="test",
        gmf_email="test@test.com",
        gmf_password="test",
        firefox_profile_dir=tmp_path / "profile",
        download_dir=tmp_path / "downloads",
        excel_output_dir=tmp_path / "reports",
        excel_template_path=template_path,
        timezone="Asia/Jakarta",
    )


@pytest.fixture
def generator(config: Config) -> ExcelReportGenerator:
    """Create an ExcelReportGenerator."""
    return ExcelReportGenerator(config)


@pytest.fixture
def sample_screenshot(tmp_path: Path) -> Path:
    """Create a sample PNG image."""
    img_path = tmp_path / "screenshot.png"
    img = PILImage.new("RGB", (800, 400), color="blue")
    img.save(img_path)
    return img_path


class TestExcelReportGenerator:
    """Tests for Excel report generation."""

    def test_prepare_template_copy_normalizes_lookup(
        self, generator: ExcelReportGenerator, config: Config, tmp_path: Path
    ) -> None:
        """Test that prepare_template_copy aggregates lookups across sheets and normalizes them."""
        out_path = tmp_path / "output_monthly.xlsx"
        generator.prepare_template_copy(config.excel_template_path, out_path)

        assert out_path.exists()
        wb = openpyxl.load_workbook(out_path)

        for name in ["01", "02", "03", "04", "05", "Master"]:
            ws = wb[name]
            assert ws.cell(row=3, column=15).value == "ICCID"
            assert ws.cell(row=3, column=16).value == "LOCATION"
            assert ws.cell(row=4, column=15).value == "8962000000000001"
            assert ws.cell(row=4, column=16).value == "Jakarta"
            assert ws.cell(row=5, column=15).value == "8962000000000002"
            assert ws.cell(row=5, column=16).value == "Surabaya"
            assert ws.cell(row=6, column=15).value == "8962000000000003"
            assert ws.cell(row=6, column=16).value == "Bandung"

    def test_update_daily_sheet_main_table_and_top5(
        self,
        generator: ExcelReportGenerator,
        config: Config,
        sample_screenshot: Path,
        tmp_path: Path,
    ) -> None:
        """Test writing daily records, Top 5 with locations, formulas, and screenshot."""
        out_path = tmp_path / "Daily-Data-Usage-M2M-202603.xlsx"
        generator.prepare_template_copy(config.excel_template_path, out_path)

        records = [
            {"date": "2026-03-01", "iccid": "8962000000000002", "total_usage_bytes": 5000000000},
            {"date": "2026-03-01", "iccid": "8962000000000001", "total_usage_bytes": 10000000000},
            {"date": "2026-03-01", "iccid": "8962000000000003", "total_usage_bytes": 2000000000},
        ]

        generator.update_daily_sheet(
            output_path=out_path,
            query_date=date(2026, 3, 1),
            records=records,
            screenshot_path=sample_screenshot,
        )

        wb = openpyxl.load_workbook(out_path)
        ws = wb["01"]

        # Main table (sorted descending): row 5 should have 10GB, row 6 5GB, row 7 2GB
        assert ws.cell(row=5, column=2).value == 1
        assert ws.cell(row=5, column=3).value == "2026-03-01"
        assert ws.cell(row=5, column=4).value == "8962000000000001"
        assert ws.cell(row=5, column=5).value == 10000000000

        assert ws.cell(row=6, column=2).value == 2
        assert ws.cell(row=6, column=4).value == "8962000000000002"
        assert ws.cell(row=6, column=5).value == 5000000000

        # Total formula
        assert ws.cell(row=55, column=4).value == "TOTAL"
        assert ws.cell(row=55, column=5).value == "=SUM(E5:E54)"

        # Top 5 table
        assert ws.cell(row=5, column=7).value == 1
        assert ws.cell(row=5, column=9).value == "8962000000000001"
        assert ws.cell(row=5, column=10).value == "Jakarta"  # Looked up from table
        assert ws.cell(row=5, column=11).value == 10000000000
        assert ws.cell(row=5, column=12).value == "=K5/(1024^3)"

        assert ws.cell(row=6, column=10).value == "Surabaya"
        assert ws.cell(row=7, column=10).value == "Bandung"

        # Summary cards
        assert ws.cell(row=13, column=9).value == "=E55"
        assert ws.cell(row=13, column=10).value == "=I13/(1024^3)"
        assert ws.cell(row=13, column=11).value == "=COUNTA(D5:D54)"

        # Screenshot check
        assert hasattr(ws, "_images")
        assert len(ws._images) == 1
        image = ws._images[0]
        assert image.anchor._from.col == 7
        assert image.anchor._from.row == 14
        max_width_emu = sum(
            ws.column_dimensions[column].width or 8.43 for column in "HIJKL"
        ) * 7 * 9525
        assert image.anchor.ext.cx <= max_width_emu

    def test_rerun_overwrites_cleanly(
        self, generator: ExcelReportGenerator, config: Config, tmp_path: Path
    ) -> None:
        """Test that rerunning on the same day cleanly replaces prior data."""
        out_path = tmp_path / "Daily-Data-Usage-M2M-202603.xlsx"
        generator.prepare_template_copy(config.excel_template_path, out_path)

        initial_records = [
            {"date": "2026-03-01", "iccid": f"896200000000000{i}", "total_usage_bytes": i * 1000}
            for i in range(1, 4)
        ]
        generator.update_daily_sheet(out_path, date(2026, 3, 1), initial_records)

        new_records = [
            {"date": "2026-03-01", "iccid": "8962000000000001", "total_usage_bytes": 99999}
        ]
        generator.update_daily_sheet(out_path, date(2026, 3, 1), new_records)

        wb = openpyxl.load_workbook(out_path)
        ws = wb["01"]

        assert ws.cell(row=5, column=4).value == "8962000000000001"
        assert ws.cell(row=5, column=5).value == 99999
        assert ws.cell(row=6, column=4).value is None
        assert ws.cell(row=6, column=5).value is None
        assert ws.cell(row=7, column=4).value is None

    def test_generate_report_from_artifact(
        self,
        generator: ExcelReportGenerator,
        config: Config,
        sample_screenshot: Path,
        tmp_path: Path,
    ) -> None:
        """Test high-level generate_report with UsageReportArtifact."""
        artifact = UsageReportArtifact(
            raw_path=tmp_path / "raw.xlsx",
            query_date=date(2026, 3, 2),
            rows=[{"date": "2026-03-02", "iccid": "8962000000000001", "total_usage_bytes": 12345}],
        )

        report_path = generator.generate_report(
            artifact_or_path=artifact,
            screenshot_path=sample_screenshot,
            query_date=date(2026, 3, 2),
        )

        assert report_path.exists()
        assert "Daily-Data-Usage-M2M-202603.xlsx" in report_path.name

        wb = openpyxl.load_workbook(report_path)
        ws = wb["02"]
        assert ws.cell(row=5, column=4).value == "8962000000000001"
        assert ws.cell(row=5, column=5).value == 12345

    def test_missing_template_raises(self, generator: ExcelReportGenerator, tmp_path: Path) -> None:
        """Test missing source template raises ExcelReportError."""
        with pytest.raises(ExcelReportError, match="not found"):
            generator.prepare_template_copy(Path("/nonexistent/tpl.xlsx"), tmp_path / "out.xlsx")

    def test_missing_screenshot_raises(
        self, generator: ExcelReportGenerator, config: Config, tmp_path: Path
    ) -> None:
        """Test missing screenshot file raises ExcelReportError."""
        out_path = tmp_path / "out.xlsx"
        generator.prepare_template_copy(config.excel_template_path, out_path)
        with pytest.raises(ExcelReportError, match="Screenshot not found"):
            generator.update_daily_sheet(
                output_path=out_path,
                query_date=date(2026, 3, 1),
                records=[],
                screenshot_path=Path("/nonexistent/pic.png"),
            )

    def test_corrupt_template_raises(self, generator: ExcelReportGenerator, tmp_path: Path) -> None:
        """Test corrupt template raises ExcelReportError."""
        bad_tpl = tmp_path / "bad.xlsx"
        bad_tpl.write_bytes(b"not a zip file")
        with pytest.raises(ExcelReportError, match="not a valid XLSX"):
            generator.prepare_template_copy(bad_tpl, tmp_path / "out.xlsx")
