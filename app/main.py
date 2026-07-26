from fastapi import FastAPI
from app.api.v1.router import api_router
from fastapi.middleware.cors import CORSMiddleware
from app.middleware.pool_saturation import PoolSaturationMiddleware

app = FastAPI(
    title="NOCIQ API",
    version="1.0.0",
    description="NOCIQ Backend API"
)

app.add_middleware(PoolSaturationMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3001", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check():
    return {"status": "ok"}


app.include_router(api_router, prefix="/api/v1")
