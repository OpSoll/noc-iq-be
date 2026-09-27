import secrets

import pytest
from fastapi import Depends, FastAPI, Security
from httpx import AsyncClient
from sqlalchemy.orm import Session
from starlette.status import HTTP_200_OK, HTTP_403_FORBIDDEN

from app.db.session import get_db
from app.models.api_key import APIKey
from app.security import get_api_key, has_scope, hash_api_key

# Create a dummy app for testing
app = FastAPI()


@app.get("/protected")
async def protected_route(api_key: APIKey = Security(get_api_key)):
    return {"message": "Success"}


@app.get("/protected-with-scope")
async def protected_route_with_scope(
    api_key: APIKey = Security(has_scope, required_scopes=["read:protected"])
):
    return {"message": "Success"}


@pytest.fixture
def api_key_and_secret(db: Session):
    secret = secrets.token_urlsafe(32)
    hashed_key = hash_api_key(secret)
    api_key = APIKey(key=hashed_key, scopes=["read:protected"])
    db.add(api_key)
    db.commit()
    db.refresh(api_key)
    return api_key, secret


@pytest.mark.asyncio
async def test_api_key_auth_valid_key(async_client: AsyncClient, api_key_and_secret):
    _, secret = api_key_and_secret
    response = await async_client.get("/protected", headers={"X-API-Key": secret})
    assert response.status_code == HTTP_200_OK


@pytest.mark.asyncio
async def test_api_key_auth_invalid_key(async_client: AsyncClient):
    response = await async_client.get("/protected", headers={"X-API-Key": "invalid"})
    assert response.status_code == HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_api_key_auth_missing_key(async_client: AsyncClient):
    response = await async_client.get("/protected")
    assert response.status_code == HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_api_key_auth_with_scope(async_client: AsyncClient, api_key_and_secret):
    _, secret = api_key_and_secret
    response = await async_client.get(
        "/protected-with-scope", headers={"X-API-Key": secret}
    )
    assert response.status_code == HTTP_200_OK


@pytest.mark.asyncio
async def test_api_key_auth_missing_scope(async_client: AsyncClient, db: Session):
    secret = secrets.token_urlsafe(32)
    hashed_key = hash_api_key(secret)
    api_key = APIKey(key=hashed_key, scopes=["read:other"])
    db.add(api_key)
    db.commit()
    db.refresh(api_key)

    response = await async_client.get(
        "/protected-with-scope", headers={"X-API-Key": secret}
    )
    assert response.status_code == HTTP_403_FORBIDDEN