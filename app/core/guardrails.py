from fastapi import Request, HTTPException

MAX_CONTENT_LENGTH = 10 * 1024 * 1024

async def check_payload_size(request: Request):
    content_length = request.headers.get('content-length')
    if content_length and int(content_length) > MAX_CONTENT_LENGTH:
        raise HTTPException(status_code=413, detail="Payload too large")
