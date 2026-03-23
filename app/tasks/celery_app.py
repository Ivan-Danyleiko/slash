from celery import Celery

from app.core.config import get_settings

settings = get_settings()
celery_app = Celery("prediction_market_scanner", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    # Graceful worker recycle after N tasks — prevents memory bloat from
    # accumulated heap fragmentation after heavy LLM/DB cycles.
    worker_max_tasks_per_child=200,
    # Soft memory limit: worker auto-restarts if RSS exceeds ~512 MB.
    worker_max_memory_per_child=524288,  # KB (512 MB)
)
