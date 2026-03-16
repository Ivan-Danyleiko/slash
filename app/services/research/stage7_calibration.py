from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Signal, SignalHistory, Stage7AgentDecision


@dataclass(slots=True)
class _DecisionRow:
    signal_id: int
    provider: str
    confidence: float
    profitable: bool | None


def _direction_aware_return(row: SignalHistory) -> float | None:
    if row.probability_at_signal is None or row.probability_after_6h is None:
        return None
    p0 = float(row.probability_at_signal)
    p1 = float(row.probability_after_6h)
    direction = str(row.signal_direction or "").strip().upper()
    raw = p1 - p0
    return -raw if direction == "NO" else raw


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _ece(rows: list[_DecisionRow], *, bins: int = 10) -> float:
    if not rows:
        return 0.0
    edges = [i / bins for i in range(bins + 1)]
    total = len(rows)
    acc = 0.0
    for i in range(bins):
        lo = edges[i]
        hi = edges[i + 1]
        if i == bins - 1:
            bucket = [r for r in rows if lo <= r.confidence <= hi]
        else:
            bucket = [r for r in rows if lo <= r.confidence < hi]
        if not bucket:
            continue
        actual = sum(1 for r in bucket if bool(r.profitable)) / len(bucket)
        expected = sum(r.confidence for r in bucket) / len(bucket)
        acc += abs(actual - expected) * (len(bucket) / total)
    return float(acc)


def build_stage7_calibration_report(
    db: Session,
    *,
    days: int = 90,
    horizon: str = "6h",
) -> dict[str, Any]:
    cutoff = datetime.now(UTC) - timedelta(days=max(1, int(days)))
    if str(horizon).strip().lower() != "6h":
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "summary": {"days": int(days), "horizon": str(horizon), "unsupported_horizon": True},
            "rows": [],
            "buckets": [],
            "by_provider": {},
        }

    stage7_rows = list(
        db.scalars(
            select(Stage7AgentDecision)
            .where(
                Stage7AgentDecision.created_at >= cutoff,
                Stage7AgentDecision.signal_id.is_not(None),
                Stage7AgentDecision.decision == "KEEP",
            )
            .order_by(Stage7AgentDecision.id.desc())
        )
    )
    if not stage7_rows:
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "summary": {
                "days": int(days),
                "horizon": "6h",
                "rows_total": 0,
                "known_outcomes": 0,
                "precision_keep": 0.0,
                "precision_conf_ge_0_7": 0.0,
                "ece": 0.0,
                "calibration_rows": 0,
                "recall_profitable_signals": 0.0,
            },
            "rows": [],
            "buckets": [],
            "by_provider": {},
        }

    signal_ids = sorted({int(d.signal_id) for d in stage7_rows if d.signal_id is not None and int(d.signal_id) > 0})
    signals = list(db.scalars(select(Signal).where(Signal.id.in_(signal_ids))))
    signal_by_id = {int(s.id): s for s in signals}

    hist_rows = list(
        db.scalars(
            select(SignalHistory)
            .where(
                SignalHistory.signal_id.in_(signal_ids),
                SignalHistory.probability_at_signal.is_not(None),
                SignalHistory.probability_after_6h.is_not(None),
            )
            .order_by(SignalHistory.signal_id.asc(), SignalHistory.timestamp.desc())
        )
    )
    history_by_signal: dict[int, SignalHistory] = {}
    for h in hist_rows:
        sid = int(h.signal_id or 0)
        if sid > 0 and sid not in history_by_signal:
            history_by_signal[sid] = h

    rows: list[_DecisionRow] = []
    for d in stage7_rows:
        sid = int(d.signal_id or 0)
        if sid <= 0:
            continue
        signal = signal_by_id.get(sid)
        hist = history_by_signal.get(sid)
        ret = _direction_aware_return(hist) if hist else None
        profitable = (ret > 0.0) if ret is not None else None
        base_conf = float(signal.confidence_score) if signal and signal.confidence_score is not None else 0.5
        adj = float(d.confidence_adjustment or 0.0)
        conf = _clamp01(base_conf + adj)
        rows.append(
            _DecisionRow(
                signal_id=sid,
                provider=str(d.provider or "unknown"),
                confidence=conf,
                profitable=profitable,
            )
        )

    known = [r for r in rows if r.profitable is not None]
    precision_keep = (sum(1 for r in known if bool(r.profitable)) / len(known)) if known else 0.0
    known_high = [r for r in known if r.confidence >= 0.70]
    precision_high = (sum(1 for r in known_high if bool(r.profitable)) / len(known_high)) if known_high else 0.0

    latest_decision_by_signal: dict[int, Stage7AgentDecision] = {}
    for d in stage7_rows:
        sid = int(d.signal_id or 0)
        if sid > 0 and sid not in latest_decision_by_signal:
            latest_decision_by_signal[sid] = d
    profitable_total = 0
    profitable_keep = 0
    for sid in signal_ids:
        hist = history_by_signal.get(sid)
        if not hist:
            continue
        ret = _direction_aware_return(hist)
        if ret is None or ret <= 0:
            continue
        profitable_total += 1
        d = latest_decision_by_signal.get(sid)
        if d and str(d.decision or "").upper() == "KEEP":
            profitable_keep += 1
    recall = (profitable_keep / profitable_total) if profitable_total else 0.0

    bucket_ranges = [(0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0)]
    buckets: list[dict[str, Any]] = []
    for lo, hi in bucket_ranges:
        if hi < 1.0:
            bucket = [r for r in known if lo <= r.confidence < hi]
        else:
            bucket = [r for r in known if lo <= r.confidence <= hi]
        if not bucket:
            buckets.append(
                {
                    "range": [lo, hi],
                    "count": 0,
                    "expected_confidence": 0.0,
                    "actual_hit_rate": 0.0,
                    "calibration_error": 0.0,
                }
            )
            continue
        expected = sum(r.confidence for r in bucket) / len(bucket)
        actual = sum(1 for r in bucket if bool(r.profitable)) / len(bucket)
        buckets.append(
            {
                "range": [lo, hi],
                "count": len(bucket),
                "expected_confidence": round(float(expected), 6),
                "actual_hit_rate": round(float(actual), 6),
                "calibration_error": round(abs(float(actual) - float(expected)), 6),
            }
        )

    by_provider: dict[str, dict[str, Any]] = {}
    provider_values = sorted({r.provider for r in rows})
    for provider in provider_values:
        vals = [r for r in known if r.provider == provider]
        if not vals:
            continue
        by_provider[provider] = {
            "rows": len(vals),
            "precision_keep": round(sum(1 for r in vals if bool(r.profitable)) / len(vals), 6),
            "avg_confidence": round(sum(r.confidence for r in vals) / len(vals), 6),
        }

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "days": int(days),
            "horizon": "6h",
            "rows_total": len(rows),
            "known_outcomes": len(known),
            "precision_keep": round(float(precision_keep), 6),
            "precision_conf_ge_0_7": round(float(precision_high), 6),
            "ece": round(_ece(known), 6),
            "calibration_rows": len(known),
            "recall_profitable_signals": round(float(recall), 6),
        },
        "buckets": buckets,
        "by_provider": by_provider,
        "rows": [
            {
                "signal_id": r.signal_id,
                "provider": r.provider,
                "confidence": round(r.confidence, 6),
                "profitable": r.profitable,
            }
            for r in rows
        ],
    }
