import time
from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI
from httpx import AsyncClient
from jose import jwt
from starlette.status import HTTP_200_OK, HTTP_401_UNAUTHORIZED

from app.core.auth import JWTBearer
from app.core.config import get_settings

settings = get_settings()


# Create a dummy app for testing
app = FastAPI()


@app.get("/protected")
async def protected_route(payload: dict = Depends(JWTBearer())):
    return {"user": payload.get("sub")}


@pytest.fixture
def valid_token():
    to_encode = {"sub": "testuser", "exp": time.time() + 60, "aud": settings.API_AUDIENCE}
    encoded_jwt = jwt.encode(to_encode, settings.PRIVATE_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


@pytest.fixture
def expired_token():
    to_encode = {"sub": "testuser", "exp": time.time() - 60, "aud": settings.API_AUDIENCE}
    encoded_jwt = jwt.encode(to_encode, settings.PRIVATE_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


@pytest.mark.asyncio def test_jwt_bearer_valid_token(valid_token):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/protected", headers={"Authorization": f"Bearer {valid_token}"})
        assert response.status_code == HTTP_200_OK
        assert response.json() == {"user": "testuser"}


@pytest.mark.asyncio
async def test_jwt_bearer_expired_token(expired_token):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/protected", headers={"Authorization": f"Bearer {expired_token}"})
        assert response.status_code == HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_jwt_bearer_invalid_token():
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/protected", headers={"Authorization": "Bearer invalidtoken"})
        assert response.status_code == HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_jwt_bearer_no_token():
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/protected")
        assert response.status_code == HTTP_401_UNAUTHORIZED