import logging
from typing import List, Optional
from urllib.parse import urlparse

from pydantic import BaseSettings, field_validator
from pydantic_settings import BaseSettings as PydanticBaseSettings

from app.core.env_validators import (
    validate_postgres_url,
    validate_redis_url,
    validate_min_length,
    validate_allowed_origins,
)

logger = logging.getLogger(__name__)


VALID_STELLAR_NETWORKS = {"testnet", "mainnet", "futurenet", "standalone"}
VALID_CONTRACT_EXECUTION_MODES = {"local_adapter", "soroban_rpc"}



class Settings(BaseSettings):
    PROJECT_NAME: str = "NOCIQ API"
    VERSION: str = "1.0.0"
    REDIS_URL: str = "redis://localhost:6379"
    OTEL_SERVICE_NAME: str = "nociq-api"
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://localhost:4317"
    DEBUG: bool = False
    DATABASE_URL: str = "postgresql://postgres:password@localhost:5432/nociq"
    API_V1_PREFIX: str = "/api/v1"
    API_V2_PREFIX: str = "/api/v2"
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
    # BE-364: Authoritative asset issuer for the configured payout asset.
    # For USDC on testnet this is the Circle testnet issuer address.
    # Must be set to a non-empty G-address when CONTRACT_EXECUTION_MODE=soroban_rpc.
    PAYMENT_ASSET_ISSUER: str = ""
    # Trusted-proxy settings (#205)
    # Number of trusted reverse-proxy hops in front of this app.
    # Set to 0 when running without a proxy (uses direct connection IP).
    # Set to N when N proxy hops are trusted (e.g. 1 for a single load balancer).
    # Only the Nth entry from the right of X-Forwarded-For is used, preventing
    # spoofed headers injected by untrusted clients from being trusted.
    TRUSTED_PROXY_COUNT: int = 0

    # Auth rate limiting settings
    AUTH_MAX_FAILED_ATTEMPTS: int = 5  # Max failed login attempts before lockout
    AUTH_LOCKOUT_DURATION_MINUTES: int = 15  # Lockout duration in minutes
    AUTH_RATE_LIMIT_REQUESTS: int = 10  # Max requests per window
    AUTH_RATE_LIMIT_WINDOW_SECONDS: int = 300  # Rate limit window in seconds

    # Input size and payload guardrails
    MAX_REQUEST_BODY_SIZE_BYTES: int = 10 * 1024 * 1024  # 10 MB max request body size
    MAX_FILE_UPLOAD_SIZE_BYTES: int = 10 * 1024 * 1024  # 10 MB max file upload size (matches existing import limit)
    MAX_BULK_OUTAGES_COUNT: int = 1000  # Max number of outages in bulk create/import
    MAX_WEBHOOK_PAYLOAD_SIZE_BYTES: int = 1024 * 1024  # 1 MB max webhook payload size
    MAX_AFFECTED_SERVICES_COUNT: int = 100  # Max number of affected services per outage
    MAX_SITE_NAME_LENGTH: int = 255  # Max site name length
    MAX_DESCRIPTION_LENGTH: int = 5000  # Max description length
    MAX_WEBHOOK_EVENTS_COUNT: int = 50  # Max webhook events per webhook
    MAX_WEBHOOK_NAME_LENGTH: int = 255  # Max webhook name length
    MAX_WEBHOOK_URL_LENGTH: int = 2048  # Max webhook URL length

    # Webhook retry backoff policy (#236)
    # Comma-separated base delay seconds for each retry attempt.
    # e.g. "30,120,600" means 30 s on first retry, 2 min on second, 10 min on third.
    WEBHOOK_RETRY_BASE_DELAYS: str = "30,120,600"
    # Hard cap on any single computed delay (seconds) to prevent retry storms.
    WEBHOOK_RETRY_MAX_DELAY_SECONDS: int = 3600
    # BE-295: Grace window (seconds) during which the previous secret is still accepted.
    WEBHOOK_SECRET_GRACE_WINDOW_SECONDS: int = 3600

    # Application secret keys
    # SECURITY: Generate with: openssl rand -hex 32
    # These must be set to non-empty values before deploying to production.
    SECRET_KEY: str = ""
    JWT_SECRET_KEY: str = ""

    # Cold-start optimisation (#355)
    STARTUP_WARM_CACHE_ENABLED: bool = True
    STARTUP_LAZY_LOAD_MODULES: List[str] = [
        "app.services.contracts.sla_adapter",
        "app.services.contracts.translation",
        "app.services.outage_store",
        "app.services.sla_metric_registry",
    ]

    # Contract canonicalisation (#357)
    CONTRACT_CANONICAL_SALT: str = ""
    # Contract idempotency (#362)
    CONTRACT_IDEMPOTENCY_TTL_SECONDS: int = 3600

    # Bridge fallback strategy (#363)
    BRIDGE_FALLBACK_ENABLED: bool = True
    BRIDGE_CIRCUIT_BREAKER_THRESHOLD: int = 3
    BRIDGE_CIRCUIT_BREAKER_COOLDOWN_SECONDS: int = 30

    # Zero-downtime migration (#411)
    MIGRATION_BATCH_SIZE: int = 500
    MIGRATION_SHADOW_SUFFIX: str = "_shadow"
    model_config = {"env_prefix": "", "case_sensitive": True, "env_file": ".env"}

    @property
    def horizon_url(self) -> str:
        """Horizon base URL derived from STELLAR_NETWORK."""
        if self.STELLAR_NETWORK == "mainnet":
            return "https://horizon.stellar.org"
        return "https://horizon-testnet.stellar.org"

    IDEMPOTENCY_KEY_TTL_HOURS: int = 24

    WALLET_CACHE_LOCK_TIMEOUT: int = 5
    WALLET_CACHE_LOCK_PREFIX: str = "wallet:lock:"
    WALLET_CACHE_TTL: int = 300
    REDIS_URL: str = "redis://localhost:6379/1"

    class Config:
        env_file = ".env"


    REDIS_URL: str = "redis://localhost:6379/0"

    WEBHOOK_WORKER_MIN: int = 1
    WEBHOOK_WORKER_MAX: int = 10
    WEBHOOK_QUEUE_SCALE_UP_THRESHOLD: int = 100
    WEBHOOK_QUEUE_SCALE_DOWN_THRESHOLD: int = 10

    DB_POOL_SATURATION_THRESHOLD: float = 0.9
    DB_POOL_REJECT_AFTER_SECONDS: int = 30
    DB_POOL_SIZE: int = 10
    DB_POOL_MAX_OVERFLOW: int = 20

    RATE_LIMIT_BACKEND: str = "redis"
    RATE_LIMIT_MAX_KEYS: int = 10000
    RATE_LIMIT_EVICT_BATCH_SIZE: int = 100

    METRICS_CARDINALITY_BUDGET: int = 1000

    class Config:
        env_file = ".env"
        extra = "ignore"
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


