from fastapi import APIRouter, HTTPException, Query, status, Depends

from app.models.wallet import (
    Wallet,
    WalletBalanceResponse,
    WalletCreateRequest,
    WalletCreateResponse,
    WalletFundingStateResponse,
    WalletLinkRequest,
    WalletStatusResponse,
    WalletTrustlineResponse,
)
from app.services.wallet_registry import WalletRegistry
from app.services.audit_log import audit_log, WalletAuditEvents
from app.core.security import require_engineer

router = APIRouter()


@router.post("/create", response_model=WalletCreateResponse, status_code=status.HTTP_201_CREATED)
def create_wallet(payload: WalletCreateRequest, current_user=Depends(require_engineer)):
    actor_id = getattr(current_user, "id", None)
    try:
        result = WalletRegistry.create_wallet(payload)
        audit_log.log(
            WalletAuditEvents.WALLET_CREATED,
            actor_id=actor_id,
            details={"user_id": payload.user_id if hasattr(payload, "user_id") else None},
        )
        return result
    except Exception as exc:
        audit_log.log(
            WalletAuditEvents.WALLET_CREATE_FAILED,
            actor_id=actor_id,
            details={"error": str(exc)},
        )
        raise


@router.post("/link", response_model=Wallet)
def link_wallet(payload: WalletLinkRequest, current_user=Depends(require_engineer)):
    actor_id = getattr(current_user, "id", None)
    try:
        result = WalletRegistry.link_wallet(payload)
        audit_log.log(
            WalletAuditEvents.WALLET_LINKED,
            actor_id=actor_id,
            details={"user_id": payload.user_id if hasattr(payload, "user_id") else None},
        )
        return result
    except ValueError as exc:
        audit_log.log(
            WalletAuditEvents.WALLET_LINK_FAILED,
            actor_id=actor_id,
            details={"error": str(exc)},
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get("/ping")
def wallets_ping():
    return {"message": "wallets ok"}


@router.get("/{user_id}", response_model=Wallet)
def get_wallet(
    user_id: str,
    refresh: bool = Query(False, description="Force a live re-fetch instead of returning cached data"),
    current_user=Depends(require_engineer),
):
    actor_id = getattr(current_user, "id", None)
    try:
        wallet = WalletRegistry.get_wallet(user_id, refresh=refresh)
        if not wallet:
            raise HTTPException(status_code=404, detail="Wallet not found")
        audit_log.log(
            WalletAuditEvents.WALLET_FETCHED,
            actor_id=actor_id,
            details={"user_id": user_id, "refresh": refresh},
        )
        return wallet
    except HTTPException:
        raise
    except Exception as exc:
        audit_log.log(
            WalletAuditEvents.WALLET_FETCH_FAILED,
            actor_id=actor_id,
            details={"user_id": user_id, "error": str(exc)},
        )
        raise


@router.get("/{user_id}/status", response_model=WalletStatusResponse)
def get_wallet_status(
    user_id: str,
    refresh: bool = Query(False, description="Force a live re-fetch instead of returning cached data"),
    current_user=Depends(require_engineer),
):
    actor_id = getattr(current_user, "id", None)
    try:
        wallet_status = WalletRegistry.get_status(user_id, refresh=refresh)
        if not wallet_status:
            raise HTTPException(status_code=404, detail="Wallet not found")
        audit_log.log(
            WalletAuditEvents.WALLET_STATUS_CHECKED,
            actor_id=actor_id,
            details={"user_id": user_id, "refresh": refresh},
        )
        return wallet_status
    except HTTPException:
        raise
    except Exception as exc:
        audit_log.log(
            WalletAuditEvents.WALLET_STATUS_CHECK_FAILED,
            actor_id=actor_id,
            details={"user_id": user_id, "error": str(exc)},
        )
        raise


@router.get("/{user_id}/trustline", response_model=WalletTrustlineResponse, summary="Check trustline readiness for a wallet")
def get_wallet_trustline(
    user_id: str,
    refresh: bool = Query(False, description="Force a live re-fetch instead of returning cached data"),
    current_user=Depends(require_engineer),
):
    actor_id = getattr(current_user, "id", None)
    try:
        result = WalletRegistry.get_trustline(user_id, refresh=refresh)
        if not result:
            raise HTTPException(status_code=404, detail="Wallet not found")
        audit_log.log(
            WalletAuditEvents.WALLET_TRUSTLINE_CHECKED,
            actor_id=actor_id,
            details={"user_id": user_id, "refresh": refresh},
        )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        audit_log.log(
            WalletAuditEvents.WALLET_TRUSTLINE_CHECK_FAILED,
            actor_id=actor_id,
            details={"user_id": user_id, "error": str(exc)},
        )
        raise


@router.get("/{user_id}/funding-state", response_model=WalletFundingStateResponse, summary="Get current funding state of a wallet")
def get_wallet_funding_state(
    user_id: str,
    refresh: bool = Query(False, description="Force a live re-fetch instead of returning cached data"),
    current_user=Depends(require_engineer),
):
    actor_id = getattr(current_user, "id", None)
    try:
        result = WalletRegistry.get_funding_state(user_id, refresh=refresh)
        if not result:
            raise HTTPException(status_code=404, detail="Wallet not found")
        audit_log.log(
            WalletAuditEvents.WALLET_FUNDING_STATE_CHECKED,
            actor_id=actor_id,
            details={"user_id": user_id, "refresh": refresh},
        )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        audit_log.log(
            WalletAuditEvents.WALLET_FUNDING_STATE_CHECK_FAILED,
            actor_id=actor_id,
            details={"user_id": user_id, "error": str(exc)},
        )
        raise


@router.get("/{address}/balance", response_model=WalletBalanceResponse)
def get_wallet_balance(
    address: str,
    refresh: bool = Query(False, description="Force a live re-fetch instead of returning cached data"),
    current_user=Depends(require_engineer),
):
    actor_id = getattr(current_user, "id", None)
    try:
        balance = WalletRegistry.get_balance(address, refresh=refresh)
        if not balance:
            raise HTTPException(status_code=404, detail="Wallet not found")
        audit_log.log(
            WalletAuditEvents.WALLET_BALANCE_CHECKED,
            actor_id=actor_id,
            details={"address": address, "refresh": refresh},
        )
        return balance
    except HTTPException:
        raise
    except Exception as exc:
        audit_log.log(
            WalletAuditEvents.WALLET_BALANCE_CHECK_FAILED,
            actor_id=actor_id,
            details={"address": address, "error": str(exc)},
        )
        raise
