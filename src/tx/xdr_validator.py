from app.services.stellar.memo import MEMO_TEXT_MAX_BYTES, MemoValidationError


def validate_xdr(memo_text=None):
    """Reject oversized text memos before submission; valid input passes through."""
    if memo_text is not None and len(memo_text.encode("utf-8")) > MEMO_TEXT_MAX_BYTES:
        raise MemoValidationError(
            f"MEMO_TEXT exceeds {MEMO_TEXT_MAX_BYTES} bytes."
        )
    return True
