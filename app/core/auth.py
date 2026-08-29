from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer
from jose import JWTError, jwt
from starlette.status import HTTP_401_UNAUTHORIZED

from app.core.config import settings


class JWTBearer(HTTPBearer):
    def __init__(self, auto_error: bool = True):
        super(JWTBearer, self).__init__(auto_error=auto_error)

    async def __call__(self, request):
        credentials = await super().__call__(request)
        if credentials:
            if not credentials.scheme == "Bearer":
                raise HTTPException(
                    status_code=HTTP_401_UNAUTHORIZED,
                    detail="Invalid authentication scheme.",
                )
            try:
                payload = jwt.decode(
                    credentials.credentials,
                    settings.PUBLIC_KEY,
                    algorithms=[settings.ALGORITHM],
                    audience=settings.API_AUDIENCE,
                )
                return payload
            except JWTError:
                raise HTTPException(
                    status_code=HTTP_401_UNAUTHORIZED,
                    detail="Invalid token or expired token.",
                )
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization code.",
        )