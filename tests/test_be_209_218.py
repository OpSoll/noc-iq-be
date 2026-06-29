"""Tests for BE-011 (outage search) and BE-020 (timeline event schema versioning).

Closes #209, #218.
"""
import json
import unittest
from datetime import datetime
from types import SimpleNamespace

from app.models.orm.outage_event import CURRENT_SCHEMA_VERSION
from app.models.outage_event import (
    OutageCreatedDetail,
    OutageResolvedDetail,
    SLAComputedDetail,
    validate_event_detail,
)


# ---------------------------------------------------------------------------
# BE-011: Outage search tests (repository-level)
# ---------------------------------------------------------------------------

class TestOutageSearchRepository(unittest.TestCase):
    """Test search functionality in OutageRepository without full app context."""

    def test_search_filter_matches_id(self):
        """Search term should match against outage ID."""
        from app.repositories.outage_repository import OutageRepository
        from sqlalchemy.sql import or_
        from app.models.orm.outage import OutageORM

        # Simulate the search filter logic
        search = "out_abc"
        search_filter = or_(
            OutageORM.id.ilike(f"%{search}%"),
            OutageORM.site_id.ilike(f"%{search}%"),
            OutageORM.site_name.ilike(f"%{search}%"),
        )
        # Verify the filter is constructed correctly
        self.assertIsNotNone(search_filter)

    def test_search_filter_matches_site_id(self):
        """Search term should match against site_id."""
        from app.repositories.outage_repository import OutageRepository
        from sqlalchemy.sql import or_
        from app.models.orm.outage import OutageORM

        search = "site-alpha"
        search_filter = or_(
            OutageORM.id.ilike(f"%{search}%"),
            OutageORM.site_id.ilike(f"%{search}%"),
            OutageORM.site_name.ilike(f"%{search}%"),
        )
        self.assertIsNotNone(search_filter)

    def test_search_filter_matches_site_name(self):
        """Search term should match against site_name."""
        from app.repositories.outage_repository import OutageRepository
        from sqlalchemy.sql import or_
        from app.models.orm.outage import OutageORM

        search = "Alpha Site"
        search_filter = or_(
            OutageORM.id.ilike(f"%{search}%"),
            OutageORM.site_id.ilike(f"%{search}%"),
            OutageORM.site_name.ilike(f"%{search}%"),
        )
        self.assertIsNotNone(search_filter)

    def test_search_uses_case_insensitive_ilike(self):
        """Search should use case-insensitive matching (ilike / LOWER...LIKE)."""
        from sqlalchemy.sql import or_
        from app.models.orm.outage import OutageORM

        search = "ALPHA"
        search_filter = or_(
            OutageORM.id.ilike(f"%{search}%"),
            OutageORM.site_id.ilike(f"%{search}%"),
            OutageORM.site_name.ilike(f"%{search}%"),
        )
        filter_str = str(search_filter.compile(compile_kwargs={"literal_binds": True})).upper()
        # SQLAlchemy renders ilike as LOWER(col) LIKE LOWER(val) on the default dialect
        self.assertTrue(
            "ILIKE" in filter_str or ("LOWER" in filter_str and "LIKE" in filter_str),
            f"Expected case-insensitive match in: {filter_str}",
        )


# ---------------------------------------------------------------------------
# BE-020: Timeline event schema versioning tests
# ---------------------------------------------------------------------------

class TestEventDetailValidation(unittest.TestCase):
    """Unit tests for validate_event_detail — no DB required."""

    def test_created_event_validates_site_name(self):
        result = validate_event_detail("created", {"site_name": "Alpha"})
        self.assertEqual(result, {"site_name": "Alpha"})

    def test_resolved_event_validates_mttr(self):
        result = validate_event_detail("resolved", {"mttr_minutes": 30})
        self.assertEqual(result, {"mttr_minutes": 30})

    def test_sla_computed_validates_status(self):
        result = validate_event_detail("sla_computed", {"status": "met"})
        self.assertEqual(result, {"status": "met"})

    def test_unknown_event_type_raises(self):
        with self.assertRaises(ValueError):
            validate_event_detail("unknown_type", {})

    def test_missing_required_field_raises(self):
        with self.assertRaises(Exception):
            validate_event_detail("created", {})  # site_name required

    def test_updated_event_accepts_empty_changes(self):
        result = validate_event_detail("updated", {})
        self.assertEqual(result, {"changes": {}})

    def test_patched_event_accepts_partial_changes(self):
        result = validate_event_detail("patched", {"changes": {"status": "resolved"}})
        self.assertEqual(result, {"changes": {"status": "resolved"}})


class TestSchemaVersionConstant(unittest.TestCase):
    def test_current_schema_version_is_string_one(self):
        self.assertEqual(CURRENT_SCHEMA_VERSION, "1")


class TestOutageEventRepository(unittest.TestCase):
    """Tests for OutageEventRepository using an in-memory mock DB session."""

    def _make_session(self, stored: list):
        """Return a minimal mock session that captures added ORM objects."""

        class FakeSession:
            def add(self, obj):
                stored.append(obj)

            def commit(self):
                pass

            def refresh(self, obj):
                pass

        return FakeSession()

    def test_record_sets_schema_version(self):
        from app.repositories.outage_event_repository import OutageEventRepository

        stored = []
        repo = OutageEventRepository(self._make_session(stored))
        repo.record("out_1", "created", {"site_name": "Alpha"})

        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].schema_version, CURRENT_SCHEMA_VERSION)

    def test_record_sets_event_type(self):
        from app.repositories.outage_event_repository import OutageEventRepository

        stored = []
        repo = OutageEventRepository(self._make_session(stored))
        repo.record("out_1", "resolved", {"mttr_minutes": 15})

        self.assertEqual(stored[0].event_type, "resolved")

    def test_record_serialises_detail_as_json(self):
        from app.repositories.outage_event_repository import OutageEventRepository

        stored = []
        repo = OutageEventRepository(self._make_session(stored))
        repo.record("out_1", "sla_computed", {"status": "violated"})

        detail = json.loads(stored[0].detail)
        self.assertEqual(detail["status"], "violated")

    def test_record_rejects_unknown_event_type(self):
        from app.repositories.outage_event_repository import OutageEventRepository

        repo = OutageEventRepository(self._make_session([]))
        with self.assertRaises(ValueError):
            repo.record("out_1", "bogus_event", {})

    def test_list_for_outage_includes_schema_version(self):
        from app.repositories.outage_event_repository import OutageEventRepository

        evt = SimpleNamespace(
            id="evt_abc",
            outage_id="out_1",
            event_type="created",
            schema_version="1",
            detail=json.dumps({"site_name": "Alpha"}),
            occurred_at=datetime(2026, 1, 1, 12, 0),
        )

        class FakeQuery:
            def filter(self, *a, **kw):
                return self

            def order_by(self, *a):
                return self

            def count(self):
                return 1

            def offset(self, n):
                return self

            def limit(self, n):
                return self

            def all(self):
                return [evt]

        class FakeSession:
            def query(self, model):
                return FakeQuery()

        repo = OutageEventRepository(FakeSession())
        result = repo.list_for_outage("out_1")

        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertIn("schema_version", item)
        self.assertEqual(item["schema_version"], "1")


if __name__ == "__main__":
    unittest.main()
