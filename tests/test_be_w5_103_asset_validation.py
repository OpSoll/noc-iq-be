"""Tests for Stellar asset-code and issuer validation in payout pipeline
(BE-W5-103 / issue #364).

Covers:
- validate_asset_config rejects invalid codes and issuers
- SLAAdapter.validate_payout_asset uses configured values by default
- validate_critical_settings rejects missing/malformed issuer in soroban_rpc mode
- AssetValidationError carries the correct error code
"""
import pytest
from unittest.mock import MagicMock

from app.services.contracts.translation import AssetValidationError, validate_asset_config


VALID_CODE = "USDC"
VALID_ISSUER = "G" + "A" * 55  # 56-char G-address


# ---------------------------------------------------------------------------
# validate_asset_config
# ---------------------------------------------------------------------------

class TestValidateAssetConfig:
    def test_valid_pair_passes(self):
        validate_asset_config(VALID_CODE, VALID_ISSUER)  # no exception

    def test_empty_code_raises_with_error_code(self):
        with pytest.raises(AssetValidationError) as exc_info:
            validate_asset_config("", VALID_ISSUER)
        assert exc_info.value.ERROR_CODE == "INVALID_ASSET_CONFIG"
        assert "asset_code" in str(exc_info.value)

    def test_whitespace_only_code_raises(self):
        with pytest.raises(AssetValidationError):
            validate_asset_config("   ", VALID_ISSUER)

    def test_non_alphanumeric_code_raises(self):
        with pytest.raises(AssetValidationError):
            validate_asset_config("USD-C", VALID_ISSUER)

    def test_code_too_long_raises(self):
        with pytest.raises(AssetValidationError):
            validate_asset_config("A" * 13, VALID_ISSUER)

    def test_max_length_code_passes(self):
        validate_asset_config("A" * 12, VALID_ISSUER)

    def test_empty_issuer_raises(self):
        with pytest.raises(AssetValidationError) as exc_info:
            validate_asset_config(VALID_CODE, "")
        assert "asset_issuer" in str(exc_info.value)

    def test_issuer_wrong_prefix_raises(self):
        with pytest.raises(AssetValidationError):
            validate_asset_config(VALID_CODE, "S" + "A" * 55)

    def test_issuer_too_short_raises(self):
        with pytest.raises(AssetValidationError):
            validate_asset_config(VALID_CODE, "G" + "A" * 30)

    def test_issuer_too_long_raises(self):
        with pytest.raises(AssetValidationError):
            validate_asset_config(VALID_CODE, "G" + "A" * 56)  # 57 chars total

    def test_xlm_native_asset_code_passes(self):
        """XLM (native asset) uses a short code."""
        validate_asset_config("XLM", VALID_ISSUER)

    def test_single_char_code_passes(self):
        validate_asset_config("X", VALID_ISSUER)

    def test_numeric_code_passes(self):
        validate_asset_config("USDC12", VALID_ISSUER)


# ---------------------------------------------------------------------------
# AssetValidationError
# ---------------------------------------------------------------------------

class TestAssetValidationError:
    def test_error_code_attribute(self):
        err = AssetValidationError("test detail")
        assert err.ERROR_CODE == "INVALID_ASSET_CONFIG"

    def test_error_message_includes_code(self):
        err = AssetValidationError("something went wrong")
        assert "INVALID_ASSET_CONFIG" in str(err)
        assert "something went wrong" in str(err)

    def test_is_value_error_subclass(self):
        assert issubclass(AssetValidationError, ValueError)


# ---------------------------------------------------------------------------
# SLAAdapter.validate_payout_asset
# ---------------------------------------------------------------------------

class TestSLAAdapterValidatePayoutAsset:
    def _settings(self, code=VALID_CODE, issuer=VALID_ISSUER):
        s = MagicMock()
        s.PAYMENT_ASSET_CODE = code
        s.PAYMENT_ASSET_ISSUER = issuer
        s.horizon_url = "https://horizon-testnet.stellar.org"
        return s

    def test_valid_config_does_not_raise(self):
        from app.services.contracts.sla_adapter import SLAAdapter
        SLAAdapter(settings=self._settings()).validate_payout_asset()

    def test_missing_issuer_in_config_raises(self):
        from app.services.contracts.sla_adapter import SLAAdapter
        with pytest.raises(AssetValidationError):
            SLAAdapter(settings=self._settings(issuer="")).validate_payout_asset()

    def test_invalid_code_in_config_raises(self):
        from app.services.contracts.sla_adapter import SLAAdapter
        with pytest.raises(AssetValidationError):
            SLAAdapter(settings=self._settings(code="")).validate_payout_asset()

    def test_explicit_arguments_override_config(self):
        from app.services.contracts.sla_adapter import SLAAdapter
        adapter = SLAAdapter(settings=self._settings())
        # Valid explicit pair should pass
        adapter.validate_payout_asset(asset_code=VALID_CODE, asset_issuer=VALID_ISSUER)

    def test_explicit_bad_issuer_raises_even_if_config_valid(self):
        from app.services.contracts.sla_adapter import SLAAdapter
        adapter = SLAAdapter(settings=self._settings())
        with pytest.raises(AssetValidationError):
            adapter.validate_payout_asset(asset_code=VALID_CODE, asset_issuer="BADISSUER")


# ---------------------------------------------------------------------------
# validate_critical_settings – soroban_rpc mode requires valid issuer
# ---------------------------------------------------------------------------

class TestConfigValidationWithIssuer:
    def _base_config(self, **overrides):
        from app.core.config import Settings
        defaults = {
            "DATABASE_URL": "postgresql://user:pass@localhost/db",
            "ALLOWED_ORIGINS": ["http://localhost:3000"],
            "STELLAR_NETWORK": "testnet",
            "CONTRACT_EXECUTION_MODE": "soroban_rpc",
            "PAYMENT_ASSET_CODE": "USDC",
            "PAYMENT_FROM_ADDRESS": "SYSTEM_POOL",
            "PAYMENT_TO_ADDRESS": "OUTAGE_SETTLEMENT",
            "PAYMENT_ASSET_ISSUER": VALID_ISSUER,
            "CELERY_TASK_ALWAYS_EAGER": True,
        }
        defaults.update(overrides)
        return Settings(**defaults)

    def test_valid_issuer_in_soroban_mode_passes(self):
        from app.core.config import validate_critical_settings
        validate_critical_settings(self._base_config())  # no exception

    def test_missing_issuer_in_soroban_mode_raises(self):
        from app.core.config import validate_critical_settings
        cfg = self._base_config(PAYMENT_ASSET_ISSUER="")
        with pytest.raises(ValueError, match="PAYMENT_ASSET_ISSUER"):
            validate_critical_settings(cfg)

    def test_malformed_issuer_in_soroban_mode_raises(self):
        from app.core.config import validate_critical_settings
        cfg = self._base_config(PAYMENT_ASSET_ISSUER="not-a-stellar-address")
        with pytest.raises(ValueError, match="PAYMENT_ASSET_ISSUER"):
            validate_critical_settings(cfg)

    def test_issuer_not_required_in_local_adapter_mode(self):
        from app.core.config import validate_critical_settings
        cfg = self._base_config(
            CONTRACT_EXECUTION_MODE="local_adapter",
            PAYMENT_ASSET_ISSUER="",
        )
        # Should pass — local_adapter doesn't require issuer
        validate_critical_settings(cfg)
