"""
Stage19 Daily Quality Report

Computes pipeline effectiveness metrics, compares against baseline,
generates PASS/FAIL verdict per acceptance gate.

Outputs:
  - stage19_report dict (returned by build_stage19_report())
  - baseline artifact saved to DB (artifacts table or job_run details)

Metrics computed:
  utility_pass_rate         — fraction of evaluated signals passing utility gate
  signals_sent_per_day      — from UserEvent signal_sent (7d avg)
  ece_overall               — Expected Calibration Error (uncalibrated baseline)
  ece_calibrated            — ECE after Stage19A calibration
  brier_score               — raw Brier score on resolved SignalHistory
  brier_skill_score         — BSS vs naive climatology (0.5 prob baseline)
  expected_vs_realized_slippage_error — median abs error (best-effort from execution_analysis)
  post_cost_ev_ci_low_80    — 80% CI lower bound of slippage_adjusted_edge distribution
  lag_arb_count_24h         — LAG_ARB_CANDIDATE signals in last 24h
  structural_arb_valid_24h  — STRUCTURAL_ARB_CANDIDATE signals (valid) in last 24h

Drift metrics (vs 7d ago):
  utility_drift_7d          — change in utility_pass_rate
  ece_drift_7d              — change in ece_overall
  spread_drift_7d           — change in avg spread (from execution_analysis)

Go/No-Go gates:
  gate_ece_improvement      — ece_calibrated < ece_overall * (1 - threshold)
  gate_slippage_error       — slippage_error < gate_slippage_error_max
  gate_no_stage17_regression — stage17 gate metrics not degraded
"""
from __future__ import annotations

import json
import math
import statistics
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.core.config import Settings


# ── Brier score helpers ───────────────────────────────────────────────────────

def _brier_score(probs: list[float], outcomes: list[float]) -> float:
    if not probs:
        return 1.0
    return round(sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / len(probs), 6)


def _brier_skill_score(brier: float, climatology_prob: float = 0.5) -> float:
    """BSS = 1 - Brier / Brier_ref. ref = naive climatology at constant 0.5."""
    brier_ref = climatology_prob * (1.0 - climatology_prob)  # = 0.25
    if brier_ref <= 0:
        return 0.0
    return round(1.0 - brier / brier_ref, 4)


def _ece(probs: list[float], outcomes: list[float], n_bins: int = 10) -> float:
    if not probs:
        return 1.0
    bins: list[list[tuple[float, float]]] = [[] for _ in range(n_bins)]
    for p, y in zip(probs, outcomes):
        idx = min(int(p * n_bins), n_bins - 1)
        bins[idx].append((p, y))
    n = len(probs)
    ece = 0.0
    for b in bins:
        if not b:
            continue
        avg_conf = sum(p for p, _ in b) / len(b)
        avg_acc = sum(y for _, y in b) / len(b)
        ece += (len(b) / n) * abs(avg_conf - avg_acc)
    return round(ece, 6)


def _percentile(data: list[float], pct: float) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = (pct / 100.0) * (len(sorted_data) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_data) - 1)
    frac = idx - lo
    return sorted_data[lo] * (1.0 - frac) + sorted_data[hi] * frac


# ── Main report builder ───────────────────────────────────────────────────────

