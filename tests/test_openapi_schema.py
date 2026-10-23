"""
Issue #502 – OpenAPI response schemas and field descriptions.

Verifies the generated OpenAPI document:
- Every /api/v1 operation declares an explicit JSON response schema for its
  success status (or is a documented 204 No Content).
- Key Pydantic models carry field descriptions so the /docs Swagger UI
  renders complete documentation.
- The new bulk-resolve endpoint is documented with a request body schema.
"""

from app.main import app


def _openapi() -> dict:
    return app.openapi()


class TestAllRoutesDocumentResponses:
    def test_every_v1_operation_has_json_response_schema(self):
        schema = _openapi()
        for path, methods in schema["paths"].items():
            if not path.startswith("/api/v1"):
                continue
            for method, operation in methods.items():
                if method not in ("get", "post", "put", "patch", "delete"):
                    continue
                success = next(
                    (code for code in ("200", "201", "202", "204") if code in operation.get("responses", {})),
                    None,
                )
                assert success is not None, f"{method.upper()} {path} has no documented success response"
                if success == "204":
                    continue
                response_spec = operation["responses"][success]
                assert "content" in response_spec, f"{method.upper()} {path} 200 response has no content"
                assert "application/json" in response_spec["content"], (
                    f"{method.upper()} {path} 200 response has no application/json schema"
                )
                # An empty schema {} is the explicit "no response body" marker
                # (e.g. the plain-text Prometheus export route).
                assert "schema" in response_spec["content"]["application/json"], (
                    f"{method.upper()} {path} 200 response has no schema key"
                )


class TestKeyModelsHaveDescriptions:
    def _schema_for(self, model_name: str) -> dict:
        components = _openapi()["components"]["schemas"]
        assert model_name in components, f"Model {model_name} missing from OpenAPI"
        return components[model_name]

    def test_outage_fields_described(self):
        schema = self._schema_for("Outage")
        for field in ("id", "site_name", "severity", "status", "description"):
            assert "description" in schema["properties"][field], f"Outage.{field} lacks a description"

    def test_paginated_outages_fields_described(self):
        schema = self._schema_for("PaginatedOutages")
        for field in ("items", "total", "limit", "offset"):
            assert "description" in schema["properties"][field], f"PaginatedOutages.{field} lacks a description"

    def test_sla_status_fields_described(self):
        schema = self._schema_for("SLAStatusResponse")
        for field in ("outage_id", "state", "threshold_minutes"):
            assert "description" in schema["properties"][field], f"SLAStatusResponse.{field} lacks a description"

    def test_bulk_resolve_request_and_response_described(self):
        request_schema = self._schema_for("BulkResolveOutageRequest")
        assert "description" in request_schema["properties"]["outage_ids"]
        response_schema = self._schema_for("BulkResolveOutageResponse")
        for field in ("succeeded", "failed", "total", "success_count", "failure_count"):
            assert "description" in response_schema["properties"][field]


class TestNewEndpointsDocumented:
    def test_bulk_resolve_route_documented(self):
        schema = _openapi()
        path = "/api/v1/outages/bulk-resolve"
        assert path in schema["paths"]
        operation = schema["paths"][path]["post"]
        assert "requestBody" in operation
        assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(
            "BulkResolveOutageRequest"
        )
        assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
            "BulkResolveOutageResponse"
        )

    def test_outages_list_documents_limit_and_offset_params(self):
        schema = _openapi()
        operation = schema["paths"]["/api/v1/outages/"]["get"]
        params = {p["name"]: p for p in operation.get("parameters", [])}
        assert "limit" in params
        assert "offset" in params
        assert params["limit"]["schema"]["maximum"] == 100
