#!/usr/bin/env python3
from __future__ import annotations

import argparse
from bisect import bisect_left
from datetime import UTC, datetime, timedelta
import json
import re
from typing import Any

import httpx
from sqlalchemy import and_, create_engine, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import Market, Platform, SignalHistory
from app.utils.http import retry_request


def _to_dt_ms(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000, tz=UTC)
    except (TypeError, ValueError):
        return None


def _to_hour_bucket(ts: datetime) -> datetime:
    return ts.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def _norm_title(title: str | None) -> str:
    raw = (title or "").strip().lower()
    raw = re.sub(r"\s+", " ", raw)
    return raw


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=True)
        except Exception:
            return str(value)
    return str(value)


def _ensure_platform(db: Session, name: str, base_url: str) -> Platform:
    platform = db.scalar(select(Platform).where(Platform.name == name))
    if platform:
        return platform
    platform = Platform(name=name, base_url=base_url)
    db.add(platform)
    db.commit()
    db.refresh(platform)
    return platform


def _fetch_manifold_markets(base_url: str, *, limit: int, before: str | None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"limit": limit}
    if before is not None:
        params["before"] = before
    resp = retry_request(
        lambda: httpx.get(f"{base_url}/markets", params=params, timeout=30.0),
        retries=3,
        backoff_seconds=1.0,
        platform="MANIFOLD",
    )
    resp.raise_for_status()
    payload = resp.json()
    return payload if isinstance(payload, list) else []


def _fetch_manifold_bets(base_url: str, *, limit: int, before: str | None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"limit": limit}
    if before is not None:
        params["before"] = before
    resp = retry_request(
        lambda: httpx.get(f"{base_url}/bets", params=params, timeout=30.0),
        retries=3,
        backoff_seconds=1.0,
        platform="MANIFOLD",
    )
    resp.raise_for_status()
    payload = resp.json()
    return payload if isinstance(payload, list) else []


