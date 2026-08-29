"""Dead-letter queue service for failed Stellar payment transactions (issue #561).

Provides:
- ``route_to_dlq()``  — persist a failed payment to the DLQ with full taxonomy.
- ``resubmit_dlq_entry()``  — mark an entry for re-submission and return it.
- ``list_dlq()``  — paginated admin listing.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.orm.payment_dlq import PaymentDeadLetterORM
from app.models.orm.payment import PaymentTransactionORM
from app.models.payment import PaymentStatus
from app.services.stellar_errors import stellar_error_taxonomy

logger = logging.getLogger(__name__)
UTC = timezone.utc


def route_to_dlq(
    db: Session,
    payment: PaymentTransactionORM,
    error_response: Optional[Dict[str, Any]] = None,
    error_code: Optional[str] = None,
    error_description: Optional[str] = None,
) -> PaymentDeadLetterORM:
    """Persist *payment* to the dead-letter queue with taxonomy details.

    Derives ``error_code``, ``error_description``, and ``retry_class`` from the
    Horizon error envelope when provided, falling back to the supplied overrides.
    """
    tags = stellar_error_taxonomy.parse_horizon_error(error_response or {})
    primary_tag = tags[0] if tags else None
    retry_cls = stellar_error_taxonomy.primary_retry_class(tags)

    resolved_code = error_code or (primary_tag.code if primary_tag else "unknown")
    resolved_desc = error_description or (primary_tag.description if primary_tag else "No description available.")
    raw_resp = json.dumps(error_response) if error_response else None

    entry = PaymentDeadLetterORM(
        id=str(uuid.uuid4()),
        original_payment_id=payment.id,
        transaction_hash=payment.transaction_hash,
        from_address=payment.from_address,
        to_address=payment.to_address,
        amount=payment.amount,
        asset_code=payment.asset_code,
        error_code=resolved_code,
        error_description=resolved_desc,
        retry_class=retry_cls.value,
        retry_count=payment.retry_count,
        resubmitted=False,
        created_at=datetime.now(UTC),
        outage_id=payment.outage_id,
        sla_result_id=payment.sla_result_id,
        raw_horizon_response=raw_resp,
    )

    db.add(entry)

    # Mark the original payment as dead_letter
    payment.status = PaymentStatus.dead_letter.value
    payment.dead_letter_reason = resolved_desc
    payment.dead_lettered_at = datetime.now(UTC)
    payment.failure_taxonomy = resolved_code

    db.commit()
    db.refresh(entry)
    logger.info(
        "Payment %s routed to DLQ (code=%s, retry_class=%s)",
        payment.id,
        resolved_code,
        retry_cls.value,
    )
    return entry


def resubmit_dlq_entry(db: Session, dlq_id: str) -> Optional[PaymentDeadLetterORM]:
    """Mark a DLQ entry for re-submission.

    Returns the entry (caller is responsible for actually submitting),
    or ``None`` if not found.
    """
    entry = db.query(PaymentDeadLetterORM).filter(PaymentDeadLetterORM.id == dlq_id).first()
    if not entry:
        return None
    entry.resubmitted = True
    entry.resubmitted_at = datetime.now(UTC)
    db.commit()
    db.refresh(entry)
    logger.info("DLQ entry %s marked for re-submission.", dlq_id)
    return entry


def list_dlq(
    db: Session,
    *,
    limit: int = 50,
    offset: int = 0,
    resubmitted: Optional[bool] = None,
) -> List[PaymentDeadLetterORM]:
    """Paginated admin listing of DLQ entries."""
    query = db.query(PaymentDeadLetterORM)
    if resubmitted is not None:
        query = query.filter(PaymentDeadLetterORM.resubmitted == resubmitted)
    return query.order_by(PaymentDeadLetterORM.created_at.desc()).offset(offset).limit(limit).all()
