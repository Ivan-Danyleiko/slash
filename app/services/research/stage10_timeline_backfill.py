from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models.models import Market, Platform


def _payload_has_list(payload: dict[str, Any] | None, key: str) -> bool:
    if not isinstance(payload, dict):
        return False
    value = payload.get(key)
    return isinstance(value, list) and len(value) > 0


def _as_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC).isoformat()
        return value.astimezone(UTC).isoformat()
    return str(value)


def build_stage10_timeline_backfill_plan(
    db: Session,
    *,
    days: int = 730,
    limit: int = 500,
) -> dict[str, Any]:
    cutoff = datetime.now(UTC) - timedelta(days=max(1, int(days)))
    try:
        markets = list(
            db.execute(
                select(Market, Platform.name)
                .join(Platform, Platform.id == Market.platform_id)
                .where(Market.fetched_at >= cutoff)
                .order_by(Market.fetched_at.desc())
                .limit(max(50, int(limit)))
            ).all()
        )
    except OperationalError:
        inspector = sa_inspect(db.get_bind())
        market_cols = {str(c.get("name")) for c in inspector.get_columns("markets")}
        platform_cols = {str(c.get("name")) for c in inspector.get_columns("platforms")}
        wanted_market = ["id", "external_market_id", "title", "source_payload", "fetched_at", "platform_id"]
        select_market: list[str] = []
        for name in wanted_market:
            if name in market_cols:
                select_market.append(f"m.{name} as {name}")
            else:
                select_market.append(f"NULL as {name}")
        platform_name_expr = "p.name as platform_name" if "name" in platform_cols else "'UNKNOWN' as platform_name"
        stmt = text(
            f"SELECT {', '.join(select_market)}, {platform_name_expr} "  # noqa: S608
            "FROM markets m LEFT JOIN platforms p ON p.id = m.platform_id "
            "WHERE m.fetched_at >= :cutoff ORDER BY m.fetched_at DESC LIMIT :limit"
        )
        raw_rows = list(
            db.execute(stmt, {"cutoff": cutoff.isoformat(), "limit": max(50, int(limit))}).mappings()
        )
        markets = []
        for r in raw_rows:
            market_obj = SimpleNamespace(
                id=r.get("id"),
                external_market_id=r.get("external_market_id"),
                title=r.get("title"),
                source_payload=r.get("source_payload"),
                fetched_at=r.get("fetched_at"),
            )
            markets.append((market_obj, r.get("platform_name")))

    totals: dict[str, int] = {}
    ready: dict[str, int] = {}
    missing: dict[str, int] = {}
    candidates: dict[str, list[dict[str, Any]]] = {}

    for market, platform_name in markets:
        platform = str(platform_name or "UNKNOWN").upper()
        totals[platform] = totals.get(platform, 0) + 1

        payload = market.source_payload if isinstance(market.source_payload, dict) else {}
        has_timeline = False
        if platform == "MANIFOLD":
            has_timeline = _payload_has_list(payload, "manifold_bets_history")
        elif platform == "METACULUS":
            has_timeline = _payload_has_list(payload, "metaculus_prediction_history")
        elif platform == "POLYMARKET":
            # Conservative default: require explicit timeline data. Snapshot availability
            # is validated in replay (MarketSnapshot <= replay timestamp), not here.
            has_timeline = _payload_has_list(payload, "polymarket_price_history")

        if has_timeline:
            ready[platform] = ready.get(platform, 0) + 1
        else:
            missing[platform] = missing.get(platform, 0) + 1
            arr = candidates.setdefault(platform, [])
            if len(arr) < 25:
                arr.append(
                    {
                        "market_id": int(market.id),
                        "external_market_id": str(market.external_market_id),
                        "title": str(market.title),
                        "fetched_at": _as_iso(market.fetched_at),
                    }
                )

    platforms = sorted(set(list(totals.keys()) + list(ready.keys()) + list(missing.keys())))
    readiness: dict[str, float] = {}
    for p in platforms:
        t = float(totals.get(p, 0) or 0.0)
        r = float(ready.get(p, 0) or 0.0)
        readiness[p] = (r / t) if t > 0 else 0.0

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "window_days": int(days),
        "markets_scanned": len(markets),
        "totals_by_platform": totals,
        "timeline_ready_by_platform": ready,
        "timeline_missing_by_platform": missing,
        "timeline_readiness_by_platform": readiness,
        "backfill_candidates": candidates,
    }


def extract_stage10_timeline_backfill_metrics(report: dict[str, Any]) -> dict[str, float]:
    readiness = dict(report.get("timeline_readiness_by_platform") or {})
    manifold = float(readiness.get("MANIFOLD") or 0.0)
    metaculus = float(readiness.get("METACULUS") or 0.0)
    return {
        "stage10_backfill_markets_scanned": float(report.get("markets_scanned") or 0.0),
        "stage10_backfill_manifold_readiness": manifold,
        "stage10_backfill_metaculus_readiness": metaculus,
    }