def _fetch_manifold_market_by_id(base_url: str, market_id: str) -> dict[str, Any] | None:
    resp = retry_request(
        lambda: httpx.get(f"{base_url}/market/{market_id}", timeout=30.0),
        retries=2,
        backoff_seconds=0.5,
        platform="MANIFOLD",
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    payload = resp.json()
    return payload if isinstance(payload, dict) else None


def _fetch_metaculus_questions(
    base_url: str,
    *,
    token: str,
    user_agent: str,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    headers = {"Authorization": f"Token {token}", "Accept": "application/json", "User-Agent": user_agent}
    resp = retry_request(
        lambda: httpx.get(
            f"{base_url}/questions/",
            params={"limit": limit, "offset": offset},
            headers=headers,
            timeout=30.0,
        ),
        retries=3,
        backoff_seconds=1.0,
        platform="METACULUS",
    )
    resp.raise_for_status()
    payload = resp.json()
    return payload if isinstance(payload, dict) else {"results": []}


def _fetch_polymarket_markets(base_url: str, *, limit: int, offset: int) -> list[dict[str, Any]]:
    resp = retry_request(
        lambda: httpx.get(
            f"{base_url}/markets",
            params={"limit": limit, "offset": offset},
            timeout=30.0,
        ),
        retries=3,
        backoff_seconds=1.0,
        platform="POLYMARKET",
    )
    resp.raise_for_status()
    payload = resp.json()
    return payload if isinstance(payload, list) else []


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            return []
    return []


def _extract_polymarket_probability_yes(row: dict[str, Any]) -> float | None:
    direct = row.get("probability")
    if isinstance(direct, (float, int)):
        v = float(direct)
        return v if 0.0 <= v <= 1.0 else None
    outcomes = _as_list(row.get("outcomes"))
    prices = _as_list(row.get("outcomePrices"))
    numeric: list[float] = []
    for p in prices:
        try:
            numeric.append(float(p))
        except (TypeError, ValueError):
            continue
    if not numeric:
        return None
    yes_idx = None
    for idx, outcome in enumerate(outcomes):
        token = str(outcome).strip().lower()
        if token in {"yes", "true", "1"}:
            yes_idx = idx
            break
    if yes_idx is None:
        yes_idx = 0 if len(numeric) == 2 else None
    if yes_idx is None or yes_idx >= len(numeric):
        return None
    val = numeric[yes_idx]
    return val if 0.0 <= val <= 1.0 else None


def _upsert_market_generic(
    db: Session,
    *,
    platform_id: int,
    external_market_id: str,
    title: str,
    description: str | None,
    category: str | None,
    url: str | None,
    status: str | None,
    probability_yes: float | None,
    volume_24h: float | None,
    liquidity_value: float | None,
    created_at: datetime | None,
    resolution_time: datetime | None,
    rules_text: str | None,
    source_payload: dict[str, Any],
) -> Market:
    market = db.scalar(
        select(Market).where(
            Market.platform_id == platform_id,
            Market.external_market_id == str(external_market_id),
        )
    )
    probability_no = (1 - probability_yes) if isinstance(probability_yes, (int, float)) else None
    if market is None:
        market = Market(
            platform_id=platform_id,
            external_market_id=str(external_market_id),
            title=title or f"market_{external_market_id}",
            description=description,
            category=category,
            url=url,
            status=status,
            probability_yes=probability_yes,
            probability_no=probability_no,
            volume_24h=volume_24h,
            liquidity_value=liquidity_value,
            created_at=created_at,
            resolution_time=resolution_time,
            rules_text=rules_text,
            source_payload=source_payload,
        )
        db.add(market)
        db.flush()
        return market

    market.title = title or market.title
    market.description = description
    market.category = category
    market.url = url
    market.status = status
    market.probability_yes = probability_yes
    market.probability_no = probability_no
    market.volume_24h = volume_24h
    market.liquidity_value = liquidity_value
    market.created_at = created_at
    market.resolution_time = resolution_time
    market.rules_text = rules_text
    market.source_payload = source_payload
    return market


def _upsert_market(
    db: Session,
    *,
    platform_id: int,
    row: dict[str, Any],
) -> Market:
    external_id = str(row.get("id") or "")
    market = db.scalar(
        select(Market).where(
            Market.platform_id == platform_id,
            Market.external_market_id == external_id,
        )
    )

    yes_prob = row.get("probability")
    description = _to_text(row.get("textDescription") or row.get("description"))
    created_at = _to_dt_ms(row.get("createdTime"))
    close_at = _to_dt_ms(row.get("closeTime"))
    if market is None:
        market = Market(
            platform_id=platform_id,
            external_market_id=external_id,
            title=row.get("question") or f"manifold_{external_id}",
            description=description,
            category=(row.get("groupSlugs") or [None])[0],
            url=f"https://manifold.markets/{row.get('creatorUsername', '')}/{row.get('slug', '')}",
            status=row.get("outcomeType"),
            probability_yes=yes_prob,
            probability_no=(1 - yes_prob) if isinstance(yes_prob, (int, float)) else None,
            volume_24h=float(row.get("volume24Hours") or 0.0),
            liquidity_value=float(row.get("totalLiquidity") or 0.0),
            created_at=created_at,
            resolution_time=close_at,
            rules_text=row.get("resolutionCriteria"),
            source_payload=row,
        )
        db.add(market)
        db.flush()
        return market

    market.title = row.get("question") or market.title
    market.description = description
    market.category = (row.get("groupSlugs") or [None])[0]
    market.status = row.get("outcomeType")
    market.probability_yes = yes_prob
    market.probability_no = (1 - yes_prob) if isinstance(yes_prob, (int, float)) else None
    market.volume_24h = float(row.get("volume24Hours") or 0.0)
    market.liquidity_value = float(row.get("totalLiquidity") or 0.0)
    market.created_at = created_at
    market.resolution_time = close_at
    market.rules_text = row.get("resolutionCriteria")
    market.source_payload = row
    return market


def _find_related_market(db: Session, manifold_market: Market) -> Market | None:
    normalized = _norm_title(manifold_market.title)
    if not normalized:
        return None
    # First pass: exact normalized title match across non-Manifold platforms.
    candidates = list(
        db.scalars(
            select(Market).where(
                Market.id != manifold_market.id,
                Market.platform_id != manifold_market.platform_id,
            )
        )
    )
    for candidate in candidates:
        if _norm_title(candidate.title) == normalized:
            return candidate
    # Fallback: shared 4+ char tokens.
    tokens = {t for t in re.findall(r"[a-z0-9]+", normalized) if len(t) >= 4}
    if not tokens:
        return None
    best: tuple[int, Market] | None = None
    for candidate in candidates:
        ct = {t for t in re.findall(r"[a-z0-9]+", _norm_title(candidate.title)) if len(t) >= 4}
        shared = len(tokens & ct)
        if shared == 0:
            continue
        if best is None or shared > best[0]:
            best = (shared, candidate)
    return best[1] if best and best[0] >= 2 else None


def _history_exists(
    db: Session,
    *,
    platform: str,
    market_id: int,
    related_market_id: int | None,
    signal_type: SignalType,
    bucket: datetime,
) -> bool:
    existing = db.scalar(
        select(SignalHistory.id).where(
            SignalHistory.platform == platform,
            SignalHistory.market_id == market_id,
            SignalHistory.related_market_id.is_(related_market_id)
            if related_market_id is None
            else SignalHistory.related_market_id == related_market_id,
            SignalHistory.signal_type == signal_type,
            SignalHistory.timestamp_bucket == bucket,
        )
    )
    return existing is not None


def _ensure_signal_history_backfill_schema(database_url: str) -> None:
    """Best-effort legacy compatibility for sqlite DBs without v3 columns."""
    if not database_url.startswith("sqlite:///"):
        return
    engine = create_engine(database_url, pool_pre_ping=True)
    with engine.begin() as conn:
        exists = conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='signal_history'"
            )
        ).scalar()
        if not exists:
            return

        columns = {
            str(row[1])
            for row in conn.execute(text("PRAGMA table_info('signal_history')")).fetchall()
        }

        if "timestamp_bucket" not in columns:
            conn.execute(text("ALTER TABLE signal_history ADD COLUMN timestamp_bucket DATETIME"))
        if "source_tag" not in columns:
            conn.execute(text("ALTER TABLE signal_history ADD COLUMN source_tag VARCHAR(64)"))
        if "missing_label_reason" not in columns:
            conn.execute(text("ALTER TABLE signal_history ADD COLUMN missing_label_reason VARCHAR(128)"))

        conn.execute(
            text(
                "UPDATE signal_history "
                "SET timestamp_bucket = strftime('%Y-%m-%d %H:00:00', timestamp) "
                "WHERE timestamp_bucket IS NULL AND timestamp IS NOT NULL"
            )
        )
        conn.execute(
            text("UPDATE signal_history SET source_tag = 'local' WHERE source_tag IS NULL")
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_signal_history_source_tag "
                "ON signal_history (source_tag)"
            )
        )
        try:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_signal_history_idempotent "
                    "ON signal_history (platform, market_id, related_market_id, signal_type, timestamp_bucket)"
                )
            )
        except Exception:
            # If legacy duplicates exist, continue; ingestion still does explicit idempotent checks.
            pass


