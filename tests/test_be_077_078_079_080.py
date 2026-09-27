"""
Tests for:
  BE-077 - Externalize asset code and settlement-address configuration
  BE-078 - Duplicate-payment prevention under concurrent resolve paths
  BE-079 - Wallet address format validation and normalization
  BE-080 - Wallet link conflict handling
"""
import pytest
from unittest.mock import MagicMock, patch
from pydantic import ValidationError

from app.core.config import Settings, validate_critical_settings
from app.models.wallet import WalletLinkRequest
from app.services.wallet_registry import WalletRegistry

# Valid 56-char Stellar public key (G + 55 base32 chars A-Z2-7)
VALID_STELLAR_KEY = "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
VALID_STELLAR_KEY_2 = "GBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


# ---------------------------------------------------------------------------
# BE-077: Externalize asset/settlement config
# ---------------------------------------------------------------------------

class TestBE077:
    def test_default_payment_config_values(self):
        s = Settings()
        assert s.PAYMENT_ASSET_CODE == "USDC"
        assert s.PAYMENT_FROM_ADDRESS == "SYSTEM_POOL"
        assert s.PAYMENT_TO_ADDRESS == "OUTAGE_SETTLEMENT"

    def test_payment_config_overridable(self):
        s = Settings(
            PAYMENT_ASSET_CODE="XLM",
            PAYMENT_FROM_ADDRESS="CUSTOM_POOL",
            PAYMENT_TO_ADDRESS="CUSTOM_SETTLEMENT",
        )
        assert s.PAYMENT_ASSET_CODE == "XLM"
        assert s.PAYMENT_FROM_ADDRESS == "CUSTOM_POOL"
        assert s.PAYMENT_TO_ADDRESS == "CUSTOM_SETTLEMENT"

    def test_empty_payment_asset_code_fails_validation(self):
        s = Settings(PAYMENT_ASSET_CODE="  ")
        with pytest.raises(ValueError, match="PAYMENT_ASSET_CODE"):
            validate_critical_settings(s)

    def test_empty_payment_from_address_fails_validation(self):
        s = Settings(PAYMENT_FROM_ADDRESS="  ")
        with pytest.raises(ValueError, match="PAYMENT_FROM_ADDRESS"):
            validate_critical_settings(s)

    def test_empty_payment_to_address_fails_validation(self):
        s = Settings(PAYMENT_TO_ADDRESS="  ")
        with pytest.raises(ValueError, match="PAYMENT_TO_ADDRESS"):
            validate_critical_settings(s)

    def test_create_for_sla_result_uses_config(self):
        from app.repositories.payment_repository import PaymentRepository
        from app.models.sla import SLAResult

        db = MagicMock()
        db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = None

        repo = PaymentRepository(db)
        sla = SLAResult(
            id=42,
            outage_id="out-1",
            status="violated",
            mttr_minutes=30,
            threshold_minutes=20,
            amount=100,
            payment_type="penalty",
            rating="poor",
        )

        captured = {}

        def fake_add(orm):
            captured["asset_code"] = orm.asset_code
            captured["from_address"] = orm.from_address
            captured["to_address"] = orm.to_address

        db.add.side_effect = fake_add
        db.refresh.side_effect = lambda orm: None

        with patch("app.repositories.payment_repository.settings") as mock_settings:
            mock_settings.PAYMENT_ASSET_CODE = "USDC_TEST"
            mock_settings.PAYMENT_FROM_ADDRESS = "POOL_TEST"
            mock_settings.PAYMENT_TO_ADDRESS = "SETTLE_TEST"
            try:
                repo.create_for_sla_result("out-1", sla)
            except Exception:
                pass  # db mock may not fully support refresh

        assert captured.get("asset_code") == "USDC_TEST"
        assert captured.get("from_address") == "POOL_TEST"
        assert captured.get("to_address") == "SETTLE_TEST"


# ---------------------------------------------------------------------------
# BE-078: Duplicate-payment prevention
# ---------------------------------------------------------------------------

