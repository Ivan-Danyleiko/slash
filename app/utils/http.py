import time
from collections.abc import Callable
from typing import TypeVar

import httpx
import structlog

logger = structlog.get_logger(__name__)
T = TypeVar("T")


def retry_request(
    fn: Callable[[], T],
    *,
    retries: int = 3,
    backoff_seconds: float = 0.5,
    platform: str,
) -> T:
    """Retry transient HTTP failures with linear backoff for collector robustness."""
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            last_exc = exc
            logger.warning(
                "collector_request_failed",
                platform=platform,
                attempt=attempt,
                retries=retries,
                error=str(exc),
            )
            if attempt < retries:
                time.sleep(backoff_seconds * attempt)
    assert last_exc is not None
    raise last_exc