def build_stage19_report(db: "Session", *, settings: "Settings") -> dict:
    from sqlalchemy import func, select
    from app.models.enums import SignalType
    from app.models.models import JobRun, Signal, SignalHistory, UserEvent
    from app.services.signals.calibration import get_calibrator, compute_baseline_ece

    now = datetime.now(UTC)
    window_24h = now - timedelta(hours=24)
    window_7d = now - timedelta(days=7)
    embargo_days = int(getattr(settings, "stage19_calibration_embargo_days", 7))
    embargo_cutoff = now - timedelta(days=embargo_days)

    # ── 1. utility_pass_rate ─────────────────────────────────────────────────
    # Proxy: fraction of signal_push runs that had signals_sent > 0 over 7d
    push_runs = list(
        db.scalars(
            select(JobRun).where(
                JobRun.job_name == "signal_push",
                JobRun.status == "SUCCESS",
                JobRun.started_at >= window_7d,
            )
        )
    )
    push_total = len(push_runs)
    push_sent = sum(
        1 for r in push_runs
        if int((r.details or {}).get("signals_sent") or 0) > 0
    )
    utility_pass_rate = round(push_sent / max(1, push_total), 4)

    # ── 2. signals_sent_per_day ──────────────────────────────────────────────
    signals_sent_7d = int(
        db.scalar(
            select(func.count()).select_from(UserEvent).where(
                UserEvent.event_type == "signal_sent",
                UserEvent.created_at >= window_7d,
            )
        ) or 0
    )
    signals_sent_per_day = round(signals_sent_7d / 7.0, 2)

    # ── 3. Brier + ECE on resolved SignalHistory ─────────────────────────────
    resolved_rows = list(
        db.scalars(
            select(SignalHistory).where(
                SignalHistory.probability_at_signal.is_not(None),
                SignalHistory.probability_after_24h.is_not(None),
                SignalHistory.signal_id.is_not(None),
                SignalHistory.timestamp >= window_7d,
                SignalHistory.timestamp < embargo_cutoff,
            )
        )
    )
    raw_probs, outcomes = [], []
    for r in resolved_rows:
        p = float(r.probability_at_signal)
        after = float(r.probability_after_24h)
        direction = str(r.signal_direction or "YES").upper()
        outcome = float(after > p) if direction == "YES" else float(after < p)
        raw_probs.append(p)
        outcomes.append(outcome)

    brier_raw = _brier_score(raw_probs, outcomes)
    brier_skill = _brier_skill_score(brier_raw)
    ece_overall = _ece(raw_probs, outcomes)

    # ECE with calibration
    ece_calibrated = ece_overall  # default: no improvement
    calibration_version = "passthrough_v1"
    if bool(getattr(settings, "stage19_calibration_enabled", True)) and raw_probs:
        try:
            cal = get_calibrator(db, settings=settings)
            cal_probs = [cal.calibrate(p) for p in raw_probs]
            ece_calibrated = _ece(cal_probs, outcomes)
            calibration_version = cal.calibration_version
        except Exception:  # noqa: BLE001
            pass

    # ── 4. Slippage error ────────────────────────────────────────────────────
    # Compare expected_costs_pct vs slippage_factor in execution_analysis (best-effort)
    recent_signals = list(
        db.scalars(
            select(Signal).where(
                Signal.execution_analysis.is_not(None),
                Signal.created_at >= window_7d,
            ).limit(500)
        )
    )
    slippage_errors: list[float] = []
    post_cost_evs: list[float] = []
    spreads: list[float] = []
    for s in recent_signals:
        ex = s.execution_analysis or {}
        expected_costs = float(ex.get("expected_costs_pct") or ex.get("costs_pct_effective") or 0.0)
        slippage_factor = float(ex.get("slippage_factor") or 0.0)
        if expected_costs > 0 and slippage_factor > 0:
            slippage_errors.append(abs(expected_costs - slippage_factor))
        ev = float(ex.get("expected_ev_after_costs_pct") or ex.get("slippage_adjusted_edge") or 0.0)
        if ev != 0.0:
            post_cost_evs.append(ev)
        spread = float(ex.get("spread_cost_pct") or 0.0)
        if spread > 0:
            spreads.append(spread)

    slippage_error = round(statistics.median(slippage_errors), 4) if slippage_errors else None
    post_cost_ev_ci_low_80 = round(_percentile(post_cost_evs, 10), 6) if post_cost_evs else None
    avg_spread = round(statistics.mean(spreads), 4) if spreads else None

    # ── 5. Lag arb + structural arb counts ──────────────────────────────────
    lag_arb_24h = int(
        db.scalar(
            select(func.count()).select_from(Signal).where(
                Signal.signal_type == SignalType.LAG_ARB_CANDIDATE,
                Signal.created_at >= window_24h,
            )
        ) or 0
    )
    structural_arb_valid_24h = int(
        db.scalar(
            select(func.count()).select_from(Signal).where(
                Signal.signal_type == SignalType.STRUCTURAL_ARB_CANDIDATE,
                Signal.created_at >= window_24h,
            )
        ) or 0
    )

    # ── 6. Stage17 regression check ─────────────────────────────────────────
    s17_runs_7d = list(
        db.scalars(
            select(JobRun).where(
                JobRun.job_name == "stage17_cycle",
                JobRun.status == "SUCCESS",
                JobRun.started_at >= window_7d,
            ).limit(100)
        )
    )
    s17_opened_7d = sum(int((r.details or {}).get("opened") or 0) for r in s17_runs_7d)
    s17_total_runs = len(s17_runs_7d)
    s17_failed_7d = int(
        db.scalar(
            select(func.count()).select_from(JobRun).where(
                JobRun.job_name == "stage17_cycle",
                JobRun.status == "FAILED",
                JobRun.started_at >= window_7d,
            )
        ) or 0
    )
    s17_fail_rate = round(s17_failed_7d / max(1, s17_total_runs + s17_failed_7d), 4)

    # ── 7. Drift (vs 7d ago window comparison) ──────────────────────────────
    # For drift we approximate: compare current vs prior_7d metrics from job_runs.
    # Simple heuristic: utility_drift = current utility_pass_rate vs prior week
    prior_push_runs = list(
        db.scalars(
            select(JobRun).where(
                JobRun.job_name == "signal_push",
                JobRun.status == "SUCCESS",
                JobRun.started_at >= window_7d - timedelta(days=7),
                JobRun.started_at < window_7d,
            )
        )
    )
    prior_push_total = len(prior_push_runs)
    prior_push_sent = sum(
        1 for r in prior_push_runs
        if int((r.details or {}).get("signals_sent") or 0) > 0
    )
    prior_utility_pass_rate = round(prior_push_sent / max(1, prior_push_total), 4) if prior_push_total > 0 else None
    utility_drift_7d = round(utility_pass_rate - prior_utility_pass_rate, 4) if prior_utility_pass_rate is not None else None

    # ── 8. PASS/FAIL gates ───────────────────────────────────────────────────
    gate_ece_improvement_pct = float(getattr(settings, "stage19_gate_ece_improvement_pct", 0.20))
    gate_slippage_error_max = float(getattr(settings, "stage19_gate_slippage_error_max", 0.20))

    ece_threshold = ece_overall * (1.0 - gate_ece_improvement_pct)
    gate_ece = (ece_calibrated <= ece_threshold) if raw_probs else None
    gate_slippage = (slippage_error is not None and slippage_error < gate_slippage_error_max)
    gate_s17_regression = (s17_fail_rate <= 0.20)
    gate_brier = (brier_raw <= 0.25)  # absolute cap: not worse than pure random

    all_defined_gates = [g for g in [gate_ece, gate_slippage, gate_s17_regression, gate_brier] if g is not None]
    overall_pass = all(all_defined_gates) if all_defined_gates else None

    verdict = "PASS" if overall_pass is True else ("FAIL" if overall_pass is False else "INCONCLUSIVE")

    # ── 9. Drift alerts ──────────────────────────────────────────────────────
    drift_alerts: list[str] = []
    drift_util_threshold = float(getattr(settings, "stage19_drift_utility_threshold", 0.20))
    drift_ece_threshold = float(getattr(settings, "stage19_drift_ece_threshold", 0.05))
    if utility_drift_7d is not None and abs(utility_drift_7d) > drift_util_threshold:
        drift_alerts.append(f"utility_drift={utility_drift_7d:+.2%} (>{drift_util_threshold:.0%} threshold)")
    if ece_calibrated > ece_overall + drift_ece_threshold:
        drift_alerts.append(f"ece_regression: calibrated={ece_calibrated:.4f} > baseline={ece_overall:.4f}")

    report = {
        "generated_at": now.isoformat(),
        "window_7d": window_7d.isoformat(),
        "verdict": verdict,
        # Core metrics
        "utility_pass_rate": utility_pass_rate,
        "signals_sent_per_day": signals_sent_per_day,
        "brier_score": brier_raw,
        "brier_skill_score": brier_skill,
        "ece_overall": ece_overall,
        "ece_calibrated": ece_calibrated,
        "calibration_version": calibration_version,
        "n_resolved_samples": len(raw_probs),
        "expected_vs_realized_slippage_error": slippage_error,
        "post_cost_ev_ci_low_80": post_cost_ev_ci_low_80,
        "avg_spread": avg_spread,
        # Signal counts
        "lag_arb_signals_24h": lag_arb_24h,
        "structural_arb_signals_24h": structural_arb_valid_24h,
        # Stage17 regression
        "stage17_opened_7d": s17_opened_7d,
        "stage17_fail_rate_7d": s17_fail_rate,
        # Drift
        "utility_drift_7d": utility_drift_7d,
        "drift_alerts": drift_alerts,
        # Gates
        "gates": {
            "ece_improvement": {"pass": gate_ece, "threshold": round(ece_threshold, 6)},
            "slippage_error": {"pass": gate_slippage, "threshold": gate_slippage_error_max},
            "stage17_no_regression": {"pass": gate_s17_regression, "threshold": 0.20},
            "brier_not_random": {"pass": gate_brier, "threshold": 0.25},
        },
    }
    return report


