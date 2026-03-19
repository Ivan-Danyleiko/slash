from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import math
import random
from statistics import median
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.models import Stage17TailPosition, Stage17TailReport
from app.services.signals.tail_circuit_breaker import check_tail_circuit_breaker


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    idx = max(0.0, min(1.0, float(p))) * (len(sorted_values) - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return float(sorted_values[lo])
    w = idx - lo
    return float((1.0 - w) * sorted_values[lo] + (w * sorted_values[hi]))


def _top10_wins(pnls: list[float]) -> list[float]:
    wins = sorted([float(x) for x in pnls if float(x) > 0.0], reverse=True)
    if not wins:
        return []
    k = max(1, int(math.ceil(0.10 * len(wins))))
    return wins[:k]


def payout_skew(pnls: list[float]) -> tuple[float, int]:
    wins = [float(x) for x in pnls if float(x) > 0.0]
    total_win = float(sum(wins))
    if total_win <= 0.0:
        return 0.0, 0
    top = _top10_wins(pnls)
    return float(sum(top)) / total_win, len(top)


def payout_skew_bootstrap_ci(
    pnls: list[float],
    *,
    n_bootstrap: int = 1000,
    seed: int = 42,
) -> tuple[float, float]:
    vals = [float(x) for x in pnls]
    if len(vals) < 2:
        s, _ = payout_skew(vals)
        return s, s
    rng = random.Random(seed)
    out: list[float] = []
    n = len(vals)
    rounds = max(100, int(n_bootstrap))
    for _ in range(rounds):
        sample = [vals[rng.randrange(0, n)] for _ in range(n)]
        s, _ = payout_skew(sample)
        out.append(s)
    out.sort()
    return _percentile(out, 0.10), _percentile(out, 0.90)


def _max_concurrent_positions(rows: list[Stage17TailPosition]) -> int:
    points: list[tuple[datetime, int]] = []
    now = datetime.now(UTC)
    for row in rows:
        open_ts = row.opened_at
        if open_ts is None:
            continue
        open_ref = open_ts if open_ts.tzinfo else open_ts.replace(tzinfo=UTC)
        close_ts = row.closed_at if row.closed_at is not None else now
        close_ref = close_ts if close_ts.tzinfo else close_ts.replace(tzinfo=UTC)
        points.append((open_ref, +1))
        points.append((close_ref, -1))
    points.sort(key=lambda x: (x[0], x[1]))
    active = 0
    peak = 0
    for _, delta in points:
        active += delta
        if active > peak:
            peak = active
    return int(max(0, peak))


def _by_category(rows: list[Stage17TailPosition]) -> dict[str, Any]:
    by: dict[str, dict[str, Any]] = {}
    for row in rows:
        cat = str(row.tail_category or "unknown")
        b = by.setdefault(cat, {"closed": 0, "wins": 0, "pnl_usd": 0.0})
        b["closed"] += 1
        pnl = float(row.realized_pnl_usd or 0.0)
        if pnl > 0:
            b["wins"] += 1
        b["pnl_usd"] += pnl
    for cat, val in by.items():
        closed = int(val["closed"] or 0)
        wins = int(val["wins"] or 0)
        val["win_rate_tail"] = (wins / closed) if closed > 0 else 0.0
        val["pnl_usd"] = float(val["pnl_usd"] or 0.0)
        by[cat] = val
    return by


def _by_variation(rows: list[Stage17TailPosition]) -> dict[str, Any]:
    by: dict[str, dict[str, Any]] = {}
    for row in rows:
        variation = str(row.tail_variation or "unknown")
        b = by.setdefault(variation, {"closed": 0, "wins": 0, "pnl_usd": 0.0})
        b["closed"] += 1
        pnl = float(row.realized_pnl_usd or 0.0)
        if pnl > 0:
            b["wins"] += 1
        b["pnl_usd"] += pnl
    for variation, val in by.items():
        closed = int(val["closed"] or 0)
        wins = int(val["wins"] or 0)
        val["win_rate_tail"] = (wins / closed) if closed > 0 else 0.0
        val["pnl_usd"] = float(val["pnl_usd"] or 0.0)
        by[variation] = val
    return by


def build_stage17_tail_report(
    db: Session,
    *,
    settings: Settings,
    days: int = 60,
    persist: bool = True,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=max(1, int(days)))
    rows = list(
        db.scalars(
            select(Stage17TailPosition)
            .where(Stage17TailPosition.opened_at >= cutoff)
            .order_by(Stage17TailPosition.opened_at.desc())
        )
    )
    closed = [r for r in rows if str(r.status or "").upper() == "CLOSED" and r.closed_at is not None]
    open_rows = [r for r in rows if str(r.status or "").upper() == "OPEN"]
    opened_last_24h = [r for r in rows if r.opened_at is not None and (r.opened_at if r.opened_at.tzinfo else r.opened_at.replace(tzinfo=UTC)) >= (now - timedelta(hours=24))]
    closed_pnls = [float(r.realized_pnl_usd or 0.0) for r in closed]
    closed_count = len(closed)
    wins_count = len([x for x in closed_pnls if x > 0.0])
    hit_rate = (wins_count / closed_count) if closed_count > 0 else 0.0
    payout, top10_count = payout_skew(closed_pnls)
    ci_low, ci_high = payout_skew_bootstrap_ci(
        closed_pnls,
        n_bootstrap=max(100, int(settings.stage17_tail_bootstrap_resamples)),
        seed=42,
    )

    durations_h = []
    for r in closed:
        if r.opened_at is None or r.closed_at is None:
            continue
        open_ts = r.opened_at if r.opened_at.tzinfo else r.opened_at.replace(tzinfo=UTC)
        close_ts = r.closed_at if r.closed_at.tzinfo else r.closed_at.replace(tzinfo=UTC)
        durations_h.append(max(0.0, (close_ts - open_ts).total_seconds() / 3600.0))
    median_ttr_h = float(median(durations_h)) if durations_h else None
    median_ttr_days = (median_ttr_h / 24.0) if median_ttr_h is not None else None

    win_multipliers: list[float] = []
    for r in closed:
        pnl = float(r.realized_pnl_usd or 0.0)
        notional = float(r.notional_usd or 0.0)
        if pnl <= 0.0 or notional <= 0.0:
            continue
        win_multipliers.append(1.0 + (pnl / notional))
    avg_win_multiplier = float(sum(win_multipliers) / len(win_multipliers)) if win_multipliers else None
    best_win_koef = max([float(r.koef_entry or 0.0) for r in closed if float(r.realized_pnl_usd or 0.0) > 0.0], default=0.0)
    avg_koef = (
        float(sum(float(r.koef_entry or 0.0) for r in rows if (r.koef_entry or 0.0) > 0.0))
        / max(1, len([1 for r in rows if (r.koef_entry or 0.0) > 0.0]))
    )
    avg_days_to_res = (
        float(sum(float(r.days_to_resolution_entry or 0.0) for r in rows if (r.days_to_resolution_entry or 0.0) > 0.0))
        / max(1, len([1 for r in rows if (r.days_to_resolution_entry or 0.0) > 0.0]))
    )
    total_notional_closed = float(sum(float(r.notional_usd or 0.0) for r in closed))
    roi_total = (float(sum(closed_pnls)) / total_notional_closed) if total_notional_closed > 0 else 0.0

    max_concurrent = _max_concurrent_positions(rows)
    ref_balance = max(1.0, float(settings.signal_tail_reference_balance_usd))
    budget_total = ref_balance * max(0.0, float(settings.signal_tail_budget_pct))
    budget_used = float(
        db.scalar(
            select(func.coalesce(func.sum(Stage17TailPosition.notional_usd), 0.0)).where(
                Stage17TailPosition.status == "OPEN"
            )
        )
        or 0.0
    )
    budget_used_pct = (budget_used / budget_total) if budget_total > 0 else 0.0
    breaker_active, breaker_reason = check_tail_circuit_breaker(
        db,
        settings=settings,
        balance_usd=ref_balance,
        api_status={"degraded": False},
    )

    min_closed = max(1, int(settings.stage17_tail_min_closed_positions))
    min_top10 = max(1, int(settings.stage17_tail_min_top10pct_wins_count))
    min_hit_rate = float(settings.stage17_tail_min_hit_rate)
    min_skew = float(settings.stage17_tail_min_payout_skew)
    min_skew_ci = float(settings.stage17_tail_min_payout_skew_ci_low_80)
    max_ttr_days = float(settings.stage17_tail_max_time_to_resolution_days)
    min_avg_win_multiplier = float(settings.stage17_tail_min_avg_win_multiplier)
    checks = {
        "min_open_positions_per_day": len(opened_last_24h) >= 5,
        "min_avg_koef": avg_koef >= 5.0,
        "max_avg_days_to_res": avg_days_to_res <= max_ttr_days,
        "no_positions_after_2027": all(
            (
                r.resolution_deadline is None
                or (r.resolution_deadline.year <= 2027)
            )
            for r in rows
        ),
        "closed_positions_ge_min": closed_count >= min_closed,
        "top10pct_wins_count_ge_min": top10_count >= min_top10,
        "hit_rate_tail_ge_min": hit_rate >= min_hit_rate,
        "payout_skew_ge_min": payout >= min_skew,
        "payout_skew_ci_low_80_ge_min": ci_low >= min_skew_ci,
        "avg_win_multiplier_ge_min": (
            (avg_win_multiplier is not None) and (avg_win_multiplier >= min_avg_win_multiplier)
        ),
        "hit_rate_after_50_closed": (closed_count < 50) or (hit_rate >= 0.08),
        "roi_after_100_closed": (closed_count < 100) or (roi_total > 0.0),
    }
    if not checks["closed_positions_ge_min"] or not checks["top10pct_wins_count_ge_min"]:
        final_decision = "NO_GO_DATA_PENDING"
        action = "collect_more_closed_tail_positions"
    elif all(checks.values()):
        final_decision = "LIMITED_GO"
        action = "enable_stage17_tail_limited_mode"
    else:
        final_decision = "NO_GO"
        action = "tune_tail_filters_and_base_rate"

    by_variation = _by_variation(closed)
    summary = {
        "days": int(days),
        "rows_total": len(rows),
        "closed_positions": closed_count,
        "open_positions": len(open_rows),
        "hit_rate_tail": round(hit_rate, 6),
        "payout_skew": round(payout, 6),
        "payout_skew_ci_low_80": round(ci_low, 6),
        "payout_skew_ci_high_80": round(ci_high, 6),
        "top10pct_wins_count": int(top10_count),
        "opened_last_24h": len(opened_last_24h),
        "time_to_resolution_median_hours": round(median_ttr_h, 3) if median_ttr_h is not None else None,
        "time_to_resolution_median_days": round(median_ttr_days, 3) if median_ttr_days is not None else None,
        "avg_days_to_resolution_entry": round(avg_days_to_res, 3),
        "avg_koef": round(avg_koef, 4),
        "best_win_koef": round(best_win_koef, 4),
        "roi_total": round(roi_total, 6),
        "avg_win_multiplier": round(avg_win_multiplier, 4) if avg_win_multiplier is not None else None,
        "max_concurrent_tail_positions": int(max_concurrent),
        "tail_budget_total_usd": round(budget_total, 4),
        "tail_budget_used_usd": round(budget_used, 4),
        "tail_budget_used_pct": round(budget_used_pct, 6),
        "circuit_breaker_active": bool(breaker_active),
        "circuit_breaker_reason": str(breaker_reason or ""),
        "checks": checks,
        "thresholds": {
            "min_closed_positions": min_closed,
            "min_top10pct_wins_count": min_top10,
            "min_hit_rate": min_hit_rate,
            "min_avg_koef": 5.0,
            "max_avg_days_to_resolution": max_ttr_days,
            "min_payout_skew": min_skew,
            "min_payout_skew_ci_low_80": min_skew_ci,
            "hit_rate_after_50_closed": 0.08,
            "roi_after_100_closed": 0.0,
            "min_avg_win_multiplier": min_avg_win_multiplier,
        },
        "final_decision": final_decision,
        "action": action,
        "by_variation": by_variation,
    }
    by_category = _by_category(closed)

    if persist:
        report_day = now.date()
        row = db.scalar(select(Stage17TailReport).where(Stage17TailReport.report_date == report_day).limit(1))
        if row is None:
            row = Stage17TailReport(report_date=report_day)
            db.add(row)
        row.closed_positions = closed_count
        row.win_rate_tail = float(hit_rate)
        row.payout_skew = float(payout)
        row.payout_skew_ci_low_80 = float(ci_low)
        row.payout_skew_ci_high_80 = float(ci_high)
        row.top10pct_wins_count = int(top10_count)
        row.time_to_resolution_median_hours = median_ttr_h
        row.avg_win_multiplier = avg_win_multiplier
        row.max_concurrent_tail_positions = int(max_concurrent)
        row.tail_budget_total_usd = float(budget_total)
        row.tail_budget_used_usd = float(budget_used)
        row.tail_budget_used_pct = float(budget_used_pct)
        row.by_category = by_category
        row.circuit_breaker_active = bool(breaker_active)
        row.circuit_breaker_reason = str(breaker_reason or "")
        row.acceptance = {
            "checks": checks,
            "thresholds": summary["thresholds"],
            "final_decision": final_decision,
            "action": action,
            "by_variation": by_variation,
        }
        db.commit()

    return {
        "summary": summary,
        "by_category": by_category,
        "by_variation": by_variation,
        "final_decision": final_decision,
        "action": action,
    }


def extract_stage17_tail_report_metrics(report: dict[str, Any]) -> dict[str, float]:
    summary = dict(report.get("summary") or {})
    by_variation = dict(report.get("by_variation") or summary.get("by_variation") or {})
    st = dict(by_variation.get("tail_stability") or {})
    br = dict(by_variation.get("tail_base_rate") or {})
    nf = dict(by_variation.get("tail_narrative_fade") or {})
    decision = str(report.get("final_decision") or "NO_GO").upper()
    score = 1.0 if decision == "GO" else 0.75 if decision == "LIMITED_GO" else 0.0
    return {
        "stage17_closed_positions": float(summary.get("closed_positions") or 0.0),
        "stage17_hit_rate_tail": float(summary.get("hit_rate_tail") or 0.0),
        "stage17_roi_total": float(summary.get("roi_total") or 0.0),
        "stage17_avg_koef": float(summary.get("avg_koef") or 0.0),
        "stage17_best_win_koef": float(summary.get("best_win_koef") or 0.0),
        "stage17_payout_skew": float(summary.get("payout_skew") or 0.0),
        "stage17_payout_skew_ci_low_80": float(summary.get("payout_skew_ci_low_80") or 0.0),
        "stage17_top10pct_wins_count": float(summary.get("top10pct_wins_count") or 0.0),
        "stage17_avg_win_multiplier": float(summary.get("avg_win_multiplier") or 0.0),
        "stage17_time_to_resolution_median_days": float(summary.get("time_to_resolution_median_days") or 0.0),
        "stage17_max_concurrent_tail_positions": float(summary.get("max_concurrent_tail_positions") or 0.0),
        "stage17_tail_budget_used_pct": float(summary.get("tail_budget_used_pct") or 0.0),
        "stage17_circuit_breaker_active": 1.0 if bool(summary.get("circuit_breaker_active")) else 0.0,
        "stage17_closed_tail_stability": float(st.get("closed") or 0.0),
        "stage17_closed_tail_base_rate": float(br.get("closed") or 0.0),
        "stage17_closed_tail_narrative_fade": float(nf.get("closed") or 0.0),
        "stage17_win_rate_tail_stability": float(st.get("win_rate_tail") or 0.0),
        "stage17_win_rate_tail_base_rate": float(br.get("win_rate_tail") or 0.0),
        "stage17_win_rate_tail_narrative_fade": float(nf.get("win_rate_tail") or 0.0),
        "stage17_final_decision_score": score,
    }
