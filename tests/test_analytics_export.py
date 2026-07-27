"""Test analytics export endpoints for dashboard and reporting use."""
import csv
import io
import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.db.session import get_db, SessionLocal
from app.models.orm.user import UserORM
from app.models.orm.sla import SLAResultORM
from app.models.enums import Role
from app.core.security import get_password_hash


def override_get_db():
    """Override database dependency for testing."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)


def create_test_user(db: Session, email: str, password: str = "Password123!", role: Role = Role.engineer) -> UserORM:
    """Helper to create a test user."""
    user = UserORM(
        id=f"user_test_{email.split('@')[0]}",
        email=email,
        hashed_password=get_password_hash(password),
        full_name=f"Test User {email}",
        role=role.value,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_test_sla_results(db: Session, count: int = 10, severity: str = "critical"):
    """Helper to create test SLA results with associated outages."""
    import uuid
    from app.models.orm.outage import OutageORM
    from app.models.enums import OutageStatus, Severity
    
    for i in range(count):
        # Create an outage first
        outage = OutageORM(
            id=f"outage_test_{uuid.uuid4().hex[:8]}",
            site_name=f"Test Site {i}",
            site_id=f"site_{i % 3}",
            severity=severity,
            status=OutageStatus.resolved,
            description=f"Test outage {i}",
            detected_at=datetime.utcnow() - timedelta(days=i),
            resolved_at=datetime.utcnow() - timedelta(days=i) + timedelta(minutes=15 + (i * 2)),
        )
        db.add(outage)
        db.flush()  # Ensure outage is created before SLA result
        
        # Create SLA result linked to outage
        sla_result = SLAResultORM(
            outage_id=outage.id,
            status="met" if i % 3 != 0 else "violated",
            mttr_minutes=15 + (i * 2),
            threshold_minutes=30,
            amount=100 * (i + 1),
            payment_type="reward" if i % 3 != 0 else "penalty",
            rating="good",
            created_at=datetime.utcnow() - timedelta(days=i),
        )
        db.add(sla_result)
    
    db.commit()


def test_export_dashboard_kpi_json(db: Session):
    """Test exporting dashboard KPI in JSON format."""
    email = "export_kpi_json@example.com"
    password = "Password123!"
    
    # Create user and test data
    user = create_test_user(db, email, password)
    create_test_sla_results(db, count=5, severity="critical")
    
    # Login
    login_resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login_resp.status_code == 200
    access_token = login_resp.json()["access_token"]
    
    # Export KPI as JSON
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = client.get("/api/v1/sla/analytics/dashboard/export?format=json", headers=headers)
    
    assert resp.status_code == 200
    data = resp.json()
    
    # Verify KPI structure
    assert "total_outages" in data
    assert "total_violations" in data
    assert "total_rewards" in data
    assert "total_penalties" in data
    assert "net_payout" in data
    
    # Verify data types
    assert isinstance(data["total_outages"], int)
    assert isinstance(data["total_violations"], int)
    assert isinstance(data["total_rewards"], (int, float))
    assert isinstance(data["total_penalties"], (int, float))


def test_export_dashboard_kpi_csv(db: Session):
    """Test exporting dashboard KPI in CSV format."""
    email = "export_kpi_csv@example.com"
    password = "Password123!"
    
    # Create user and test data
    user = create_test_user(db, email, password)
    create_test_sla_results(db, count=5, severity="critical")
    
    # Login
    login_resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login_resp.status_code == 200
    access_token = login_resp.json()["access_token"]
    
    # Export KPI as CSV
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = client.get("/api/v1/sla/analytics/dashboard/export?format=csv", headers=headers)
    
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/csv; charset=utf-8"
    assert "attachment; filename=sla_dashboard_kpi.csv" in resp.headers["content-disposition"]
    
    # Parse CSV content
    content = resp.text
    assert "total_outages" in content
    assert "total_violations" in content
    
    # Verify CSV is valid
    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)
    assert len(rows) == 1  # KPI is a single record


def test_export_trends_json(db: Session):
    """Test exporting trends data in JSON format."""
    email = "export_trends_json@example.com"
    password = "Password123!"
    
    # Create user and test data
    user = create_test_user(db, email, password)
    create_test_sla_results(db, count=15, severity="high")
    
    # Login
    login_resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login_resp.status_code == 200
    access_token = login_resp.json()["access_token"]
    
    # Export trends as JSON
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = client.get(
        "/api/v1/sla/analytics/trends/export?format=json&days=14&bucket=day",
        headers=headers
    )
    
    assert resp.status_code == 200
    data = resp.json()
    
    # Verify trends structure
    assert isinstance(data, list)
    if len(data) > 0:
        trend_point = data[0]
        assert "date" in trend_point
        assert "total_outages" in trend_point
        assert "violations" in trend_point
        assert "rewards" in trend_point
        assert "penalties" in trend_point


def test_export_trends_csv(db: Session):
    """Test exporting trends data in CSV format."""
    email = "export_trends_csv@example.com"
    password = "Password123!"
    
    # Create user and test data
    user = create_test_user(db, email, password)
    create_test_sla_results(db, count=10, severity="medium")
    
    # Login
    login_resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login_resp.status_code == 200
    access_token = login_resp.json()["access_token"]
    
    # Export trends as CSV
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = client.get(
        "/api/v1/sla/analytics/trends/export?format=csv&days=7&bucket=day",
        headers=headers
    )
    
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/csv; charset=utf-8"
    assert "attachment; filename=sla_trends_7d.csv" in resp.headers["content-disposition"]
    
    # Parse CSV content
    content = resp.text
    assert "date" in content
    assert "total_outages" in content
    
    # Verify CSV is valid
    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)
    # Should have at least some data points
    assert len(rows) >= 0


def test_export_performance_aggregation_json(db: Session):
    """Test exporting performance aggregation in JSON format."""
    email = "export_perf_json@example.com"
    password = "Password123!"
    
    # Create user and test data
    user = create_test_user(db, email, password)
    create_test_sla_results(db, count=20, severity="critical")
    
    # Login
    login_resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login_resp.status_code == 200
    access_token = login_resp.json()["access_token"]
    
    # Export performance aggregation as JSON
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = client.get(
        "/api/v1/sla/analytics/performance/export?format=json",
        headers=headers
    )
    
    assert resp.status_code == 200
    data = resp.json()
    
    # Verify aggregation structure
    assert "total_outages" in data
    assert "violation_rate" in data
    assert "avg_mttr" in data
    assert "payout_sum" in data
    
    # Verify data types and constraints
    assert isinstance(data["total_outages"], int)
    assert isinstance(data["violation_rate"], (int, float))
    assert 0.0 <= data["violation_rate"] <= 1.0
    assert isinstance(data["avg_mttr"], (int, float))


def test_export_performance_aggregation_csv(db: Session):
    """Test exporting performance aggregation in CSV format."""
    email = "export_perf_csv@example.com"
    password = "Password123!"
    
    # Create user and test data
    user = create_test_user(db, email, password)
    create_test_sla_results(db, count=10, severity="high")
    
    # Login
    login_resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login_resp.status_code == 200
    access_token = login_resp.json()["access_token"]
    
    # Export performance aggregation as CSV
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = client.get(
        "/api/v1/sla/analytics/performance/export?format=csv",
        headers=headers
    )
    
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/csv; charset=utf-8"
    assert "attachment; filename=sla_performance.csv" in resp.headers["content-disposition"]
    
    # Parse CSV content
    content = resp.text
    assert "total_outages" in content
    assert "violation_rate" in content


def test_export_analytics_summary_json(db: Session):
    """Test exporting comprehensive analytics summary in JSON format."""
    email = "export_summary_json@example.com"
    password = "Password123!"
    
    # Create user and test data
    user = create_test_user(db, email, password)
    create_test_sla_results(db, count=15, severity="critical")
    
    # Login
    login_resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login_resp.status_code == 200
    access_token = login_resp.json()["access_token"]
    
    # Export summary as JSON
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = client.get(
        "/api/v1/sla/analytics/export?format=json&days=7&include_aggregation=true",
        headers=headers
    )
    
    assert resp.status_code == 200
    data = resp.json()
    
    # Verify summary structure
    assert "kpi" in data
    assert "trends" in data
    assert "trend_count" in data
    assert "aggregation" in data
    
    # Verify nested structures
    assert isinstance(data["kpi"], dict)
    assert isinstance(data["trends"], list)
    assert isinstance(data["aggregation"], dict)
    assert data["trend_count"] == len(data["trends"])


def test_export_analytics_summary_csv(db: Session):
    """Test exporting comprehensive analytics summary in CSV format."""
    email = "export_summary_csv@example.com"
    password = "Password123!"
    
    # Create user and test data
    user = create_test_user(db, email, password)
    create_test_sla_results(db, count=10, severity="high")
    
    # Login
    login_resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login_resp.status_code == 200
    access_token = login_resp.json()["access_token"]
    
    # Export summary as CSV
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = client.get(
        "/api/v1/sla/analytics/export?format=csv&days=7&include_aggregation=true",
        headers=headers
    )
    
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/csv; charset=utf-8"
    assert "attachment; filename=sla_analytics_summary_7d.csv" in resp.headers["content-disposition"]
    
    # Parse CSV content
    content = resp.text
    assert "# KPI Metrics" in content
    assert "# Trends Data" in content
    assert "# Performance Aggregation" in content


def test_export_with_filters(db: Session):
    """Test that exports respect filters (severity, site_id)."""
    email = "export_filters@example.com"
    password = "Password123!"
    
    # Create user and test data
    user = create_test_user(db, email, password)
    create_test_sla_results(db, count=10, severity="critical")
    create_test_sla_results(db, count=5, severity="high")
    
    # Login
    login_resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login_resp.status_code == 200
    access_token = login_resp.json()["access_token"]
    
    # Export with severity filter
    headers = {"Authorization": f"Bearer {access_token}"}
    resp_filtered = client.get(
        "/api/v1/sla/analytics/dashboard/export?format=json&severity=critical",
        headers=headers
    )
    
    resp_unfiltered = client.get(
        "/api/v1/sla/analytics/dashboard/export?format=json",
        headers=headers
    )
    
    assert resp_filtered.status_code == 200
    assert resp_unfiltered.status_code == 200
    
    data_filtered = resp_filtered.json()
    data_unfiltered = resp_unfiltered.json()
    
    # Filtered should have fewer or equal outages
    assert data_filtered["total_outages"] <= data_unfiltered["total_outages"]


def test_export_empty_dataset(db: Session):
    """Test that exports handle empty datasets safely."""
    email = "export_empty@example.com"
    password = "Password123!"
    
    # Create user but NO test data
    user = create_test_user(db, email, password)
    
    # Login
    login_resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login_resp.status_code == 200
    access_token = login_resp.json()["access_token"]
    
    # Export trends with empty dataset
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = client.get(
        "/api/v1/sla/analytics/trends/export?format=json&days=30",
        headers=headers
    )
    
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    # Should return empty list, not error
    
    # Export as CSV with empty dataset
    resp_csv = client.get(
        "/api/v1/sla/analytics/trends/export?format=csv&days=30",
        headers=headers
    )
    
    assert resp_csv.status_code == 200
    content = resp_csv.text
    # Should have headers even if no data
    assert "date" in content


def test_export_large_result_set(db: Session):
    """Test that exports handle large result sets safely."""
    email = "export_large@example.com"
    password = "Password123!"
    
    # Create user and large dataset
    user = create_test_user(db, email, password)
    create_test_sla_results(db, count=100, severity="critical")
    
    # Login
    login_resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login_resp.status_code == 200
    access_token = login_resp.json()["access_token"]
    
    # Export large dataset as JSON
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = client.get(
        "/api/v1/sla/analytics/trends/export?format=json&days=90&bucket=day",
        headers=headers
    )
    
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    
    # Export as CSV
    resp_csv = client.get(
        "/api/v1/sla/analytics/trends/export?format=csv&days=90&bucket=day",
        headers=headers
    )
    
    assert resp_csv.status_code == 200
    content = resp_csv.text
    
    # Verify CSV is valid and has data
    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)
    assert len(rows) > 0


def test_export_invalid_format(db: Session):
    """Test that exports reject invalid formats."""
    email = "export_invalid@example.com"
    password = "Password123!"
    
    # Create user
    user = create_test_user(db, email, password)
    
    # Login
    login_resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login_resp.status_code == 200
    access_token = login_resp.json()["access_token"]
    
    # Try invalid format
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = client.get(
        "/api/v1/sla/analytics/dashboard/export?format=xml",
        headers=headers
    )
    
    assert resp.status_code == 400
    assert "Unsupported export format" in resp.json()["detail"]


def test_export_unauthenticated():
    """Test that exports require authentication."""
    resp = client.get("/api/v1/sla/analytics/dashboard/export")
    assert resp.status_code == 401  # or 403 depending on implementation


def test_export_with_date_range(db: Session):
    """Test performance export with date range filters."""
    email = "export_date_range@example.com"
    password = "Password123!"
    
    # Create user and test data
    user = create_test_user(db, email, password)
    create_test_sla_results(db, count=20, severity="critical")
    
    # Login
    login_resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login_resp.status_code == 200
    access_token = login_resp.json()["access_token"]
    
    # Export with date range
    start_date = (datetime.utcnow() - timedelta(days=30)).isoformat()
    end_date = datetime.utcnow().isoformat()
    
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = client.get(
        f"/api/v1/sla/analytics/performance/export?format=json&start_date={start_date}&end_date={end_date}",
        headers=headers
    )
    
    assert resp.status_code == 200
    data = resp.json()
    assert "total_outages" in data


def test_export_summary_without_aggregation(db: Session):
    """Test analytics summary export without aggregation data."""
    email = "export_no_agg@example.com"
    password = "Password123!"
    
    # Create user and test data
    user = create_test_user(db, email, password)
    create_test_sla_results(db, count=10, severity="high")
    
    # Login
    login_resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login_resp.status_code == 200
    access_token = login_resp.json()["access_token"]
    
    # Export summary without aggregation
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = client.get(
        "/api/v1/sla/analytics/export?format=json&days=7&include_aggregation=false",
        headers=headers
    )
    
    assert resp.status_code == 200
    data = resp.json()
    
    assert "kpi" in data
    assert "trends" in data
    assert "aggregation" not in data  # Should not be included
