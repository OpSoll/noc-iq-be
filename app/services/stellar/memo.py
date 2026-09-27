"""Transaction memo helper for embedding SLA result IDs (auditing).

Every Stellar settlement transaction must carry the SLA result it pays out
for, so an auditor can walk from a ledger entry back to the SLA record that
justified it. This module builds that memo, validates it *before* the
transaction envelope is built (an over-long memo is rejected by the network
at submission time, after fees and sequence numbers are already committed),
and verifies the memo that comes back on the confirmed transaction.

Stellar memo rules enforced here:
  * ``MEMO_TEXT`` — at most 28 bytes of UTF-8.
  * ``MEMO_ID``   — an unsigned 64-bit integer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Optional, Union

MEMO_TEXT_MAX_BYTES = 28
MEMO_ID_MAX = 2**64 - 1

# Prefix that marks a memo as an NOCIQ SLA settlement reference.
SLA_MEMO_PREFIX = "SLA:"

MemoType = Literal["text", "id"]

_TEXT_MEMO_RE = re.compile(rf"^{re.escape(SLA_MEMO_PREFIX)}(?P<sla_id>[A-Za-z0-9_.:-]+)$")
_NUMERIC_RE = re.compile(r"^\d+$")


class MemoValidationError(ValueError):
    """Raised when a memo cannot be constructed or fails validation."""

    ERROR_CODE = "INVALID_MEMO"

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"[{self.ERROR_CODE}] {detail}")


class MemoMismatchError(ValueError):
    """Raised when a confirmed transaction does not carry the expected memo."""

    ERROR_CODE = "MEMO_MISMATCH"

    def __init__(self, expected: str, actual: Optional[str], detail: str) -> None:
        self.expected = expected
        self.actual = actual
        self.detail = detail
        super().__init__(
            f"[{self.ERROR_CODE}] {detail} (expected={expected!r}, actual={actual!r})"
        )


@dataclass(frozen=True)
class SLAMemo:
    """A validated memo carrying an SLA result ID.

    ``value`` is the on-chain memo content: the ``SLA:<id>`` string for a
    text memo, or the integer for an ID memo.
    """

    memo_type: MemoType
    value: Union[str, int]
    sla_result_id: str

    @property
    def text(self) -> str:
        """String form of the memo as Horizon reports it."""
        return str(self.value)

    def as_dict(self) -> dict[str, Any]:
        return {
            "memo_type": self.memo_type,
            "memo": self.text,
            "sla_result_id": self.sla_result_id,
        }

    def to_stellar_memo(self):
        """Return the equivalent ``stellar_sdk`` memo object.

        Imported lazily so the rest of this module — construction and
        validation — works without the SDK installed.
        """
        from stellar_sdk import Memo, IdMemo, TextMemo  # noqa: F401

        if self.memo_type == "id":
            return IdMemo(int(self.value))
        return TextMemo(str(self.value))


def build_sla_memo(
    sla_result_id: Union[str, int],
    memo_type: Union[MemoType, Literal["auto"]] = "auto",
) -> SLAMemo:
    """Build the memo embedding *sla_result_id*.

    ``memo_type="auto"`` (the default) picks ``id`` for a numeric SLA result
    ID — exact, and free of any length limit — and ``text`` otherwise, which
    covers UUID-style identifiers.

    Raises:
        MemoValidationError: if the ID is empty, or the resulting memo would
            violate Stellar's memo constraints.
    """
    raw = "" if sla_result_id is None else str(sla_result_id).strip()
    if not raw:
        raise MemoValidationError("sla_result_id must not be empty.")

    resolved = memo_type
    if memo_type == "auto":
        resolved = "id" if _NUMERIC_RE.match(raw) else "text"

    if resolved == "id":
        if not _NUMERIC_RE.match(raw):
            raise MemoValidationError(
                f"sla_result_id {raw!r} is not a non-negative integer and "
                f"cannot be encoded as a MEMO_ID."
            )
        memo = SLAMemo(memo_type="id", value=int(raw), sla_result_id=raw)
    elif resolved == "text":
        memo = SLAMemo(
            memo_type="text", value=f"{SLA_MEMO_PREFIX}{raw}", sla_result_id=raw
        )
    else:
        raise MemoValidationError(
            f"Unsupported memo_type {memo_type!r}; expected 'text', 'id' or 'auto'."
        )

    validate_memo(memo)
    return memo


def validate_memo(memo: SLAMemo) -> SLAMemo:
    """Validate *memo* against Stellar's format rules before envelope build.

    Call this on the transaction-building path: a memo rejected here costs
    nothing, whereas one rejected by Horizon burns a sequence number.

    Raises:
        MemoValidationError: if the memo is malformed or out of range.
    """
    if not memo.sla_result_id:
        raise MemoValidationError("Memo must reference a non-empty SLA result ID.")

    if memo.memo_type == "id":
        if not isinstance(memo.value, int) or isinstance(memo.value, bool):
            raise MemoValidationError("MEMO_ID value must be an integer.")
        if not 0 <= memo.value <= MEMO_ID_MAX:
            raise MemoValidationError(
                f"MEMO_ID value {memo.value} is outside the unsigned 64-bit range."
            )
        return memo

    if memo.memo_type == "text":
        if not isinstance(memo.value, str):
            raise MemoValidationError("MEMO_TEXT value must be a string.")
        encoded = memo.value.encode("utf-8")
        if not encoded:
            raise MemoValidationError("MEMO_TEXT value must not be empty.")
        if len(encoded) > MEMO_TEXT_MAX_BYTES:
            raise MemoValidationError(
                f"MEMO_TEXT value {memo.value!r} is {len(encoded)} bytes; "
                f"Stellar allows at most {MEMO_TEXT_MAX_BYTES}."
            )
        if not _TEXT_MEMO_RE.match(memo.value):
            raise MemoValidationError(
                f"MEMO_TEXT value {memo.value!r} must match "
                f"'{SLA_MEMO_PREFIX}<sla_result_id>' using [A-Za-z0-9_.:-] characters."
            )
        return memo

    raise MemoValidationError(
        f"Unsupported memo_type {memo.memo_type!r}; expected 'text' or 'id'."
    )


def parse_sla_result_id(memo_value: Optional[str], memo_type: Optional[str] = None) -> Optional[str]:
    """Extract the SLA result ID from an on-chain memo value.

    Returns None when the memo is absent or is not an NOCIQ SLA memo.
    """
    if memo_value is None:
        return None
    raw = str(memo_value).strip()
    if not raw:
        return None
    if memo_type == "id" or (memo_type is None and _NUMERIC_RE.match(raw)):
        return raw if _NUMERIC_RE.match(raw) else None
    match = _TEXT_MEMO_RE.match(raw)
    return match.group("sla_id") if match else None


def extract_memo(transaction: Any) -> tuple[Optional[str], Optional[str]]:
    """Return ``(memo_type, memo_value)`` from a Horizon transaction record.

    Accepts the dict Horizon returns as well as any object exposing ``memo``
    and ``memo_type`` attributes (e.g. an SDK response wrapper).
    """
    if isinstance(transaction, dict):
        return transaction.get("memo_type"), transaction.get("memo")
    return getattr(transaction, "memo_type", None), getattr(transaction, "memo", None)


def verify_transaction_memo(transaction: Any, expected: SLAMemo) -> SLAMemo:
    """Verify the memo on a confirmed transaction matches *expected*.

    Horizon reports both text and id memos as strings, so the comparison is
    done on the string form after checking the memo type.

    Raises:
        MemoMismatchError: when the memo is missing, of the wrong type, or
            references a different SLA result.
    """
    memo_type, memo_value = extract_memo(transaction)

    if memo_value is None or str(memo_value).strip() == "":
        raise MemoMismatchError(expected.text, None, "Confirmed transaction has no memo")

    if memo_type is not None and memo_type != expected.memo_type:
        raise MemoMismatchError(
            expected.text,
            str(memo_value),
            f"Confirmed transaction memo_type is {memo_type!r}, "
            f"expected {expected.memo_type!r}",
        )

    if str(memo_value).strip() != expected.text:
        raise MemoMismatchError(
            expected.text, str(memo_value), "Confirmed transaction memo does not match"
        )

    return expected


def verify_sla_result_id(transaction: Any, sla_result_id: Union[str, int]) -> bool:
    """Return True when *transaction*'s memo references *sla_result_id*.

    A non-raising counterpart to :func:`verify_transaction_memo` for
    reconciliation sweeps over many transactions.
    """
    memo_type, memo_value = extract_memo(transaction)
    parsed = parse_sla_result_id(memo_value, memo_type)
    return parsed is not None and parsed == str(sla_result_id).strip()
