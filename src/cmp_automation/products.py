"""Products export compatibility module for CMP Portal.

Provides strict backwards compatibility alias for ProductsExporter pointing
directly to UsageQueryExporter with no fallback to legacy Products or Billing Status pages.
"""

from .usage_query import UsageQueryExporter, UsageReportArtifact

# Strict Usage Query adapter alias: No Products / Billing Status fallback.
ProductsExporter = UsageQueryExporter

__all__ = [
    "ProductsExporter",
    "UsageReportArtifact",
]
