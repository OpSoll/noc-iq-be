import csv
import io
import json
from typing import Iterable, Iterator, List, Optional

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


def resolve_export_columns(columns: Optional[List[str]]) -> Optional[List[str]]:
    """Validate and normalise a requested column selection.

    Returns ``None`` when no selection was made, meaning "every declared column".
    Otherwise returns the selection in the caller's order.

    Raises ``ValueError`` for an unknown column or an empty selection.
    Present-but-empty is deliberately *not* treated as "no selection": a client
    that failed to populate the parameter would otherwise export everything by
    accident, which is the opposite of what it intended.

    Public because the endpoint must validate before it starts streaming a CSV —
    a generator cannot turn a bad column into a 400 after the first chunk is sent.
    """
    if columns is None:
        return None

    selection = [column.strip() for column in columns]
    if not selection or any(not column for column in selection):
        raise ValueError(
            "Export columns must be a non-empty list of column names. "
            f"Available columns: {', '.join(_CSV_FIELDNAMES)}."
        )

    unknown = [column for column in selection if column not in _CSV_FIELDNAMES]
    if unknown:
        raise ValueError(
            f"Unknown export column(s): {', '.join(unknown)}. "
            f"Available columns: {', '.join(_CSV_FIELDNAMES)}."
        )

    return selection


_resolve_columns = resolve_export_columns


def _csv_row(outage: Outage) -> dict:
    row = _serialize_outage(outage)
    # Only emit the declared CSV columns; the Outage model may expose extra
    # fields (e.g. deleted_at) that are not part of the export contract.
    out = {key: row.get(key) for key in _CSV_FIELDNAMES}
    out["affected_services"] = json.dumps(row.get("affected_services", []))
    out["location"] = json.dumps(row.get("location"))
    out["sla_status"] = json.dumps(row.get("sla_status"))
    return out


def export_outages(
    outages: Iterable[Outage],
    format: str = "json",
    columns: Optional[List[str]] = None,
):
    """Serialise outages to JSON or CSV, optionally limited to ``columns``.

    ``columns`` is the caller's selection, in the order they asked for it. Order
    is preserved deliberately: a consumer selecting ``status,severity`` needs the
    CSV header to match, and sorting or canonicalising would silently break that
    mapping.

    Omitting ``columns`` exports every declared field, which is the behaviour
    that predates column selection. Unknown names raise rather than being dropped:
    a typo that produced a successful export missing the requested column is
    indistinguishable from a correct one until somebody opens the file.
    """
    format = format.lower()
    rows = [_serialize_outage(outage) for outage in outages]
    fieldnames = _resolve_columns(columns)

    if format == "json":
        if fieldnames is None:
            return rows
        return [{key: row.get(key) for key in fieldnames} for row in rows]

    if format != "csv":
        raise ValueError("Unsupported export format. Use 'json' or 'csv'.")

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames or _CSV_FIELDNAMES)
    writer.writeheader()

    for outage in outages:
        row = _csv_row(outage)
        if fieldnames is not None:
            row = {key: row.get(key) for key in fieldnames}
        writer.writerow(row)

    return buffer.getvalue()


def stream_outages_csv(
    batches: Iterable[Iterable[Outage]],
    columns: Optional[List[str]] = None,
) -> Iterator[str]:
    """Yield CSV text chunks without buffering the whole dataset in memory.

    ``batches`` is an iterable of outage batches (e.g. 500 rows each, pulled
    from the database with ``yield_per``); each batch is serialised and
    yielded as one CSV chunk (Issue #507).

    ``columns`` restricts the export to a validated selection, in the caller's
    order (issue #963). Callers must pass a selection already checked by
    :func:`_resolve_columns`, because a generator cannot raise a 400 after the
    response has started streaming.
    """
    fieldnames = columns or _CSV_FIELDNAMES

    header = io.StringIO()
    csv.DictWriter(header, fieldnames=fieldnames).writeheader()
    yield header.getvalue()

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    for batch in batches:
        buffer.seek(0)
        buffer.truncate(0)
        for outage in batch:
            row = _csv_row(outage)
            if columns is not None:
                row = {key: row.get(key) for key in fieldnames}
            writer.writerow(row)
        yield buffer.getvalue()
