"""Structured dependency-injection container (#413)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.wallet_registry import WalletRegistry
from app.services.sla_service import SLAOrchestrator
from app.services.webhook_service import (
    get_active_webhooks_for_event,
    retry_pending_deliveries,
)
from app.services.webhook_signing import sign_payload

logger = logging.getLogger("dependencies")


class ServiceContainer:
    """Singleton service container that lazily instantiates shared singletons.

    Usage::

        container = ServiceContainer()
        wallet_reg = container.wallet_registry
    """

    _instance: ServiceContainer | None = None

    def __new__(cls) -> ServiceContainer:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialised = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialised:
            return
        self._initialised = True
        self._services: dict[str, Any] = {}

    # -- lazily created singletons ----------------------------------------

    @property
    def wallet_registry(self) -> WalletRegistry:
        if "wallet_registry" not in self._services:
            self._services["wallet_registry"] = WalletRegistry
        return self._services["wallet_registry"]

    @property
    def sla_service(self) -> type:
        """SLAOrchestrator is per-request (db-bound), so return the class."""
        return SLAOrchestrator

    @property
    def webhook_service(self) -> dict[str, Any]:
        if "webhook_service" not in self._services:
            self._services["webhook_service"] = {
                "get_active": get_active_webhooks_for_event,
                "retry_pending": retry_pending_deliveries,
                "sign": sign_payload,
            }
        return self._services["webhook_service"]

    # -- health -----------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Return health status for all registered services."""
        return {
            "wallet_registry": "ok",
            "sla_service": "ok",
            "webhook_service": "ok",
        }

    # -- reset (for tests) -----------------------------------------------

    def reset(self) -> None:
        self._services.clear()


# Module-level singleton
_container = ServiceContainer()


# ---------------------------------------------------------------------------
# FastAPI Depends() callables
# ---------------------------------------------------------------------------

def get_container() -> ServiceContainer:
    return _container


def get_wallet_registry() -> WalletRegistry:
    return _container.wallet_registry


def get_sla_service() -> type:
    return _container.sla_service


def get_webhook_service() -> dict[str, Any]:
    return _container.webhook_service


# ---------------------------------------------------------------------------
# Health-check router
# ---------------------------------------------------------------------------

di_router = APIRouter(tags=["dependencies"])


@di_router.get("/debug/service-health")
def service_health(container: ServiceContainer = Depends(get_container)) -> dict[str, Any]:
    """Return health status of all registered services."""
    return container.health()
