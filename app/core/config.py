import logging
from typing import List, Optional
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings

from app.core.env_validators import (
    validate_postgres_url,
    validate_redis_url,
    validate_min_length,
    validate_allowed_origins,
)

logger = logging.getLogger(__name__)

VALID_STELLAR_NETWORKS = {"testnet", "mainnet", "futurenet", "standalone"}
VALID_CONTRACT_EXECUTION_MODES = {"local_adapter", "soroban_rpc"}

# ---------------------------------------------------------------------------
# Default secrets for local/dev — must be overridden in production.
# ---------------------------------------------------------------------------
_DEFAULT_SECRET = "change-me-in-production-use-a-real-secret-key!!"


class Settings(BaseSettings):
    model_config = {
        "env_prefix": "",
        "case_sensitive": True,
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    # ── Core ──────────────────────────────────────────────────────────────
    PROJECT_NAME: str = "NOCIQ API"
    VERSION: str = "1.0.0"
    DEBUG: bool = False

    DATABASE_URL: str = "postgresql://postgres:password@localhost:5432/nociq"
    REDIS_URL: str = "redis://localhost:6379/0"

    API_V1_PREFIX: str = "/api/v1"
    API_V2_PREFIX: str = "/api/v2"
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:3001",
    ]

    # ── Secrets (must be overridden in production) ────────────────────────
    SECRET_KEY: str = _DEFAULT_SECRET
    JWT_SECRET_KEY: str = _DEFAULT_SECRET

    # ── Celery ────────────────────────────────────────────────────────────
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/0"
    CELERY_TASK_ALWAYS_EAGER: bool = True

    # ── Observability ─────────────────────────────────────────────────────
    OTEL_SERVICE_NAME: str = "nociq-api"
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://localhost:4317"

    # ── Stellar ───────────────────────────────────────────────────────────
    SLA_CONTRACT_ADDRESS: str = "local-sla-calculator"
    STELLAR_NETWORK: str = "testnet"
    CONTRACT_EXECUTION_MODE: str = "local_adapter"
    PAYMENT_WEBHOOK_SECRET: str = ""
    PAYMENT_ASSET_CODE: str = "USDC"
    PAYMENT_FROM_ADDRESS: str = "SYSTEM_POOL"
    PAYMENT_TO_ADDRESS: str = "OUTAGE_SETTLEMENT"
    PAYMENT_ASSET_ISSUER: str = ""

    @property
    def horizon_url(self) -> str:
        if self.STELLAR_NETWORK == "mainnet":
            return "https://horizon.stellar.org"
        return "https://horizon-testnet.stellar.org"

    # ── Auth throttling ───────────────────────────────────────────────────
    AUTH_MAX_FAILED_ATTEMPTS: int = 5
    AUTH_LOCKOUT_DURATION_MINUTES: int = 15
    AUTH_RATE_LIMIT_REQUESTS: int = 10
    AUTH_RATE_LIMIT_WINDOW_SECONDS: int = 300

    # ── Payload guardrails ────────────────────────────────────────────────
    MAX_REQUEST_BODY_SIZE_BYTES: int = 10 * 1024 * 1024   # 10 MB
    MAX_FILE_UPLOAD_SIZE_BYTES: int = 10 * 1024 * 1024    # 10 MB
    MAX_BULK_OUTAGES_COUNT: int = 1000
    MAX_WEBHOOK_PAYLOAD_SIZE_BYTES: int = 1024 * 1024     # 1 MB
    MAX_AFFECTED_SERVICES_COUNT: int = 100
    MAX_SITE_NAME_LENGTH: int = 255
    MAX_DESCRIPTION_LENGTH: int = 5000
    MAX_WEBHOOK_EVENTS_COUNT: int = 50
    MAX_WEBHOOK_NAME_LENGTH: int = 255
    MAX_WEBHOOK_URL_LENGTH: int = 2048

    MAX_AUTH_BODY_SIZE: int = 1024                         # 1 KB
    MAX_CRUD_BODY_SIZE: int = 10 * 1024                    # 10 KB
    MAX_BULK_BODY_SIZE: int = 10 * 1024 * 1024             # 10 MB
    MAX_WEBHOOK_BODY_SIZE: int = 1 * 1024 * 1024           # 1 MB

    # ── Cache & idempotency ───────────────────────────────────────────────
    WALLET_CACHE_TTL_SECONDS: int = 60
    WALLET_CACHE_LOCK_TIMEOUT: int = 5
    WALLET_CACHE_LOCK_PREFIX: str = "wallet:lock:"
    WALLET_CACHE_TTL: int = 300
    IDEMPOTENCY_KEY_TTL_HOURS: int = 24

    # ── Webhook retry policy ──────────────────────────────────────────────
    WEBHOOK_RETRY_BASE_DELAYS: str = "30,120,600"
    WEBHOOK_RETRY_MAX_DELAY_SECONDS: int = 3600
    WEBHOOK_SECRET_GRACE_WINDOW_SECONDS: int = 3600

    # ── BE-W5-041 (#302): Queue partitioning & backpressure ──────────────
    WEBHOOK_PARTITION_COUNT: int = 4
    WEBHOOK_PARTITION_BACKPRESSURE_THRESHOLD: int = 500
    WEBHOOK_PARTITION_MAX_PENDING: int = 2000
    WEBHOOK_ENDPOINT_PARTITION_ENABLED: bool = True
    WEBHOOK_SLA_PRIORITY_PARTITION: int = 0
    WEBHOOK_PAYMENT_PRIORITY_PARTITION: int = 1

    # ── BE-W5-042 (#303): SSRF safeguards ────────────────────────────────
    WEBHOOK_SSRF_BLOCKED_CIDRS: str = (
        "127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,"
        "169.254.0.0/16,::1/128,fd00::/8,fe80::/10"
    )
    WEBHOOK_SSRF_BLOCKED_HOSTNAMES: str = (
        "localhost,localhost.localdomain,localhost6,localhost6.localdomain6,"
        "metadata.google.internal,metadata.aws.internal,169.254.169.254"
    )
    WEBHOOK_SSRF_ALLOW_PRIVATE: bool = False
    WEBHOOK_SSRF_ALLOW_LOOPBACK: bool = False
    WEBHOOK_SSRF_ALLOW_LINK_LOCAL: bool = False
    WEBHOOK_SSRF_MAX_REDIRECTS: int = 3

    # ── BE-W5-043 (#304): Payload redaction ──────────────────────────────
    WEBHOOK_REDACTION_ENABLED: bool = True
    WEBHOOK_REDACTED_FIELDS: str = (
        "seed,secret_seed,private_key,mnemonic,password,token,"
        "access_token,refresh_token,signing_key,wallet_secret"
    )
    WEBHOOK_REDACTION_MASK: str = "[REDACTED]"

    # ── BE-W5-044 (#305): SLO metrics & alert thresholds ─────────────────
    WEBHOOK_SLO_SUCCESS_TARGET: float = 0.999
    WEBHOOK_SLO_LATENCY_TARGET_MS: int = 5000
    WEBHOOK_SLO_BURN_RATE_THRESHOLD: float = 2.0
    WEBHOOK_SLO_WINDOW_SECONDS: int = 3600
    WEBHOOK_SLO_BUDGET_BURN_ALERT_PERCENT: float = 50.0

    # ── Bridge ────────────────────────────────────────────────────────────
    BRIDGE_TIMEOUT_SLA_CHECK_MS: int = 5000
    BRIDGE_TIMEOUT_PAYMENT_MS: int = 30000
    BRIDGE_TIMEOUT_BALANCE_MS: int = 10000
    BRIDGE_RESPONSE_VERSION: str = "v2"
    BRIDGE_FALLBACK_ENABLED: bool = True
    BRIDGE_CIRCUIT_BREAKER_THRESHOLD: int = 3
    BRIDGE_CIRCUIT_BREAKER_COOLDOWN_SECONDS: int = 30

    # ── Contract ──────────────────────────────────────────────────────────
    ALLOWED_CONTRACT_ADDRESSES: List[str] = []
    MAX_CONTRACT_EXECUTION_AMOUNT: float = 0.0
    CONTRACT_CALL_RATE_LIMIT: int = 10
    CONTRACT_CANONICAL_SALT: str = ""
    CONTRACT_IDEMPOTENCY_TTL_SECONDS: int = 3600

    # ── Startup ───────────────────────────────────────────────────────────
    STARTUP_WARM_CACHE_ENABLED: bool = True
    STARTUP_LAZY_LOAD_MODULES: List[str] = [
        "app.services.contracts.sla_adapter",
        "app.services.contracts.translation",
        "app.services.outage_store",
        "app.services.sla_metric_registry",
    ]

    # ── Migration ─────────────────────────────────────────────────────────
    MIGRATION_BATCH_SIZE: int = 500
    MIGRATION_SHADOW_SUFFIX: str = "_shadow"

    # ── Proxy ─────────────────────────────────────────────────────────────
    TRUSTED_PROXY_COUNT: int = 0

    # ── Worker autoscaling ────────────────────────────────────────────────
    WEBHOOK_WORKER_MIN: int = 1
    WEBHOOK_WORKER_MAX: int = 10
    WEBHOOK_QUEUE_SCALE_UP_THRESHOLD: int = 100
    WEBHOOK_QUEUE_SCALE_DOWN_THRESHOLD: int = 10

    # ── DB pool ───────────────────────────────────────────────────────────
    DB_POOL_SATURATION_THRESHOLD: float = 0.9
    DB_POOL_REJECT_AFTER_SECONDS: int = 30
    DB_POOL_SIZE: int = 10
    DB_POOL_MAX_OVERFLOW: int = 20

    # ── Rate limiting ─────────────────────────────────────────────────────
    RATE_LIMIT_BACKEND: str = "redis"
    RATE_LIMIT_MAX_KEYS: int = 10000
    RATE_LIMIT_EVICT_BATCH_SIZE: int = 100

    # ── Metrics ───────────────────────────────────────────────────────────
    METRICS_CARDINALITY_BUDGET: int = 1000

    # ── Validators ────────────────────────────────────────────────────────
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
        raise ValueError(
            f"Environment validation failed with {len(errors)} error(s): "
            f"{'; '.join(errors)}"
        )

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
            errors.append("ALLOWED_ORIGINS must contain valid http or https origins.")

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
        raise ValueError(
            "Invalid startup configuration:\n- " + "\n- ".join(errors)
        )
