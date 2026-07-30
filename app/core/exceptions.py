from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
from app.core.errors import ErrorCode

class ProblemDetails(BaseModel):
    type: str
    title: str
    status: int
    detail: str
    instance: str
    error_code: Optional[ErrorCode] = None

async def problem_details_handler(request: Request, exc: Exception):
    status_code = getattr(exc, 'status_code', 500)
    error_code = getattr(exc, 'error_code', ErrorCode.INTERNAL_ERROR if status_code >= 500 else ErrorCode.INVALID_REQUEST)
    problem = ProblemDetails(
        type="about:blank",
        title="An error occurred",
        status=status_code,
        detail=str(exc),
        instance=str(request.url),
        error_code=error_code
    )
    return JSONResponse(status_code=status_code, content=problem.dict())
