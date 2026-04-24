"""
Contract-parity integration tests for SLA preview and resolve behavior.

Covers:
- SLA preview consistency across representative severities
- Translated contract result semantics (status codes, payment_type codes, rating codes)
- Resolve endpoint returns SLA result aligned with contract adapter output
- Recompute endpoint returns consistent SLA result
"""
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
from app.services.contracts.sla_adapter import SLAContractAdapter
from app.services.contracts.translation import translate_contract_result
from app.services.sla import SLACalculator


class ContractTranslationParityTests(unittest.TestCase):
    """Assert that the contract adapter round-trips correctly for all severities."""

    SEVERITIES = ["critical", "high", "medium", "low"]

    def _local_result(self, severity: str, mttr_minutes: int) -> SLAResult:
        return SLACalculator.calculate(
            outage_id="test_out",
            severity=severity,
            mttr_minutes=mttr_minutes,
        )

    def test_violated_contract_status_translates_to_violated(self):
        raw = SLAContractAdapter.calculate_sla("out_1", "critical", mttr_minutes=999)
        self.assertEqual(raw["status"], "viol")
        translated = translate_contract_result(raw)
        self.assertEqual(translated.status, "violated")
        self.assertEqual(translated.payment_type, "penalty")

    def test_met_contract_status_translates_to_met(self):
        raw = SLAContractAdapter.calculate_sla("out_1", "low", mttr_minutes=1)
        self.assertEqual(raw["status"], "met")
        translated = translate_contract_result(raw)
        self.assertEqual(translated.status, "met")
        self.assertEqual(translated.payment_type, "reward")

    def test_rating_codes_round_trip(self):
        code_to_label = {"top": "exceptional", "high": "excellent", "good": "good", "poor": "poor"}
        for code, label in code_to_label.items():
            raw = {"outage_id": "x", "status": "met", "mttr_minutes": 1, "threshold_minutes": 10,
                   "amount": 100, "payment_type": "rew", "rating": code}
            translated = translate_contract_result(raw)
            self.assertEqual(translated.rating, label, f"rating code '{code}' should map to '{label}'")

    def test_contract_adapter_output_matches_local_calculator_for_all_severities(self):
        """Contract adapter must produce semantically equivalent results to local calculator."""
        for severity in self.SEVERITIES:
            with self.subTest(severity=severity):
                mttr = 5
                local = self._local_result(severity, mttr)
                raw = SLAContractAdapter.calculate_sla("out_x", severity, mttr)
                translated = translate_contract_result(raw)

                self.assertEqual(translated.status, local.status)
                self.assertEqual(translated.mttr_minutes, local.mttr_minutes)
                self.assertEqual(translated.threshold_minutes, local.threshold_minutes)
                self.assertEqual(translated.amount, local.amount)
                self.assertEqual(translated.payment_type, local.payment_type)
                self.assertEqual(translated.rating, local.rating)


class SLAPreviewContractParityTests(unittest.TestCase):
    """SLA preview endpoint must return results consistent with contract adapter semantics."""

    def setUp(self):
        self.client = TestClient(app)

    def test_preview_violated_critical(self):
        response = self.client.post("/api/v1/sla/preview", json={"severity": "critical", "mttr_minutes": 999})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "violated")
        self.assertEqual(body["payment_type"], "penalty")
        self.assertEqual(body["rating"], "poor")

    def test_preview_met_low_exceptional(self):
        response = self.client.post("/api/v1/sla/preview", json={"severity": "low", "mttr_minutes": 1})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "met")
        self.assertEqual(body["payment_type"], "reward")
        self.assertIn(body["rating"], ["exceptional", "excellent", "good"])

    def test_preview_severities_return_consistent_threshold(self):
        """Each severity preview must return a positive threshold_minutes."""
        for severity in ["critical", "high", "medium", "low"]:
            with self.subTest(severity=severity):
                response = self.client.post(
                    "/api/v1/sla/preview", json={"severity": severity, "mttr_minutes": 5}
                )
                self.assertEqual(response.status_code, 200)
                self.assertGreater(response.json()["threshold_minutes"], 0)


