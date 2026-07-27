from app.models.sla import SLAResult


# BE-364: Stellar asset code constraints
_MAX_ASSET_CODE_LEN = 12  # Stellar allows 1-12 alphanumeric chars
_STELLAR_ADDRESS_LEN = 56


class AssetValidationError(ValueError):
    """Raised when payout asset metadata fails validation (BE-364)."""

    ERROR_CODE = "INVALID_ASSET_CONFIG"

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"[{self.ERROR_CODE}] {detail}")


def validate_asset_config(asset_code: str, asset_issuer: str) -> None:
    """Validate Stellar asset code / issuer pair before payout submission.

    Raises:
        AssetValidationError: with ERROR_CODE="INVALID_ASSET_CONFIG" if either
            the asset code or the issuer address is malformed.
    """
    code = (asset_code or "").strip()
    issuer = (asset_issuer or "").strip()

    if not code:
        raise AssetValidationError("asset_code must not be empty.")
    if not code.isalnum():
        raise AssetValidationError(
            f"asset_code '{code}' must be alphanumeric (Stellar asset code rules)."
        )
    if len(code) > _MAX_ASSET_CODE_LEN:
        raise AssetValidationError(
            f"asset_code '{code}' exceeds maximum length of {_MAX_ASSET_CODE_LEN}."
        )

    if not issuer:
        raise AssetValidationError("asset_issuer must not be empty.")
    if not issuer.startswith("G") or len(issuer) != _STELLAR_ADDRESS_LEN:
        raise AssetValidationError(
            f"asset_issuer '{issuer}' must be a valid 56-character Stellar G-address."
        )


def translate_contract_result(raw_result: dict) -> SLAResult:
    return SLAResult(
        outage_id=raw_result["outage_id"],
        status="violated" if raw_result["status"] == "viol" else "met",
        mttr_minutes=raw_result["mttr_minutes"],
        threshold_minutes=raw_result["threshold_minutes"],
        amount=raw_result["amount"],
        payment_type="penalty" if raw_result["payment_type"] == "pen" else "reward",
        rating={
            "top": "exceptional",
            "high": "excellent",
            "good": "good",
            "poor": "poor",
        }[raw_result["rating"]],
    )
