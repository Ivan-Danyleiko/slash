from __future__ import annotations

import hashlib
import hmac
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import deps


def _settings(*, app_env: str = "dev", secret: str = "", skew: int = 300) -> SimpleNamespace:
    return SimpleNamespace(
        app_env=app_env,
        user_identity_hmac_secret=secret,
        user_identity_max_skew_sec=skew,
    )


def test_require_user_id_rejects_missing() -> None:
    with pytest.raises(HTTPException) as exc:
        deps.require_user_id(None)
    assert exc.value.status_code == 401


def test_require_user_id_rejects_bad_format() -> None:
    with pytest.raises(HTTPException) as exc:
        deps.require_user_id("abc")
    assert exc.value.status_code == 400


def test_require_user_id_prod_requires_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "get_settings", lambda: _settings(app_env="prod", secret=""))
    with pytest.raises(HTTPException) as exc:
        deps.require_user_id("123456")
    assert exc.value.status_code == 503
    assert exc.value.detail == "user_identity_hmac_secret_not_configured"


def test_require_user_id_hmac_accepts_valid_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "test-secret"
    monkeypatch.setattr(deps, "get_settings", lambda: _settings(app_env="prod", secret=secret, skew=300))
    ts = int(time.time())
    uid = "123456789"
    sig = hmac.new(secret.encode("utf-8"), f"{uid}:{ts}".encode("utf-8"), hashlib.sha256).hexdigest()
    assert deps.require_user_id(uid, sig, str(ts)) == uid


def test_require_user_id_hmac_rejects_invalid_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "test-secret"
    monkeypatch.setattr(deps, "get_settings", lambda: _settings(app_env="prod", secret=secret, skew=300))
    ts = int(time.time())
    with pytest.raises(HTTPException) as exc:
        deps.require_user_id("123456789", "deadbeef", str(ts))
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid x-user-signature"


def test_require_user_id_hmac_rejects_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "test-secret"
    monkeypatch.setattr(deps, "get_settings", lambda: _settings(app_env="prod", secret=secret, skew=10))
    ts = int(time.time()) - 100
    uid = "123456789"
    sig = hmac.new(secret.encode("utf-8"), f"{uid}:{ts}".encode("utf-8"), hashlib.sha256).hexdigest()
    with pytest.raises(HTTPException) as exc:
        deps.require_user_id(uid, sig, str(ts))
    assert exc.value.status_code == 401
    assert exc.value.detail == "Expired x-user signature"
