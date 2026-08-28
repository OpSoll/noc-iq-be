import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import app
from app.models.sla import (
    SLADashboardKPI,
    SLAPerformanceAggregation,
    SLATrendPoint,
)


class SLAAnalyticsEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides[get_db] = lambda: iter([object()])

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_dashboard_returns_empty_state(self):
        class FakeSLARepository:
            def __init__(self, db):
                self.db = db

            def aggregate_dashboard_kpis(self):
                return SLADashboardKPI(
                    total_outages=0,
                    total_violations=0,
                    total_rewards=0.0,
                    total_penalties=0.0,
                    net_payout=0.0,
                )

        with patch("app.api.v1.endpoints.sla.SLARepository", FakeSLARepository):
            response = self.client.get("/api/v1/sla/analytics/dashboard")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "total_outages": 0,
                "total_violations": 0,
                "total_rewards": 0.0,
                "total_penalties": 0.0,
                "net_payout": 0.0,
            },
        )

    def test_dashboard_returns_populated_state(self):
        class FakeSLARepository:
            def __init__(self, db):
                self.db = db

            def aggregate_dashboard_kpis(self):
                return SLADashboardKPI(
                    total_outages=12,
                    total_violations=3,
                    total_rewards=2750.0,
                    total_penalties=425.0,
                    net_payout=2325.0,
                )

        with patch("app.api.v1.endpoints.sla.SLARepository", FakeSLARepository):
            response = self.client.get("/api/v1/sla/analytics/dashboard")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total_outages"], 12)
        self.assertEqual(response.json()["total_violations"], 3)
        self.assertEqual(response.json()["net_payout"], 2325.0)

    def test_trends_returns_empty_state(self):
        class FakeSLARepository:
            def __init__(self, db):
                self.db = db

            def aggregate_trends(self, limit_days=7):
                self.limit_days = limit_days
                return []

        with patch("app.api.v1.endpoints.sla.SLARepository", FakeSLARepository):
            response = self.client.get("/api/v1/sla/analytics/trends?days=14")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_trends_returns_populated_state(self):
        class FakeSLARepository:
            def __init__(self, db):
                self.db = db

            def aggregate_trends(self, limit_days=7):
                return [
                    SLATrendPoint(
                        date="2026-03-20",
                        total_outages=2,
                        violations=1,
                        rewards=300.0,
                        penalties=75.0,
                    ),
                    SLATrendPoint(
                        date="2026-03-21",
                        total_outages=4,
                        violations=2,
                        rewards=500.0,
                        penalties=125.0,
                    ),
                ]

        with patch("app.api.v1.endpoints.sla.SLARepository", FakeSLARepository):
            response = self.client.get("/api/v1/sla/analytics/trends")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 2)
        self.assertEqual(response.json()[0]["date"], "2026-03-20")
        self.assertEqual(response.json()[1]["violations"], 2)

    def test_performance_aggregation_returns_empty_state(self):
        class FakeSLARepository:
            def __init__(self, db):
                self.db = db

            def aggregate_performance(self, start_date=None, end_date=None):
                return SLAPerformanceAggregation(
                    total_outages=0,
                    violation_rate=0.0,
                    avg_mttr=0.0,
                    payout_sum=0.0,
                )

        with patch("app.api.v1.endpoints.sla.SLARepository", FakeSLARepository):
            response = self.client.get("/api/v1/sla/performance/aggregation")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "total_outages": 0,
                "violation_rate": 0.0,
                "avg_mttr": 0.0,
                "payout_sum": 0.0,
            },
        )

    def test_performance_aggregation_returns_populated_state(self):
        class FakeSLARepository:
            def __init__(self, db):
                self.db = db

            def aggregate_performance(self, start_date=None, end_date=None):
                return SLAPerformanceAggregation(
                    total_outages=8,
                    violation_rate=0.375,
                    avg_mttr=14.25,
                    payout_sum=1875.0,
                )

        with patch("app.api.v1.endpoints.sla.SLARepository", FakeSLARepository):
            response = self.client.get(
                "/api/v1/sla/performance/aggregation"
                "?start_date=2026-03-01T00:00:00Z&end_date=2026-03-31T23:59:59Z"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total_outages"], 8)
        self.assertEqual(response.json()["violation_rate"], 0.375)
        self.assertEqual(response.json()["avg_mttr"], 14.25)
        self.assertEqual(response.json()["payout_sum"], 1875.0)


if __name__ == "__main__":
    unittest.main()
