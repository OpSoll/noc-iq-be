"""
Issues #955 / #956 – Route-level OpenAPI completeness.

#955: every route declares a ``response_model`` (or returns 204 No Content),
      unless it is listed in ``RESPONSE_MODEL_EXEMPTIONS`` with a reason.
#956: every route carries at least one tag, and an explicit ``summary=`` or a
      docstring (rendered as the OpenAPI description).

Both checks fail on *new* omissions. ``UNDOCUMENTED_ROUTES_BASELINE`` records
routes that predate the check; it may only shrink — once a route gains a
summary or docstring, the stale-entry test fails until it is removed here.

Routes are keyed as ``"METHOD /path"``.
"""

from fastapi.routing import APIRoute

from app.main import app

# Routes that intentionally return a non-modelled body. Keep the reason
# specific; add an entry only when a Pydantic model genuinely does not fit.
RESPONSE_MODEL_EXEMPTIONS: dict[str, str] = {
    # Infrastructure probes return ad-hoc JSONResponse payloads with a
    # dynamic status code (200/503) for load balancers and orchestrators.
    "GET /health": "infrastructure probe",
    "GET /health/liveness": "infrastructure probe",
    "GET /health/readiness": "infrastructure probe; JSONResponse with 200/503",
    "GET /health/worker": "infrastructure probe; JSONResponse with 200/503",
    "GET /health/concurrency": "infrastructure probe; JSONResponse with 200/503",
    "GET /api/v1/health/detailed": "infrastructure probe; JSONResponse with 200/503",
    # Prometheus text exposition format, not JSON.
    "GET /metrics": "Prometheus text exposition format",
    "GET /api/v1/metrics/prometheus": "Prometheus text exposition format",
    "GET /api/v1/metrics/metrics/prometheus": "Prometheus text exposition format",
    "GET /api/v1/health/metrics/prometheus": "Prometheus text exposition format",
    # Format-switched file exports: the body shape depends on ?format=.
    "GET /api/v1/outages/export": "JSON/CSV file export",
    "GET /api/v1/payments/accounting-report": "CSV/PDF file export",
    "GET /api/v1/sla/analytics/export": "JSON/CSV file export",
    "GET /api/v1/sla/analytics/dashboard/export": "JSON/CSV file export",
    "GET /api/v1/sla/analytics/trends/export": "JSON/CSV file export",
    "GET /api/v1/sla/analytics/performance/export": "JSON/CSV file export",
    # TODO: introduce response schemas for these JSON endpoints.
    "POST /api/v1/disputes/{dispute_id}/re-simulate": "pending schema: simulation comparison payload",
    "POST /api/v1/webhooks/dead-letter-queue/{delivery_id}/redeliver": "pending schema: redelivery ack",
}

# Routes without a summary or docstring when this check was introduced.
# Do NOT add to this list — document new routes instead.
UNDOCUMENTED_ROUTES_BASELINE: frozenset[str] = frozenset({
    "DELETE /api/v1/outages/{outage_id}",
    "DELETE /api/v1/webhooks/{webhook_id}",
    "GET /api/v1/audit",
    "GET /api/v1/auth/me",
    "GET /api/v1/auth/ping",
    "GET /api/v1/health/metrics/cardinality",
    "GET /api/v1/health/metrics/pool",
    "GET /api/v1/health/metrics/rate-limits",
    "GET /api/v1/health/metrics/webhook-workers",
    "GET /api/v1/metrics/cardinality",
    "GET /api/v1/metrics/metrics/cardinality",
    "GET /api/v1/metrics/metrics/pool",
    "GET /api/v1/metrics/metrics/rate-limits",
    "GET /api/v1/metrics/metrics/webhook-workers",
    "GET /api/v1/metrics/pool",
    "GET /api/v1/metrics/rate-limits",
    "GET /api/v1/metrics/webhook-workers",
    "GET /api/v1/outages/deleted",
    "GET /api/v1/outages/export",
    "GET /api/v1/outages/violations",
    "GET /api/v1/outages/{outage_id}",
    "GET /api/v1/outages/{outage_id}/transitions",
    "GET /api/v1/payments/",
    "GET /api/v1/payments/accounting-report",
    "GET /api/v1/payments/dead-letter",
    "GET /api/v1/payments/idempotency/metrics",
    "GET /api/v1/payments/ping",
    "GET /api/v1/payments/{transaction_id}",
    "GET /api/v1/payments/{transaction_id}/history",
    "GET /api/v1/sla/analytics/dashboard",
    "GET /api/v1/sla/analytics/trends",
    "GET /api/v1/transactions/{tx_hash}/provenance",
    "GET /api/v1/wallets/metrics",
    "GET /api/v1/wallets/ping",
    "GET /api/v1/wallets/{address}/balance",
    "GET /api/v1/wallets/{user_id}",
    "GET /api/v1/wallets/{user_id}/status",
    "GET /api/v1/webhooks",
    "GET /api/v1/webhooks/{webhook_id}",
    "GET /api/v1/webhooks/{webhook_id}/deliveries",
    "GET /api/v2/health",
    "GET /health",
    "GET /health/liveness",
    "GET /health/readiness",
    "GET /metrics",
    "PATCH /api/v1/webhooks/{webhook_id}",
    "POST /api/v1/auth/login",
    "POST /api/v1/auth/logout",
    "POST /api/v1/auth/refresh",
    "POST /api/v1/auth/register",
    "POST /api/v1/outages/",
    "POST /api/v1/outages/bulk",
    "POST /api/v1/outages/{outage_id}/restore",
    "POST /api/v1/payments/reconcile",
    "POST /api/v1/payments/{outage_id}/create",
    "POST /api/v1/payments/{transaction_id}/reconcile",
    "POST /api/v1/payments/{transaction_id}/replay",
    "POST /api/v1/payments/{transaction_id}/retry",
    "POST /api/v1/transactions/{tx_hash}/verify",
    "POST /api/v1/wallets/create",
    "POST /api/v1/wallets/link",
    "POST /api/v1/webhooks",
    "POST /api/v1/webhooks/{webhook_id}/deliveries/{delivery_id}/retry",
    "PUT /api/v1/outages/{outage_id}",
    "PUT /api/v1/sla/config/{severity}",
})


