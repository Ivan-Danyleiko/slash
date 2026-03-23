"""
Stage19 Workstream A — Calibration Engine v1

Post-hoc probability calibration for signal predictions.
Trained on resolved SignalHistory rows (prob_at_signal vs actual outcome),
using purged time-split with embargo to prevent leakage.

Methods:
  sigmoid  — Platt scaling (logistic regression on raw probs). Default.
  isotonic — Isotonic regression. More flexible; requires larger n.
  passthrough — Identity. Used when n < min_samples.

Usage:
  calibrator = load_or_train_calibrator(db, settings)
  cal_prob = calibrator.calibrate(raw_prob)

Output fields added to execution payload:
  calibrated_prob_yes       — post-hoc adjusted probability
  calibration_version       — e.g. "sigmoid_v1" or "passthrough"
  calibration_confidence    — fraction of training samples used (0–1)
  calibration_ece           — Expected Calibration Error on hold-out
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.core.config import Settings


# ── Calibration math (no external ML libs required) ──────────────────────────

def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _platt_fit(probs: list[float], outcomes: list[float]) -> tuple[float, float]:
    """Fit Platt scaling (logistic regression) via gradient descent.

    Returns (a, b) such that calibrated_prob = sigmoid(a * raw_prob + b).
    Initialised with a=1, b=0 (identity). Uses fixed-step GD (100 iters).
    """
    a, b = 1.0, 0.0
    lr = 0.5
    n = len(probs)
    for _ in range(200):
        grad_a, grad_b = 0.0, 0.0
        for p, y in zip(probs, outcomes):
            pred = _sigmoid(a * p + b)
            err = pred - y
            grad_a += err * p
            grad_b += err
        a -= lr * grad_a / n
        b -= lr * grad_b / n
    return a, b


def _isotonic_fit(probs: list[float], outcomes: list[float]) -> list[tuple[float, float]]:
    """Pool adjacent violators (PAV) isotonic regression.

    Returns list of (threshold, calibrated_value) breakpoints.
    """
    # Bin into 20 equal-width buckets and apply PAV on bucket means.
    n_bins = min(20, max(5, len(probs) // 10))
    bins: list[list[float]] = [[] for _ in range(n_bins)]
    for p, y in zip(probs, outcomes):
        idx = min(int(p * n_bins), n_bins - 1)
        bins[idx].append(y)
    means = [(sum(b) / len(b)) if b else None for b in bins]
    filled = []
    for i, m in enumerate(means):
        if m is None:
            filled.append(filled[-1] if filled else 0.5)
        else:
            filled.append(m)

    # PAV: pool adjacent violators to enforce monotonicity.
    result = list(filled)
    changed = True
    while changed:
        changed = False
        i = 0
        while i < len(result) - 1:
            if result[i] > result[i + 1]:
                merged = (result[i] + result[i + 1]) / 2.0
                result[i] = merged
                result[i + 1] = merged
                changed = True
            i += 1
    breakpoints = [(i / n_bins, result[i]) for i in range(n_bins)]
    return breakpoints


def _apply_isotonic(prob: float, breakpoints: list[tuple[float, float]]) -> float:
    if not breakpoints:
        return prob
    thresholds = [bp[0] for bp in breakpoints]
    idx = 0
    for i, t in enumerate(thresholds):
        if prob >= t:
            idx = i
    return float(breakpoints[idx][1])


def _ece(probs_cal: list[float], outcomes: list[float], n_bins: int = 10) -> float:
    """Expected Calibration Error (equal-width bins)."""
    if not probs_cal:
        return 1.0
    bins: list[list[tuple[float, float]]] = [[] for _ in range(n_bins)]
    for p, y in zip(probs_cal, outcomes):
        idx = min(int(p * n_bins), n_bins - 1)
        bins[idx].append((p, y))
    n = len(probs_cal)
    ece = 0.0
    for b in bins:
        if not b:
            continue
        avg_conf = sum(p for p, _ in b) / len(b)
        avg_acc = sum(y for _, y in b) / len(b)
        ece += (len(b) / n) * abs(avg_conf - avg_acc)
    return round(ece, 6)


# ── Calibrator dataclass ──────────────────────────────────────────────────────

@dataclass
class SignalCalibrator:
    method: str = "passthrough"  # sigmoid | isotonic | passthrough
    # Platt params
    platt_a: float = 1.0
    platt_b: float = 0.0
    # Isotonic breakpoints
    iso_breakpoints: list[tuple[float, float]] = field(default_factory=list)
    # Meta
    n_train: int = 0
    ece_holdout: float = 1.0
    calibration_version: str = "passthrough_v1"
    trained_at: str = ""

    def calibrate(self, raw_prob: float) -> float:
        """Apply calibration to a raw probability."""
        raw_prob = max(0.001, min(0.999, float(raw_prob)))
        if self.method == "sigmoid":
            cal = _sigmoid(self.platt_a * raw_prob + self.platt_b)
        elif self.method == "isotonic" and self.iso_breakpoints:
            cal = _apply_isotonic(raw_prob, self.iso_breakpoints)
        else:
            return raw_prob
        return max(0.001, min(0.999, cal))

    @property
    def calibration_confidence(self) -> float:
        """0–1: fraction of recommended minimum samples (50) used."""
        return min(1.0, self.n_train / max(1, 50))


_PASSTHROUGH = SignalCalibrator(method="passthrough", calibration_version="passthrough_v1")


# ── Training from resolved SignalHistory ──────────────────────────────────────

def train_calibrator(
    db: "Session",
    *,
    settings: "Settings",
    signal_type_filter: str | None = None,
) -> SignalCalibrator:
    """Train calibrator on resolved SignalHistory rows.

    Uses purged time-split with embargo to prevent leakage:
    - Train: rows older than embargo cutoff
    - Holdout: rows from last 14 days before embargo

    Returns passthrough calibrator if insufficient data.
    """
    from sqlalchemy import select
    from app.models.models import SignalHistory

    min_samples = max(10, int(settings.stage19_calibration_min_samples))
    embargo_days = max(1, int(settings.stage19_calibration_embargo_days))
    method = str(settings.stage19_calibration_method or "sigmoid").strip().lower()

    now = datetime.now(UTC)
    embargo_cutoff = now - timedelta(days=embargo_days)
    holdout_start = embargo_cutoff - timedelta(days=14)

    # Load resolved rows (have both probability_at_signal + at least one outcome)
    q = select(SignalHistory).where(
        SignalHistory.probability_at_signal.is_not(None),
        SignalHistory.signal_id.is_not(None),
        SignalHistory.timestamp < embargo_cutoff,
    )
    if signal_type_filter:
        q = q.where(SignalHistory.signal_type == signal_type_filter)

    rows = list(db.scalars(q))
    # Resolved = has probability_after_24h (as proxy for outcome knowledge)
    resolved = [r for r in rows if r.probability_after_24h is not None]

    if len(resolved) < min_samples:
        return SignalCalibrator(
            method="passthrough",
            n_train=len(resolved),
            calibration_version="passthrough_v1_insufficient_data",
            trained_at=now.isoformat(),
        )

    # Build (prob, outcome) pairs.
    # outcome = 1 if probability went up (signal was correct), 0 otherwise.
    # For YES signals: outcome = 1 if prob_after > prob_at_signal.
    train_rows = [r for r in resolved if r.timestamp < holdout_start]
    holdout_rows = [r for r in resolved if holdout_start <= r.timestamp < embargo_cutoff]

    def _pairs(rlist) -> tuple[list[float], list[float]]:
        ps, ys = [], []
        for r in rlist:
            p = float(r.probability_at_signal)
            after = float(r.probability_after_24h)
            direction = str(r.signal_direction or "YES").upper()
            outcome = float(after > p) if direction == "YES" else float(after < p)
            ps.append(p)
            ys.append(outcome)
        return ps, ys

    if len(train_rows) < min_samples:
        # Use all resolved for training, skip holdout ECE
        train_probs, train_outcomes = _pairs(resolved)
        holdout_probs, holdout_outcomes = [], []
    else:
        train_probs, train_outcomes = _pairs(train_rows)
        holdout_probs, holdout_outcomes = _pairs(holdout_rows)

    # Fit model
    if method == "isotonic" and len(train_probs) >= min_samples * 2:
        iso_bp = _isotonic_fit(train_probs, train_outcomes)
        cal_version = "isotonic_v1"
        calibrator = SignalCalibrator(
            method="isotonic",
            iso_breakpoints=iso_bp,
            n_train=len(train_probs),
            calibration_version=cal_version,
            trained_at=now.isoformat(),
        )
    else:
        a, b = _platt_fit(train_probs, train_outcomes)
        cal_version = "sigmoid_v1"
        calibrator = SignalCalibrator(
            method="sigmoid",
            platt_a=a,
            platt_b=b,
            n_train=len(train_probs),
            calibration_version=cal_version,
            trained_at=now.isoformat(),
        )

    # Compute ECE on holdout
    if holdout_probs:
        cal_holdout = [calibrator.calibrate(p) for p in holdout_probs]
        calibrator.ece_holdout = _ece(cal_holdout, holdout_outcomes)
    else:
        # Self-ECE as rough proxy
        cal_train = [calibrator.calibrate(p) for p in train_probs]
        calibrator.ece_holdout = _ece(cal_train, train_outcomes)

    return calibrator


# ── Runtime cache: one calibrator per worker process ─────────────────────────

_CACHE: dict[str, tuple[SignalCalibrator, datetime]] = {}
_CACHE_TTL_HOURS = 6.0


def get_calibrator(
    db: "Session",
    *,
    settings: "Settings",
    signal_type_filter: str | None = None,
) -> SignalCalibrator:
    """Return cached calibrator or retrain if stale/missing."""
    if not bool(settings.stage19_calibration_enabled):
        return _PASSTHROUGH

    cache_key = signal_type_filter or "__all__"
    cached = _CACHE.get(cache_key)
    if cached is not None:
        cal, cached_at = cached
        age_hours = (datetime.now(UTC) - cached_at).total_seconds() / 3600.0
        if age_hours < _CACHE_TTL_HOURS:
            return cal

    cal = train_calibrator(db, settings=settings, signal_type_filter=signal_type_filter)
    _CACHE[cache_key] = (cal, datetime.now(UTC))
    return cal


def compute_baseline_ece(db: "Session", *, embargo_days: int = 7) -> float:
    """Compute raw (uncalibrated) ECE as baseline for Stage19 acceptance gate."""
    from sqlalchemy import select
    from app.models.models import SignalHistory

    cutoff = datetime.now(UTC) - timedelta(days=embargo_days)
    rows = list(
        db.scalars(
            select(SignalHistory).where(
                SignalHistory.probability_at_signal.is_not(None),
                SignalHistory.probability_after_24h.is_not(None),
                SignalHistory.signal_id.is_not(None),
                SignalHistory.timestamp < cutoff,
            )
        )
    )
    if not rows:
        return 1.0
    probs = [float(r.probability_at_signal) for r in rows]
    outcomes = []
    for r in rows:
        direction = str(r.signal_direction or "YES").upper()
        after = float(r.probability_after_24h)
        p = float(r.probability_at_signal)
        outcomes.append(float(after > p) if direction == "YES" else float(after < p))
    return _ece(probs, outcomes)
