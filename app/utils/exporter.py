import csv
import io
import json
from typing import Iterable, Iterator

from app.models.outage import Outage


def _serialize_outage(outage: Outage) -> dict:
    return outage.model_dump(mode="json")


_CSV_FIELDNAMES = [
    "id",
    "site_name",
    "site_id",
    "severity",
    "status",
    "detected_at",
    "resolved_at",
    "description",
    "affected_services",
    "affected_subscribers",
    "assigned_to",
    "created_by",
    "location",
    "sla_status",
]


def _csv_row(outage: Outage) -> dict:
    row = _serialize_outage(outage)
    # Only emit the declared CSV columns; the Outage model may expose extra
    # fields (e.g. deleted_at) that are not part of the export contract.
    out = {key: row.get(key) for key in _CSV_FIELDNAMES}
    out["affected_services"] = json.dumps(row.get("affected_services", []))
    out["location"] = json.dumps(row.get("location"))
    out["sla_status"] = json.dumps(row.get("sla_status"))
    return out


def export_outages(outages: Iterable[Outage], format: str = "json"):
    format = format.lower()
    rows = [_serialize_outage(outage) for outage in outages]

    if format == "json":
        return rows

    if format != "csv":
        raise ValueError("Unsupported export format. Use 'json' or 'csv'.")

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_CSV_FIELDNAMES)
    writer.writeheader()

    for outage in outages:
        writer.writerow(_csv_row(outage))

    return buffer.getvalue()


def stream_outages_csv(batches: Iterable[Iterable[Outage]]) -> Iterator[str]:
    """Yield CSV text chunks without buffering the whole dataset in memory.

    ``batches`` is an iterable of outage batches (e.g. 500 rows each, pulled
    from the database with ``yield_per``); each batch is serialised and
    yielded as one CSV chunk (Issue #507).
    """
    header = io.StringIO()
    csv.DictWriter(header, fieldnames=_CSV_FIELDNAMES).writeheader()
    yield header.getvalue()

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_CSV_FIELDNAMES)
    for batch in batches:
        buffer.seek(0)
        buffer.truncate(0)
        for outage in batch:
            writer.writerow(_csv_row(outage))
        yield buffer.getvalue()
