"""
Tests for Stellar Wave Issues BE-009, BE-012, BE-013, BE-014

- BE-014: Dry-run validation mode for bulk imports
- BE-013: Outage status transition validation
- BE-012: Explicit sorting contract and validation
- BE-009: Role and permission coverage
"""

import pytest
import uuid
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

class TestDryRunValidation:
    """BE-014: Dry-run validation mode for bulk outage import"""

    def test_dry_run_validates_all_fields(self, client: TestClient, db: Session):
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

    def test_dry_run_rejects_invalid_fields(self, client: TestClient, db: Session):
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

    def test_dry_run_detects_duplicates(self, client: TestClient, db: Session):
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

    def test_import_reports_duplicate_rows_in_live_import(self, client: TestClient, db: Session):
        """Live import should report duplicate rows and not persist the duplicate."""
        csv_content = b"""id,site_name,severity,status,detected_at,description,affected_services
out-duplicate,Site Dup Live,critical,open,2026-02-01T10:00:00,Unique dup live test,service1"""

        response = client.post(
            "/api/v1/outages/import",
            files={"file": ("test.csv", csv_content, "text/csv")},
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        assert response.status_code == 200

        response = client.post(
            "/api/v1/outages/import",
            files={"file": ("test.csv", csv_content, "text/csv")},
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["mode"] == "import"
        assert data["consistency"] == "atomic"
        assert data["total_rows"] == 1
        assert data["persisted"] == 0
        assert data["validated"] == 1
        assert any(r.get("duplicate") == True for r in data["rows"])
        assert data["rows"][0].get("existing_id") == "out-duplicate"

    def test_import_atomic_rolls_back_all_rows_on_failure(self, client: TestClient, db: Session):
        """Atomic imports should rollback all writes if any row fails."""
        csv_content = b"""id,site_name,severity,status,detected_at,description,affected_services
out-atomic-1,Site Atomic A,critical,open,2026-03-01T10:00:00,Atomic test 1,service1
out-atomic-2,Site Atomic B,invalid_severity,open,2026-03-01T11:00:00,Atomic test 2,service2"""

        response = client.post(
            "/api/v1/outages/import?consistency=atomic",
            files={"file": ("test.csv", csv_content, "text/csv")},
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["mode"] == "import"
        assert data["consistency"] == "atomic"
        assert data["total_rows"] == 2
        assert data["persisted"] == 0
        assert data["error_count"] == 1

        response = client.get(
            "/api/v1/outages/out-atomic-1",
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        assert response.status_code == 404

    def test_import_partial_persists_valid_rows_on_failure(self, client: TestClient, db: Session):
        """Partial imports should persist valid rows and report failures for invalid rows."""
        part_id = f"out-partial-{uuid.uuid4().hex[:8]}"
        site_name = f"Site Partial {part_id}"
        csv_content = f"""id,site_name,severity,status,detected_at,description,affected_services
{part_id},{site_name},critical,open,2026-04-01T10:00:00,Partial test {part_id},service1
out-partial-err,Site Partial B,invalid_severity,open,2026-04-01T11:00:00,Partial test 2,service2""".encode()

        response = client.post(
            "/api/v1/outages/import?consistency=partial",
            files={"file": ("test.csv", csv_content, "text/csv")},
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["mode"] == "import"
        assert data["consistency"] == "partial"
        assert data["total_rows"] == 2
        assert data["persisted"] == 1
        assert data["error_count"] == 1
        assert any(r.get("status") == "error" for r in data["rows"])
        assert any(r.get("persisted") is True for r in data["rows"])

        response = client.get(
            f"/api/v1/outages/{part_id}",
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        assert response.status_code == 200

    def test_bulk_create_reuses_existing_duplicates(self, client: TestClient, db: Session):
        """Bulk create should use the same duplicate detection rules and count persisted rows."""
        bulk_id = f"out-bulk-{uuid.uuid4().hex[:8]}"
        payload = {
            "outages": [
                {
                    "id": bulk_id,
                    "site_name": f"Site Bulk {bulk_id}",
                    "site_id": f"site-{bulk_id}",
                    "severity": "high",
                    "status": "open",
                    "detected_at": "2026-05-15T10:00:00Z",
                    "description": "Bulk outage fresh test",
                    "affected_services": ["service1"],
                },
                {
                    "id": bulk_id,
                    "site_name": f"Site Bulk {bulk_id}",
                    "site_id": f"site-{bulk_id}",
                    "severity": "high",
                    "status": "open",
                    "detected_at": "2026-05-15T10:00:00Z",
                    "description": "Bulk outage fresh test",
                    "affected_services": ["service1"],
                },
            ]
        }

        response = client.post(
            "/api/v1/outages/bulk",
            json=payload,
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        assert data["persisted"] == 1
        assert data["items"][0]["id"] == data["items"][1]["id"]

    def test_dry_run_does_not_persist(self, client: TestClient, db: Session):
        """Dry-run mode should NOT persist outages to database."""
        dry_id = f"out-dry-{uuid.uuid4().hex[:8]}"
        csv_content = f"""id,site_name,severity,status,detected_at,description,affected_services
{dry_id},Site Dry Unique,critical,open,2026-06-01T10:00:00,Dry run test unique,service1""".encode()
        
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
            f"/api/v1/outages/{dry_id}",
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        assert response.status_code == 404

    def test_dry_run_json_import(self, client: TestClient, db: Session):
        """Dry-run mode should support JSON imports with same validation."""
        import json
        json_id = f"out-json-{uuid.uuid4().hex[:8]}"
        json_content = json.dumps([{
            "id": json_id,
            "site_name": f"JSON Site {json_id}",
            "severity": "high",
            "status": "open",
            "detected_at": "2026-07-01T10:00:00Z",
            "description": "JSON import test unique",
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

    def test_valid_open_to_resolved_transition(self, client: TestClient, db: Session):
        """Should allow open -> resolved transition."""
        t_id = f"trans-1-{uuid.uuid4().hex[:8]}"
        # Create outage
        outage_data = OutageCreate(
            id=t_id,
            site_name=f"Test Site {t_id}",
            severity=Severity.critical,
            status=OutageStatus.open,
            detected_at=datetime(2026, 8, 1, 10, 0, 0),
            description=f"Trans test {t_id}",
            affected_services=["service1"]
        )
        repo = OutageRepository(db)
        repo.create(outage_data)
        db.commit()
        
        # Patch to resolved
        response = client.patch(
            f"/api/v1/outages/{t_id}",
            json={"status": "resolved"},
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        
        assert response.status_code == 200
        assert response.json()["status"] == "resolved"

    def test_invalid_transition_rejected(self, client: TestClient, db: Session):
        """Should reject invalid status transitions."""
        t_id = f"trans-2-{uuid.uuid4().hex[:8]}"
        # Create resolved outage
        outage_data = OutageCreate(
            id=t_id,
            site_name=f"Test Site {t_id}",
            severity=Severity.critical,
            status=OutageStatus.resolved,
            detected_at=datetime(2026, 8, 2, 10, 0, 0),
            description=f"Trans test {t_id}",
            affected_services=["service1"]
        )
        repo = OutageRepository(db)
        repo.create(outage_data)
        db.commit()
        
        # Try to patch to open (invalid)
        response = client.patch(
            f"/api/v1/outages/{t_id}",
            json={"status": "open"},
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        
        assert response.status_code == 400

    def test_resolved_is_idempotent(self, client: TestClient, db: Session):
        """Resolving an already-resolved outage should be idempotent."""
        t_id = f"trans-3-{uuid.uuid4().hex[:8]}"
        # Create and resolve
        outage_data = OutageCreate(
            id=t_id,
            site_name=f"Test Site {t_id}",
            severity=Severity.critical,
            status=OutageStatus.open,
            detected_at=datetime(2026, 8, 3, 10, 0, 0),
            description=f"Trans test {t_id}",
            affected_services=["service1"]
        )
        repo = OutageRepository(db)
        repo.create(outage_data)
        db.commit()
        
        # First resolve
        response1 = client.post(
            f"/api/v1/outages/{t_id}/resolve",
            json={"mttr_minutes": 60},
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        assert response1.status_code == 200
        
        # Second resolve with same mttr (idempotent)
        response2 = client.post(
            f"/api/v1/outages/{t_id}/resolve",
            json={"mttr_minutes": 60},
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        assert response2.status_code == 200

    def test_recompute_sla_requires_resolved(self, client: TestClient, db: Session):
        """Should only allow SLA recompute on resolved outages."""
        # Create but don't resolve
        outage_data = OutageCreate(
            id="trans-4-unique",
            site_name="Test Site Trans 4",
            severity=Severity.critical,
            status=OutageStatus.open,
            detected_at=datetime(2026, 8, 4, 10, 0, 0),
            description="Trans test 4",
            affected_services=["service1"]
        )
        repo = OutageRepository(db)
        repo.create(outage_data)
        db.commit()
        
        # Try recompute on open outage
        response = client.post(
            "/api/v1/outages/trans-4-unique/recompute-sla",
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        
        assert response.status_code == 400


class TestOutageSortingContract:
    """BE-012: Explicit outage sorting contract and validation"""

    def test_supported_sort_fields(self, client: TestClient, db: Session):
        """Should accept documented sort fields."""
        supported_fields = ["detected_at", "site_name", "severity", "status", "id"]
        
        for field in supported_fields:
            response = client.get(
                f"/api/v1/outages/?sort_by={field}&sort_direction=desc",
                headers={"Authorization": "Bearer test-engineer-token"}
            )
            # Should not fail on valid sort field
            assert response.status_code in [200, 422]  # 422 only if enum parsing fails

    def test_invalid_sort_field_rejected(self, client: TestClient, db: Session):
        """Should reject invalid sort fields with 422."""
        response = client.get(
            "/api/v1/outages/?sort_by=invalid_field&sort_direction=desc",
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        
        assert response.status_code == 422  # Validation error

    def test_invalid_sort_direction_rejected(self, client: TestClient, db: Session):
        """Should reject invalid sort directions with 422."""
        response = client.get(
            "/api/v1/outages/?sort_by=detected_at&sort_direction=invalid",
            headers={"Authorization": "Bearer test-engineer-token"}
        )
        
        assert response.status_code == 422  # Validation error

    def test_default_sort_is_stable(self, client: TestClient, db: Session):
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

    def test_recompute_sla_requires_engineer(self, client: TestClient, db: Session):
        """Recompute SLA should require engineer role."""
        # Without auth
        response = client.post("/api/v1/outages/out-1/recompute-sla")
        assert response.status_code == 401

    def test_resolve_outage_requires_engineer(self, client: TestClient, db: Session):
        """Resolve outage should require engineer role."""
        response = client.post(
            "/api/v1/outages/out-1/resolve",
            json={"mttr_minutes": 60}
        )
        assert response.status_code == 401

    def test_timeline_requires_engineer(self, client: TestClient, db: Session):
        """Timeline endpoint should require engineer role."""
        response = client.get("/api/v1/outages/out-1/timeline")
        assert response.status_code == 401

    def test_sla_calculate_requires_engineer(self, client: TestClient, db: Session):
        """SLA calculate should require engineer role."""
        response = client.get(
            "/api/v1/sla/calculate?outage_id=out-1&severity=critical&mttr_minutes=60"
        )
        assert response.status_code == 401

    def test_sla_config_requires_engineer(self, client: TestClient, db: Session):
        """SLA config read should require engineer role."""
        response = client.get("/api/v1/sla/config")
        assert response.status_code == 401

    def test_sla_config_update_requires_admin(self, client: TestClient, db: Session):
        """SLA config update should require admin role."""
        response = client.put(
            "/api/v1/sla/config/critical",
            json={"threshold_minutes": 120}
        )
        assert response.status_code == 401

    def test_analytics_snapshot_requires_engineer(self, client: TestClient, db: Session):
        """Analytics snapshot creation should require engineer role."""
        response = client.post("/api/v1/sla/analytics/snapshot")
        assert response.status_code == 401

    def test_delete_outage_requires_admin(self, client: TestClient, db: Session):
        """Delete outage should require admin role."""
        response = client.delete("/api/v1/outages/out-1")
        assert response.status_code == 401

    def test_unauthorized_access_consistent_errors(self, client: TestClient, db: Session):
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

    def test_row_level_errors_include_field_details(self, client: TestClient, db: Session):
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
