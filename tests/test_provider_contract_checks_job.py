from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.tasks.jobs import provider_contract_checks_job


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


class _Resp:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = "err"

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, get_fn, timeout=None):
        self._get_fn = get_fn

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get(self, url, params=None, headers=None, timeout=None):  # noqa: ANN001
        return self._get_fn(url, params=params, headers=headers, timeout=timeout)


def test_provider_contract_checks_job_all_ok(monkeypatch) -> None:
    db = _session()

    def _fake_get(url, params=None, headers=None, timeout=None):  # noqa: ANN001
        if "questions" in url:
            return _Resp(200, {"results": []})
        return _Resp(200, [])

    monkeypatch.setattr("app.tasks.jobs.httpx.Client", lambda **kw: _FakeClient(_fake_get))
    out = provider_contract_checks_job(db)
    assert out["status"] == "ok"
    result = out["result"]
    assert result["checks_failed"] == 0
    assert result["checks_total"] >= 2


def test_provider_contract_checks_job_handles_failures(monkeypatch) -> None:
    db = _session()

    def _fake_get(url, params=None, headers=None, timeout=None):  # noqa: ANN001
        if "manifold" in url:
            return _Resp(500, {"error": "x"})
        return _Resp(200, [])

    monkeypatch.setattr("app.tasks.jobs.httpx.Client", lambda **kw: _FakeClient(_fake_get))
    out = provider_contract_checks_job(db)
    assert out["status"] == "error"
    assert out["result"]["checks_failed"] >= 1