def _routes() -> list[APIRoute]:
    return [r for r in app.routes if isinstance(r, APIRoute) and r.include_in_schema]


def _keys(route: APIRoute) -> set[str]:
    return {f"{method} {route.path}" for method in route.methods}


def _all_keys() -> set[str]:
    return {key for route in _routes() for key in _keys(route)}


def _lacks_response_model(route: APIRoute) -> bool:
    return route.response_model is None and route.status_code != 204


def _lacks_docs(route: APIRoute) -> bool:
    return not route.summary and not (route.description or "").strip()


class TestResponseModelCoverage:
    def test_routes_without_models_are_detected(self):
        offenders = {k for r in _routes() if _lacks_response_model(r) for k in _keys(r)}
        unexpected = sorted(offenders - set(RESPONSE_MODEL_EXEMPTIONS))
        assert not unexpected, (
            "Routes missing a response_model (declare one, or add a documented "
            f"exemption to RESPONSE_MODEL_EXEMPTIONS): {unexpected}"
        )

    def test_exemptions_are_documented(self):
        missing_reason = [k for k, reason in RESPONSE_MODEL_EXEMPTIONS.items() if not reason.strip()]
        assert not missing_reason, f"Exemptions without a reason: {missing_reason}"

    def test_exemptions_are_not_stale(self):
        offenders = {k for r in _routes() if _lacks_response_model(r) for k in _keys(r)}
        stale = sorted(set(RESPONSE_MODEL_EXEMPTIONS) - offenders)
        assert not stale, f"Exempted routes now have a response_model or no longer exist; remove: {stale}"

    def test_detector_flags_route_without_model(self):
        route = APIRoute("/probe", lambda: {}, methods=["GET"])
        assert _lacks_response_model(route)

    def test_detector_accepts_204_route(self):
        route = APIRoute("/probe", lambda: None, methods=["DELETE"], status_code=204)
        assert not _lacks_response_model(route)


class TestRouteSummaryAndTags:
    def test_every_route_has_a_tag(self):
        untagged = sorted(k for r in _routes() if not r.tags for k in _keys(r))
        assert not untagged, f"Routes without tags: {untagged}"

    def test_routes_without_summary_are_detected(self):
        offenders = {k for r in _routes() if _lacks_docs(r) for k in _keys(r)}
        unexpected = sorted(offenders - UNDOCUMENTED_ROUTES_BASELINE)
        assert not unexpected, (
            "Routes missing both summary= and a docstring (add one; do not "
            f"extend UNDOCUMENTED_ROUTES_BASELINE): {unexpected}"
        )

    def test_baseline_is_not_stale(self):
        offenders = {k for r in _routes() if _lacks_docs(r) for k in _keys(r)}
        stale = sorted(UNDOCUMENTED_ROUTES_BASELINE - offenders)
        assert not stale, f"Routes now documented or removed; drop from baseline: {stale}"

    def test_openapi_operations_have_summary_and_tags(self):
        for path, methods in app.openapi()["paths"].items():
            for method, operation in methods.items():
                assert operation.get("summary"), f"{method.upper()} {path} has no summary"
                assert operation.get("tags"), f"{method.upper()} {path} has no tags"

    def test_detector_flags_undocumented_untagged_route(self):
        def handler():
            return {}

        route = APIRoute("/probe", handler, methods=["GET"])
        assert _lacks_docs(route)
        assert not route.tags

    def test_detector_accepts_summary_or_docstring(self):
        def documented():
            """Return a probe."""

        assert not _lacks_docs(APIRoute("/a", lambda: {}, methods=["GET"], summary="Probe"))
        assert not _lacks_docs(APIRoute("/b", documented, methods=["GET"]))
