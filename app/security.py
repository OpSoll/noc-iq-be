import hashlib
import hmac
from typing import List

from fastapi import Depends, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from sqlalchemy.orm import Session
from starlette.status import HTTP_403_FORBIDDEN

from app.core.config import settings
from app.db.session import get_db
from app.models.api_key import APIKey

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def hash_api_key(api_key: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), api_key.encode(), hashlib.sha256).hexdigest()


async def get_api_key(
    api_key: str = Security(api_key_header),
    db: Session = Depends(get_db),
) -> APIKey:
    if not api_key:
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN, detail="API key is missing"
        )

    hashed_key = hash_api_key(api_key)
    db_api_key = db.query(APIKey).filter(APIKey.key == hashed_key).first()

    if not db_api_key or not db_api_key.is_active:
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN, detail="Invalid API key"
        )

    return db_api_key


def has_scope(required_scopes: List[str]):
    def _has_scope(api_key: APIKey = Security(get_api_key)) -> bool:
        for scope in required_scopes:
            if scope not in api_key.scopes:
                raise HTTPException(
                    status_code=HTTP_403_FORBIDDEN,
                    detail=f"Missing required scope: {scope}",
                )
        return True

    return _has_scope