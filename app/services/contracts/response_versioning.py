from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field

from app.core.config import settings

SchemaVersion = Literal["v1", "v2"]


class BridgeResponseV1(BaseModel):
    status: str
    amount: float
    asset_code: str
    from_address: str
    to_address: str
    tx_hash: Optional[str] = None
    error: Optional[str] = None


class BridgeResponseV2(BaseModel):
    status: str
    amount: float
    asset_code: str
    from_address: str
    to_address: str
    tx_hash: Optional[str] = None
    error: Optional[str] = None
    network: Optional[str] = None
    block_number: Optional[int] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


BridgeResponse = Union[BridgeResponseV1, BridgeResponseV2]

_TARGET_VERSION: SchemaVersion = settings.BRIDGE_RESPONSE_VERSION


def get_target_version() -> SchemaVersion:
    return _TARGET_VERSION


def detect_version(raw: dict[str, Any]) -> SchemaVersion:
    if "bridge_schema_version" in raw:
        version = raw["bridge_schema_version"]
        if version in ("v1", "v2"):
            return version
    if "network" in raw or "block_number" in raw or "metadata" in raw:
        return "v2"
    return "v1"


def normalize_response(
    raw: dict[str, Any],
    target_version: Optional[SchemaVersion] = None,
) -> Union[BridgeResponseV1, BridgeResponseV2]:
    source_version = detect_version(raw)
    target = target_version or _TARGET_VERSION

    if source_version == target:
        if target == "v2":
            return BridgeResponseV2.model_validate(raw)
        return BridgeResponseV1.model_validate(raw)

    if source_version == "v1" and target == "v2":
        return BridgeResponseV2(
            status=raw.get("status", ""),
            amount=raw.get("amount", 0),
            asset_code=raw.get("asset_code", ""),
            from_address=raw.get("from_address", ""),
            to_address=raw.get("to_address", ""),
            tx_hash=raw.get("tx_hash"),
            error=raw.get("error"),
            network=raw.get("network"),
            block_number=raw.get("block_number"),
            metadata=raw.get("metadata", {}),
        )

    if source_version == "v2" and target == "v1":
        return BridgeResponseV1(
            status=raw.get("status", ""),
            amount=raw.get("amount", 0),
            asset_code=raw.get("asset_code", ""),
            from_address=raw.get("from_address", ""),
            to_address=raw.get("to_address", ""),
            tx_hash=raw.get("tx_hash"),
            error=raw.get("error"),
        )

    if target == "v2":
        return BridgeResponseV2.model_validate(raw)
    return BridgeResponseV1.model_validate(raw)
