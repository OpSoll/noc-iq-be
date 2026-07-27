from fastapi import Request
from fastapi.responses import JSONResponse

async def problem_details_handler(request: Request, exc: Exception):
    status_code = getattr(exc, 'status_code', 500)
    return JSONResponse(status_code=status_code, content={"type": "error", "detail": str(exc)})
