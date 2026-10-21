from fastapi import Request, HTTPException, status

MAX_QUERY_PARAM_LENGTH = 255
MAX_TOTAL_QUERY_STRING_LENGTH = 1024

async def verify_query_param_length_middleware(request: Request, call_next):
    """
    Middleware that guards against overly long query strings and individual query parameters.
    Rejects with HTTP 422 if bounds are exceeded.
    """
    query_string = request.url.query
    if len(query_string) > MAX_TOTAL_QUERY_STRING_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Query string exceeds maximum allowed length of {MAX_TOTAL_QUERY_STRING_LENGTH} characters.",
        )

    for param, values in request.query_params.multi_items():
        for value in values:
            if len(value) > MAX_QUERY_PARAM_LENGTH:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Query parameter '{param}' exceeds maximum length of {MAX_QUERY_PARAM_LENGTH} characters.",
                )

    response = await call_next(request)
    return response