def ingest_manifold_historical(
    db: Session,
    *,
    max_pages: int,
    page_size: int,
    start_days: int,
) -> dict[str, int]:
    settings = get_settings()
    platform = _ensure_platform(db, "MANIFOLD", "https://manifold.markets")
    cutoff = datetime.now(UTC) - timedelta(days=max(1, start_days))

    before: str | None = None
    pages = 0
    ingested_markets = 0
    created_history = 0
    skipped_existing_history = 0
    created_divergence = 0
    created_rules_risk = 0

    while pages < max_pages:
        rows = _fetch_manifold_markets(settings.manifold_api_base_url, limit=page_size, before=before)
        if not rows:
            break

        next_before_id: str | None = None
        for row in rows:
            created_at = _to_dt_ms(row.get("createdTime"))
            if created_at is None:
                continue
            if created_at < cutoff:
                continue

            market = _upsert_market(db, platform_id=platform.id, row=row)
            ingested_markets += 1

            related = _find_related_market(db, market)
            signal_type = SignalType.RULES_RISK
            divergence = None
            related_prob = None
            related_market_id = None
            if related and market.probability_yes is not None and related.probability_yes is not None:
                related_market_id = related.id
                related_prob = float(related.probability_yes)
                divergence = abs(float(market.probability_yes) - related_prob)
                signal_type = SignalType.DIVERGENCE

            bucket = _to_hour_bucket(created_at)
            platform_label = "MANIFOLD" if related_market_id is None else "MANIFOLD|XPLAT"
            if _history_exists(
                db,
                platform=platform_label,
                market_id=market.id,
                related_market_id=related_market_id,
                signal_type=signal_type,
                bucket=bucket,
            ):
                skipped_existing_history += 1
                continue

            db.add(
                SignalHistory(
                    signal_id=None,
                    signal_type=signal_type,
                    timestamp=created_at,
                    timestamp_bucket=bucket,
                    platform=platform_label,
                    source_tag="manifold_api",
                    market_id=market.id,
                    related_market_id=related_market_id,
                    probability_at_signal=float(market.probability_yes) if market.probability_yes is not None else None,
                    related_market_probability=related_prob,
                    divergence=divergence,
                    liquidity=float(market.liquidity_value or 0.0),
                    volume_24h=float(market.volume_24h or 0.0),
                    missing_label_reason="historical_label_pending",
                    simulated_trade={
                        "source": "manifold_api_backfill",
                        "manifold_market_id": market.external_market_id,
                    },
                )
            )
            created_history += 1
            if signal_type == SignalType.DIVERGENCE:
                created_divergence += 1
            else:
                created_rules_risk += 1

            row_id = row.get("id")
            if isinstance(row_id, str) and row_id:
                next_before_id = row_id

        db.commit()
        pages += 1
        if next_before_id is None:
            break
        before = next_before_id

    return {
        "pages_processed": pages,
        "markets_upserted": ingested_markets,
        "history_created": created_history,
        "history_skipped_existing": skipped_existing_history,
        "divergence_created": created_divergence,
        "rules_risk_created": created_rules_risk,
    }


