from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "nociq",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    beat_schedule={
        "webhook-autoscale-check": {
            "task": "app.tasks.webhook_autoscaler.periodic_autoscale_check",
            "schedule": 30.0,
        },
    },
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)

celery_app.autodiscover_tasks(["app.tasks"])
