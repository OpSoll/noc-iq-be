import csv
import io
import logging
from typing import Optional
from urllib.parse import quote

from app.core.config import settings
from app.services.sla import calculate_sla
from app.models.enums import OutageStatus

logger = logging.getLogger(__name__)

EXPLORER_TRANSACTION_URLS = {
    "testnet": "https://explorer.stellar.org/#/transaction/",
    "mainnet": "https://stellar.expert/explorer/public/tx/",
}


def build_transaction_explorer_url(
    transaction_hash: str,
    network: Optional[str] = None,
) -> Optional[str]:
    """Return a network-specific transaction link, or None for unknown networks."""
    network_name = (network or settings.STELLAR_NETWORK).strip().lower()
    base_url = EXPLORER_TRANSACTION_URLS.get(network_name)
    if not base_url:
        logger.warning("explorer.transaction_url.unsupported_network")
        return None
    return f"{base_url}{quote(str(transaction_hash).strip(), safe='')}"


def export_outages(outages: list, format: str):
    if format == "json":
        items = []
        for outage in outages:
            entry = outage.dict()

            if outage.status == OutageStatus.resolved:
                entry["sla"] = calculate_sla(
                    outage_id=outage.id,
                    severity=outage.severity.value,
                    mttr_minutes=outage.mttr_minutes,
                )

            items.append(entry)

        return items

    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow([
            "id",
            "service",
            "severity",
            "status",
            "started_at",
            "mttr_minutes",
            "sla_status",
            "sla_amount",
        ])

        for outage in outages:
            sla_status = ""
            sla_amount = ""

            if outage.status == OutageStatus.resolved:
                sla = calculate_sla(
                    outage_id=outage.id,
                    severity=outage.severity.value,
                    mttr_minutes=outage.mttr_minutes,
                )
                sla_status = sla["status"]
                sla_amount = sla["amount"]

            writer.writerow([
                outage.id,
                outage.service,
                outage.severity.value,
                outage.status.value,
                outage.started_at,
                outage.mttr_minutes,
                sla_status,
                sla_amount,
            ])

        return output.getvalue()

    raise ValueError("Invalid format")