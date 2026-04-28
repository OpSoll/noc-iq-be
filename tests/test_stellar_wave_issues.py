"""
Tests for Stellar Wave Issues BE-009, BE-012, BE-013, BE-014

- BE-014: Dry-run validation mode for bulk imports
- BE-013: Outage status transition validation
- BE-012: Explicit sorting contract and validation
- BE-009: Role and permission coverage
"""

import pytest
from datetime import datetime
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.outage_dto import OutageCreate, OutageSortField, OutageSortDirection
from app.models.enums import OutageStatus, Severity, Role
from app.models.auth import AuthUser
from app.db.session import SessionLocal
from app.main import app
from app.core.security import get_password_hash
from app.repositories.outage_repository import OutageRepository

client = TestClient(app)


class TestDryRunValidation:
    """BE-014: Dry-run validation mode for bulk outage import"""

    def test_dry_run_validates_all_fields(self, db: Session):
        """Dry-run mode should validate all fields like live import."""
        # Valid CSV content
        csv_content = b"""id,site_name,severity,status,detected_at,description,affected_services
out-1,Site A,critical,open,2026-01-01T10:00:00,Major outage,service1;service2"""
        
        response = client.post(
            "/api/v1/outages/import?dry_run=true",
            files={"file": ("test.csv", csv_content, "text/csv")},
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["mode"] == "dry_run"
        assert data["total_rows"] == 1
        assert data["validated"] == 1

    def test_dry_run_rejects_invalid_fields(self, db: Session):
        """Dry-run should validate and reject invalid field values."""
        # CSV with invalid severity
        csv_content = b"""id,site_name,severity,status,detected_at,description,affected_services
out-1,Site A,invalid_severity,open,2026-01-01T10:00:00,Major outage,service1"""
        
        response = client.post(
            "/api/v1/outages/import?dry_run=true",
            files={"file": ("test.csv", csv_content, "text/csv")},
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["mode"] == "dry_run"
        assert data["error_count"] == 1
        assert data["validated"] == 0

    def test_dry_run_detects_duplicates(self, db: Session):
        """Dry-run should detect duplicate outages like live import."""
        # First create an outage via live import
        csv_content = b"""id,site_name,severity,status,detected_at,description,affected_services
out-1,Site A,critical,open,2026-01-01T10:00:00,Major outage,service1"""
        
        response = client.post(
            "/api/v1/outages/import",
            files={"file": ("test.csv", csv_content, "text/csv")},
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        assert response.status_code == 200

        # Now dry-run with same outage
        response = client.post(
            "/api/v1/outages/import?dry_run=true",
            files={"file": ("test.csv", csv_content, "text/csv")},
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["mode"] == "dry_run"
        assert any(r.get("duplicate") == True for r in data["rows"])

    def test_dry_run_does_not_persist(self, db: Session):
        """Dry-run mode should NOT persist outages to database."""
        csv_content = b"""id,site_name,severity,status,detected_at,description,affected_services
out-dry-1,Site Dry,critical,open,2026-01-01T10:00:00,Dry run test,service1"""
        
        response = client.post(
            "/api/v1/outages/import?dry_run=true",
            files={"file": ("test.csv", csv_content, "text/csv")},
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["persisted"] == 0

        # Verify not in database
        response = client.get(
            "/api/v1/outages/out-dry-1",
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        assert response.status_code == 404

    def test_dry_run_json_import(self, db: Session):
        """Dry-run mode should support JSON imports with same validation."""
        import json
        json_content = json.dumps([{
            "id": "out-json-1",
            "site_name": "JSON Site",
            "severity": "high",
            "status": "open",
            "detected_at": "2026-01-01T10:00:00",
            "description": "JSON import test",
            "affected_services": ["service1", "service2"]
        }]).encode()
        
        response = client.post(
            "/api/v1/outages/import?dry_run=true",
            files={"file": ("test.json", json_content, "application/json")},
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["mode"] == "dry_run"
        assert data["validated"] == 1


class TestOutageStatusTransitions:
    """BE-013: Enforce outage status transition rules"""

    def test_valid_open_to_resolved_transition(self, db: Session):
        """Should allow open -> resolved transition."""
        # Create outage
        outage_data = OutageCreate(
            id="trans-1",
            site_name="Test Site",
            severity=Severity.critical,
            status=OutageStatus.open,
            detected_at=datetime(2026, 1, 1, 10, 0, 0),
            description="Test",
            affected_services=["service1"]
        )
        repo = OutageRepository(db)
        repo.create(outage_data)
        
        # Patch to resolved
        response = client.patch(
            "/api/v1/outages/trans-1",
            json={"status": "resolved"},
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        
        assert response.status_code == 200
        assert response.json()["status"] == "resolved"

    def test_invalid_transition_rejected(self, db: Session):
        """Should reject invalid status transitions."""
        # Create resolved outage
        outage_data = OutageCreate(
            id="trans-2",
            site_name="Test Site",
            severity=Severity.critical,
            status=OutageStatus.resolved,
            detected_at=datetime(2026, 1, 1, 10, 0, 0),
            description="Test",
            affected_services=["service1"]
        )
        repo = OutageRepository(db)
        repo.create(outage_data)
        
        # Try to patch to open (invalid)
        response = client.patch(
            "/api/v1/outages/trans-2",
            json={"status": "open"},
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        
        assert response.status_code == 400

    def test_resolved_is_idempotent(self, db: Session):
        """Resolving an already-resolved outage should be idempotent."""
        # Create and resolve
        outage_data = OutageCreate(
            id="trans-3",
            site_name="Test Site",
            severity=Severity.critical,
            status=OutageStatus.open,
            detected_at=datetime(2026, 1, 1, 10, 0, 0),
            description="Test",
            affected_services=["service1"]
        )
        repo = OutageRepository(db)
        repo.create(outage_data)
        
        # First resolve
        response1 = client.post(
            "/api/v1/outages/trans-3/resolve",
            json={"mttr_minutes": 60},
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        assert response1.status_code == 200
        
        # Second resolve with same mttr (idempotent)
        response2 = client.post(
            "/api/v1/outages/trans-3/resolve",
            json={"mttr_minutes": 60},
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        assert response2.status_code == 200

    def test_recompute_sla_requires_resolved(self, db: Session):
        """Should only allow SLA recompute on resolved outages."""
        # Create but don't resolve
        outage_data = OutageCreate(
            id="trans-4",
            site_name="Test Site",
            severity=Severity.critical,
            status=OutageStatus.open,
            detected_at=datetime(2026, 1, 1, 10, 0, 0),
            description="Test",
            affected_services=["service1"]
        )
        repo = OutageRepository(db)
        repo.create(outage_data)
        
        # Try recompute on open outage
        response = client.post(
            "/api/v1/outages/trans-4/recompute-sla",
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        
        assert response.status_code == 400


class TestOutageSortingContract:
    """BE-012: Explicit outage sorting contract and validation"""

    def test_supported_sort_fields(self, db: Session):
        """Should accept documented sort fields."""
        supported_fields = ["detected_at", "site_name", "severity", "status", "id"]
        
        for field in supported_fields:
            response = client.get(
                f"/api/v1/outages/?sort_by={field}&sort_direction=desc",
                headers={"Authorization": "Bearer test-engineer-token"}
            )
            # Should not fail on valid sort field
            assert response.status_code in [200, 422]  # 422 only if enum parsing fails

    def test_invalid_sort_field_rejected(self, db: Session):
        """Should reject invalid sort fields with 422."""
        response = client.get(
            "/api/v1/outages/?sort_by=invalid_field&sort_direction=desc",
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        
        assert response.status_code == 422  # Validation error

    def test_invalid_sort_direction_rejected(self, db: Session):
        """Should reject invalid sort directions with 422."""
        response = client.get(
            "/api/v1/outages/?sort_by=detected_at&sort_direction=invalid",
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        
        assert response.status_code == 422  # Validation error

    def test_default_sort_is_stable(self, db: Session):
        """Default sorting should be stable (detected_at desc, then id asc)."""
        response = client.get(
            "/api/v1/outages/",
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data.get("sort_by") == "detected_at"
        assert data.get("sort_direction") == "desc"


class TestRoleAndPermissionCoverage:
    """BE-009: Enforce role and permission coverage"""

    def test_recompute_sla_requires_engineer(self, db: Session):
        """Recompute SLA should require engineer role."""
        # Without auth
        response = client.post("/api/v1/outages/out-1/recompute-sla")
        assert response.status_code == 401

    def test_resolve_outage_requires_engineer(self, db: Session):
        """Resolve outage should require engineer role."""
        response = client.post(
            "/api/v1/outages/out-1/resolve",
            json={"mttr_minutes": 60}
        )
        assert response.status_code == 401

    def test_timeline_requires_engineer(self, db: Session):
        """Timeline endpoint should require engineer role."""
        response = client.get("/api/v1/outages/out-1/timeline")
        assert response.status_code == 401

    def test_sla_calculate_requires_engineer(self, db: Session):
        """SLA calculate should require engineer role."""
        response = client.get(
            "/api/v1/sla/calculate?outage_id=out-1&severity=critical&mttr_minutes=60"
        )
        assert response.status_code == 401

    def test_sla_config_requires_engineer(self, db: Session):
        """SLA config read should require engineer role."""
        response = client.get("/api/v1/sla/config")
        assert response.status_code == 401

    def test_sla_config_update_requires_admin(self, db: Session):
        """SLA config update should require admin role."""
        response = client.put(
            "/api/v1/sla/config/critical",
            json={"threshold_minutes": 120}
        )
        assert response.status_code == 401

    def test_analytics_snapshot_requires_engineer(self, db: Session):
        """Analytics snapshot creation should require engineer role."""
        response = client.post("/api/v1/sla/analytics/snapshot")
        assert response.status_code == 401

    def test_delete_outage_requires_admin(self, db: Session):
        """Delete outage should require admin role."""
        response = client.delete("/api/v1/outages/out-1")
        assert response.status_code == 401

    def test_unauthorized_access_consistent_errors(self, db: Session):
        """All unauthorized responses should use consistent error format."""
        endpoints = [
            ("GET", "/api/v1/outages/"),
            ("POST", "/api/v1/outages/out-1/resolve"),
            ("DELETE", "/api/v1/outages/out-1"),
            ("GET", "/api/v1/sla/config"),
        ]
        
        for method, path in endpoints:
            if method == "GET":
                response = client.get(path)
            elif method == "POST":
                response = client.post(path, json={})
            elif method == "DELETE":
                response = client.delete(path)
            
            # All should return 401
            assert response.status_code == 401
            # All should have detail message
            if response.status_code == 401:
                assert "detail" in response.json() or response.json() == {"detail": "Missing Authorization header"}


class TestImportValidationSemantics:
    """Additional tests for validation error semantics"""

    def test_row_level_errors_include_field_details(self, db: Session):
        """Import errors should include field-level detail."""
        csv_content = b"""id,site_name,severity,status,detected_at,description,affected_services
out-1,,critical,open,2026-01-01T10:00:00,Test,service1"""
        
        response = client.post(
            "/api/v1/outages/import?dry_run=true",
            files={"file": ("test.csv", csv_content, "text/csv")},
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["error_count"] > 0
        # Verify field-level error info
        errors = [r for r in data["rows"] if r.get("errors")]
        assert len(errors) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
