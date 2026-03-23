from celery import Celery
from src.core.config import settings

app = Celery(
    'tasks',
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL
)

app.autodiscover_tasks(['src.tasks'])