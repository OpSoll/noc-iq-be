from typing import List
from urllib.parse import urlparse

from pydantic_settings import BaseSettings


VALID_STELLAR_NETWORKS = {"testnet", "mainnet", "futurenet", "standalone"}
VALID_CONTRACT_EXECUTION_MODES = {"local_adapter", "soroban_rpc"}


class Settings(BaseSettings):
    PROJECT_NAME: str = "NOCIQ API"
    VERSION: str = "1.0.0"
    DEBUG: bool = False
    DATABASE_URL: str = "postgresql://postgres:password@localhost:5432/nociq"
    API_V1_PREFIX: str = "/api/v1"
    ALLOWED_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:3001"]
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/0"
    CELERY_TASK_ALWAYS_EAGER: bool = True
    SLA_CONTRACT_ADDRESS: str = "local-sla-calculator"
    STELLAR_NETWORK: str = "testnet"
    CONTRACT_EXECUTION_MODE: str = "local_adapter"
    PAYMENT_WEBHOOK_SECRET: str = ""
    WALLET_CACHE_TTL_SECONDS: int = 60  # how long wallet data is considered fresh
    PAYMENT_ASSET_CODE: str = "USDC"
    PAYMENT_FROM_ADDRESS: str = "SYSTEM_POOL"
    PAYMENT_TO_ADDRESS: str = "OUTAGE_SETTLEMENT"

    class Config:
        env_file = ".env"


settings = Settings()


def validate_critical_settings(config: Settings) -> None:
    errors: list[str] = []

    if not config.PROJECT_NAME.strip():
        errors.append("PROJECT_NAME must not be empty.")

    if not config.VERSION.strip():
        errors.append("VERSION must not be empty.")

    if not config.API_V1_PREFIX.startswith("/"):
        errors.append("API_V1_PREFIX must start with '/'.")

    if len(config.API_V1_PREFIX) > 1 and config.API_V1_PREFIX.endswith("/"):
        errors.append("API_V1_PREFIX must not end with '/' unless it is the root path.")

    if not config.DATABASE_URL.strip():
        errors.append("DATABASE_URL must not be empty.")
    else:
        parsed_database_url = urlparse(config.DATABASE_URL)
        if not parsed_database_url.scheme:
            errors.append("DATABASE_URL must include a valid URL scheme.")

    if not config.ALLOWED_ORIGINS:
        errors.append("ALLOWED_ORIGINS must include at least one origin.")
    else:
        invalid_origins = [
            origin
            for origin in config.ALLOWED_ORIGINS
            if not origin.startswith(("http://", "https://"))
        ]
        if invalid_origins:
            errors.append(
                "ALLOWED_ORIGINS must contain valid http or https origins."
            )

    if config.STELLAR_NETWORK not in VALID_STELLAR_NETWORKS:
        errors.append(
            "STELLAR_NETWORK must be one of: "
            + ", ".join(sorted(VALID_STELLAR_NETWORKS))
            + "."
        )

    if config.CONTRACT_EXECUTION_MODE not in VALID_CONTRACT_EXECUTION_MODES:
        errors.append(
            "CONTRACT_EXECUTION_MODE must be one of: "
            + ", ".join(sorted(VALID_CONTRACT_EXECUTION_MODES))
            + "."
        )

    if not config.CELERY_TASK_ALWAYS_EAGER:
        if not config.CELERY_BROKER_URL.strip():
            errors.append(
                "CELERY_BROKER_URL must not be empty when CELERY_TASK_ALWAYS_EAGER is false."
            )
        if not config.CELERY_RESULT_BACKEND.strip():
            errors.append(
                "CELERY_RESULT_BACKEND must not be empty when CELERY_TASK_ALWAYS_EAGER is false."
            )

    if not config.PAYMENT_ASSET_CODE.strip():
        errors.append("PAYMENT_ASSET_CODE must not be empty.")
    if not config.PAYMENT_FROM_ADDRESS.strip():
        errors.append("PAYMENT_FROM_ADDRESS must not be empty.")
    if not config.PAYMENT_TO_ADDRESS.strip():
        errors.append("PAYMENT_TO_ADDRESS must not be empty.")

    if errors:
        raise ValueError("Invalid startup configuration:\n- " + "\n- ".join(errors))
