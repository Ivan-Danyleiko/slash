from dateutil import parser
import httpx

from app.core.config import get_settings
from app.schemas.collector import NormalizedMarketDTO
from app.services.collectors.base import BaseCollector
from app.utils.http import retry_request


class MetaculusCollector(BaseCollector):
    platform_name = "METACULUS"

    def fetch_markets(self) -> list[NormalizedMarketDTO]:
        settings = get_settings()
        if not settings.metaculus_api_token:
            raise RuntimeError(
                "METACULUS_API_TOKEN is required: Metaculus API now requires authenticated access"
            )

        url = f"{settings.metaculus_api_base_url}/questions/"
        base_headers = {"User-Agent": settings.metaculus_user_agent, "Accept": "application/json"}
        auth_schemes = ["Token", "Bearer"]
        resp: httpx.Response | None = None
        last_error: Exception | None = None

        for scheme in auth_schemes:
            headers = {**base_headers, "Authorization": f"{scheme} {settings.metaculus_api_token}"}
            try:
                candidate = retry_request(
                    lambda: httpx.get(
                        url,
                        params={"limit": 100, "status": "open"},
                        headers=headers,
                        timeout=20.0,
                    ),
                    retries=3,
                    backoff_seconds=1.0,
                    platform=self.platform_name,
                )
                if candidate.status_code in (401, 403):
                    resp = candidate
                    continue
                candidate.raise_for_status()
                resp = candidate
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue

        if resp is None:
            if last_error:
                raise RuntimeError(f"Metaculus request failed: {last_error}") from last_error
            raise RuntimeError("Metaculus request failed: no response")
        if resp.status_code in (401, 403):
            detail = (resp.text or "").strip()[:240]
            raise RuntimeError(
                f"Metaculus auth rejected ({resp.status_code}). "
                "Check METACULUS_API_TOKEN validity/permissions. "
                f"Response: {detail}"
            )

        payload = resp.json()
        rows = payload.get("results", [])

        markets: list[NormalizedMarketDTO] = []
        for row in rows:
            cp = row.get("community_prediction", {})
            yes_prob = cp.get("full", {}).get("q2")
            markets.append(
                NormalizedMarketDTO(
                    platform=self.platform_name,
                    external_market_id=str(row.get("id")),
                    title=row.get("title", ""),
                    description=row.get("description"),
                    category=(row.get("categories") or [None])[0],
                    url=f"https://www.metaculus.com/questions/{row.get('id')}",
                    status=str(row.get("status")),
                    probability_yes=float(yes_prob) if isinstance(yes_prob, (float, int)) else None,
                    probability_no=(1 - float(yes_prob)) if isinstance(yes_prob, (float, int)) else None,
                    volume_24h=None,
                    liquidity_value=None,
                    created_at=parser.parse(row["created_time"]) if row.get("created_time") else None,
                    resolution_time=parser.parse(row["resolve_time"]) if row.get("resolve_time") else None,
                    rules_text=row.get("resolution_criteria"),
                    source_payload=row,
                )
            )
        return markets
