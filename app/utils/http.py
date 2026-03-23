import random
import time
from collections.abc import Callable
from typing import TypeVar

import httpx
import structlog

from app.core.secrets import redact_text

logger = structlog.get_logger(__name__)
T = TypeVar("T")


def retry_request(
    fn: Callable[[], T],
    *,
    retries: int = 3,
    backoff_seconds: float = 0.5,
    platform: str,
) -> T:
    """Retry transient HTTP failures with exponential+jitter backoff.

    Handles 429 rate-limit responses explicitly:
    - Reads Retry-After header (seconds or HTTP-date) when present
    - Falls back to exponential backoff with jitter if header absent
    - Does NOT count 429 as a hard failure — retries up to `retries` times
    """
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = fn()
            # If fn() returns an httpx.Response and it's a rate-limit, handle it.
            if isinstance(resp, httpx.Response) and resp.status_code == 429:
                retry_after_raw = resp.headers.get("Retry-After", "")
                wait: float
                try:
                    wait = float(retry_after_raw)
                except (ValueError, TypeError):
                    # Exponential backoff with full jitter
                    wait = backoff_seconds * (2 ** attempt) + random.uniform(0.0, backoff_seconds)
                wait = min(wait, 60.0)  # cap at 60s to prevent blocking forever
                logger.warning(
                    "collector_rate_limited",
                    platform=platform,
                    attempt=attempt,
                    retry_after_seconds=round(wait, 1),
                )
                if attempt < retries:
                    time.sleep(wait)
                    continue
                # Exhausted retries on 429 — raise as HTTPStatusError
                resp.raise_for_status()
            return resp  # type: ignore[return-value]
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            last_exc = exc
            logger.warning(
                "collector_request_failed",
                platform=platform,
                attempt=attempt,
                retries=retries,
                error=redact_text(str(exc), max_len=200),
            )
            if attempt < retries:
                jitter = random.uniform(0.0, backoff_seconds * 0.5)
                time.sleep(backoff_seconds * attempt + jitter)
    assert last_exc is not None
    raise last_exc
