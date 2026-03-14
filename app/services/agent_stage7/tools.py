from __future__ import annotations

from datetime import UTC, datetime, timedelta
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.models import Market, Platform, Signal, SignalHistory
from app.observability.tracing import stage7_span

_HORIZON_TO_FIELD = {
    "1h": SignalHistory.probability_after_1h,
    "6h": SignalHistory.probability_after_6h,
    "24h": SignalHistory.probability_after_24h,
}

_STOPWORDS = {
    "the",
    "a",
    "an",
    "will",
    "is",
    "are",
    "to",
    "in",
    "for",
    "of",
    "on",
    "and",
    "or",
}

_TOKEN_ALIASES = {
    "election": "elect",
    "presidency": "president",
}


def _normalize_horizon(horizon: str) -> str:
    h = str(horizon or "6h").strip().lower()
    return h if h in _HORIZON_TO_FIELD else "6h"


def _tokens(value: str) -> set[str]:
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", str(value or "").lower())
    out: set[str] = set()
    for raw in cleaned.split():
        if not raw or raw in _STOPWORDS or len(raw) <= 2:
            continue
        token = raw
        if token.endswith("ing") and len(token) > 5:
            token = token[:-3]
        elif token.endswith("ed") and len(token) > 4:
            token = token[:-2]
        elif token.endswith("s") and len(token) >= 4:
            token = token[:-1]
        out.add(_TOKEN_ALIASES.get(token, token))
    return out


def _title_similarity(a: str, b: str) -> float:
    ta = _tokens(a)
    tb = _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / max(union, 1)


def get_signal_context(db: Session, signal_id: int) -> dict[str, Any]:
    with stage7_span("stage7.tool.get_signal_context"):
        signal = db.get(Signal, int(signal_id))
        if signal is None:
            return {
                "signal_id": int(signal_id),
                "signal_type": None,
                "confidence": 0.0,
                "liquidity": 0.0,
                "ev_v2": 0.0,
                "risk_flags": ["signal_not_found"],
                "market_id": None,
            }
        hist = db.scalar(
            select(SignalHistory)
            .where(SignalHistory.signal_id == signal.id)
            .order_by(SignalHistory.timestamp.desc())
            .limit(1)
        )
        confidence = min(0.95, max(0.05, float((hist.liquidity if hist else 0.0) or 0.0)))
        liquidity = float((hist.liquidity if hist else 0.0) or 0.0)
        ev_v2 = float((hist.divergence if hist else 0.0) or 0.0) * 0.20
        return {
            "signal_id": int(signal.id),
            "signal_type": str(signal.signal_type.value if hasattr(signal.signal_type, "value") else signal.signal_type),
            "confidence": round(confidence, 6),
            "liquidity": round(liquidity, 6),
            "ev_v2": round(ev_v2, 6),
            "risk_flags": [],
            "market_id": int(signal.market_id),
        }


def get_signal_history_metrics(db: Session, signal_type: str, horizon: str) -> dict[str, Any]:
    with stage7_span("stage7.tool.get_signal_history_metrics"):
        h = _normalize_horizon(horizon)
        cutoff = datetime.now(UTC) - timedelta(days=90)
        rows = list(
            db.scalars(
                select(SignalHistory)
                .where(SignalHistory.timestamp >= cutoff)
                .where(SignalHistory.signal_type == str(signal_type).strip().upper())
                .order_by(SignalHistory.timestamp.desc())
            )
        )
        returns: list[float] = []
        for row in rows:
            p0 = row.probability_at_signal
            p1 = getattr(row, _HORIZON_TO_FIELD[h].key)
            if p0 is None or p1 is None:
                continue
            returns.append(float(p1) - float(p0))
        n = len(returns)
        wins = [x for x in returns if x > 0]
        losses = [x for x in returns if x <= 0]
        return {
            "signal_type": str(signal_type).strip().upper(),
            "horizon": h,
            "hit_rate": round((len(wins) / n), 6) if n else 0.0,
            "avg_win": round((sum(wins) / len(wins)), 6) if wins else 0.0,
            "avg_loss": round((sum(losses) / len(losses)), 6) if losses else 0.0,
            "n_samples": n,
        }