def get_settings() -> Settings:
    return settings


def validate_critical_settings(config: Settings) -> None:
    errors: list[str] = []

    if not config.PROJECT_NAME.strip():
        errors.append("PROJECT_NAME must not be empty.")

    if not config.VERSION.strip():
        errors.append("VERSION must not be empty.")

    if not config.API_V1_PREFIX.startswith("/"):
        errors.append("API_V1_PREFIX must start with '/'.")

    if not config.API_V2_PREFIX.startswith("/"):
        errors.append("API_V2_PREFIX must start with '/'.")

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

    # BE-364: Require a well-formed issuer address in soroban_rpc mode.
    if config.CONTRACT_EXECUTION_MODE == "soroban_rpc":
        issuer = config.PAYMENT_ASSET_ISSUER.strip()
        if not issuer:
            errors.append(
                "PAYMENT_ASSET_ISSUER must be set when CONTRACT_EXECUTION_MODE=soroban_rpc."
            )
        elif not issuer.startswith("G") or len(issuer) != 56:
            errors.append(
                "PAYMENT_ASSET_ISSUER must be a valid 56-character Stellar G-address."
            )

    if config.TRUSTED_PROXY_COUNT < 0:
        errors.append("TRUSTED_PROXY_COUNT must be >= 0.")

    try:
        delays = [int(d.strip()) for d in config.WEBHOOK_RETRY_BASE_DELAYS.split(",") if d.strip()]
        if not delays:
            errors.append("WEBHOOK_RETRY_BASE_DELAYS must contain at least one value.")
        elif any(d < 0 for d in delays):
            errors.append("WEBHOOK_RETRY_BASE_DELAYS values must be >= 0.")
    except ValueError:
        errors.append("WEBHOOK_RETRY_BASE_DELAYS must be a comma-separated list of integers.")

    if config.WEBHOOK_RETRY_MAX_DELAY_SECONDS <= 0:
        errors.append("WEBHOOK_RETRY_MAX_DELAY_SECONDS must be > 0.")

    if not config.SECRET_KEY.strip():
        errors.append("SECRET_KEY must not be empty.")

    if not config.JWT_SECRET_KEY.strip():
        errors.append("JWT_SECRET_KEY must not be empty.")

    if errors:
        raise ValueError("Invalid startup configuration:\n- " + "\n- ".join(errors))
