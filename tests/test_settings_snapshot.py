"""Tests for issue #994 — settings snapshot endpoint for admins.

Acceptance criteria:
- Admin only (non-admins get 403, unauthenticated get 401)
- Secrets excluded (SECRET_KEY, JWT_SECRET_KEY, API_KEY, etc.)
- Grouped by section (celery, webhook, payment, stellar, etc.)
- Unit test verifies snapshot
"""
from __future__ import annotations

import pytest

from app.api.v1.endpoints.health import _build_settings_snapshot, _SECRET_SETTING_NAMES


class TestSettingsSnapshotAccess:
    """Endpoint access control — admin only."""

    def test_admin_can_access(self, client):
        """Admin token returns 200 with the snapshot."""
        response = client.get(
            "/api/v1/health/settings",
            headers={"Authorization": "Bearer test-admin-token"},
        )
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body, dict)
        # Must be grouped — at least one section present.
        assert len(body) > 0

    def test_engineer_forbidden(self, client):
        """Engineer role gets 403."""
        response = client.get(
            "/api/v1/health/settings",
            headers={"Authorization": "Bearer test-engineer-token"},
        )
        assert response.status_code == 403

    def test_unauthenticated_rejected(self, client):
        """No auth header returns 401."""
        response = client.get("/api/v1/health/settings")
        assert response.status_code == 401

    def test_invalid_token_rejected(self, client):
        """Invalid token returns 401."""
        response = client.get(
            "/api/v1/health/settings",
            headers={"Authorization": "Bearer not-a-real-token"},
        )
        assert response.status_code == 401


class TestSecretsExcluded:
    """Secret settings must never appear in the snapshot."""

    def test_secret_key_absent(self):
        snapshot = _build_settings_snapshot()
        flat = _flatten(snapshot)
        assert "SECRET_KEY" not in flat

    def test_jwt_secret_key_absent(self):
        snapshot = _build_settings_snapshot()
        flat = _flatten(snapshot)
        assert "JWT_SECRET_KEY" not in flat

    def test_api_key_absent(self):
        snapshot = _build_settings_snapshot()
        flat = _flatten(snapshot)
        assert "API_KEY" not in flat

    def test_all_secret_names_absent(self):
        snapshot = _build_settings_snapshot()
        flat = _flatten(snapshot)
        for name in _SECRET_SETTING_NAMES:
            assert name not in flat, f"Secret setting {name!r} leaked into snapshot"

    def test_secret_values_not_in_any_section(self):
        """No section value should contain the default secret string."""
        from app.core.config import _DEFAULT_SECRET

        snapshot = _build_settings_snapshot()
        flat = _flatten(snapshot)
        for key, value in flat.items():
            if isinstance(value, str):
                assert value != _DEFAULT_SECRET, (
                    f"Setting {key!r} contains the default secret value"
                )


class TestGroupedBySection:
    """Snapshot must be grouped by logical section."""

    def test_has_expected_sections(self):
        snapshot = _build_settings_snapshot()
        expected_sections = {"celery", "webhook", "db", "payment", "stellar"}
        actual_sections = set(snapshot.keys())
        assert expected_sections.issubset(actual_sections), (
            f"Missing sections: {expected_sections - actual_sections}"
        )

    def test_celery_section_contains_celery_settings(self):
        snapshot = _build_settings_snapshot()
        celery = snapshot.get("celery", {})
        assert "CELERY_BROKER_URL" in celery
        assert "CELERY_TASK_ALWAYS_EAGER" in celery

    def test_db_section_contains_db_settings(self):
        snapshot = _build_settings_snapshot()
        db = snapshot.get("db", {})
        assert "DB_POOL_SIZE" in db

    def test_general_section_contains_core_settings(self):
        snapshot = _build_settings_snapshot()
        general = snapshot.get("general", {})
        assert "PROJECT_NAME" in general
        assert "VERSION" in general

    def test_sections_are_sorted(self):
        snapshot = _build_settings_snapshot()
        for section, items in snapshot.items():
            keys = list(items.keys())
            assert keys == sorted(keys), (
                f"Section {section!r} keys are not sorted"
            )


class TestSnapshotContent:
    """Sanity checks on snapshot values."""

    def test_returns_dict_of_dicts(self):
        snapshot = _build_settings_snapshot()
        assert isinstance(snapshot, dict)
        for section, items in snapshot.items():
            assert isinstance(items, dict), f"Section {section!r} is not a dict"

    def test_non_secret_values_present(self):
        snapshot = _build_settings_snapshot()
        flat = _flatten(snapshot)
        # Non-secret settings should be present.
        assert flat.get("PROJECT_NAME") == "NOCIQ API"
        assert flat.get("API_V1_PREFIX") == "/api/v1"

    def test_horizon_url_included_as_safe_property(self):
        snapshot = _build_settings_snapshot()
        flat = _flatten(snapshot)
        assert "horizon_url" in flat
        assert "horizon.stellar.org" not in flat["horizon_url"]  # default is testnet

    def test_supports_friendbot_included(self):
        snapshot = _build_settings_snapshot()
        flat = _flatten(snapshot)
        assert "supports_friendbot" in flat


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flatten(snapshot: dict) -> dict:
    """Flatten a nested {section: {key: value}} dict into {key: value}."""
    result = {}
    for items in snapshot.values():
        if isinstance(items, dict):
            result.update(items)
    return result
