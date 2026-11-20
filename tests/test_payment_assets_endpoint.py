"""Tests for the settlement asset listing endpoint (#883)."""

from fastapi.testclient import TestClient

from app.core.config import settings

AUTH = {"Authorization": "Bearer test-engineer-token"}
URL = "/api/v1/payments/assets"


def test_always_lists_native_xlm(client: TestClient):
    response = client.get(URL, headers=AUTH)

    assert response.status_code == 200
    xlm = next(asset for asset in response.json() if asset["code"] == "XLM")
    assert xlm["native"] is True
    assert xlm["issuer"] is None


def test_includes_configured_asset_and_issuer(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "PAYMENT_ASSET_CODE", "USDC")
    monkeypatch.setattr(settings, "PAYMENT_ASSET_ISSUER", "GISSUER")

    body = client.get(URL, headers=AUTH).json()
    usdc = next(asset for asset in body if asset["code"] == "USDC")

    assert usdc["issuer"] == "GISSUER"
    assert usdc["native"] is False


def test_does_not_duplicate_native_asset(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "PAYMENT_ASSET_CODE", "XLM")

    codes = [asset["code"] for asset in client.get(URL, headers=AUTH).json()]

    assert codes.count("XLM") == 1


def test_requires_authentication(client: TestClient):
    assert client.get(URL).status_code in {401, 403}
