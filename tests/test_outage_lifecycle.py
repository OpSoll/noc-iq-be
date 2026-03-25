import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import app
from app.models.outage import Outage
from app.models.payment import PaymentTransaction
from app.models.sla import SLAResult


class OutageLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides[get_db] = lambda: iter([object()])

        self.outage = Outage(
            id="out_1",
            site_name="Site A",
            site_id="site_1",
            severity="critical",
            status="resolved",
            detected_at=datetime.now(),
            resolved_at=datetime.now(),
            description="Fiber cut",
            affected_services=["4G"],
        )
        self.sla = SLAResult(
            id=1,
            outage_id="out_1",
            status="violated",
            mttr_minutes=20,
            threshold_minutes=15,
            amount=-500,
            payment_type="penalty",
            rating="poor",
        )
        self.payment = PaymentTransaction(
            id="pay_1",
            transaction_hash="tx_1",
            type="penalty",
            amount=500.0,
            asset_code="USDC",
            from_address="SYSTEM_POOL",
            to_address="OUTAGE_SETTLEMENT",
            status="pending",
            outage_id="out_1",
            sla_result_id=1,
            created_at=datetime.now(),
        )

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_list_outages(self):
        class FakeOutageRepo:
            def __init__(self, db):
                self.db = db

            def list(self, severity=None, status=None, page=1, page_size=20):
                return {"items": [self_outage], "total": 1, "page": page, "page_size": page_size}

        self_outage = self.outage
        with patch("app.api.v1.endpoints.outages.OutageRepository", FakeOutageRepo):
            response = self.client.get("/api/v1/outages")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["items"][0]["id"], "out_1")

    def test_create_outage(self):
        class FakeOutageRepo:
            def __init__(self, db):
                self.db = db

            def create(self, payload):
                return self_outage

        self_outage = Outage(
            id="out_new",
            site_name="Site B",
            site_id="site_2",
            severity="high",
            status="open",
            detected_at=datetime.now(),
            description="Power issue",
            affected_services=["5G"],
        )
        payload = {
            "id": "out_new",
            "site_name": "Site B",
            "site_id": "site_2",
            "severity": "high",
            "status": "open",
            "detected_at": datetime.now().isoformat(),
            "description": "Power issue",
            "affected_services": ["5G"],
        }
        with patch("app.api.v1.endpoints.outages.OutageRepository", FakeOutageRepo):
            response = self.client.post("/api/v1/outages", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], "out_new")

    def test_resolve_outage(self):
        self_outage = self.outage
        self_sla = self.sla
        self_payment = self.payment

        class FakeOutageRepo:
            def __init__(self, db):
                self.db = db

            def resolve(self, outage_id, mttr_minutes):
                return self_outage

        class FakeSLARepo:
            def __init__(self, db):
                self.db = db

            def create_if_changed(self, sla):
                return self_sla

        class FakePaymentRepo:
            def __init__(self, db):
                self.db = db

            def create_for_sla_result(self, outage_id, stored_sla):
                return self_payment

        with patch("app.api.v1.endpoints.outages.OutageRepository", FakeOutageRepo), patch(
            "app.api.v1.endpoints.outages.SLARepository", FakeSLARepo
        ), patch("app.api.v1.endpoints.outages.PaymentRepository", FakePaymentRepo), patch(
            "app.api.v1.endpoints.outages.trigger_sla_violation_webhooks", lambda *args, **kwargs: []
        ):
            response = self.client.post("/api/v1/outages/out_1/resolve", json={"mttr_minutes": 20})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["outage"]["id"], "out_1")
        self.assertEqual(body["sla"]["payment_type"], "penalty")
        self.assertEqual(body["payment"]["sla_result_id"], 1)

    def test_recompute_sla(self):
        self_outage = self.outage
        self_sla = self.sla
        self_payment = self.payment

        class FakeOutageRepo:
            def __init__(self, db):
                self.db = db

            def get(self, outage_id):
                return self_outage

            def get_orm(self, outage_id):
                return SimpleNamespace(mttr_minutes=20)

        class FakeSLARepo:
            def __init__(self, db):
                self.db = db

            def create_if_changed(self, sla):
                return self_sla

        class FakePaymentRepo:
            def __init__(self, db):
                self.db = db

            def create_for_sla_result(self, outage_id, stored_sla):
                return self_payment

        with patch("app.api.v1.endpoints.outages.OutageRepository", FakeOutageRepo), patch(
            "app.api.v1.endpoints.outages.SLARepository", FakeSLARepo
        ), patch("app.api.v1.endpoints.outages.PaymentRepository", FakePaymentRepo), patch(
            "app.api.v1.endpoints.outages.trigger_sla_violation_webhooks", lambda *args, **kwargs: []
        ):
            response = self.client.post("/api/v1/outages/out_1/recompute-sla")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["sla"]["outage_id"], "out_1")
        self.assertEqual(body["payment"]["id"], "pay_1")


if __name__ == "__main__":
    unittest.main()
