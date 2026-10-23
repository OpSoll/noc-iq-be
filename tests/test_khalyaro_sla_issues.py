"""Unit tests for khalyaro's SLA issues (#552, #553, #554, #555).

Covers:
- #552: reward credit tier for exceptional site uptime (>99.99%)
- #553: historical audit log comparison service (compare_results)
- #554: batch result deduplication scanner
- #555: penalty cap per billing period (max 100% of monthly contract fee)
"""
import pytest

from app.models.sla import SLAResult
from app.services.sla.sla_calculator import (
    EXCEPTIONAL_UPTIME_PCT,
    SLACalculator,
    deduplicate_outage_ids,
)


# ---------------------------------------------------------------------------
# Issue #552 — reward credit for exceptional site uptime (>99.99%)
# ---------------------------------------------------------------------------

class TestRewardCredit:
    def test_reward_granted_above_9999(self):
        result = SLACalculator.calculate_reward_credit("critical", 99.999)
        assert result["reward_granted"] is True
        assert result["payment_type"] == "reward"
        assert result["rating"] == "exceptional"
        assert result["reason_code"] == "uptime_exceptional"

    def test_credit_based_on_reward_base(self):
        result = SLACalculator.calculate_reward_credit("critical", 100.0)
        assert result["credit"] == 750  # critical reward_base

    def test_credit_varies_by_severity(self):
        assert SLACalculator.calculate_reward_credit("low", 99.995)["credit"] == 600

    def test_no_reward_exactly_at_9999(self):
        result = SLACalculator.calculate_reward_credit("high", EXCEPTIONAL_UPTIME_PCT)
        assert result["reward_granted"] is False
        assert result["credit"] == 0

    def test_no_reward_below_9999(self):
        result = SLACalculator.calculate_reward_credit("medium", 99.9)
        assert result["reward_granted"] is False
        assert result["credit"] == 0

    def test_unknown_severity_raises(self):
        with pytest.raises(ValueError):
            SLACalculator.calculate_reward_credit("emergency", 99.995)

    def test_severity_case_insensitive(self):
        result = SLACalculator.calculate_reward_credit("CRITICAL", 99.995)
        assert result["reward_granted"] is True
        assert result["credit"] == 750


# ---------------------------------------------------------------------------
# Issue #553 — historical audit log comparison service
# ---------------------------------------------------------------------------

class TestCompareResults:
    def _result(self, outage_id="outage-1", mttr_minutes=10, **overrides):
        return SLACalculator.calculate(outage_id, "critical", mttr_minutes, **overrides)

    def test_detects_changed_fields(self):
        original = self._result("outage-1", mttr_minutes=10)
        recalculated = self._result("outage-1", mttr_minutes=40)

        diff = SLACalculator.compare_results(original, recalculated)

        assert "amount" in diff["changed"]
        assert "decision_trace" in diff["changed"]
        assert "mttr_minutes" in diff["changed"]
        assert diff["differences"]["amount"] == {
            "original": original.amount,
            "recalculated": recalculated.amount,
        }

    def test_identical_results_have_no_changes(self):
        original = self._result("outage-1", mttr_minutes=10)
        recalculated = self._result("outage-1", mttr_minutes=10)

        diff = SLACalculator.compare_results(original, recalculated)

        assert diff["changed"] == []
        assert diff["differences"] == {}

    def test_accepts_plain_dicts(self):
        original = {"outage_id": "outage-1", "amount": 1500, "status": "violated"}
        recalculated = {"outage_id": "outage-1", "amount": 1000, "status": "violated"}

        diff = SLACalculator.compare_results(original, recalculated)

        assert "amount" in diff["changed"]
        assert diff["original"]["amount"] == 1500
        assert diff["recalculated"]["amount"] == 1000

    def test_returns_summary_structure(self):
        diff = SLACalculator.compare_results(
            self._result("outage-1", mttr_minutes=10),
            self._result("outage-1", mttr_minutes=40),
        )
        assert set(diff.keys()) == {"changed", "original", "recalculated", "differences"}
        assert isinstance(diff["original"], dict)
        assert isinstance(diff["recalculated"], dict)

    def test_audit_logging_does_not_break_comparison(self):
        # Audit logging is defensive: comparison must succeed regardless of
        # whether the audit trail service is available.
        original = self._result("outage-1", mttr_minutes=10)
        recalculated = self._result("outage-1", mttr_minutes=40)
        diff = SLACalculator.compare_results(original, recalculated)
        assert "amount" in diff["changed"]


