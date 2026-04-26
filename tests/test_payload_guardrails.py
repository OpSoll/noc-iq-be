import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.outage_dto import BulkOutageCreate, OutageCreate
from app.models.enums import OutageStatus, Severity
from app.models.webhook import WebhookEvent
from app.api.v1.endpoints.webhooks import WebhookCreate, WebhookUpdate
from datetime import datetime


def test_outage_create_field_limits():
    """Test field size limits on OutageCreate model."""
    # Test site_name length limit
    with pytest.raises(ValueError, match=f"site_name too long.*{settings.MAX_SITE_NAME_LENGTH}"):
        OutageCreate(
            id="test-1",
            site_name="x" * (settings.MAX_SITE_NAME_LENGTH + 1),
            severity=Severity.critical,
            status=OutageStatus.active,
            detected_at=datetime.now(),
            description="Test outage",
            affected_services=["service1"]
        )

    # Test description length limit
    with pytest.raises(ValueError, match=f"description too long.*{settings.MAX_DESCRIPTION_LENGTH}"):
        OutageCreate(
            id="test-1",
            site_name="Test Site",
            severity=Severity.critical,
            status=OutageStatus.active,
            detected_at=datetime.now(),
            description="x" * (settings.MAX_DESCRIPTION_LENGTH + 1),
            affected_services=["service1"]
        )

    # Test affected_services count limit
    with pytest.raises(ValueError, match=f"too many affected services.*{settings.MAX_AFFECTED_SERVICES_COUNT}"):
        OutageCreate(
            id="test-1",
            site_name="Test Site",
            severity=Severity.critical,
            status=OutageStatus.active,
            detected_at=datetime.now(),
            description="Test outage",
            affected_services=["service"] * (settings.MAX_AFFECTED_SERVICES_COUNT + 1)
        )

    # Test valid outage creation
    outage = OutageCreate(
        id="test-1",
        site_name="Test Site",
        severity=Severity.critical,
        status=OutageStatus.active,
        detected_at=datetime.now(),
        description="Test outage",
        affected_services=["service1", "service2"]
    )
    assert outage.site_name == "Test Site"


def test_bulk_outage_create_limits():
    """Test bulk outage creation limits."""
    # Create valid outages
    valid_outage = OutageCreate(
        id="test-1",
        site_name="Test Site",
        severity=Severity.critical,
        status=OutageStatus.active,
        detected_at=datetime.now(),
        description="Test outage",
        affected_services=["service1"]
    )

    # Test bulk count limit
    with pytest.raises(ValueError, match=f"too many outages.*{settings.MAX_BULK_OUTAGES_COUNT}"):
        BulkOutageCreate(
            outages=[valid_outage] * (settings.MAX_BULK_OUTAGES_COUNT + 1)
        )

    # Test valid bulk creation
    bulk = BulkOutageCreate(outages=[valid_outage, valid_outage])
    assert len(bulk.outages) == 2


def test_webhook_create_limits():
    """Test webhook creation field limits."""
    # Test name length limit
    with pytest.raises(ValueError, match=f"name too long.*{settings.MAX_WEBHOOK_NAME_LENGTH}"):
        WebhookCreate(
            name="x" * (settings.MAX_WEBHOOK_NAME_LENGTH + 1),
            url="https://example.com/webhook",
            events=[WebhookEvent.SLA_VIOLATION]
        )

    # Test URL length limit
    long_url = "https://example.com/" + "x" * (settings.MAX_WEBHOOK_URL_LENGTH - 20)
    with pytest.raises(ValueError, match=f"url too long.*{settings.MAX_WEBHOOK_URL_LENGTH}"):
        WebhookCreate(
            name="Test Webhook",
            url=long_url,
            events=[WebhookEvent.SLA_VIOLATION]
        )

    # Test events count limit
    with pytest.raises(ValueError, match=f"too many events.*{settings.MAX_WEBHOOK_EVENTS_COUNT}"):
        WebhookCreate(
            name="Test Webhook",
            url="https://example.com/webhook",
            events=[WebhookEvent.SLA_VIOLATION] * (settings.MAX_WEBHOOK_EVENTS_COUNT + 1)
        )

    # Test valid webhook creation
    webhook = WebhookCreate(
        name="Test Webhook",
        url="https://example.com/webhook",
        events=[WebhookEvent.SLA_VIOLATION, WebhookEvent.SLA_RESOLVED]
    )
    assert webhook.name == "Test Webhook"


def test_webhook_update_limits():
    """Test webhook update field limits."""
    # Test name length limit
    with pytest.raises(ValueError, match=f"name too long.*{settings.MAX_WEBHOOK_NAME_LENGTH}"):
        WebhookUpdate(
            name="x" * (settings.MAX_WEBHOOK_NAME_LENGTH + 1)
        )

    # Test URL length limit
    long_url = "https://example.com/" + "x" * (settings.MAX_WEBHOOK_URL_LENGTH - 20)
    with pytest.raises(ValueError, match=f"url too long.*{settings.MAX_WEBHOOK_URL_LENGTH}"):
        WebhookUpdate(url=long_url)

    # Test events count limit
    with pytest.raises(ValueError, match=f"too many events.*{settings.MAX_WEBHOOK_EVENTS_COUNT}"):
        WebhookUpdate(
            events=[WebhookEvent.SLA_VIOLATION] * (settings.MAX_WEBHOOK_EVENTS_COUNT + 1)
        )

    # Test valid webhook update
    update = WebhookUpdate(
        name="Updated Webhook",
        events=[WebhookEvent.SLA_VIOLATION]
    )
    assert update.name == "Updated Webhook"


def test_payload_size_middleware_large_request(client: TestClient):
    """Test that large request bodies are rejected by middleware."""
    # Create a large payload that exceeds MAX_REQUEST_BODY_SIZE_BYTES
    large_payload = {"data": "x" * (settings.MAX_REQUEST_BODY_SIZE_BYTES + 1000)}

    # This should be rejected by the middleware before reaching the endpoint
    response = client.post("/api/v1/outages/", json=large_payload)
    assert response.status_code == 413
    assert "Request body too large" in response.json()["detail"]


def test_file_upload_size_limit(client: TestClient, db: Session):
    """Test file upload size limits in import endpoint."""
    # Create a file that exceeds the limit
    large_content = "id,site_name,severity,status,detected_at,description,affected_services\n"
    large_content += "x" * (settings.MAX_FILE_UPLOAD_SIZE_BYTES + 1000)

    # This should be rejected during file reading
    files = {"file": ("large.csv", large_content, "text/csv")}
    response = client.post("/api/v1/outages/import?dry_run=true", files=files)
    assert response.status_code == 413
    assert "File exceeds" in response.json()["detail"]


def test_import_row_count_limit(client: TestClient, db: Session):
    """Test that import endpoint rejects files with too many rows."""
    # Create CSV with too many rows
    csv_content = "id,site_name,severity,status,detected_at,description,affected_services\n"
    for i in range(settings.MAX_BULK_OUTAGES_COUNT + 1):
        csv_content += f"outage-{i},Site {i},critical,active,2024-01-01T00:00:00,Description {i},service1\n"

    files = {"file": ("large.csv", csv_content, "text/csv")}
    response = client.post("/api/v1/outages/import?dry_run=true", files=files)
    assert response.status_code == 400
    assert "Too many rows in file" in response.json()["detail"]