import hashlib
import hmac
import re
import threading
import time

from fastapi import Depends, Header, HTTPException

from app.core.config import get_settings

_USER_ID_RE = re.compile(r"^[0-9]{5,20}$")
_WRITE_THROTTLE_LOCK = threading.Lock()
_LAST_ADMIN_WRITE_TS_BY_KEY: dict[str, float] = {}
_READ_THROTTLE_LOCK = threading.Lock()
_LAST_ADMIN_READ_TS_BY_KEY: dict[str, float] = {}
_THROTTLE_KEY_TTL_SEC = 3600.0
_THROTTLE_MAP_MAX_KEYS = 4096


def _prune_throttle_map(values: dict[str, float], now: float) -> None:
    if len(values) < _THROTTLE_MAP_MAX_KEYS:
        return
    cutoff = now - _THROTTLE_KEY_TTL_SEC
    stale = [k for k, ts in values.items() if ts < cutoff]
    for k in stale:
        values.pop(k, None)
    # If map is still too large (e.g. constant churn), drop oldest keys.
    overflow = len(values) - _THROTTLE_MAP_MAX_KEYS
    if overflow > 0:
        oldest = sorted(values.items(), key=lambda x: x[1])[:overflow]
        for k, _ in oldest:
            values.pop(k, None)


def require_admin(x_api_key: str = Header(default="")) -> None:
    settings = get_settings()
    key = str(settings.admin_api_key or "").strip()
    if not key or key == "change-me":
        raise HTTPException(status_code=503, detail="admin_api_key_not_configured")
    if not hmac.compare_digest(x_api_key, key):
        raise HTTPException(status_code=401, detail="Invalid admin API key")


def require_user_id(
    x_user_id: str | None = Header(default=None),
    x_user_signature: str | None = Header(default=None),
    x_user_ts: str | None = Header(default=None),
) -> str:
    user_id = str(x_user_id or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Missing x-user-id")
    if not _USER_ID_RE.match(user_id):
        raise HTTPException(status_code=400, detail="Invalid x-user-id format")
    settings = get_settings()
    secret = str(settings.user_identity_hmac_secret or "").strip()
    if not secret and str(settings.app_env or "").strip().lower() == "prod":
        raise HTTPException(status_code=503, detail="user_identity_hmac_secret_not_configured")
    if secret:
        sig = str(x_user_signature or "").strip().lower()
        ts_raw = str(x_user_ts or "").strip()
        if not sig:
            raise HTTPException(status_code=401, detail="Missing x-user-signature")
        if not ts_raw:
            raise HTTPException(status_code=401, detail="Missing x-user-ts")
        try:
            ts = int(ts_raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid x-user-ts") from exc
        now = int(time.time())
        max_skew = max(1, int(settings.user_identity_max_skew_sec))
        if abs(now - ts) > max_skew:
            raise HTTPException(status_code=401, detail="Expired x-user signature")
        expected = hmac.new(
            secret.encode("utf-8"),
            f"{user_id}:{ts}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest().lower()
        if not hmac.compare_digest(sig, expected):
            raise HTTPException(status_code=401, detail="Invalid x-user-signature")
    return user_id


def require_admin_write_throttle(x_api_key: str = Header(default="")) -> None:
    """Minimal per-key write throttle to reduce accidental heavy POST storms."""
    settings = get_settings()
    min_interval_ms = max(0, int(settings.admin_write_min_interval_ms))
    if min_interval_ms <= 0:
        return
    now = time.monotonic()
    min_interval_s = float(min_interval_ms) / 1000.0
    key_fingerprint = hashlib.sha256(x_api_key.encode("utf-8")).hexdigest() if x_api_key else "__empty__"
    with _WRITE_THROTTLE_LOCK:
        _prune_throttle_map(_LAST_ADMIN_WRITE_TS_BY_KEY, now)
        last = _LAST_ADMIN_WRITE_TS_BY_KEY.get(key_fingerprint)
        if last is not None and (now - last) < min_interval_s:
            raise HTTPException(status_code=429, detail="Too many write requests")
        _LAST_ADMIN_WRITE_TS_BY_KEY[key_fingerprint] = now


def require_admin_read_throttle(x_api_key: str = Header(default="")) -> None:
    """Lightweight read throttle for heavy analytics/report endpoints."""
    settings = get_settings()
    min_interval_ms = max(0, int(settings.admin_read_min_interval_ms))
    if min_interval_ms <= 0:
        return
    now = time.monotonic()
    min_interval_s = float(min_interval_ms) / 1000.0
    key_fingerprint = hashlib.sha256(x_api_key.encode("utf-8")).hexdigest() if x_api_key else "__empty__"
    with _READ_THROTTLE_LOCK:
        _prune_throttle_map(_LAST_ADMIN_READ_TS_BY_KEY, now)
        last = _LAST_ADMIN_READ_TS_BY_KEY.get(key_fingerprint)
        if last is not None and (now - last) < min_interval_s:
            raise HTTPException(status_code=429, detail="Too many read requests")
        _LAST_ADMIN_READ_TS_BY_KEY[key_fingerprint] = now