# ---------------------------------------------------------------------------
# Issue #554 — batch result deduplication scanner
# ---------------------------------------------------------------------------

class TestDeduplicateOutageIds:
    def test_removes_duplicates_preserving_order(self):
        unique, skipped = deduplicate_outage_ids(
            ["a", "b", "a", "c", "b", "a"]
        )
        assert unique == ["a", "b", "c"]
        assert skipped == ["a", "b", "a"]

    def test_no_duplicates_returns_unchanged(self):
        unique, skipped = deduplicate_outage_ids(["a", "b", "c"])
        assert unique == ["a", "b", "c"]
        assert skipped == []

    def test_repeated_same_id(self):
        unique, skipped = deduplicate_outage_ids(["x", "x", "x"])
        assert unique == ["x"]
        assert skipped == ["x", "x"]

    def test_empty_input(self):
        unique, skipped = deduplicate_outage_ids([])
        assert unique == []
        assert skipped == []

    def test_duplicates_are_reported_for_warning(self):
        # The skipped list is meant to feed a warning message to the operator.
        unique, skipped = deduplicate_outage_ids(["a", "b", "a"])
        assert unique == ["a", "b"]
        assert len(skipped) == 1
        assert skipped[0] == "a"


# ---------------------------------------------------------------------------
# Issue #555 — penalty cap per billing period (max 100% monthly contract fee)
# ---------------------------------------------------------------------------

class TestPenaltyCap:
    def test_penalty_capped_by_monthly_contract_fee(self):
        # critical: threshold 15 min, penalty 100/min
        result = SLACalculator.calculate(
            "outage-1", "critical", mttr_minutes=30, monthly_contract_fee=1000
        )
        # raw penalty 15 * 100 = 1500 -> capped at 1000
        assert result.amount == -1000
        assert result.penalty_capped is True

    def test_penalty_below_cap_not_capped(self):
        result = SLACalculator.calculate(
            "outage-1", "critical", mttr_minutes=20, monthly_contract_fee=1000
        )
        # raw penalty 5 * 100 = 500 <= 1000 -> not capped
        assert result.amount == -500
        assert result.penalty_capped is False

    def test_default_behavior_preserved_without_contract_fee(self):
        result = SLACalculator.calculate("outage-1", "critical", mttr_minutes=30)
        assert result.amount == -1500
        assert result.penalty_capped is False
        assert result.decision_trace == (
            "MTTR 30 > threshold 15 (overtime 15 minutes)"
        )

    def test_max_penalty_override_wins(self):
        result = SLACalculator.calculate(
            "outage-1",
            "critical",
            mttr_minutes=30,
            monthly_contract_fee=1000,
            max_penalty=300,
        )
        assert result.amount == -300
        assert result.penalty_capped is True

    def test_reward_path_never_capped(self):
        result = SLACalculator.calculate(
            "outage-1", "critical", mttr_minutes=5, monthly_contract_fee=1000
        )
        assert result.status == "met"
        assert result.penalty_capped is False

    def test_capped_decision_trace_mentions_cap(self):
        result = SLACalculator.calculate(
            "outage-1", "critical", mttr_minutes=30, monthly_contract_fee=1000
        )
        assert "penalty capped at 1000" in result.decision_trace

    def test_calculate_sla_forwards_contract_fee(self):
        result = SLACalculator.calculate_sla(
            "outage-1", "critical", mttr_minutes=30, monthly_contract_fee=1000
        )
        assert result.amount == -1000
        assert result.penalty_capped is True

    def test_model_defaults_to_not_capped(self):
        result = self._plain_met_result()
        assert result.penalty_capped is False

    def _plain_met_result(self) -> SLAResult:
        return SLACalculator.calculate("outage-1", "low", mttr_minutes=5)