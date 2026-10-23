"""Stellar Horizon RPC response parser and error taxonomy mapper (issue #562).

Maps raw Horizon result codes (e.g. ``op_underfunded``, ``tx_bad_auth``) to
human-readable descriptions and retry classifications so that callers never
need to inspect raw XDR error strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.models.payment import RetryClass

# ---------------------------------------------------------------------------
# Known Horizon result code → (human description, retry class)
# ---------------------------------------------------------------------------

_TX_CODES: Dict[str, tuple[str, RetryClass]] = {
    "tx_success":              ("Transaction succeeded.",                        RetryClass.unknown),
    "tx_failed":               ("One or more operations failed.",                RetryClass.semantic),
    "tx_too_early":            ("Ledger close time before min_time.",            RetryClass.network),
    "tx_too_late":             ("Ledger close time after max_time.",             RetryClass.network),
    "tx_missing_operation":    ("No operations in transaction.",                 RetryClass.semantic),
    "tx_bad_seq":              ("Sequence number mismatch.",                     RetryClass.network),
    "tx_bad_auth":             ("Insufficient valid signatures.",                RetryClass.semantic),
    "tx_insufficient_balance": ("Insufficient XLM balance to cover fees.",      RetryClass.semantic),
    "tx_no_source_account":    ("Source account does not exist.",               RetryClass.semantic),
    "tx_insufficient_fee":     ("Fee too low; network rejected transaction.",   RetryClass.rate_limit),
    "tx_bad_auth_extra":       ("Extraneous signatures present.",               RetryClass.semantic),
    "tx_internal_error":       ("Horizon internal error.",                       RetryClass.network),
}

_OP_CODES: Dict[str, tuple[str, RetryClass]] = {
    "op_success":              ("Operation succeeded.",                          RetryClass.unknown),
    "op_malformed":            ("Operation is malformed.",                       RetryClass.semantic),
    "op_underfunded":          ("Source account has insufficient funds.",       RetryClass.semantic),
    "op_src_no_trust":         ("Source account missing trustline.",            RetryClass.semantic),
    "op_src_not_authorized":   ("Source account not authorised for asset.",     RetryClass.semantic),
    "op_no_destination":       ("Destination account does not exist.",          RetryClass.semantic),
    "op_no_trust":             ("Destination account missing trustline.",       RetryClass.semantic),
    "op_not_authorized":       ("Destination not authorised for asset.",        RetryClass.semantic),
    "op_line_full":            ("Destination trustline is full.",               RetryClass.semantic),
    "op_no_issuer":            ("Asset issuer does not exist.",                 RetryClass.semantic),
    "op_too_many_subentries":  ("Too many sub-entries on source account.",      RetryClass.semantic),
    "op_exceeded_work_limit":  ("Too much work performed.",                      RetryClass.rate_limit),
    "op_bad_auth":             ("Insufficient signatures for operation.",       RetryClass.semantic),
    "op_not_supported":        ("Operation type not supported on this network.", RetryClass.semantic),
}

_UNKNOWN_DESCRIPTION = "Unknown Horizon result code."


@dataclass
class TaxonomyTag:
    """Structured classification of a Horizon result code."""

    code: str
    description: str
    retry_class: RetryClass
    is_known: bool

    def as_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "description": self.description,
            "retry_class": self.retry_class.value,
            "is_known": self.is_known,
        }


class StellarErrorTaxonomy:
    """Parse Horizon error responses and return structured taxonomy tags.

    Usage::

        taxonomy = StellarErrorTaxonomy()
        tag = taxonomy.parse_code("op_underfunded")
        # tag.description  -> "Source account has insufficient funds."
        # tag.retry_class  -> RetryClass.semantic
    """

    def parse_code(self, code: str) -> TaxonomyTag:
        """Return a :class:`TaxonomyTag` for a single result code string."""
        code = code.strip().lower()
        if code in _TX_CODES:
            desc, retry_class = _TX_CODES[code]
            return TaxonomyTag(code=code, description=desc, retry_class=retry_class, is_known=True)
        if code in _OP_CODES:
            desc, retry_class = _OP_CODES[code]
            return TaxonomyTag(code=code, description=desc, retry_class=retry_class, is_known=True)
        return TaxonomyTag(
            code=code,
            description=_UNKNOWN_DESCRIPTION,
            retry_class=RetryClass.unknown,
            is_known=False,
        )

    def parse_horizon_error(self, error_response: Dict[str, Any]) -> List[TaxonomyTag]:
        """Parse a Horizon error envelope and return all result code tags.

        Handles both transaction-level ``extras.result_codes.transaction``
        and operation-level ``extras.result_codes.operations`` fields.
        """
        tags: List[TaxonomyTag] = []
        extras = error_response.get("extras") or {}
        result_codes = extras.get("result_codes") or {}

        tx_code: Optional[str] = result_codes.get("transaction")
        if tx_code:
            tags.append(self.parse_code(tx_code))

        for op_code in result_codes.get("operations") or []:
            if op_code:
                tags.append(self.parse_code(op_code))

        return tags

    def primary_retry_class(self, tags: List[TaxonomyTag]) -> RetryClass:
        """Return the most severe retry classification from a list of tags."""
        order = [RetryClass.semantic, RetryClass.rate_limit, RetryClass.network, RetryClass.unknown]
        for cls in order:
            if any(t.retry_class == cls for t in tags):
                return cls
        return RetryClass.unknown


# Module-level singleton.
stellar_error_taxonomy = StellarErrorTaxonomy()
