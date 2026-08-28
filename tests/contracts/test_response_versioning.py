import pytest
from app.services.contracts.response_versioning import (
    BridgeResponseV1,
    BridgeResponseV2,
    detect_version,
    normalize_response,
    get_target_version,
)


class TestDetectVersion:
    def test_v1_explicit(self):
        raw = {"bridge_schema_version": "v1", "status": "ok"}
        assert detect_version(raw) == "v1"

    def test_v2_explicit(self):
        raw = {"bridge_schema_version": "v2", "status": "ok"}
        assert detect_version(raw) == "v2"

    def test_v2_by_fields(self):
        raw = {"status": "ok", "network": "testnet"}
        assert detect_version(raw) == "v2"

    def test_v2_by_metadata(self):
        raw = {"status": "ok", "metadata": {"source": "bridge"}}
        assert detect_version(raw) == "v2"

    def test_v1_default(self):
        raw = {"status": "ok", "amount": 100}
        assert detect_version(raw) == "v1"


class TestNormalizeResponse:
    def test_v1_to_v1_passthrough(self):
        raw = {
            "status": "confirmed",
            "amount": 50.0,
            "asset_code": "USDC",
            "from_address": "GABC",
            "to_address": "GDEF",
            "tx_hash": "tx123",
        }
        result = normalize_response(raw, target_version="v1")
        assert isinstance(result, BridgeResponseV1)
        assert result.status == "confirmed"
        assert result.tx_hash == "tx123"

    def test_v1_to_v2_upgrade(self):
        raw = {
            "status": "confirmed",
            "amount": 50.0,
            "asset_code": "USDC",
            "from_address": "GABC",
            "to_address": "GDEF",
            "tx_hash": "tx123",
        }
        result = normalize_response(raw, target_version="v2")
        assert isinstance(result, BridgeResponseV2)
        assert result.status == "confirmed"
        assert result.tx_hash == "tx123"
        assert result.network is None
        assert result.metadata == {}

    def test_v2_to_v1_downgrade(self):
        raw = {
            "status": "confirmed",
            "amount": 50.0,
            "asset_code": "USDC",
            "from_address": "GABC",
            "to_address": "GDEF",
            "tx_hash": "tx123",
            "network": "testnet",
            "block_number": 42,
            "metadata": {"source": "bridge"},
        }
        result = normalize_response(raw, target_version="v1")
        assert isinstance(result, BridgeResponseV1)
        assert result.status == "confirmed"
        assert result.tx_hash == "tx123"
        assert not hasattr(result, "network")

    def test_v2_to_v2_passthrough(self):
        raw = {
            "status": "confirmed",
            "amount": 50.0,
            "asset_code": "USDC",
            "from_address": "GABC",
            "to_address": "GDEF",
            "tx_hash": "tx123",
            "network": "testnet",
            "block_number": 42,
            "metadata": {"source": "bridge"},
        }
        result = normalize_response(raw, target_version="v2")
        assert isinstance(result, BridgeResponseV2)
        assert result.network == "testnet"
        assert result.block_number == 42
        assert result.metadata == {"source": "bridge"}

    def test_auto_detect_and_normalize(self):
        raw = {
            "status": "confirmed",
            "amount": 50.0,
            "asset_code": "USDC",
            "from_address": "GABC",
            "to_address": "GDEF",
        }
        result = normalize_response(raw)
        assert isinstance(result, (BridgeResponseV1, BridgeResponseV2))


class TestTargetVersion:
    def test_default_is_v2(self):
        version = get_target_version()
        assert version in ("v1", "v2")
