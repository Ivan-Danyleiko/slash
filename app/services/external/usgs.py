from __future__ import annotations

from datetime import UTC, datetime, timedelta
import math
from typing import Any

import httpx


def estimate_no_earthquake_probability(
    *,
    min_magnitude: float = 4.5,
    lookback_days: int = 365,
    timeout_seconds: float = 8.0,
) -> dict[str, Any] | None:
    """
    Estimate P(no earthquake >= min_magnitude in next day) via Poisson approximation
    from USGS historical count.
    """
    mag = float(min_magnitude)
    if (not math.isfinite(mag)) or mag < 1.0 or mag > 9.9:
        return None
    end = datetime.now(UTC).date()
    start = (datetime.now(UTC) - timedelta(days=max(30, int(lookback_days)))).date()
    url = (
        "https://earthquake.usgs.gov/fdsnws/event/1/count"
        f"?format=geojson&starttime={start}&endtime={end}&minmagnitude={mag:.1f}"
    )
    try:
        resp = httpx.get(url, timeout=max(1.0, float(timeout_seconds)))
        if resp.status_code != 200:
            return None
        payload = resp.json() or {}
        cnt = int(payload.get("count") or 0)
        avg_per_day = float(cnt) / float(max(1, int(lookback_days)))
        p_no = math.exp(-avg_per_day)
        p_no = max(0.001, min(0.999, p_no))
        return {
            "our_prob": p_no,
            "confidence": 0.75,
            "source": "external_usgs_poisson",
            "reasoning": f"count_{int(lookback_days)}d={cnt},avg_per_day={avg_per_day:.3f}",
        }
    except Exception:  # noqa: BLE001
        return None
