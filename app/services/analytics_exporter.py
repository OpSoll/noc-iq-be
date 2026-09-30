# app/services/analytics_exporter.py
import csv
import io
from datetime import datetime
from typing import List, Literal

from app.models.payment import PaymentTransaction


def generate_accounting_report_csv(payments: List[PaymentTransaction]) -> str:
    """Generate a CSV accounting report from a list of payment transactions."""
    output = io.StringIO()
    writer = csv.writer(output)

    # Header row
    writer.writerow([
        "Transaction ID",
        "Status",
        "Type",
        "Amount",
        "Asset",
        "Created At",
        "Confirmed At",
        "Outage ID",
    ])

    # Data rows
    for p in payments:
        writer.writerow([
            p.id,
            p.status,
            p.type,
            p.amount,
            f"{p.asset_code}:{p.asset_issuer}" if p.asset_issuer else p.asset_code,
            p.created_at.isoformat(),
            p.confirmed_at.isoformat() if p.confirmed_at else "N/A",
            p.outage_id,
        ])

    return output.getvalue()


def generate_accounting_report_pdf(payments: List[PaymentTransaction]) -> bytes:
    """Generate a PDF accounting report from a list of payment transactions."""
    # Placeholder for PDF generation logic
    raise NotImplementedError("PDF export is not yet implemented.")


class AnalyticsExporter:
    """Service for exporting analytics reports in various formats."""

    def export(
        self,
        format: Literal["csv", "pdf"],
        payments: List[PaymentTransaction],
        date_from: datetime,
        date_to: datetime,
    ) -> str | bytes:
        """Export a payment transaction summary report."""
        if format == "csv":
            return generate_accounting_report_csv(payments)
        elif format == "pdf":
            return generate_accounting_report_pdf(payments)
        else:
            raise ValueError(f"Unsupported export format: {format}")