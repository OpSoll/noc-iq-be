import asyncio

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from starlette.status import HTTP_200_OK, HTTP_429_TOO_MANY_REQUESTS

from app.core.rate_limiter import RateLimiterMiddleware

# Create a dummy app for testing
app = FastAPI()
app.add_middleware(RateLimiterMiddleware, limit=100, window_seconds=60)


@app.get("/")
async def root():
    return {"message": "Hello, world!"}


@pytest.mark.asyncio
async def test_rate_limiter():
    async with AsyncClient(app=app, base_url="http://test") as client:
        # Make 100 requests
        for _ in range(100):
            response = await client.get("/")
            assert response.status_code == HTTP_200_OK

        # Make one more request
        response = await client.get("/")
        assert response.status_code == HTTP_429_TOO_MANY_REQUESTS
        assert "Retry-After" in response.headers