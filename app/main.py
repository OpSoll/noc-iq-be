from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.config import settings, validate_critical_settings

validate_critical_settings(settings)

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="NOCIQ Backend API"
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Health check
@app.get("/health")
def health_check():
    return {"status": "ok"}

# API routes
app.include_router(api_router, prefix="/api/v1")
