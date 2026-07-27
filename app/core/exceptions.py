from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

class ProblemDetails(BaseModel):
    type: str
    title: str
    status: int
    detail: str
    instance: str

async def problem_details_handler(request: Request, exc: Exception):
    status_code = getattr(exc, 'status_code', 500)
    problem = ProblemDetails(
        type="about:blank",
        title="An error occurred",
        status=status_code,
        detail=str(exc),
        instance=str(request.url)
    )
    return JSONResponse(status_code=status_code, content=problem.dict())
