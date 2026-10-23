Start The API
uvicorn app.main:app --reload
The backend will be available at:

http://localhost:8000
Swagger docs: http://localhost:8000/docs
Liveness check: http://localhost:8000/health/liveness
Readiness check: http://localhost:8000/health/readiness
Detailed health (DB + Celery broker + worker heartbeat): http://localhost:8000/api/v1/health/detailed
Prometheus metrics: http://localhost:8000/metrics
Legacy compatibility: http://localhost:8000/health
