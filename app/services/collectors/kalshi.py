from datetime import UTC, datetime
import httpx

from app.core.config import get_settings
from app.schemas.collector import NormalizedMarketDTO
from app.services.collectors.base import BaseCollector
from app.utils.http import retry_request


class KalshiCollector(BaseCollector):
    platform_name = "KALSHI"

    @staticmethod
    def _parse_ts(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            token = str(value).replace("Z", "+00:00")
            dt = datetime.fromisoformat(token)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _pick_float(row: dict, keys: tuple[str, ...]) -> float | None:
        for key in keys:
            value = row.get(key)
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                try:
                    return float(value)
                except ValueError:
                    continue
        return None

    @staticmethod
    def _normalize_category(row: dict) -> str:
        category = str(row.get("category") or "").strip().lower()
        event_ticker = str(row.get("event_ticker") or row.get("eventTicker") or "").strip().lower()
        title = str(row.get("title") or row.get("question") or "").strip().lower()
        blob = " ".join((category, event_ticker, title))
        if any(token in blob for token in ("btc", "bitcoin", "eth", "crypto")):
            return "crypto"
        if any(token in blob for token in ("fed", "cpi", "gdp", "nfp", "inflation", "rate", "sp500", "nasdaq")):
            return "finance"
        if any(token in blob for token in ("election", "president", "senate", "house", "vote", "trump", "biden")):
            return "politics"
        if any(token in blob for token in ("nba", "nfl", "mlb", "nhl", "soccer", "football", "match", "game")):
            return "sports"
        return "other"

    def _headers(self) -> dict[str, str]:
        settings = get_settings()
        headers = {"Accept": "application/json"}
        if settings.kalshi_api_key:
            headers["Authorization"] = f"Bearer {settings.kalshi_api_key}"
        return headers

    def _fetch_live_markets(self) -> list[dict]:
        settings = get_settings()
        url = f"{settings.kalshi_api_base_url}/markets"
        resp = retry_request(
            lambda: httpx.get(url, params={"limit": 200, "status": "open"}, headers=self._headers(), timeout=20.0),
            retries=3,
            backoff_seconds=1.0,
            platform=self.platform_name,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        if isinstance(payload, dict):
            return list(payload.get("markets") or payload.get("results") or [])
        if isinstance(payload, list):
            return payload
        return []

    def _fetch_historical_cutoff(self) -> tuple[str | None, str | None]:
        settings = get_settings()
        url = f"{settings.kalshi_historical_api_base_url}/cutoff"
        try:
            resp = retry_request(
                lambda: httpx.get(url, headers=self._headers(), timeout=15.0),
                retries=2,
                backoff_seconds=1.0,
                platform=self.platform_name,
            )
            if resp.status_code != 200:
                return None, "kalshi_historical_cutoff_unavailable"
            payload = resp.json() or {}
            if isinstance(payload, dict):
                value = payload.get("cutoff") or payload.get("timestamp")
                if value:
                    return str(value), None
            return None, "kalshi_historical_cutoff_missing"
        except Exception:  # noqa: BLE001
            return None, "kalshi_historical_cutoff_request_failed"

    def _fetch_historical_markets(self, *, cutoff: str | None) -> tuple[list[dict], str | None]:
        settings = get_settings()
        url = f"{settings.kalshi_historical_api_base_url}/markets"
        params: dict[str, str | int] = {"limit": 200, "status": "settled"}
        if cutoff:
            params["after"] = cutoff
        try:
            resp = retry_request(
                lambda: httpx.get(url, params=params, headers=self._headers(), timeout=20.0),
                retries=2,
                backoff_seconds=1.0,
                platform=self.platform_name,
            )
            if resp.status_code != 200:
                return [], "kalshi_historical_markets_unavailable"
            payload = resp.json() or {}
            if isinstance(payload, dict):
                return list(payload.get("markets") or payload.get("results") or []), None
            if isinstance(payload, list):
                return payload, None
            return [], "kalshi_historical_markets_empty_payload"
        except Exception:  # noqa: BLE001
            return [], "kalshi_historical_markets_request_failed"

    def fetch_markets(self) -> list[NormalizedMarketDTO]:
        cutoff, cutoff_reason = self._fetch_historical_cutoff()
        rows = self._fetch_live_markets()
        historical_rows, historical_reason = self._fetch_historical_markets(cutoff=cutoff)
        if historical_rows:
            rows.extend(historical_rows)
        reason_codes: list[str] = []
        if cutoff_reason:
            reason_codes.append(cutoff_reason)
        if historical_reason:
            reason_codes.append(historical_reason)
        if not historical_rows:
            reason_codes.append("kalshi_historical_rows_empty")
        items: list[NormalizedMarketDTO] = []
        for row in rows:
            yes_bid = self._pick_float(row, ("yes_bid_dollars", "yes_bid", "yesBidDollars", "yesBid"))
            yes_ask = self._pick_float(row, ("yes_ask_dollars", "yes_ask", "yesAskDollars", "yesAsk"))
            prob = self._pick_float(row, ("last_price_dollars", "last_price", "lastPriceDollars", "yes_price"))
            if prob is None and isinstance(yes_ask, (int, float)):
                prob = float(yes_ask)
            spread_cents = None
            if isinstance(yes_bid, (int, float)) and isinstance(yes_ask, (int, float)):
                spread_cents = max(0.0, (float(yes_ask) - float(yes_bid)) * 100.0)
            payload = {
                **row,
                "execution_source": "kalshi_api",
                "spread_cents": spread_cents,
                "best_bid_yes": yes_bid,
                "best_ask_yes": yes_ask,
                "open_interest_fp": row.get("open_interest_fp"),
                "notional_value_dollars": row.get("notional_value_dollars"),
                "previous_yes_bid_dollars": row.get("previous_yes_bid_dollars"),
                "historical_cutoff": cutoff,
                "historical_reason_codes": reason_codes,
            }
            items.append(
                NormalizedMarketDTO(
                    platform=self.platform_name,
                    external_market_id=str(row.get("ticker") or row.get("id") or ""),
                    title=str(row.get("title") or row.get("question") or ""),
                    description=row.get("subtitle") or row.get("description"),
                    category=self._normalize_category(row),
                    url=row.get("url"),
                    status=str(row.get("status") or ""),
                    probability_yes=float(prob) if isinstance(prob, (int, float)) else None,
                    probability_no=(1.0 - float(prob)) if isinstance(prob, (int, float)) else None,
                    volume_24h=float(row.get("volume_24h_fp") or row.get("volume_24h") or row.get("volume") or 0.0),
                    liquidity_value=float(row.get("liquidity") or row.get("notional_value_dollars") or 0.0),
                    created_at=self._parse_ts(row.get("open_time") or row.get("created_at")),
                    resolution_time=self._parse_ts(row.get("close_time") or row.get("expiration_time")),
                    rules_text=row.get("rules") or row.get("rules_primary") or row.get("settlement_rules"),
                    source_payload=payload,
                )
            )
        return items
