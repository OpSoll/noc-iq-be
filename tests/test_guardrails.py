import asyncio
from app.core.guardrails import check_payload_size, MAX_CONTENT_LENGTH

def test_constants():
    assert MAX_CONTENT_LENGTH == 10485760
