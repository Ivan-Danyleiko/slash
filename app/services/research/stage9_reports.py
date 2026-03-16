from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.models import Market, Platform, Signal, SignalHistory, Stage7AgentDecision


def _safe_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def build_stage9_consensus_quality_report(db: Session, *, days: int = 14) -> dict[str, Any]:
    cutoff = datetime.now(UTC) - timedelta(days=max(1, int(days)))
    rows = list(
        db.scalars(
            select(Stage7AgentDecision)
            .where(Stage7AgentDecision.created_at >= cutoff)
            .order_by(Stage7AgentDecision.created_at.desc())
            .limit(5000)
        )
    )
    total = len(rows)
    has_meta = 0
    has_three = 0
    has_two = 0
    two_source_mode_count = 0
    insufficient_sources_count = 0
    reason_counts: dict[str, int] = {}
    for row in rows:
        ev = row.evidence_bundle or {}
        consensus = ev.get("external_consensus") if isinstance(ev, dict) else {}
        p_poly = _safe_float((consensus or {}).get("polymarket_prob"))
        p_man = _safe_float((consensus or {}).get("manifold_prob"))
        p_meta = _safe_float((consensus or {}).get("metaculus_median"))
        reason_codes = list((consensus or {}).get("consensus_reason_codes") or [])
        for code in reason_codes:
            token = str(code or "").strip()
            if not token:
                continue
            reason_counts[token] = reason_counts.get(token, 0) + 1
        if "consensus_two_source_mode" in reason_codes:
            two_source_mode_count += 1
        if "consensus_insufficient_sources" in reason_codes:
            insufficient_sources_count += 1
        present = sum(v is not None for v in (p_poly, p_man, p_meta))
        if p_meta is not None:
            has_meta += 1
        if present >= 2:
            has_two += 1
        if present >= 3:
            has_three += 1
    return {
        "window_days": days,
        "rows_total": total,
        "metaculus_median_fill_rate": (has_meta / total) if total else 0.0,
        "consensus_2source_share": (has_two / total) if total else 0.0,
        "consensus_3source_share": (has_three / total) if total else 0.0,
        "consensus_two_source_mode_share": (two_source_mode_count / total) if total else 0.0,
        "consensus_insufficient_sources_share": (insufficient_sources_count / total) if total else 0.0,
        "consensus_reason_codes": reason_counts,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def build_stage9_directional_labeling_report(db: Session, *, days: int = 30) -> dict[str, Any]:
    cutoff = datetime.now(UTC) - timedelta(days=max(1, int(days)))
    rows = list(
        db.scalars(
            select(SignalHistory).where(SignalHistory.timestamp >= cutoff).order_by(SignalHistory.timestamp.desc()).limit(10000)
        )
    )
    total = len(rows)
    direction_set = sum(1 for r in rows if (r.signal_direction or "").strip().upper() in {"YES", "NO"})
    direction_missing = sum(1 for r in rows if (r.missing_label_reason or "") == "direction_missing")
    void_count = sum(1 for r in rows if (r.resolved_outcome or "").strip().upper() == "VOID")
    return {
        "window_days": days,
        "rows_total": total,
        "direction_labeled_share": (direction_set / total) if total else 0.0,
        "direction_missing_label_share": (direction_missing / total) if total else 0.0,
        "void_outcome_share": (void_count / total) if total else 0.0,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def build_stage9_execution_realism_report(db: Session, *, days: int = 14) -> dict[str, Any]:
    cutoff = datetime.now(UTC) - timedelta(days=max(1, int(days)))
    signals = list(
        db.scalars(
            select(Signal).where(Signal.created_at >= cutoff).order_by(Signal.created_at.desc()).limit(3000)
        )
    )
    total = len(signals)
    non_zero_edge = 0
    assumption_versions: dict[str, int] = {}
    for s in signals:
        ex = s.execution_analysis or {}
        edge = _safe_float(ex.get("expected_ev_after_costs_pct")) or 0.0
        if abs(edge) > 1e-9:
            non_zero_edge += 1
        av = str(ex.get("assumptions_version") or "unknown")
        assumption_versions[av] = assumption_versions.get(av, 0) + 1

    market_cov_total = int(
        db.scalar(select(func.count()).select_from(Market).where(Market.fetched_at >= cutoff))
        or 0
    )
    polymarket_cov_total = int(
        db.scalar(
            select(func.count())
            .select_from(Market)
            .where(
                Market.fetched_at >= cutoff,
                Market.platform_id
                == select(Platform.id).where(Platform.name == "POLYMARKET").scalar_subquery(),
            )
        )
        or 0
    )
    spread_cov = int(
        db.scalar(
            select(func.count()).select_from(Market).where(
                Market.fetched_at >= cutoff,
                (Market.spread_cents.is_not(None)) | (Market.best_bid_yes.is_not(None)) | (Market.best_ask_yes.is_not(None)),
            )
        )
        or 0
    )
    polymarket_spread_cov = int(
        db.scalar(
            select(func.count())
            .select_from(Market)
            .where(
                Market.fetched_at >= cutoff,
                Market.platform_id
                == select(Platform.id).where(Platform.name == "POLYMARKET").scalar_subquery(),
                (Market.spread_cents.is_not(None)) | (Market.best_bid_yes.is_not(None)) | (Market.best_ask_yes.is_not(None)),
            )
        )
        or 0
    )
    oi_cov = int(
        db.scalar(
            select(func.count()).select_from(Market).where(
                Market.fetched_at >= cutoff,
                Market.open_interest.is_not(None),
            )
        )
        or 0
    )

    calibration = _stage9_calibration_and_precision_metrics(db, cutoff=cutoff)
    return {
        "window_days": days,
        "signals_total": total,
        "non_zero_edge_share": (non_zero_edge / total) if total else 0.0,
        "assumptions_versions": assumption_versions,
        "spread_coverage_share": (spread_cov / market_cov_total) if market_cov_total else 0.0,
        "polymarket_spread_coverage_share": (polymarket_spread_cov / polymarket_cov_total) if polymarket_cov_total else 0.0,
        "polymarket_markets_total": polymarket_cov_total,
        "open_interest_coverage_share": (oi_cov / market_cov_total) if market_cov_total else 0.0,
        **calibration,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def _stage9_calibration_and_precision_metrics(db: Session, *, cutoff: datetime) -> dict[str, Any]:
    hist_rows = list(
        db.execute(
            select(SignalHistory, Market.category)
            .join(Market, Market.id == SignalHistory.market_id, isouter=True)
            .where(
                SignalHistory.timestamp >= cutoff,
                SignalHistory.resolved_success.is_not(None),
                SignalHistory.probability_at_signal.is_not(None),
            )
            .order_by(SignalHistory.timestamp.desc())
            .limit(10000)
        ).all()
    )
    preds: list[float] = []
    ys: list[float] = []
    history_rows: list[SignalHistory] = []
    by_category: dict[str, tuple[list[float], list[float]]] = {}
    for row, category_raw in hist_rows:
        history_rows.append(row)
        p0 = float(row.probability_at_signal or 0.5)
        direction = str(row.signal_direction or "").strip().upper()
        p_success = p0 if direction == "YES" else (1.0 - p0 if direction == "NO" else 0.5)
        y = 1.0 if bool(row.resolved_success) else 0.0
        clipped = max(0.0, min(1.0, p_success))
        preds.append(clipped)
        ys.append(y)
        category = str(category_raw or "other").strip().lower() or "other"
        cpreds, cys = by_category.get(category, ([], []))
        cpreds.append(clipped)
        cys.append(y)
        by_category[category] = (cpreds, cys)

    brier, bss, ece = _brier_bss_ece(preds, ys)

    long_idx = [k for k, p in enumerate(preds) if p <= 0.15]
    longshot_bias = ((sum(ys[k] - preds[k] for k in long_idx) / len(long_idx)) if long_idx else 0.0)

    # Precision@K by expected edge ranking.
    sig_rows = list(
        db.scalars(
            select(Signal).where(Signal.created_at >= cutoff).order_by(Signal.created_at.desc()).limit(3000)
        )
    )
    ranked: list[tuple[float, int]] = []
    resolved_by_signal: dict[int, float] = {}
    for row in history_rows:
        if row.signal_id and row.signal_id not in resolved_by_signal:
            resolved_by_signal[row.signal_id] = 1.0 if bool(row.resolved_success) else 0.0
    for s in sig_rows:
        ex = s.execution_analysis or {}
        edge = _safe_float(ex.get("expected_ev_after_costs_pct"))
        if edge is None:
            continue
        ranked.append((float(edge), int(s.id)))
    ranked.sort(key=lambda x: x[0], reverse=True)

    def _precision_at_k(k: int) -> float:
        top = ranked[:k]
        if not top:
            return 0.0
        labeled = [resolved_by_signal.get(sid) for _, sid in top]
        labeled = [x for x in labeled if x is not None]
        if not labeled:
            return 0.0
        return float(sum(labeled) / len(labeled))

    # PR-AUC (AUPRC) on resolved directional rows.
    auprc = _auprc(preds, ys)

    brier_skill_score_per_category: dict[str, float] = {}
    ece_per_category: dict[str, float] = {}
    for category, (cpreds, cys) in by_category.items():
        _, cbss, cece = _brier_bss_ece(cpreds, cys)
        brier_skill_score_per_category[category] = cbss
        ece_per_category[category] = cece

    return {
        "brier_score": brier,
        "brier_skill_score": bss,
        "ece": ece,
        "brier_skill_score_per_category": brier_skill_score_per_category,
        "ece_per_category": ece_per_category,
        "longshot_bias_error_0_15pct": longshot_bias,
        "precision_at_10": _precision_at_k(10),
        "precision_at_25": _precision_at_k(25),
        "precision_at_50": _precision_at_k(50),
        "auprc": auprc,
    }


def _brier_bss_ece(preds: list[float], ys: list[float]) -> tuple[float, float, float]:
    n = len(preds)
    if n == 0:
        return 0.0, 0.0, 0.0
    brier = sum((p - y) ** 2 for p, y in zip(preds, ys)) / n
    y_bar = sum(ys) / n
    brier_ref = sum((y_bar - y) ** 2 for y in ys) / n
    bss = (1.0 - (brier / brier_ref)) if brier_ref > 0 else 0.0
    ece = 0.0
    for i in range(10):
        lo = i / 10.0
        hi = (i + 1) / 10.0
        idx = [k for k, p in enumerate(preds) if (lo <= p < hi or (i == 9 and p == 1.0))]
        if not idx:
            continue
        conf = sum(preds[k] for k in idx) / len(idx)
        acc = sum(ys[k] for k in idx) / len(idx)
        ece += abs(acc - conf) * (len(idx) / n)
    return brier, bss, ece


def _auprc(preds: list[float], ys: list[float]) -> float:
    n = len(preds)
    positives = sum(1 for y in ys if y >= 0.5)
    if n == 0 or positives == 0:
        return 0.0
    pairs = sorted(zip(preds, ys), key=lambda t: t[0], reverse=True)
    tp = 0.0
    fp = 0.0
    last_recall = 0.0
    area = 0.0
    for _, y in pairs:
        if y >= 0.5:
            tp += 1.0
        else:
            fp += 1.0
        recall = tp / float(positives)
        denom = tp + fp
        precision = tp / denom if denom > 0 else 0.0
        area += precision * max(0.0, recall - last_recall)
        last_recall = recall
    return area
