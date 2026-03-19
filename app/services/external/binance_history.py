from __future__ import annotations

import math
import statistics
from typing import Any

import httpx


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(float(x) / math.sqrt(2.0)))


def estimate_probability_for_level(
    *,
    symbol: str,
    spot_price: float,
    target_price: float,
    days_to_deadline: float,
    direction: str,
    timeout_seconds: float = 8.0,
) -> dict[str, Any] | None:
    """
    Estimate P(price above/below target by deadline) via log-normal approximation
    using Binance daily klines.
    """
    if float(spot_price) <= 0 or float(target_price) <= 0:
        return None
    d = str(direction or "").lower()
    if d not in {"above", "below"}:
        return None

    try:
        resp = httpx.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": str(symbol).upper(), "interval": "1d", "limit": 365},
            timeout=max(1.0, float(timeout_seconds)),
        )
        if resp.status_code != 200:
            return None
        payload = resp.json()
        if not isinstance(payload, list) or len(payload) < 40:
            return None
        closes: list[float] = []
        for row in payload:
            if not isinstance(row, list) or len(row) < 5:
                continue
            try:
                c = float(row[4])
            except Exception:  # noqa: BLE001
                continue
            if c > 0:
                closes.append(c)
        if len(closes) < 40:
            return None

        rets: list[float] = []
        for i in range(1, len(closes)):
            prev = float(closes[i - 1])
            cur = float(closes[i])
            if prev > 0 and cur > 0:
                rets.append(math.log(cur / prev))
        if len(rets) < 30:
            return None
        mu_d = float(statistics.mean(rets))
        sig_d = float(statistics.pstdev(rets))
        if sig_d <= 1e-9:
            return None

        t = max(1.0, float(days_to_deadline)) / 365.0
        mu = mu_d * 365.0
        sigma = sig_d * math.sqrt(365.0)
        vol_term = sigma * math.sqrt(max(1e-9, t))
        drift_term = (mu - 0.5 * sigma * sigma) * t
        z = (math.log(float(target_price) / float(spot_price)) - drift_term) / max(1e-9, vol_term)
        p_above = 1.0 - _normal_cdf(z)
        p_above = max(0.001, min(0.999, float(p_above)))
        out_prob = p_above if d == "above" else (1.0 - p_above)
        return {
            "our_prob": out_prob,
            "confidence": min(0.85, 0.45 + (len(rets) / 1000.0)),
            "source": "external_binance_lognormal",
            "reasoning": (
                f"symbol={str(symbol).upper()},target={float(target_price):.2f},"
                f"days={float(days_to_deadline):.1f}"
            ),
        }
    except Exception:  # noqa: BLE001
        return None

