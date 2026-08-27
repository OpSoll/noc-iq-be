import logging
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
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
from app.middleware.deprecation import DeprecationHeaderMiddleware
from app.middleware.payload_size import PayloadSizeMiddleware
from app.middleware.pool_saturation import PoolSaturationMiddleware
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


async def check_worker_queue_bindings() -> dict:
    """Probe Celery queue bindings for orchestration readiness gates.

    BE-W5-051: Health signals are exposed for orchestration readiness gates.
    Returns a non-strict probe result suitable for liveness/readiness checks.
    Never raises — failures are reported in the response payload.
    """
    try:
        from app.tasks.celery_app import verify_queue_bindings
        probe = verify_queue_bindings(strict=False)
        return probe
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "ok": False,
            "error": str(exc),
            "required": [],
            "observed": [],
            "missing": [],
            "workers_seen": 0,
            "timeout_seconds": getattr(
                settings, "CELERY_QUEUE_PROBE_TIMEOUT_SECONDS", 5.0
            ),
        }

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_env_schema()
    setup_db_metrics()
    yield

from app.api.v1.router import api_router
from app.core.lifespan import app_lifespan
from app.core.rate_limiter import RateLimiterMiddleware

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="NOCIQ Backend API",
    lifespan=app_lifespan,
)

app.add_middleware(PoolSaturationMiddleware)

app.add_middleware(CorrelationMiddleware)

app.add_middleware(PayloadSizeMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Issue #514: cache preflight responses for 24h so browsers stop reissuing
    # OPTIONS before every API fetch.
    max_age=86400,
)
app.add_middleware(RateLimiterMiddleware)

# Issue #509: gzip-compress API responses larger than 1 KB.
app.add_middleware(GZipMiddleware, minimum_size=1000)


# ---------------------------------------------------------------------------
# API version header middleware (#414)
# ---------------------------------------------------------------------------

@app.middleware("http")
async def add_api_version_header(request, call_next):
    response = await call_next(request)
    response.headers["X-API-Version"] = settings.VERSION
    return response

app.add_middleware(BodySizeLimitMiddleware)

# Issue #511: RFC 8594 deprecation headers for legacy /api/v0/* routes.
app.add_middleware(DeprecationHeaderMiddleware)


# Health checks
@app.get("/health/liveness")
def liveness():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}

@app.get("/health/readiness")
async def readiness():
    db_ok = await check_database()
    celery_ok = await check_celery()
    queue_probe = await check_worker_queue_bindings()
    overall_ok = db_ok and celery_ok and bool(queue_probe.get("ok"))
    status = "ok" if overall_ok else "degraded"
    payload = {
        "status": status,
        "timestamp": datetime.utcnow().isoformat(),
        "dependencies": {
            "database": "ok" if db_ok else "down",
            "celery": "ok" if celery_ok else "down",
            "queue_bindings": "ok" if queue_probe.get("ok") else "missing",
        },
        "queue_bindings": queue_probe,
    }
    # Return 503 when degraded so orchestrators can fail readiness gates.
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=200 if overall_ok else 503, content=payload)


@app.get("/health/worker")
async def worker_health():
    """Celery worker queue-bindings health probe.

    BE-W5-051: Health signals are exposed for orchestration readiness gates.
    Returns 200 when all required queues are bound to active workers, else
    503 with a breakdown of missing/observed queue names.
    """
    queue_probe = await check_worker_queue_bindings()
    from fastapi.responses import JSONResponse
    status_code = 200 if queue_probe.get("ok") else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ok" if queue_probe.get("ok") else "missing_queues",
            "timestamp": datetime.utcnow().isoformat(),
            **queue_probe,
        },
    )


@app.get("/health/concurrency")
async def concurrency_health():
    """Per-environment concurrency profile + live DB/broker saturation.

    BE-W5-055: Worker concurrency settings are profiled and documented per
    environment; DB pool and broker connections stay within safe limits;
    guardrail alerts fire before saturation failures.
    """
    concurrency_guardrails_payload: dict = {}
    try:
        from app.services.concurrency_guardrails import guardrails_dict
        from app.tasks.celery_app import celery_app as _celery_app
        concurrency_guardrails_payload = guardrails_dict(_celery_app)
    except Exception as exc:  # pragma: no cover - defensive
        # Never let this endpoint 500 — degrade gracefully.
        concurrency_guardrails_payload = {"error": str(exc)}

    live = concurrency_guardrails_payload.get("live_metrics", {}) or {}
    alerts_active = bool(live.get("alerts_active"))
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=200,
        content={
            "status": "ok" if not alerts_active else "guardrail_alert",
            "timestamp": datetime.utcnow().isoformat(),
            **concurrency_guardrails_payload,
        },
    )

@app.get("/health")
def health_check():
    return {"status": "ok"}

# Startup optimisation (#355)
from app.core.startup_optimizer import startup_router, run_startup_optimization

# Queue analysis (#356)
from app.tasks.queue_analyzer import queue_analysis_router

# Run startup optimisation
run_startup_optimization()

# Health / metrics routers mounted at root (outside api_router prefix)
app.include_router(startup_router)
app.include_router(queue_analysis_router)

# API routes

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