def get_market_snapshot(db: Session, market_id: int) -> dict[str, Any]:
    with stage7_span("stage7.tool.get_market_snapshot"):
        market = db.get(Market, int(market_id))
        if market is None:
            return {
                "market_id": int(market_id),
                "platform": None,
                "probability": None,
                "volume_24h": None,
                "resolution_time": None,
                "title": None,
            }
        platform = db.scalar(select(Platform.name).where(Platform.id == market.platform_id))
        return {
            "market_id": int(market.id),
            "platform": str(platform or ""),
            "probability": (float(market.probability_yes) if market.probability_yes is not None else None),
            "volume_24h": (float(market.volume_24h) if market.volume_24h is not None else None),
            "resolution_time": market.resolution_time.isoformat() if market.resolution_time else None,
            "title": str(market.title or ""),
        }


def get_cross_platform_consensus(db: Session, event_id: str) -> dict[str, Any]:
    with stage7_span("stage7.tool.get_cross_platform_consensus"):
        # event_id is normalized event key for Stage 7; currently we use market title as key.
        title = str(event_id or "").strip()
        if not title:
            return {"event_id": "", "polymarket_prob": None, "manifold_prob": None, "metaculus_median": None}

        stmt = (
            select(Platform.name, Market.title, Market.probability_yes)
            .join(Platform, Platform.id == Market.platform_id)
            .order_by(Market.fetched_at.desc())
            .limit(400)
        )
        rows = list(db.execute(stmt))
        best: dict[str, tuple[float, float]] = {}
        for platform_name, candidate_title, candidate_prob in rows:
            if not isinstance(candidate_prob, (int, float)):
                continue
            sim = _title_similarity(title, str(candidate_title or ""))
            pname = str(platform_name or "").upper()
            prev = best.get(pname)
            if prev is None or sim > prev[0]:
                best[pname] = (sim, float(candidate_prob))
        def _pick(name: str) -> float | None:
            item = best.get(name)
            if item is None or item[0] < 0.25:
                return None
            return float(item[1])
        return {
            "event_id": title,
            "polymarket_prob": _pick("POLYMARKET"),
            "manifold_prob": _pick("MANIFOLD"),
            "metaculus_median": _pick("METACULUS"),
        }


def get_readiness_gate_status(db: Session, settings: Settings) -> dict[str, Any]:
    with stage7_span("stage7.tool.get_readiness_gate_status"):
        from app.services.research.readiness_gate import build_stage5_readiness_gate

        stage5_gate = build_stage5_readiness_gate(db, days=30, horizon="6h", min_labeled_returns=30)
        return {
            "stage5_gate": str(stage5_gate.get("status") or "FAIL"),
            "data_quality_gate": bool(str(stage5_gate.get("status") or "") in {"PASS", "WARN"}),
        }


def get_research_decision(db: Session, signal_type: str) -> dict[str, Any]:
    with stage7_span("stage7.tool.get_research_decision"):
        from app.services.research.walkforward import build_walkforward_report

        walk = build_walkforward_report(
            db,
            days=90,
            horizon="6h",
            signal_type=signal_type,
            train_days=30,
            test_days=14,
            step_days=14,
            embargo_hours=24,
            min_samples_per_window=100,
            bootstrap_sims=500,
        )
        rows = list(walk.get("rows") or [])
        row = rows[0] if rows else {}
        windows = list(row.get("windows") or [])
        negative_test_windows = 0
        valid_windows = 0
        for w in windows:
            test = w.get("test") or {}
            if int(test.get("n") or 0) <= 0:
                continue
            valid_windows += 1
            if float(test.get("avg_return") or 0.0) < 0.0:
                negative_test_windows += 1
        negative_share = (negative_test_windows / valid_windows) if valid_windows else 1.0
        overfit_flags: list[str] = []
        if float(row.get("avg_test_hit_rate") or 0.0) > 0.63:
            overfit_flags.append("hit_rate_gt_63pct")
        if float(row.get("avg_test_return") or 0.0) > 0.15:
            overfit_flags.append("avg_return_gt_15pct")
        walk_verdict = "LOW_CONFIDENCE" if bool(row.get("low_confidence")) else "OK"
        if negative_share > 0.30:
            walk_verdict = "UNSTABLE"
        return {
            "signal_type": str(signal_type).strip().upper(),
            "walk_forward_verdict": walk_verdict,
            "overfit_flags": overfit_flags,
            "negative_window_share": round(float(negative_share), 6),
        }
