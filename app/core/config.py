from pydantic import BaseSettings


class Settings(BaseSettings):
    PROJECT_NAME: str = "NOCIQ API"
    VERSION: str = "1.0.0"

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


settings = Settings()
