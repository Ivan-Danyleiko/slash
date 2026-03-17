from app.services.collectors.polymarket import PolymarketCollector
from app.core.config import get_settings


class _FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


def test_polymarket_clob_enabled_with_bid_ask_uses_clob_mode(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("POLYMARKET_CLOB_ENABLED", "true")
    get_settings.cache_clear()

    def _fake_get(url, **kwargs):  # noqa: ANN001
        if str(url).endswith("/markets"):
            return _FakeResponse(
                200,
                [
                    {
                        "id": "pm-1",
                        "question": "Will BTC hit 120k?",
                        "status": "open",
                        "probability": 0.61,
                        "volume24h": 10000,
                        "liquidity": 50000,
                        "clobTokenId": "token-1",
                    }
                ],
            )
        if "/book" in str(url):
            return _FakeResponse(200, {"bids": [{"price": "0.60", "size": "10"}], "asks": [{"price": "0.62", "size": "8"}]})
        return _FakeResponse(404, {})

    monkeypatch.setattr("app.services.collectors.polymarket.httpx.get", _fake_get)
    rows = PolymarketCollector().fetch_markets()
    assert len(rows) == 1
    payload = rows[0].source_payload or {}
    assert payload.get("execution_source") == "clob_api"
    assert float(payload.get("spread_cents") or 0.0) > 0.0
    assert "clob_unavailable_fallback_gamma" not in payload
    get_settings.cache_clear()


def test_polymarket_clob_enabled_without_bid_ask_falls_back_to_gamma(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("POLYMARKET_CLOB_ENABLED", "true")
    get_settings.cache_clear()

    def _fake_get(url, **kwargs):  # noqa: ANN001
        return _FakeResponse(
            200,
            [
                {
                    "id": "pm-2",
                    "question": "Will ETH hit 10k?",
                    "status": "open",
                    "probability": 0.42,
                    "volume24h": 9000,
                    "liquidity": 40000,
                }
            ],
        )

    monkeypatch.setattr("app.services.collectors.polymarket.httpx.get", _fake_get)
    rows = PolymarketCollector().fetch_markets()
    assert len(rows) == 1
    payload = rows[0].source_payload or {}
    assert payload.get("execution_source") == "gamma_api"
    assert payload.get("clob_unavailable_fallback_gamma") is True
    assert payload.get("clob_reason_code") == "clob_token_missing"
    get_settings.cache_clear()
