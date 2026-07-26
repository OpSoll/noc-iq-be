import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.api.v1.router import api_router
from app.core.config import validate_env_schema
from app.middleware.body_size_limiter import BodySizeLimitMiddleware
from app.metrics.database_metrics import router as metrics_router, setup_db_metrics
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_env_schema()
    setup_db_metrics()
    yield


app = FastAPI(
    title="NOCIQ API",
    version="1.0.0",
    description="NOCIQ Backend API",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3001", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(BodySizeLimitMiddleware)


# Health check
@app.get("/health")
def health_check():
    return {"status": "ok"}

# API routes
app.include_router(api_router, prefix="/api/v1")

# Metrics routes
app.include_router(metrics_router)
