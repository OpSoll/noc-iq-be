import logging
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI
from datetime import datetime
from sqlalchemy import text
from redis import Redis

from app.api.v1.router import api_router
from app.core.config import settings, validate_env_schema, validate_critical_settings
from app.api.v2.router import api_v2_router
from app.core.config import settings, validate_critical_settings
from app.core.session_hygiene import debug_router as session_debug_router
from app.core.dependencies import di_router
from app.db.session import engine
from app.middleware.body_size_limiter import BodySizeLimitMiddleware
from app.middleware.correlation import CorrelationMiddleware
from app.middleware.payload_size import PayloadSizeMiddleware
from app.metrics.database_metrics import router as metrics_router, setup_db_metrics

validate_critical_settings(settings)

async def check_database() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            conn.commit()
        return True
    except Exception:
        return False

async def check_celery() -> bool:
    try:
        r = Redis.from_url(settings.CELERY_BROKER_URL)
        r.ping()
        return True
    except Exception:
        return False

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_env_schema()
    setup_db_metrics()
    yield

from app.api.v1.router import api_router
from app.core.lifespan import lifespan
from app.core.rate_limiter import RateLimiterMiddleware

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="NOCIQ Backend API",
    lifespan=lifespan,
)

# Add correlation middleware first (before CORS to ensure it runs on all requests)
app.add_middleware(CorrelationMiddleware)

# Add payload size middleware (after correlation, before CORS)
app.add_middleware(PayloadSizeMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimiterMiddleware)


# ---------------------------------------------------------------------------
# API version header middleware (#414)
# ---------------------------------------------------------------------------

@app.middleware("http")
async def add_api_version_header(request, call_next):
    response = await call_next(request)
    response.headers["X-API-Version"] = settings.VERSION
    return response

app.add_middleware(BodySizeLimitMiddleware)


# Health checks
@app.get("/health/liveness")
def liveness():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}

@app.get("/health/readiness")
async def readiness():
    db_ok = await check_database()
    celery_ok = await check_celery()
    status = "ok" if db_ok and celery_ok else "degraded"
    return {
        "status": status,
        "timestamp": datetime.utcnow().isoformat(),
        "dependencies": {
            "database": "ok" if db_ok else "down",
            "celery": "ok" if celery_ok else "down",
        }
    }

@app.get("/health")
def health_check():
    return {"status": "ok"}

# Debug / dependency-injection routers (admin only)
app.include_router(session_debug_router)
app.include_router(di_router)

# Deprecation header for v1 endpoints (#414)
@app.middleware("http")
async def add_v1_deprecation_header(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(settings.API_V1_PREFIX):
        response.headers["Deprecation"] = "true"
        response.headers["Sunset"] = "2027-01-01T00:00:00Z"
    return response

# API v1 routes
app.include_router(api_router, prefix=settings.API_V1_PREFIX)

# API v2 routes
app.include_router(api_v2_router, prefix=settings.API_V2_PREFIX)
app.include_router(api_router, prefix="/api/v1")

# Metrics routes
app.include_router(metrics_router)
