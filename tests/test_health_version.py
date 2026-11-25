"""Tests for the build metadata endpoint (#891)."""

from fastapi.testclient import TestClient

from app.core.config import settings

URL = "/api/v1/health/version"


def test_returns_configured_version(client: TestClient):
    response = client.get(URL)

    assert response.status_code == 200
    assert response.json() == {"version": settings.VERSION}


def test_includes_commit_and_build_time_when_set(client: TestClient, monkeypatch):
    monkeypatch.setenv("GIT_COMMIT", "abc123")
    monkeypatch.setenv("BUILD_TIME", "2026-01-01T00:00:00Z")

    body = client.get(URL).json()

    assert body["version"] == settings.VERSION
    assert body["commit"] == "abc123"
    assert body["build_time"] == "2026-01-01T00:00:00Z"


def test_does_not_expose_secrets(client: TestClient, monkeypatch):
    monkeypatch.setenv("GIT_COMMIT", "abc123")

    body = client.get(URL).json()

    assert set(body).issubset({"version", "commit", "build_time"})
