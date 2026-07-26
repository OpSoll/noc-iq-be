import logging
from typing import Optional
from pydantic import BaseSettings, field_validator

from app.core.env_validators import (
    validate_postgres_url,
    validate_redis_url,
    validate_min_length,
    validate_allowed_origins,
)

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    PROJECT_NAME: str = "NOCIQ API"
    VERSION: str = "1.0.0"

    DATABASE_URL: str = "postgresql://localhost:5432/nociq"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    SECRET_KEY: str = "change-me-in-production-use-a-real-secret-key!!"
    JWT_SECRET_KEY: str = "change-me-in-production-use-a-real-jwt-secret-key!!"
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:3001"

    MAX_AUTH_BODY_SIZE: int = 1024  # 1 KB
    MAX_CRUD_BODY_SIZE: int = 10 * 1024  # 10 KB
    MAX_BULK_BODY_SIZE: int = 10 * 1024 * 1024  # 10 MB
    MAX_WEBHOOK_BODY_SIZE: int = 1 * 1024 * 1024  # 1 MB

    @field_validator("DATABASE_URL")
    @classmethod
    def _validate_database_url(cls, v: str) -> str:
        return validate_postgres_url(v)

    @field_validator("CELERY_BROKER_URL")
    @classmethod
    def _validate_celery_broker_url(cls, v: str) -> str:
        return validate_redis_url(v)

    @field_validator("SECRET_KEY")
    @classmethod
    def _validate_secret_key(cls, v: str) -> str:
        return validate_min_length(v, 32, "SECRET_KEY")

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def _validate_jwt_secret_key(cls, v: str) -> str:
        return validate_min_length(v, 32, "JWT_SECRET_KEY")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()


def validate_env_schema() -> None:
    errors: list[str] = []

    try:
        validate_postgres_url(settings.DATABASE_URL)
    except ValueError as exc:
        errors.append(str(exc))

    try:
        validate_redis_url(settings.CELERY_BROKER_URL)
    except ValueError as exc:
        errors.append(str(exc))

    try:
        validate_min_length(settings.SECRET_KEY, 32, "SECRET_KEY")
    except ValueError as exc:
        errors.append(str(exc))

    try:
        validate_min_length(settings.JWT_SECRET_KEY, 32, "JWT_SECRET_KEY")
    except ValueError as exc:
        errors.append(str(exc))

    try:
        validate_allowed_origins(settings.ALLOWED_ORIGINS)
    except ValueError as exc:
        errors.append(str(exc))

    if errors:
        for err in errors:
            logger.error("Environment validation error: %s", err)
        raise ValueError(f"Environment validation failed with {len(errors)} error(s): {'; '.join(errors)}")

    logger.info("Environment schema validation passed.")
