from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "nociq",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.tasks.sla_tasks",
        "app.tasks.webhook_tasks",
        "app.tasks.idempotency_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_trace_propagators=[
        "opentelemetry.instrumentation.celery.propagator",
    ],
    task_track_started=True,
    task_acks_late=True,
    task_always_eager=settings.CELERY_TASK_ALWAYS_EAGER,
    task_store_eager_result=True,
    worker_prefetch_multiplier=1,
    result_expires=86400,  # 24 hours

    beat_schedule={
        "webhook-autoscale-check": {
            "task": "app.tasks.webhook_autoscaler.periodic_autoscale_check",
            "schedule": 30.0,
        },
        "retry-pending-webhook-deliveries": {
            "task": "app.tasks.webhook_tasks.retry_pending_webhook_deliveries",
            "schedule": 60.0,
        },
        "cleanup-expired-idempotency-keys": {
            "task": "app.tasks.idempotency_tasks.cleanup_expired_idempotency_keys",
            "schedule": 3600.0,  # every hour
        },
    },
)

celery_app.autodiscover_tasks(["app.tasks"])