def build_stage19_baseline(db: "Session", *, settings: "Settings") -> dict:
    """Take a one-time baseline snapshot. Returns existing baseline if already taken today."""
    from sqlalchemy import select
    from app.models.models import JobRun

    today = datetime.now(UTC).date()
    existing = db.scalar(
        select(JobRun).where(
            JobRun.job_name == "stage19_baseline",
            JobRun.status == "SUCCESS",
            JobRun.started_at >= datetime.combine(today, datetime.min.time(), tzinfo=UTC),
        )
    )
    if existing:
        return {"skipped": True, "reason": "baseline_already_taken_today", "existing_id": existing.id}

    report = build_stage19_report(db, settings=settings)
    baseline = {
        "baseline_date": today.isoformat(),
        "utility_pass_rate": report["utility_pass_rate"],
        "signals_sent_per_day": report["signals_sent_per_day"],
        "brier_score": report["brier_score"],
        "ece_overall": report["ece_overall"],
        "expected_vs_realized_slippage_error": report["expected_vs_realized_slippage_error"],
        "post_cost_ev_ci_low_80": report["post_cost_ev_ci_low_80"],
        "guard_thresholds": {
            "min_signals_sent_per_day": max(0, (report["signals_sent_per_day"] or 0) - 1),
            "max_brier_score": round((report["brier_score"] or 0.25) + 0.05, 4),
            "max_slippage_error": round((report["expected_vs_realized_slippage_error"] or 0.20) * 1.5, 4),
        },
    }
    return baseline