class TestBE078:
    def test_unique_constraint_on_sla_result_id(self):
        from app.models.orm.payment import PaymentTransactionORM
        col = PaymentTransactionORM.__table__.c["sla_result_id"]
        assert col.unique, "sla_result_id must have a unique constraint"

    def test_get_by_sla_result_uses_for_update(self):
        from app.repositories.payment_repository import PaymentRepository

        db = MagicMock()
        query_mock = MagicMock()
        filter_mock = MagicMock()
        for_update_mock = MagicMock()
        for_update_mock.first.return_value = None

        db.query.return_value = query_mock
        query_mock.filter.return_value = filter_mock
        filter_mock.with_for_update.return_value = for_update_mock

        repo = PaymentRepository(db)
        repo.get_by_sla_result(1, for_update=True)
        filter_mock.with_for_update.assert_called_once()

    def test_get_by_sla_result_no_lock_by_default(self):
        from app.repositories.payment_repository import PaymentRepository

        db = MagicMock()
        query_mock = MagicMock()
        filter_mock = MagicMock()
        filter_mock.first.return_value = None

        db.query.return_value = query_mock
        query_mock.filter.return_value = filter_mock

        repo = PaymentRepository(db)
        repo.get_by_sla_result(1)
        filter_mock.with_for_update.assert_not_called()


# ---------------------------------------------------------------------------
# BE-079: Wallet address format validation
# ---------------------------------------------------------------------------

class TestBE079:
    def test_valid_stellar_key_accepted(self):
        req = WalletLinkRequest(user_id="u1", public_key=VALID_STELLAR_KEY)
        assert req.public_key == VALID_STELLAR_KEY

    def test_key_not_starting_with_G_rejected(self):
        bad_key = "X" + "A" * 55
        with pytest.raises(ValidationError, match="Stellar public key"):
            WalletLinkRequest(user_id="u1", public_key=bad_key)

    def test_key_wrong_length_rejected(self):
        with pytest.raises(ValidationError):
            WalletLinkRequest(user_id="u1", public_key="GSHORT")

    def test_key_with_invalid_chars_rejected(self):
        # 56 chars but contains '1' which is not in base32 alphabet (A-Z2-7)
        bad_key = "G" + "1" * 55
        with pytest.raises(ValidationError, match="Stellar public key"):
            WalletLinkRequest(user_id="u1", public_key=bad_key)

    def test_empty_key_rejected(self):
        with pytest.raises(ValidationError):
            WalletLinkRequest(user_id="u1", public_key="")


# ---------------------------------------------------------------------------
# BE-080: Wallet link conflict handling
# ---------------------------------------------------------------------------

class TestBE080:
    def setup_method(self):
        WalletRegistry._wallets_by_user.clear()
        WalletRegistry._wallets_by_address.clear()

    def test_link_new_wallet_succeeds(self):
        req = WalletLinkRequest(user_id="user1", public_key=VALID_STELLAR_KEY)
        wallet = WalletRegistry.link_wallet(req)
        assert wallet.user_id == "user1"
        assert wallet.public_key == VALID_STELLAR_KEY

    def test_relink_same_user_same_address_succeeds(self):
        req = WalletLinkRequest(user_id="user1", public_key=VALID_STELLAR_KEY)
        WalletRegistry.link_wallet(req)
        wallet = WalletRegistry.link_wallet(req)
        assert wallet.public_key == VALID_STELLAR_KEY

    def test_user_already_linked_to_different_address_raises(self):
        WalletRegistry.link_wallet(WalletLinkRequest(user_id="user1", public_key=VALID_STELLAR_KEY))
        with pytest.raises(ValueError, match="already linked to a different wallet address"):
            WalletRegistry.link_wallet(WalletLinkRequest(user_id="user1", public_key=VALID_STELLAR_KEY_2))

    def test_address_already_linked_to_different_user_raises(self):
        WalletRegistry.link_wallet(WalletLinkRequest(user_id="user1", public_key=VALID_STELLAR_KEY))
        with pytest.raises(ValueError, match="already linked to a different user"):
            WalletRegistry.link_wallet(WalletLinkRequest(user_id="user2", public_key=VALID_STELLAR_KEY))

    def test_link_wallet_endpoint_returns_409_on_conflict(self):
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        payload = {"user_id": "user1", "public_key": VALID_STELLAR_KEY}
        client.post("/api/v1/wallets/link", json=payload)

        # Link same address to different user → 409
        conflict_payload = {"user_id": "user2", "public_key": VALID_STELLAR_KEY}
        response = client.post("/api/v1/wallets/link", json=conflict_payload)
        assert response.status_code == 409
