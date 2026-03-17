import httpx
import json
from datetime import datetime, timezone

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
        # Correct endpoint: /book?token_id=... (not /order-book/{id})
        url = f"{settings.polymarket_clob_api_base_url}/book"
        headers = {"Accept": "application/json"}
        if settings.polymarket_clob_api_key:
            headers["Authorization"] = f"Bearer {settings.polymarket_clob_api_key}"
        try:
            resp = retry_request(
                lambda: httpx.get(url, params={"token_id": token_id}, headers=headers, timeout=10.0),
                retries=2,
                backoff_seconds=0.5,
                platform=self.platform_name,
            )
            if resp.status_code != 200:
                return None, None, "clob_http_non_200"
            payload = resp.json() or {}
            bids = payload.get("bids") if isinstance(payload, dict) else None
            asks = payload.get("asks") if isinstance(payload, dict) else None
            # CLOB returns bids ascending (best bid = last) and asks descending (best ask = last)
            bid = None
            ask = None
            if isinstance(bids, list) and bids:
                best_bid = bids[-1]
                if isinstance(best_bid, dict):
                    bid = self._pick_float(best_bid, ("price", "p"))
            if isinstance(asks, list) and asks:
                best_ask = asks[-1]
                if isinstance(best_ask, dict):
                    ask = self._pick_float(best_ask, ("price", "p"))
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
        # Fetch up to 2000 active markets with pagination
        rows: list[dict] = []
        offset = 0
        page_size = 100
        while len(rows) < 2000:
            resp = retry_request(
                lambda: httpx.get(url, params={  # noqa: B023
                    "limit": page_size,
                    "offset": offset,
                    "active": "true",
                    "closed": "false",
                }, timeout=20.0),
                retries=3,
                backoff_seconds=1.0,
                platform=self.platform_name,
            )
            resp.raise_for_status()
            page = resp.json()
            if not page:
                break
            rows.extend(page)
            if len(page) < page_size:
                break
            offset += page_size

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

            # Stage 9 CLOB mode: fetch real bid/ask only for markets with real liquidity
            # (> $500) to avoid 500 sequential HTTP calls per sync cycle.
            clob_eligible = (
                settings.polymarket_clob_enabled
                and float(row.get("liquidityNum") or row.get("liquidity") or 0) >= 500
            )
            if clob_eligible:
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

            # Parse resolution time from endDate field
            resolution_time: datetime | None = None
            end_date_raw = row.get("endDate") or row.get("endDateIso")
            if end_date_raw:
                try:
                    resolution_time = datetime.fromisoformat(
                        str(end_date_raw).replace("Z", "+00:00")
                    )
                    if resolution_time.tzinfo is None:
                        resolution_time = resolution_time.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    pass

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
                    resolution_time=resolution_time,
                    source_payload=payload,
                )
            )
        return markets
