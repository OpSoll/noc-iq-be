from fastapi import FastAPI
from app.api.v1.router import api_router

app = FastAPI(
    title="NOCIQ API",
    version="1.0.0",
    description="NOCIQ Backend API"
)

# Health check
@app.get("/health")
def health_check():
    return {"status": "ok"}

# API routes
app.include_router(api_router, prefix="/api/v1")
