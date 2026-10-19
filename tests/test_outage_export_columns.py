"""Outage export: column selection, defaults and rejection of unknown columns.

#963 asked for selectable export columns. The contract is now:

- ``columns`` names the fields to emit, in the order given;
- omitting it exports every column, which is the pre-existing behaviour;
- an unknown column is rejected with 400 rather than silently dropped;
- an explicitly empty ``columns`` is rejected, since "export nothing" is never a
  request anyone means.

Two properties are worth calling out, because both fail quietly:

  - **Order is the caller's.** A consumer that selects ``status,severity`` needs
    the CSV header to match that order. Sorting or canonicalising would silently
    break the mapping.
  - **Unknown columns must not be ignored.** A typo like ``sevrity`` returning a
    successful export without that column is indistinguishable from a correct one
    until someone reads the file.
"""

import csv
import io
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

AUTH = {"Authorization": "Bearer test-engineer-token"}
EXPORT_URL = "/api/v1/outages/export"

# The declared CSV contract, in exporter.py.
ALL_COLUMNS = [
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


@pytest.fixture
def seeded(db: Session):
    from app.models.enums import OutageStatus, Severity
    from app.models.outage_dto import OutageCreate
    from app.repositories.outage_repository import OutageRepository

    repo = OutageRepository(db)
    repo.create(
        OutageCreate(
            id="exp-1",
            site_name="Frankfurt DC",
            site_id="site-fra",
            severity=Severity.critical,
            status=OutageStatus.open,
            detected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            description="Edge latency",
            affected_services=["edge"],
        )
    )
    db.commit()


def csv_header(response) -> list[str]:
    return next(csv.reader(io.StringIO(response.text)))


def csv_rows(response) -> list[dict]:
    return list(csv.DictReader(io.StringIO(response.text)))


def export(client: TestClient, columns=None, **params):
    """GET the export endpoint.

    ``columns=None`` omits the parameter entirely, which is how the
    default-to-everything behaviour is exercised; passing a string — including an
    empty one — sends it.
    """
    query = {"format": "csv", **params}
    if columns is not None:
        query["columns"] = columns
    return client.get(EXPORT_URL, headers=AUTH, params=query)


class TestDefaultsToAllColumns:
    def test_no_columns_parameter_exports_every_column(self, client: TestClient, seeded):
        response = export(client)

        assert response.status_code == 200, response.text
        assert csv_header(response) == ALL_COLUMNS

    def test_default_matches_the_declared_contract(self, client: TestClient, seeded):
        """Pinned against exporter._CSV_FIELDNAMES rather than a copy.

        A test asserting a hardcoded list would keep passing after the export
        contract changed, which is exactly when it should stop.
        """
        from app.utils.exporter import _CSV_FIELDNAMES

        assert csv_header(export(client)) == _CSV_FIELDNAMES

    def test_default_includes_data_for_each_column(self, client: TestClient, seeded):
        row = csv_rows(export(client))[0]

        assert row["id"] == "exp-1"
        assert row["severity"] == "critical"


class TestColumnSelection:
    def test_single_column(self, client: TestClient, seeded):
        response = export(client, columns="id")

        assert response.status_code == 200
        assert csv_header(response) == ["id"]

    def test_multiple_columns_in_the_requested_order(self, client: TestClient, seeded):
        response = export(client, columns="status,severity,id")

        assert response.status_code == 200
        # Order is the caller's; reordering would break the column mapping.
        assert csv_header(response) == ["status", "severity", "id"]

    def test_whitespace_around_columns_is_ignored(self, client: TestClient, seeded):
        """``?columns=id, severity`` is a natural thing to type."""
        response = export(client, columns="id, severity")

        assert response.status_code == 200
        assert csv_header(response) == ["id", "severity"]

    def test_selected_columns_carry_their_values(self, client: TestClient, seeded):
        row = csv_rows(export(client, columns="id,severity"))[0]

        assert row == {"id": "exp-1", "severity": "critical"}

    def test_unselected_columns_are_absent(self, client: TestClient, seeded):
        response = export(client, columns="id")

        assert "description" not in csv_header(response)
        assert "location" not in csv_header(response)

    def test_json_format_honours_selection(self, client: TestClient, seeded):
        """Selection applies to both formats, not just CSV."""
        response = client.get(
            EXPORT_URL, headers=AUTH, params={"format": "json", "columns": "id,severity"}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body[0]) == {"id", "severity"}


class TestInvalidColumnsRejected:
    def test_unknown_column_is_rejected(self, client: TestClient, seeded):
        """Not silently dropped.

        A typo would otherwise produce a successful export missing the column the
        caller asked for, which is indistinguishable from correct until read.
        """
        response = export(client, columns="id,not_a_column")

        assert response.status_code == 400

    def test_misspelled_known_column_is_rejected(self, client: TestClient, seeded):
        """The realistic typo, and the reason this check exists."""
        response = export(client, columns="id,sevrity")

        assert response.status_code == 400

    def test_error_names_the_unknown_column(self, client: TestClient, seeded):
        detail = export(client, columns="id,not_a_column").json()["detail"]

        assert "not_a_column" in detail

    def test_empty_column_string_is_rejected(self, client: TestClient, seeded):
        """``?columns=`` is present-but-empty, not the same as omitting it.

        Treating it as "all columns" would make a client that failed to populate
        the parameter export everything by accident.
        """
        response = export(client, columns="")

        assert response.status_code == 400

    def test_leading_comma_is_rejected(self, client: TestClient, seeded):
        response = export(client, columns=",id")

        assert response.status_code == 400

    def test_trailing_comma_is_rejected(self, client: TestClient, seeded):
        response = export(client, columns="id,")

        assert response.status_code == 400

    def test_one_valid_column_does_not_excuse_an_invalid_one(self, client: TestClient, seeded):
        """Validation is over the whole list, not just the first entry."""
        response = export(client, columns="id,valid_but_wrong,severity")

        assert response.status_code == 400