def ingest_manifold_markets_only(
    db: Session,
    *,
    max_pages: int,
    page_size: int,
    start_days: int,
) -> dict[str, int]:
    settings = get_settings()
    platform = _ensure_platform(db, "MANIFOLD", "https://manifold.markets")
    cutoff = datetime.now(UTC) - timedelta(days=max(1, start_days))
    before: str | None = None
    pages = 0
    markets_upserted = 0
    while pages < max_pages:
        rows = _fetch_manifold_markets(settings.manifold_api_base_url, limit=page_size, before=before)
        if not rows:
            break
        next_before_id: str | None = None
        for row in rows:
            created_at = _to_dt_ms(row.get("createdTime"))
            if created_at is None or created_at < cutoff:
                continue
            _upsert_market(db, platform_id=platform.id, row=row)
            markets_upserted += 1
            row_id = row.get("id")
            if isinstance(row_id, str) and row_id:
                next_before_id = row_id
        db.commit()
        pages += 1
        if next_before_id is None:
            break
        before = next_before_id
    return {"pages_processed": pages, "markets_upserted": markets_upserted}


def _as_utc(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def _find_market_by_external_id(db: Session, platform_id: int, external_market_id: str) -> Market | None:
    return db.scalar(
        select(Market).where(
            Market.platform_id == platform_id,
            Market.external_market_id == str(external_market_id),
        )
    )


def _nearest_future_prob(points: list[tuple[datetime, float]], target_ts: datetime) -> float | None:
    if not points:
        return None
    timestamps = [p[0] for p in points]
    idx = bisect_left(timestamps, target_ts)
    if idx >= len(points):
        return None
    return float(points[idx][1])


def _build_history_rows_from_market_points(
    db: Session,
    *,
    market: Market,
    points: list[tuple[datetime, float, float]],  # ts, p_before, p_after
    source_tag: str,
) -> dict[str, int]:
    # Collapse to 1 row per hour bucket to align with idempotent key.
    dedup_by_bucket: dict[datetime, tuple[datetime, float, float]] = {}
    for ts, p_before, p_after in points:
        bucket = _to_hour_bucket(ts)
        existing = dedup_by_bucket.get(bucket)
        if existing is None or ts < existing[0]:
            dedup_by_bucket[bucket] = (ts, p_before, p_after)
    collapsed = sorted(dedup_by_bucket.values(), key=lambda x: x[0])

    if not collapsed:
        return {"created": 0, "skipped_existing": 0, "divergence_created": 0, "rules_risk_created": 0}

    series_after = [(ts, p_after) for ts, _, p_after in collapsed]
    related = _find_related_market(db, market)
    own_platform = db.scalar(select(Platform.name).where(Platform.id == market.platform_id)) or "MANIFOLD"
    related_platform = (
        db.scalar(select(Platform.name).where(Platform.id == related.platform_id)) if related else None
    ) or ""
    platform_label = own_platform if not related_platform else f"{own_platform}|{related_platform}"[:64]

    created = 0
    skipped_existing = 0
    divergence_created = 0
    rules_risk_created = 0
    for ts, p_before, p_after in collapsed:
        bucket = _to_hour_bucket(ts)
        signal_type = SignalType.RULES_RISK
        divergence = None
        related_prob = None
        related_market_id = None
        if related and related.probability_yes is not None:
            signal_type = SignalType.DIVERGENCE
            related_market_id = related.id
            related_prob = float(related.probability_yes)
            divergence = abs(float(p_before) - related_prob)

        if _history_exists(
            db,
            platform=platform_label,
            market_id=market.id,
            related_market_id=related_market_id,
            signal_type=signal_type,
            bucket=bucket,
        ):
            skipped_existing += 1
            continue

        p1h = _nearest_future_prob(series_after, ts + timedelta(hours=1))
        p6h = _nearest_future_prob(series_after, ts + timedelta(hours=6))
        p24h = _nearest_future_prob(series_after, ts + timedelta(hours=24))

        db.add(
            SignalHistory(
                signal_id=None,
                signal_type=signal_type,
                timestamp=ts,
                timestamp_bucket=bucket,
                platform=platform_label,
                source_tag=source_tag,
                market_id=market.id,
                related_market_id=related_market_id,
                probability_at_signal=float(p_before),
                related_market_probability=related_prob,
                divergence=divergence,
                liquidity=float(market.liquidity_value or 0.0),
                volume_24h=float(market.volume_24h or 0.0),
                probability_after_1h=p1h,
                probability_after_6h=p6h,
                probability_after_24h=p24h,
                labeled_at=datetime.now(UTC) if any(x is not None for x in (p1h, p6h, p24h)) else None,
                missing_label_reason=(
                    None
                    if any(x is not None for x in (p1h, p6h, p24h))
                    else "historical_horizon_not_available"
                ),
                simulated_trade={"source": source_tag, "mode": "manifold_bets_timeseries"},
            )
        )
        created += 1
        if signal_type == SignalType.DIVERGENCE:
            divergence_created += 1
        else:
            rules_risk_created += 1

    return {
        "created": created,
        "skipped_existing": skipped_existing,
        "divergence_created": divergence_created,
        "rules_risk_created": rules_risk_created,
    }


def ingest_manifold_bets_historical(
    db: Session,
    *,
    max_pages: int,
    page_size: int,
    start_days: int,
    warmup_market_pages: int,
    max_missing_market_fetches: int,
) -> dict[str, int]:
    settings = get_settings()
    platform = _ensure_platform(db, "MANIFOLD", "https://manifold.markets")
    cutoff = datetime.now(UTC) - timedelta(days=max(1, start_days))

    # Warm up local markets table so contractId -> market mapping is available.
    warmup = ingest_manifold_markets_only(
        db,
        max_pages=max(1, warmup_market_pages),
        page_size=min(500, max(50, page_size)),
        start_days=start_days,
    )

    before: str | None = None
    pages = 0
    bets_seen = 0
    bets_used = 0
    points_by_market: dict[int, list[tuple[datetime, float, float]]] = {}
    old_bets_skipped = 0
    unknown_market_skipped = 0
    invalid_prob_skipped = 0
    missing_market_fetched = 0
    missing_market_failed = 0
    missing_market_cache: set[str] = set()

    while pages < max_pages:
        rows = _fetch_manifold_bets(settings.manifold_api_base_url, limit=page_size, before=before)
        if not rows:
            break

        next_before_id: str | None = None
        for row in rows:
            bets_seen += 1
            created_at = _to_dt_ms(row.get("createdTime"))
            if created_at is None:
                continue
            if created_at < cutoff:
                old_bets_skipped += 1
                continue
            contract_id = str(row.get("contractId") or "")
            if not contract_id:
                unknown_market_skipped += 1
                continue
            market = _find_market_by_external_id(db, platform.id, contract_id)
            if market is None:
                if (
                    contract_id not in missing_market_cache
                    and missing_market_fetched < max_missing_market_fetches
                ):
                    try:
                        fetched = _fetch_manifold_market_by_id(settings.manifold_api_base_url, contract_id)
                        if fetched:
                            market = _upsert_market(db, platform_id=platform.id, row=fetched)
                            db.flush()
                            missing_market_fetched += 1
                        else:
                            missing_market_failed += 1
                        missing_market_cache.add(contract_id)
                    except Exception:
                        db.rollback()
                        missing_market_failed += 1
                        missing_market_cache.add(contract_id)
                if market is None:
                    unknown_market_skipped += 1
                    continue
            prob_before = row.get("probBefore")
            prob_after = row.get("probAfter")
            if not isinstance(prob_before, (int, float)) or not isinstance(prob_after, (int, float)):
                invalid_prob_skipped += 1
                continue
            p_before = float(prob_before)
            p_after = float(prob_after)
            if not (0.0 <= p_before <= 1.0 and 0.0 <= p_after <= 1.0):
                invalid_prob_skipped += 1
                continue

            points_by_market.setdefault(market.id, []).append((_as_utc(created_at) or created_at, p_before, p_after))
            bets_used += 1
            row_id = row.get("id")
            if isinstance(row_id, str) and row_id:
                next_before_id = row_id

        pages += 1
        if next_before_id is None:
            break
        before = next_before_id

    history_created = 0
    history_skipped_existing = 0
    divergence_created = 0
    rules_risk_created = 0
    markets_with_points = 0
    for market_id, pts in points_by_market.items():
        market = db.get(Market, market_id)
        if market is None:
            continue
        pts_sorted = sorted(pts, key=lambda x: x[0])
        if not pts_sorted:
            continue
        markets_with_points += 1
        built = _build_history_rows_from_market_points(
            db,
            market=market,
            points=pts_sorted,
            source_tag="manifold_bets_api",
        )
        history_created += built["created"]
        history_skipped_existing += built["skipped_existing"]
        divergence_created += built["divergence_created"]
        rules_risk_created += built["rules_risk_created"]
    db.commit()

    return {
        "pages_processed": pages,
        "bets_seen": bets_seen,
        "bets_used": bets_used,
        "old_bets_skipped": old_bets_skipped,
        "unknown_market_skipped": unknown_market_skipped,
        "invalid_prob_skipped": invalid_prob_skipped,
        "missing_market_fetched": missing_market_fetched,
        "missing_market_failed": missing_market_failed,
        "markets_with_points": markets_with_points,
        "history_created": history_created,
        "history_skipped_existing": history_skipped_existing,
        "divergence_created": divergence_created,
        "rules_risk_created": rules_risk_created,
        "warmup_markets_upserted": int(warmup.get("markets_upserted") or 0),
    }


def ingest_local_historical(
    db: Session,
    *,
    start_days: int,
    max_rows: int,
) -> dict[str, int]:
    cutoff = datetime.now(UTC) - timedelta(days=max(1, start_days))
    markets = list(
        db.scalars(
            select(Market)
            .where(or_(Market.created_at.is_(None), Market.created_at >= cutoff))
            .order_by(Market.created_at.desc().nullslast())
            .limit(max_rows)
        )
    )
    platform_by_id = {p.id: p.name for p in db.scalars(select(Platform))}
    by_norm_title: dict[str, list[Market]] = {}
    for market in markets:
        by_norm_title.setdefault(_norm_title(market.title), []).append(market)

    created = 0
    skipped_existing = 0
    skipped_missing_market = 0
    for market in markets:
        if market is None:
            skipped_missing_market += 1
            continue
        related = None
        candidates = by_norm_title.get(_norm_title(market.title), [])
        for c in candidates:
            if c.id != market.id and c.platform_id != market.platform_id:
                related = c
                break
        ts = market.created_at if market.created_at is not None else datetime.now(UTC) - timedelta(hours=1)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        bucket = _to_hour_bucket(ts)
        own_platform = platform_by_id.get(market.platform_id, "LOCAL")
        other_platform = platform_by_id.get(related.platform_id, "") if related else ""
        platform_label = own_platform if not other_platform else f"{own_platform}|{other_platform}"[:64]
        signal_type = SignalType.DIVERGENCE if related else SignalType.RULES_RISK
        divergence = None
        if related and market.probability_yes is not None and related.probability_yes is not None:
            divergence = abs(float(market.probability_yes) - float(related.probability_yes))

        if _history_exists(
            db,
            platform=platform_label,
            market_id=market.id,
            related_market_id=(related.id if related else None),
            signal_type=signal_type,
            bucket=bucket,
        ):
            skipped_existing += 1
            continue
        db.add(
            SignalHistory(
                signal_id=None,
                signal_type=signal_type,
                timestamp=ts,
                timestamp_bucket=bucket,
                platform=platform_label,
                source_tag="local_backfill",
                market_id=market.id,
                related_market_id=(related.id if related else None),
                probability_at_signal=market.probability_yes,
                related_market_probability=related.probability_yes if related else None,
                divergence=divergence,
                liquidity=float(market.liquidity_value or 0.0),
                volume_24h=float(market.volume_24h or 0.0),
                missing_label_reason="historical_label_pending",
                simulated_trade={"source": "local_markets_backfill"},
            )
        )
        created += 1
    db.commit()
    return {
        "markets_scanned": len(markets),
        "history_created": created,
        "history_skipped_existing": skipped_existing,
        "history_skipped_missing_market": skipped_missing_market,
    }


def ingest_metaculus_markets_only(
    db: Session,
    *,
    max_pages: int,
    page_size: int,
    start_days: int,
) -> dict[str, int | str]:
    settings = get_settings()
    if not settings.metaculus_api_token:
        return {"error": "METACULUS_API_TOKEN is required for metaculus_markets provider"}

    platform = _ensure_platform(db, "METACULUS", "https://www.metaculus.com")
    cutoff = datetime.now(UTC) - timedelta(days=max(1, start_days))
    offset = 0
    pages = 0
    markets_upserted = 0

    while pages < max_pages:
        payload = _fetch_metaculus_questions(
            settings.metaculus_api_base_url,
            token=settings.metaculus_api_token,
            user_agent=settings.metaculus_user_agent,
            limit=page_size,
            offset=offset,
        )
        rows = payload.get("results") or []
        if not rows:
            break
        processed = 0
        for row in rows:
            q = row.get("question") if isinstance(row.get("question"), dict) else row
            created_raw = q.get("created_at") or row.get("created_at")
            created_at = None
            if isinstance(created_raw, str):
                try:
                    created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                except ValueError:
                    created_at = None
            if created_at is not None and created_at < cutoff:
                continue

            cp = row.get("community_prediction") if isinstance(row.get("community_prediction"), dict) else {}
            full = cp.get("full") if isinstance(cp.get("full"), dict) else {}
            q2 = full.get("q2")
            prob = float(q2) if isinstance(q2, (int, float)) and 0.0 <= float(q2) <= 1.0 else None

            _upsert_market_generic(
                db,
                platform_id=platform.id,
                external_market_id=str(row.get("id") or q.get("id") or ""),
                title=str(row.get("title") or q.get("title") or ""),
                description=_to_text(row.get("description") or q.get("description")),
                category=None,
                url=f"https://www.metaculus.com/questions/{row.get('id') or q.get('id')}",
                status=_to_text(row.get("status") or q.get("status")),
                probability_yes=prob,
                volume_24h=None,
                liquidity_value=None,
                created_at=created_at,
                resolution_time=None,
                rules_text=_to_text(row.get("resolution_criteria") or q.get("resolution_criteria")),
                source_payload=row if isinstance(row, dict) else {},
            )
            processed += 1
            markets_upserted += 1

        db.commit()
        pages += 1
        if processed == 0:
            break
        offset += len(rows)
        if len(rows) < page_size:
            break

    return {"pages_processed": pages, "markets_upserted": markets_upserted}


def ingest_polymarket_markets_only(
    db: Session,
    *,
    max_pages: int,
    page_size: int,
    start_days: int,
) -> dict[str, int]:
    settings = get_settings()
    platform = _ensure_platform(db, "POLYMARKET", "https://polymarket.com")
    cutoff = datetime.now(UTC) - timedelta(days=max(1, start_days))
    offset = 0
    pages = 0
    markets_upserted = 0

    while pages < max_pages:
        rows = _fetch_polymarket_markets(settings.polymarket_api_base_url, limit=page_size, offset=offset)
        if not rows:
            break
        processed = 0
        for row in rows:
            created_at = _to_dt_ms(row.get("createdAt"))
            if created_at is not None and created_at < cutoff:
                continue
            yes_prob = _extract_polymarket_probability_yes(row)
            _upsert_market_generic(
                db,
                platform_id=platform.id,
                external_market_id=str(row.get("id") or ""),
                title=str(row.get("question") or ""),
                description=_to_text(row.get("description")),
                category=_to_text(row.get("category")),
                url=_to_text(row.get("url")),
                status=_to_text(row.get("status")),
                probability_yes=yes_prob,
                volume_24h=float(row.get("volume24h") or row.get("volume24hr") or row.get("volumeNum") or 0.0),
                liquidity_value=float(row.get("liquidity") or row.get("liquidityNum") or 0.0),
                created_at=created_at,
                resolution_time=_to_dt_ms(row.get("endDate") or row.get("endTime")),
                rules_text=_to_text(row.get("rules")),
                source_payload=row if isinstance(row, dict) else {},
            )
            processed += 1
            markets_upserted += 1

        db.commit()
        pages += 1
        if processed == 0:
            break
        offset += len(rows)
        if len(rows) < page_size:
            break

    return {"pages_processed": pages, "markets_upserted": markets_upserted}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill historical data into signal_history.")
    parser.add_argument(
        "--provider",
        choices=[
            "manifold",
            "manifold_bets",
            "metaculus_markets",
            "polymarket_markets",
            "xplat_markets",
            "local",
        ],
        default="manifold",
    )
    parser.add_argument("--max-pages", type=int, default=30)
    parser.add_argument("--page-size", type=int, default=200)
    parser.add_argument("--start-days", type=int, default=120)
    parser.add_argument("--warmup-market-pages", type=int, default=10)
    parser.add_argument("--max-missing-market-fetches", type=int, default=300)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = get_settings()
    _ensure_signal_history_backfill_schema(settings.database_url)
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with session_factory() as db:
        if args.provider == "manifold":
            result = ingest_manifold_historical(
                db,
                max_pages=max(1, min(args.max_pages, 200)),
                page_size=max(1, min(args.page_size, 1000)),
                start_days=max(1, min(args.start_days, 3650)),
            )
        elif args.provider == "manifold_bets":
            result = ingest_manifold_bets_historical(
                db,
                max_pages=max(1, min(args.max_pages, 500)),
                page_size=max(1, min(args.page_size, 1000)),
                start_days=max(1, min(args.start_days, 3650)),
                warmup_market_pages=max(1, min(args.warmup_market_pages, 200)),
                max_missing_market_fetches=max(0, min(args.max_missing_market_fetches, 20000)),
            )
        elif args.provider == "local":
            result = ingest_local_historical(
                db,
                start_days=max(1, min(args.start_days, 3650)),
                max_rows=max(1, min(args.max_pages * args.page_size, 100000)),
            )
        elif args.provider == "metaculus_markets":
            result = ingest_metaculus_markets_only(
                db,
                max_pages=max(1, min(args.max_pages, 500)),
                page_size=max(1, min(args.page_size, 500)),
                start_days=max(1, min(args.start_days, 3650)),
            )
        elif args.provider == "polymarket_markets":
            result = ingest_polymarket_markets_only(
                db,
                max_pages=max(1, min(args.max_pages, 500)),
                page_size=max(1, min(args.page_size, 500)),
                start_days=max(1, min(args.start_days, 3650)),
            )
        elif args.provider == "xplat_markets":
            meta = ingest_metaculus_markets_only(
                db,
                max_pages=max(1, min(args.max_pages, 200)),
                page_size=max(1, min(args.page_size, 200)),
                start_days=max(1, min(args.start_days, 3650)),
            )
            poly = ingest_polymarket_markets_only(
                db,
                max_pages=max(1, min(args.max_pages, 200)),
                page_size=max(1, min(args.page_size, 200)),
                start_days=max(1, min(args.start_days, 3650)),
            )
            result = {"metaculus": meta, "polymarket": poly}
        else:
            result = {"error": "unsupported provider"}
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
