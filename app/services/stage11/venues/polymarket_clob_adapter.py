from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from typing import Any

import httpx

from app.core.config import Settings
from app.core.secrets import redact_text
from app.services.stage11.venues.base import (
    Stage11PlaceRequest,
    Stage11PlaceResult,
    Stage11StatusResult,
)


class PolymarketClobAdapter:
    def __init__(self, *, settings: Settings) -> None:
        self.settings = settings

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.polymarket_clob_api_key:
            headers["Authorization"] = f"Bearer {self.settings.polymarket_clob_api_key}"
        return headers

    def _is_dry_run(self) -> bool:
        return bool(getattr(self.settings, "stage11_venue_dry_run", True))

    def _sdk_enabled(self) -> bool:
        return bool(getattr(self.settings, "stage11_clob_use_sdk", False))

    def _sdk_client(self):  # type: ignore[no-untyped-def]
        if not self._sdk_enabled():
            return None
        private_key = str(getattr(self.settings, "stage11_clob_private_key", "") or "").strip()
        if not private_key:
            return None
        try:
            from py_clob_client.client import ClobClient  # type: ignore
        except Exception:  # noqa: BLE001
            return None
        kwargs: dict[str, Any] = {
            "host": str(self.settings.polymarket_clob_api_base_url),
            "key": private_key,
            "chain_id": int(getattr(self.settings, "stage11_clob_chain_id", 137)),
        }
        funder = str(getattr(self.settings, "stage11_clob_funder_address", "") or "").strip()
        if funder:
            kwargs["funder"] = funder
        try:
            client = ClobClient(**kwargs)
            if hasattr(client, "create_or_derive_api_creds") and hasattr(client, "set_api_creds"):
                try:
                    creds = client.create_or_derive_api_creds()
                    client.set_api_creds(creds)
                except Exception:  # noqa: BLE001
                    pass
            return client
        except Exception:  # noqa: BLE001
            return None

    def place_order(self, req: Stage11PlaceRequest) -> Stage11PlaceResult:
        if self._is_dry_run():
            synthetic = hashlib.sha256(
                f"{req.idempotency_key}:{req.order_id}:{datetime.now(UTC).isoformat()}".encode("utf-8")
            ).hexdigest()[:24]
            return Stage11PlaceResult(
                status="SUBMITTED",
                venue_order_id=f"dry_{synthetic}",
                response_payload={
                    "adapter_mode": "dry_run",
                    "venue": "POLYMARKET_CLOB",
                    "synthetic_order_id": f"dry_{synthetic}",
                },
            )

        # Live mode is best-effort and safe-fail; adapter never throws to caller.
        try:
            sdk = self._sdk_client()
            if sdk is not None:
                try:
                    # py-clob-client path (signed order submission).
                    from py_clob_client.clob_types import OrderArgs, OrderType  # type: ignore

                    # Convert requested_price (0..1) into cents integer when SDK expects price in cents.
                    price_cents = None
                    if req.requested_price is not None:
                        price_cents = max(1, min(99, int(round(float(req.requested_price) * 100.0))))
                    size = float(req.notional_usd or 0.0)
                    if size <= 0.0:
                        return Stage11PlaceResult(
                            status="FAILED",
                            venue_order_id=None,
                            response_payload={"adapter_mode": "sdk", "error": "invalid_size"},
                            error="invalid_size",
                        )
                    order_args = OrderArgs(
                        token_id=str(req.market_id),
                        side=str(req.side).upper(),
                        size=size,
                        price=price_cents if price_cents is not None else 50,
                    )
                    signed_order = sdk.create_order(order_args)
                    resp = sdk.post_order(signed_order, OrderType.GTC)
                    data = resp if isinstance(resp, dict) else {"raw": str(resp)}
                    venue_order_id = str(data.get("orderID") or data.get("order_id") or data.get("id") or "")
                    if not venue_order_id:
                        return Stage11PlaceResult(
                            status="UNKNOWN_SUBMIT",
                            venue_order_id=None,
                            response_payload={"adapter_mode": "sdk", **data},
                            error="missing_order_id",
                        )
                    return Stage11PlaceResult(
                        status="SUBMITTED",
                        venue_order_id=venue_order_id,
                        response_payload={"adapter_mode": "sdk", **data},
                    )
                except Exception as exc:  # noqa: BLE001
                    # SDK path failed, continue with HTTP fallback.
                    pass

            payload: dict[str, Any] = {
                "market_id": req.market_id,
                "side": req.side.upper(),
                "notional_usd": float(req.notional_usd),
                "limit_price": req.requested_price,
                "client_order_id": req.idempotency_key,
            }
            with httpx.Client(timeout=12.0) as client:
                resp = client.post(
                    f"{self.settings.polymarket_clob_api_base_url}/orders",
                    headers=self._headers(),
                    json=payload,
                )
                if resp.status_code in (404, 405):
                    resp = client.post(
                        f"{self.settings.polymarket_clob_api_base_url}/order",
                        headers=self._headers(),
                        json=payload,
                    )
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"text": resp.text[:1000]}
            if resp.status_code >= 500:
                return Stage11PlaceResult(status="UNKNOWN_SUBMIT", venue_order_id=None, response_payload=data, error="venue_5xx")
            if resp.status_code >= 400:
                return Stage11PlaceResult(status="FAILED", venue_order_id=None, response_payload=data, error=f"http_{resp.status_code}")
            venue_order_id = str(data.get("order_id") or data.get("id") or "")
            if not venue_order_id:
                return Stage11PlaceResult(status="UNKNOWN_SUBMIT", venue_order_id=None, response_payload=data, error="missing_order_id")
            return Stage11PlaceResult(status="SUBMITTED", venue_order_id=venue_order_id, response_payload=data)
        except Exception as exc:  # noqa: BLE001
            return Stage11PlaceResult(
                status="UNKNOWN_SUBMIT",
                venue_order_id=None,
                response_payload={"error": redact_text(str(exc)), "adapter_mode": "live"},
                error="request_failed",
            )

    def cancel_order(self, venue_order_id: str) -> Stage11StatusResult:
        if self._is_dry_run():
            return Stage11StatusResult(
                status="CANCELLED_SAFE",
                response_payload={"adapter_mode": "dry_run", "venue_order_id": venue_order_id},
            )
        try:
            sdk = self._sdk_client()
            if sdk is not None:
                try:
                    if hasattr(sdk, "cancel"):
                        resp = sdk.cancel(venue_order_id)
                    elif hasattr(sdk, "cancel_order"):
                        resp = sdk.cancel_order(venue_order_id)
                    else:
                        resp = {"status": "unknown"}
                    data = resp if isinstance(resp, dict) else {"raw": str(resp)}
                    return Stage11StatusResult(status="CANCELLED_SAFE", response_payload={"adapter_mode": "sdk", **data})
                except Exception:  # noqa: BLE001
                    pass
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(
                    f"{self.settings.polymarket_clob_api_base_url}/order/{venue_order_id}/cancel",
                    headers=self._headers(),
                )
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"text": resp.text[:1000]}
            if resp.status_code >= 400:
                return Stage11StatusResult(status="UNKNOWN_SUBMIT", response_payload=data, error=f"http_{resp.status_code}")
            return Stage11StatusResult(status="CANCELLED_SAFE", response_payload=data)
        except Exception as exc:  # noqa: BLE001
            return Stage11StatusResult(
                status="UNKNOWN_SUBMIT",
                response_payload={"error": redact_text(str(exc))},
                error="cancel_failed",
            )

    def fetch_order_status(self, venue_order_id: str) -> Stage11StatusResult:
        if self._is_dry_run():
            return Stage11StatusResult(
                status="FILLED",
                response_payload={"adapter_mode": "dry_run", "venue_order_id": venue_order_id},
                fill_price=0.5,
                fill_size_usd=100.0,
                fee_usd=0.0,
            )
        try:
            sdk = self._sdk_client()
            if sdk is not None:
                try:
                    if hasattr(sdk, "get_order"):
                        resp = sdk.get_order(venue_order_id)
                    elif hasattr(sdk, "get_order_by_id"):
                        resp = sdk.get_order_by_id(venue_order_id)
                    else:
                        resp = {"status": "unknown"}
                    data = resp if isinstance(resp, dict) else {"raw": str(resp)}
                    raw_status = str(data.get("status") or "").upper()
                    filled_notional = data.get("filled_notional_usd") or data.get("filledSize")
                    fee = data.get("fee_usd") or data.get("fee")
                    if raw_status in {"FILLED", "EXECUTED"}:
                        return Stage11StatusResult(
                            status="FILLED",
                            response_payload={"adapter_mode": "sdk", **data},
                            fill_price=(float(data.get("avg_price")) if data.get("avg_price") is not None else None),
                            fill_size_usd=(float(filled_notional) if filled_notional is not None else None),
                            fee_usd=(float(fee) if fee is not None else 0.0),
                        )
                    if raw_status in {"CANCELLED", "CANCELED"}:
                        return Stage11StatusResult(status="CANCELLED_SAFE", response_payload={"adapter_mode": "sdk", **data})
                    if raw_status in {"OPEN", "PARTIAL", "PARTIALLY_FILLED"}:
                        return Stage11StatusResult(
                            status="SUBMITTED",
                            response_payload={"adapter_mode": "sdk", **data},
                            fill_size_usd=(float(filled_notional) if filled_notional is not None else None),
                            fee_usd=(float(fee) if fee is not None else 0.0),
                            is_partial=raw_status in {"PARTIAL", "PARTIALLY_FILLED"},
                        )
                except Exception:  # noqa: BLE001
                    pass
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(
                    f"{self.settings.polymarket_clob_api_base_url}/order/{venue_order_id}",
                    headers=self._headers(),
                )
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"text": resp.text[:1000]}
            if resp.status_code >= 500:
                return Stage11StatusResult(status="UNKNOWN_SUBMIT", response_payload=data, error="venue_5xx")
            if resp.status_code >= 400:
                return Stage11StatusResult(status="UNKNOWN_SUBMIT", response_payload=data, error=f"http_{resp.status_code}")

            raw_status = str(data.get("status") or "").upper()
            filled_notional = data.get("filled_notional_usd") or data.get("filledSize")
            fee = data.get("fee_usd") or data.get("fee")
            if raw_status in {"FILLED", "EXECUTED"}:
                return Stage11StatusResult(
                    status="FILLED",
                    response_payload=data,
                    fill_price=(float(data.get("avg_price")) if data.get("avg_price") is not None else None),
                    fill_size_usd=(float(filled_notional) if filled_notional is not None else None),
                    fee_usd=(float(fee) if fee is not None else 0.0),
                )
            if raw_status in {"CANCELLED", "CANCELED"}:
                return Stage11StatusResult(status="CANCELLED_SAFE", response_payload=data)
            if raw_status in {"OPEN", "PARTIAL", "PARTIALLY_FILLED"}:
                return Stage11StatusResult(
                    status="SUBMITTED",
                    response_payload=data,
                    fill_size_usd=(float(filled_notional) if filled_notional is not None else None),
                    fee_usd=(float(fee) if fee is not None else 0.0),
                    is_partial=raw_status in {"PARTIAL", "PARTIALLY_FILLED"},
                )
            return Stage11StatusResult(status="UNKNOWN_SUBMIT", response_payload=data, error="unknown_status")
        except Exception as exc:  # noqa: BLE001
            return Stage11StatusResult(
                status="UNKNOWN_SUBMIT",
                response_payload={"error": redact_text(str(exc))},
                error="status_failed",
            )