class ResolveContractParityTests(unittest.TestCase):
    """Resolve endpoint SLA output must align with contract adapter translation."""

    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides[get_db] = lambda: iter([object()])

        self.outage = Outage(
            id="out_c1",
            site_name="Site C",
            site_id="site_c",
            severity="high",
            status="resolved",
            detected_at=datetime.now(),
            resolved_at=datetime.now(),
            description="Link down",
            affected_services=["LTE"],
        )
        self.sla = SLAResult(
            id=10,
            outage_id="out_c1",
            status="violated",
            mttr_minutes=60,
            threshold_minutes=30,
            amount=-300.0,
            payment_type="penalty",
            rating="poor",
        )
        self.payment = PaymentTransaction(
            id="pay_c1",
            transaction_hash="tx_c1",
            type="penalty",
            amount=300.0,
            asset_code="USDC",
            from_address="SYSTEM_POOL",
            to_address="OUTAGE_SETTLEMENT",
            status="pending",
            outage_id="out_c1",
            sla_result_id=10,
            created_at=datetime.now(),
        )

    def tearDown(self):
        app.dependency_overrides.clear()

    def _make_repos(self):
        outage, sla, payment = self.outage, self.sla, self.payment

        class FakeOutageRepo:
            def __init__(self, db): pass
            def resolve(self, outage_id, mttr_minutes): return outage

        class FakeSLARepo:
            def __init__(self, db): pass
            def create_if_changed(self, s): return sla

        class FakePaymentRepo:
            def __init__(self, db): pass
            def create_for_sla_result(self, outage_id, s): return payment

        return FakeOutageRepo, FakeSLARepo, FakePaymentRepo

    def test_resolve_sla_status_matches_contract_translation(self):
        FakeOutageRepo, FakeSLARepo, FakePaymentRepo = self._make_repos()
        with patch("app.api.v1.endpoints.outages.OutageRepository", FakeOutageRepo), \
             patch("app.api.v1.endpoints.outages.SLARepository", FakeSLARepo), \
             patch("app.api.v1.endpoints.outages.PaymentRepository", FakePaymentRepo), \
             patch("app.api.v1.endpoints.outages.OutageEventRepository"), \
             patch("app.api.v1.endpoints.outages.audit_log"), \
             patch("app.api.v1.endpoints.outages.trigger_sla_violation_webhooks", lambda *a, **kw: None):
            response = self.client.post("/api/v1/outages/out_c1/resolve", json={"mttr_minutes": 60})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["sla"]["status"], "violated")
        self.assertEqual(body["sla"]["payment_type"], "penalty")
        self.assertEqual(body["payment"]["type"], "penalty")

    def test_resolve_met_sla_returns_reward(self):
        outage = self.outage
        sla_met = SLAResult(
            id=11, outage_id="out_c1", status="met", mttr_minutes=5,
            threshold_minutes=30, amount=150.0, payment_type="reward", rating="exceptional",
        )
        payment_met = PaymentTransaction(
            id="pay_c2", transaction_hash="tx_c2", type="reward", amount=150.0,
            asset_code="USDC", from_address="SYSTEM_POOL", to_address="OUTAGE_SETTLEMENT",
            status="pending", outage_id="out_c1", sla_result_id=11, created_at=datetime.now(),
        )

        class FakeOutageRepo:
            def __init__(self, db): pass
            def resolve(self, outage_id, mttr_minutes): return outage

        class FakeSLARepo:
            def __init__(self, db): pass
            def create_if_changed(self, s): return sla_met

        class FakePaymentRepo:
            def __init__(self, db): pass
            def create_for_sla_result(self, outage_id, s): return payment_met

        with patch("app.api.v1.endpoints.outages.OutageRepository", FakeOutageRepo), \
             patch("app.api.v1.endpoints.outages.SLARepository", FakeSLARepo), \
             patch("app.api.v1.endpoints.outages.PaymentRepository", FakePaymentRepo), \
             patch("app.api.v1.endpoints.outages.OutageEventRepository"), \
             patch("app.api.v1.endpoints.outages.audit_log"), \
             patch("app.api.v1.endpoints.outages.trigger_sla_violation_webhooks", lambda *a, **kw: None):
            response = self.client.post("/api/v1/outages/out_c1/resolve", json={"mttr_minutes": 5})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["sla"]["status"], "met")
        self.assertEqual(body["sla"]["payment_type"], "reward")
        self.assertEqual(body["payment"]["type"], "reward")

    def test_recompute_returns_consistent_sla(self):
        outage = self.outage
        sla, payment = self.sla, self.payment

        class FakeOutageRepo:
            def __init__(self, db): pass
            def get(self, outage_id): return outage
            def get_orm_locked(self, outage_id): return SimpleNamespace(mttr_minutes=60)

        class FakeSLARepo:
            def __init__(self, db): pass
            def create_if_changed(self, s): return sla

        class FakePaymentRepo:
            def __init__(self, db): pass
            def create_for_sla_result(self, outage_id, s): return payment

        with patch("app.api.v1.endpoints.outages.OutageRepository", FakeOutageRepo), \
             patch("app.api.v1.endpoints.outages.SLARepository", FakeSLARepo), \
             patch("app.api.v1.endpoints.outages.PaymentRepository", FakePaymentRepo), \
             patch("app.api.v1.endpoints.outages.OutageEventRepository"), \
             patch("app.api.v1.endpoints.outages.audit_log"), \
             patch("app.api.v1.endpoints.outages.trigger_sla_violation_webhooks", lambda *a, **kw: None):
            response = self.client.post("/api/v1/outages/out_c1/recompute-sla")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["sla"]["outage_id"], "out_c1")
        self.assertEqual(body["sla"]["status"], "violated")
        self.assertEqual(body["payment"]["sla_result_id"], 10)


if __name__ == "__main__":
    unittest.main()
