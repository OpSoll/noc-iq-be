from app.schemas.sla_dispute import paginate_evidence


def test_paginate_evidence_bounds_and_has_more():
    items = [{"url": str(i)} for i in range(5)]
    page = paginate_evidence(items, limit=2, offset=1)
    assert page["items"] == items[1:3]
    assert page["total"] == 5 and page["has_more"] is True
    assert paginate_evidence(items, limit=1000, max_limit=3)["limit"] == 3
    assert paginate_evidence([])["items"] == [] and not paginate_evidence([])["has_more"]
