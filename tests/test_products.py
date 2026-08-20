"""Tests for ProductsExporter backwards compatibility alias."""

from cmp_automation.config import Config
from cmp_automation.products import ProductsExporter, UsageReportArtifact
from cmp_automation.usage_query import UsageQueryExporter


def test_products_exporter_is_usage_query_exporter():
    """Verify ProductsExporter strictly aliases UsageQueryExporter."""
    assert ProductsExporter is UsageQueryExporter


def test_products_exporter_instantiation(tmp_path):
    """Verify ProductsExporter initializes UsageQueryExporter with config."""
    template_file = tmp_path / "template.xlsx"
    template_file.touch()
    cfg = Config(
        cmp_username="test",
        cmp_password="test",
        gmf_email="test@test.com",
        gmf_password="test",
        firefox_profile_dir=str(tmp_path / "profile"),
        download_dir=str(tmp_path / "downloads"),
        excel_template_path=str(template_file),
        excel_output_dir=str(tmp_path / "reports"),
        timezone="Asia/Jakarta",
    )
    exporter = ProductsExporter(cfg, diagnose_export=True)
    assert isinstance(exporter, UsageQueryExporter)
    assert exporter.config == cfg
    assert exporter.diagnose_export is True


def test_usage_report_artifact_reexported():
    """Verify UsageReportArtifact is exported from products module."""
    assert UsageReportArtifact is not None
