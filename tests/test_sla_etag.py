"""
Issue #503 – HTTP ETag caching headers on idempotent GET /api/v1/sla/results.

Verifies:
- ``GET /api/v1/sla/status`` and ``GET /api/v1/sla/calculate`` emit an MD5
  ``ETag`` header computed from the response payload.
- Replaying the request with a matching ``If-None-Match`` header returns
  ``304 Not Modified`` with an empty body.
- Different inputs produce different ETags; stale tags still return 200.
"""

from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer test-engineer-token"}

STATUS_URL = "/api/v1/sla/status?outage_id=out-etag-1&severity=critical&mttr_minutes=10"
CALC_URL = "/api/v1/sla/calculate?outage_id=out-etag-1&severity=critical&mttr_minutes=10"


class TestSLAStatusETag:
    def test_status_returns_etag_header(self, client: TestClient):
        response = client.get(STATUS_URL, headers=AUTH)
        assert response.status_code == 200
        etag = response.headers.get("ETag")
        assert etag is not None
        assert etag.startswith('"') and etag.endswith('"')

    def test_status_returns_304_on_matching_if_none_match(self, client: TestClient):
        first = client.get(STATUS_URL, headers=AUTH)
        etag = first.headers["ETag"]

        second = client.get(
            STATUS_URL,
            headers={**AUTH, "If-None-Match": etag},
        )
        assert second.status_code == 304
        assert second.content == b""

    def test_status_returns_200_on_stale_tag(self, client: TestClient):
        stale = client.get(STATUS_URL, headers={**AUTH, "If-None-Match": '"deadbeef"'})
        assert stale.status_code == 200
        assert stale.json()["outage_id"] == "out-etag-1"

    def test_etag_changes_with_input(self, client: TestClient):
        a = client.get(STATUS_URL, headers=AUTH)
        b = client.get(
            "/api/v1/sla/status?outage_id=out-etag-2&severity=critical&mttr_minutes=10",
            headers=AUTH,
        )
        assert a.headers["ETag"] != b.headers["ETag"]


class TestSLACalculateETag:
    def test_calculate_returns_etag_header(self, client: TestClient):
        response = client.get(CALC_URL, headers=AUTH)
        assert response.status_code == 200
        assert response.headers.get("ETag") is not None

    def test_calculate_returns_304_on_matching_if_none_match(self, client: TestClient):
        first = client.get(CALC_URL, headers=AUTH)
        etag = first.headers["ETag"]

        second = client.get(CALC_URL, headers={**AUTH, "If-None-Match": etag})
        assert second.status_code == 304
        assert second.content == b""

    def test_calculate_returns_200_on_mismatched_tag(self, client: TestClient):
        response = client.get(CALC_URL, headers={**AUTH, "If-None-Match": '"mismatch"'})
        assert response.status_code == 200
        assert response.json()["outage_id"] == "out-etag-1"

    def test_calculate_requires_auth(self, client: TestClient):
        response = client.get(CALC_URL)
        assert response.status_code == 401
