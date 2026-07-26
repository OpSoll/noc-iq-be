from celery import Celery

celery_app = Celery(
    "nociq",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/1",
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_trace_propagators=[
        "opentelemetry.instrumentation.celery.propagator",
    ],
)
