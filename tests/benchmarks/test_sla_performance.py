import time

from app.services.sla import SLACalculator


def test_1000_sla_calculations_complete_under_50ms():
    started_at = time.perf_counter()
    for index in range(1_000):
        SLACalculator.calculate("benchmark", "high", index % 90)
    elapsed_seconds = time.perf_counter() - started_at

    assert elapsed_seconds < 0.05, f"1,000 SLA calculations took {elapsed_seconds * 1_000:.2f} ms"