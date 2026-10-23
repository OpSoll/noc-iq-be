"""Tests for the SLA result ID transaction memo helper.

Acceptance criteria covered:
  * Construct ``Memo.text`` or ``Memo.id`` containing the SLA Result ID.
  * Validate the memo format before building the transaction envelope.
  * Verify the memo string on the confirmed transaction response.
"""
from __future__ import annotations

import pytest

from app.services.stellar.memo import (
    MEMO_ID_MAX,
    MEMO_TEXT_MAX_BYTES,
    SLA_MEMO_PREFIX,
    MemoMismatchError,
    MemoValidationError,
    SLAMemo,
    build_sla_memo,
    parse_sla_result_id,
    validate_memo,
    verify_sla_result_id,
    verify_transaction_memo,
)


# --------------------------------------------------------------------------- #
# Construction                                                                 #
# --------------------------------------------------------------------------- #

def test_numeric_sla_result_id_builds_an_id_memo():
    memo = build_sla_memo(4271)

    assert memo.memo_type == "id"
    assert memo.value == 4271
    assert memo.sla_result_id == "4271"
    assert memo.text == "4271"


def test_non_numeric_sla_result_id_builds_a_text_memo():
    memo = build_sla_memo("sla-9f2c-4d")

    assert memo.memo_type == "text"
    assert memo.value == f"{SLA_MEMO_PREFIX}sla-9f2c-4d"
    assert memo.sla_result_id == "sla-9f2c-4d"


def test_text_memo_can_be_forced_for_a_numeric_id():
    memo = build_sla_memo(42, memo_type="text")

    assert memo.memo_type == "text"
    assert memo.value == "SLA:42"


def test_sla_result_id_is_stripped():
    assert build_sla_memo("  77  ").sla_result_id == "77"


def test_empty_sla_result_id_is_rejected():
    for empty in ("", "   ", None):
        with pytest.raises(MemoValidationError):
            build_sla_memo(empty)


def test_non_numeric_id_cannot_be_forced_into_an_id_memo():
    with pytest.raises(MemoValidationError) as exc:
        build_sla_memo("sla-9f2c", memo_type="id")

    assert exc.value.ERROR_CODE == "INVALID_MEMO"


def test_unknown_memo_type_is_rejected():
    with pytest.raises(MemoValidationError):
        build_sla_memo("42", memo_type="hash")


# --------------------------------------------------------------------------- #
# Validation before envelope build                                             #
# --------------------------------------------------------------------------- #

def test_text_memo_at_the_byte_limit_is_accepted():
    sla_id = "0" * (MEMO_TEXT_MAX_BYTES - len(SLA_MEMO_PREFIX))
    memo = build_sla_memo(sla_id, memo_type="text")

    assert len(memo.text.encode("utf-8")) == MEMO_TEXT_MAX_BYTES


def test_text_memo_over_the_byte_limit_is_rejected():
    sla_id = "0" * (MEMO_TEXT_MAX_BYTES - len(SLA_MEMO_PREFIX) + 1)

    with pytest.raises(MemoValidationError) as exc:
        build_sla_memo(sla_id, memo_type="text")

    assert "28" in str(exc.value)


def test_multibyte_text_memo_is_measured_in_bytes():
    # 13 three-byte characters + the 4-byte prefix = 43 bytes.
    with pytest.raises(MemoValidationError):
        validate_memo(SLAMemo(memo_type="text", value="SLA:" + "★" * 13, sla_result_id="x"))


def test_id_memo_beyond_uint64_is_rejected():
    with pytest.raises(MemoValidationError):
        validate_memo(
            SLAMemo(memo_type="id", value=MEMO_ID_MAX + 1, sla_result_id="1")
        )


def test_id_memo_at_uint64_max_is_accepted():
    memo = SLAMemo(memo_type="id", value=MEMO_ID_MAX, sla_result_id=str(MEMO_ID_MAX))

    assert validate_memo(memo) is memo


def test_text_memo_with_illegal_characters_is_rejected():
    with pytest.raises(MemoValidationError):
        validate_memo(SLAMemo(memo_type="text", value="SLA:a b", sla_result_id="a b"))


def test_text_memo_without_the_sla_prefix_is_rejected():
    with pytest.raises(MemoValidationError):
        validate_memo(SLAMemo(memo_type="text", value="42", sla_result_id="42"))


def test_validate_memo_rejects_unknown_type():
    with pytest.raises(MemoValidationError):
        validate_memo(SLAMemo(memo_type="hash", value="x", sla_result_id="1"))


# --------------------------------------------------------------------------- #
# Verification on the confirmed transaction                                    #
# --------------------------------------------------------------------------- #

def test_confirmed_id_memo_is_verified():
    memo = build_sla_memo(4271)
    confirmed = {"hash": "a" * 64, "memo_type": "id", "memo": "4271", "successful": True}

    assert verify_transaction_memo(confirmed, memo) is memo


def test_confirmed_text_memo_is_verified():
    memo = build_sla_memo("sla-9f2c-4d")
    confirmed = {"memo_type": "text", "memo": "SLA:sla-9f2c-4d"}

    assert verify_transaction_memo(confirmed, memo) is memo


def test_missing_memo_on_confirmed_transaction_raises():
    memo = build_sla_memo(4271)

    with pytest.raises(MemoMismatchError) as exc:
        verify_transaction_memo({"hash": "a" * 64}, memo)

    assert exc.value.actual is None


def test_wrong_sla_result_id_raises():
    memo = build_sla_memo(4271)

    with pytest.raises(MemoMismatchError) as exc:
        verify_transaction_memo({"memo_type": "id", "memo": "4272"}, memo)

    assert exc.value.expected == "4271"
    assert exc.value.actual == "4272"


def test_wrong_memo_type_raises():
    memo = build_sla_memo(4271)

    with pytest.raises(MemoMismatchError):
        verify_transaction_memo({"memo_type": "text", "memo": "SLA:4271"}, memo)


def test_verification_accepts_an_object_response():
    class Confirmed:
        memo_type = "text"
        memo = "SLA:sla-1"

    memo = build_sla_memo("sla-1")

    assert verify_transaction_memo(Confirmed(), memo) is memo


# --------------------------------------------------------------------------- #
# Parsing helpers                                                              #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "memo_value,memo_type,expected",
    [
        ("SLA:sla-9f2c", "text", "sla-9f2c"),
        ("4271", "id", "4271"),
        ("4271", None, "4271"),
        ("unrelated memo", "text", None),
        ("", "text", None),
        (None, None, None),
    ],
)
def test_parse_sla_result_id(memo_value, memo_type, expected):
    assert parse_sla_result_id(memo_value, memo_type) == expected


def test_verify_sla_result_id_does_not_raise():
    assert verify_sla_result_id({"memo_type": "id", "memo": "4271"}, 4271) is True
    assert verify_sla_result_id({"memo_type": "id", "memo": "4272"}, 4271) is False
    assert verify_sla_result_id({}, 4271) is False
