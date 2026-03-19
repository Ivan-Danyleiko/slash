from __future__ import annotations

from typing import Any

import httpx


def estimate_weather_base_rate(
    *,
    api_key: str,
    lat: float,
    lon: float,
    timeout_seconds: float = 8.0,
) -> dict[str, Any] | None:
    """
    Lightweight placeholder for weather-based base-rate.
    Uses OpenWeather current weather endpoint availability as health proxy.
    Returns None when API unavailable or key missing.
    """
    key = str(api_key or "").strip()
    if not key:
        return None
    try:
        resp = httpx.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"lat": float(lat), "lon": float(lon), "appid": key},
            timeout=max(1.0, float(timeout_seconds)),
        )
        if resp.status_code != 200:
            return None
        payload = resp.json() or {}
        clouds = float(((payload.get("clouds") or {}).get("all") or 0.0))
        # Placeholder heuristic: higher cloudiness => higher chance of precipitation event.
        p = max(0.01, min(0.99, 0.05 + (clouds / 200.0)))
        return {
            "our_prob": p,
            "confidence": 0.35,
            "source": "external_openweather_placeholder",
            "reasoning": f"cloudiness={clouds:.1f}",
        }
    except Exception:  # noqa: BLE001
        return None

