import httpx
import json

from app.core.config import get_settings
from app.schemas.collector import NormalizedMarketDTO
from app.services.collectors.base import BaseCollector
from app.utils.http import retry_request


class PolymarketCollector(BaseCollector):
    """Partial integration. Uses Gamma markets endpoint where fields may vary."""

    platform_name = "POLYMARKET"

    @staticmethod
    def _as_list(value: object) -> list:
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return parsed
            except Exception:  # noqa: BLE001
                return []
        return []

    @classmethod
    def _extract_probability_yes(cls, row: dict) -> float | None:
        direct = row.get("probability")
        if isinstance(direct, (float, int)):
            return float(direct)

        outcomes = cls._as_list(row.get("outcomes"))
        prices = cls._as_list(row.get("outcomePrices"))
        if not prices:
            return None
        numeric_prices: list[float] = []
        for p in prices:
            try:
                numeric_prices.append(float(p))
            except (TypeError, ValueError):
                continue
        if not numeric_prices:
            return None

        yes_idx = None
        for idx, outcome in enumerate(outcomes):
            token = str(outcome).strip().lower()
            if token in {"yes", "true", "1"}:
                yes_idx = idx
                break
        if yes_idx is None:
            yes_idx = 0 if len(numeric_prices) == 2 else None
        if yes_idx is None or yes_idx >= len(numeric_prices):
            return None
        yes_prob = numeric_prices[yes_idx]
        if 0.0 <= yes_prob <= 1.0:
            return yes_prob
        return None

    @staticmethod
    def _pick_float(row: dict, keys: tuple[str, ...]) -> float | None:
        for key in keys:
            value = row.get(key)
            if isinstance(value, (float, int)):
                return float(value)
            if isinstance(value, str):
                try:
                    return float(value)
                except ValueError:
                    continue
        return None

    @staticmethod
    def _extract_token_id(row: dict) -> str | None:
        for key in ("clobTokenId", "clob_token_id", "token_id", "yes_token_id", "yesTokenId"):
            value = row.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        token_ids = row.get("clobTokenIds") or row.get("clob_token_ids")
        if isinstance(token_ids, list) and token_ids:
            return str(token_ids[0]).strip()
        if isinstance(token_ids, str):
            parsed = PolymarketCollector._as_list(token_ids)
            if parsed:
                return str(parsed[0]).strip()
        return None

    def _fetch_clob_top(self, *, token_id: str, settings) -> tuple[float | None, float | None, str | None]:
        url = f"{settings.polymarket_clob_api_base_url}/order-book/{token_id}"
        headers = {"Accept": "application/json"}
        if settings.polymarket_clob_api_key:
            headers["Authorization"] = f"Bearer {settings.polymarket_clob_api_key}"
        try:
            resp = retry_request(
                lambda: httpx.get(url, headers=headers, timeout=10.0),
                retries=2,
                backoff_seconds=0.5,
                platform=self.platform_name,
            )
            if resp.status_code != 200:
                return None, None, "clob_http_non_200"
            payload = resp.json() or {}
            bids = payload.get("bids") if isinstance(payload, dict) else None
            asks = payload.get("asks") if isinstance(payload, dict) else None
            bid = None
            ask = None
            if isinstance(bids, list) and bids:
                top_bid = bids[0]
                if isinstance(top_bid, list) and top_bid:
                    bid = self._pick_float({"v": top_bid[0]}, ("v",))
                elif isinstance(top_bid, dict):
                    bid = self._pick_float(top_bid, ("price", "p"))
            if isinstance(asks, list) and asks:
                top_ask = asks[0]
                if isinstance(top_ask, list) and top_ask:
                    ask = self._pick_float({"v": top_ask[0]}, ("v",))
                elif isinstance(top_ask, dict):
                    ask = self._pick_float(top_ask, ("price", "p"))
            if bid is None or ask is None:
                return None, None, "clob_orderbook_empty"
            if ask < bid:
                return None, None, "clob_invalid_spread"
            return bid, ask, None
        except Exception:  # noqa: BLE001
            return None, None, "clob_request_failed"

    def fetch_markets(self) -> list[NormalizedMarketDTO]:
        settings = get_settings()
        url = f"{settings.polymarket_api_base_url}/markets"
        resp = retry_request(
            lambda: httpx.get(url, params={"limit": 100}, timeout=20.0),
            retries=3,
            backoff_seconds=1.0,
            platform=self.platform_name,
        )
        resp.raise_for_status()
        rows = resp.json()

        markets: list[NormalizedMarketDTO] = []
        for row in rows:
            prob = self._extract_probability_yes(row)
            payload = {**row, "execution_source": "gamma_api"}
            is_neg_risk = row.get("negRisk") if isinstance(row.get("negRisk"), bool) else row.get("neg_risk")
            if isinstance(is_neg_risk, bool):
                payload["neg_risk"] = is_neg_risk
            if isinstance(row.get("openInterest"), (int, float)):
                payload["openInterest"] = row.get("openInterest")
            if isinstance(row.get("notionalValue"), (int, float)):
                payload["notionalValueDollars"] = row.get("notionalValue")

            # Stage 9 CLOB mode: prefer explicit bid/ask when available;
            # fallback to gamma-only mode with a reason marker.
            if settings.polymarket_clob_enabled:
                token_id = self._extract_token_id(row)
                if token_id:
                    bid, ask, clob_reason = self._fetch_clob_top(token_id=token_id, settings=settings)
                else:
                    bid, ask, clob_reason = None, None, "clob_token_missing"
                if bid is not None and ask is not None and ask >= bid:
                    payload["execution_source"] = "clob_api"
                    payload["best_bid_yes"] = bid
                    payload["best_ask_yes"] = ask
                    payload["spread_cents"] = max(0.0, (ask - bid) * 100.0)
                else:
                    payload["clob_unavailable_fallback_gamma"] = True
                    if clob_reason:
                        payload["clob_reason_code"] = clob_reason

            markets.append(
                NormalizedMarketDTO(
                    platform=self.platform_name,
                    external_market_id=str(row.get("id")),
                    title=row.get("question", ""),
                    description=row.get("description"),
                    category=row.get("category"),
                    url=row.get("url"),
                    status=row.get("status"),
                    probability_yes=float(prob) if isinstance(prob, (float, int)) else None,
                    probability_no=(1 - float(prob)) if isinstance(prob, (float, int)) else None,
                    volume_24h=float(row.get("volume24h") or row.get("volume24hr") or row.get("volumeNum") or 0),
                    liquidity_value=float(row.get("liquidity") or row.get("liquidityNum") or 0),
                    rules_text=row.get("rules"),
                    source_payload=payload,
                )
            )
        return markets
