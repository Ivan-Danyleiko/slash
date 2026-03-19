#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.models import Market, Signal, SignalHistory, Stage7AgentDecision


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _normalize_side(direction: str | None) -> str:
    v = str(direction or "YES").strip().upper()
    return "NO" if v == "NO" else "YES"


def _normalize_category(raw: str | None) -> str:
    t = str(raw or "").strip().lower()
    if any(k in t for k in ("crypto", "bitcoin", "ethereum", "solana", "token")):
        return "crypto"
    if any(k in t for k in ("sport", "nba", "nfl", "mlb", "soccer", "football", "championship", "tennis")):
        return "sports"
    if any(k in t for k in ("finance", "stock", "fed", "rate", "inflation", "gdp", "earnings", "nasdaq", "sp500")):
        return "finance"
    if any(k in t for k in ("politic", "election", "president", "senate", "congress", "white house", "war")):
        return "politics"
    return "other"


def _normalize_signal_mode(raw: str | None) -> str:
    t = str(raw or "").strip().lower()
    if not t:
        return "unknown"
    if "momentum" in t:
        return "momentum"
    if "uncertainty" in t or "liquid" in t:
        return "uncertainty_liquid"
    return t


def _extract_entry_yes_price(
    signal: Signal,
    market: Market,
    decision: Stage7AgentDecision,
    signal_prob_map: dict[int, float],
) -> float | None:
    candidates: list[float | None] = []

    meta = signal.metadata_json or {}
    if isinstance(meta, dict):
        candidates.extend(
            [
                _as_float(meta.get("ask_price")),
                _as_float(meta.get("best_ask_yes")),
                _as_float(meta.get("market_prob")),
                _as_float(meta.get("probability_yes")),
            ]
        )

    evidence = decision.evidence_bundle or {}
    if isinstance(evidence, dict):
        ims = evidence.get("internal_metrics_snapshot") or {}
        if isinstance(ims, dict):
            candidates.extend(
                [
                    _as_float(ims.get("market_prob")),
                    _as_float(ims.get("probability_yes")),
                ]
            )
        consensus = evidence.get("external_consensus") or {}
        if isinstance(consensus, dict):
            candidates.extend(
                [
                    _as_float(consensus.get("polymarket_prob")),
                    _as_float(consensus.get("manifold_prob")),
                    _as_float(consensus.get("metaculus_median")),
                ]
            )

    candidates.append(signal_prob_map.get(signal.id))
    candidates.append(_as_float(market.probability_yes))

    for p in candidates:
        if p is None:
            continue
        if 0.0 <= p <= 1.0:
            return p
    return None


def _extract_kelly(signal: Signal, decision: Stage7AgentDecision) -> float | None:
    evidence = decision.evidence_bundle or {}
    if isinstance(evidence, dict):
        ims = evidence.get("internal_metrics_snapshot") or {}
        if isinstance(ims, dict):
            k = _as_float(ims.get("kelly_fraction"))
            if k is not None:
                return k
        k = _as_float(evidence.get("kelly_fraction"))
        if k is not None:
            return k
    ea = signal.execution_analysis or {}
    if isinstance(ea, dict):
        return _as_float(ea.get("kelly_fraction"))
    return None


def _extract_score(signal: Signal, decision: Stage7AgentDecision) -> float | None:
    score = _as_float(signal.confidence_score)
    if score is not None:
        return score
    sb = signal.score_breakdown_json or {}
    if isinstance(sb, dict):
        score = _as_float(sb.get("composite_score"))
        if score is not None:
            return score
    evidence = decision.evidence_bundle or {}
    if isinstance(evidence, dict):
        ims = evidence.get("internal_metrics_snapshot") or {}
        if isinstance(ims, dict):
            return _as_float(ims.get("confidence"))
    return None


def _pnl_per_1usd(side: str, entry_yes: float, outcome: str) -> float:
    if side == "YES":
        return (1.0 - entry_yes) if outcome == "YES" else (-entry_yes)
    # side == "NO": buy NO share at price (1 - p_yes)
    return entry_yes if outcome == "NO" else (-(1.0 - entry_yes))


def run(
    days: int,
    limit: int,
    *,
    invert_momentum: bool = False,
    invert_uncertainty_liquid: bool = False,
    mode_filter: set[str] | None = None,
) -> dict[str, Any]:
    cutoff = datetime.now(UTC) - timedelta(days=max(1, int(days)))
    with SessionLocal() as db:
        latest_keep_ids = (
            select(
                Stage7AgentDecision.signal_id.label("signal_id"),
                func.max(Stage7AgentDecision.id).label("max_id"),
            )
            .where(
                Stage7AgentDecision.signal_id.is_not(None),
                Stage7AgentDecision.decision == "KEEP",
                Stage7AgentDecision.created_at >= cutoff,
            )
            .group_by(Stage7AgentDecision.signal_id)
            .subquery()
        )

        rows = list(
            db.execute(
                select(Signal, Market, Stage7AgentDecision)
                .join(Market, Market.id == Signal.market_id)
                .join(
                    latest_keep_ids,
                    latest_keep_ids.c.signal_id == Signal.id,
                )
                .join(
                    Stage7AgentDecision,
                    Stage7AgentDecision.id == latest_keep_ids.c.max_id,
                )
                .order_by(Stage7AgentDecision.created_at.desc())
                .limit(max(1, int(limit)))
            )
            .all()
        )

        signal_ids = [int(s.id) for s, _, _ in rows]
        if not signal_ids:
            return {
                "days": days,
                "signals_considered": 0,
                "resolved_signals": 0,
                "message": "No latest KEEP decisions in window.",
            }

        hist_rows = list(
            db.execute(
                select(
                    SignalHistory.signal_id,
                    SignalHistory.resolved_outcome,
                    SignalHistory.probability_at_signal,
                    SignalHistory.timestamp,
                )
                .where(
                    SignalHistory.signal_id.in_(signal_ids),
                    SignalHistory.resolved_outcome.in_(["YES", "NO"]),
                )
                .order_by(SignalHistory.signal_id, SignalHistory.timestamp.desc())
            )
            .all()
        )

        resolved_map: dict[int, str] = {}
        signal_prob_map: dict[int, float] = {}
        for sid, outcome, p0, _ in hist_rows:
            if sid is None:
                continue
            sid_int = int(sid)
            if sid_int not in resolved_map and outcome in ("YES", "NO"):
                resolved_map[sid_int] = str(outcome)
            if sid_int not in signal_prob_map:
                p = _as_float(p0)
                if p is not None and 0.0 <= p <= 1.0:
                    signal_prob_map[sid_int] = p

        per_category: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "wins": 0, "pnl_sum_per_1usd": 0.0}
        )
        per_signal_mode: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "wins": 0, "pnl_sum_per_1usd": 0.0}
        )
        score_values: list[float] = []
        kelly_values: list[float] = []
        pnl_values: list[float] = []
        wins = 0
        resolved_total = 0

        for signal, market, decision in rows:
            outcome = resolved_map.get(int(signal.id))
            if outcome not in ("YES", "NO"):
                continue

            entry_yes = _extract_entry_yes_price(signal, market, decision, signal_prob_map)
            if entry_yes is None:
                continue

            mode = _normalize_signal_mode(signal.signal_mode)
            if mode_filter and mode not in mode_filter:
                continue

            side = _normalize_side(signal.signal_direction)
            if invert_momentum and mode == "momentum":
                side = "NO" if side == "YES" else "YES"
            if invert_uncertainty_liquid and mode == "uncertainty_liquid":
                side = "NO" if side == "YES" else "YES"
            pnl = _pnl_per_1usd(side, entry_yes, outcome)
            win = pnl > 0.0

            resolved_total += 1
            wins += 1 if win else 0
            pnl_values.append(pnl)

            cat = _normalize_category(market.category or signal.title)
            bucket = per_category[cat]
            bucket["count"] += 1
            bucket["wins"] += 1 if win else 0
            bucket["pnl_sum_per_1usd"] += float(pnl)

            mode_bucket = per_signal_mode[mode]
            mode_bucket["count"] += 1
            mode_bucket["wins"] += 1 if win else 0
            mode_bucket["pnl_sum_per_1usd"] += float(pnl)

            score = _extract_score(signal, decision)
            if score is not None:
                score_values.append(score)

            kelly = _extract_kelly(signal, decision)
            if kelly is not None:
                kelly_values.append(kelly)

        if resolved_total == 0:
            return {
                "days": days,
                "signals_considered": len(rows),
                "resolved_signals": 0,
                "message": "No resolved YES/NO outcomes for selected KEEP signals.",
            }

        score_values.sort()
        q50 = score_values[len(score_values) // 2] if score_values else None
        q75 = score_values[int(len(score_values) * 0.75)] if score_values else None
        q90 = score_values[int(len(score_values) * 0.90)] if score_values else None

        per_category_out: dict[str, Any] = {}
        for cat, agg in per_category.items():
            count = int(agg["count"])
            pnl_sum = float(agg["pnl_sum_per_1usd"])
            per_category_out[cat] = {
                "count": count,
                "win_rate": round(float(agg["wins"]) / count, 6) if count > 0 else None,
                "roi_per_1usd": round(pnl_sum / count, 6) if count > 0 else None,
            }

        per_signal_mode_out: dict[str, Any] = {}
        for mode, agg in per_signal_mode.items():
            count = int(agg["count"])
            pnl_sum = float(agg["pnl_sum_per_1usd"])
            per_signal_mode_out[mode] = {
                "count": count,
                "win_rate": round(float(agg["wins"]) / count, 6) if count > 0 else None,
                "roi_per_1usd": round(pnl_sum / count, 6) if count > 0 else None,
            }

        return {
            "days": days,
            "invert_momentum": invert_momentum,
            "invert_uncertainty_liquid": invert_uncertainty_liquid,
            "mode_filter": sorted(mode_filter) if mode_filter else None,
            "signals_considered": len(rows),
            "resolved_signals": resolved_total,
            "win_rate": round(wins / resolved_total, 6),
            "roi_per_1usd": round(sum(pnl_values) / resolved_total, 6),
            "avg_kelly": round(sum(kelly_values) / len(kelly_values), 6) if kelly_values else None,
            "score_distribution": {
                "count": len(score_values),
                "min": round(score_values[0], 6) if score_values else None,
                "q50": round(q50, 6) if q50 is not None else None,
                "q75": round(q75, 6) if q75 is not None else None,
                "q90": round(q90, 6) if q90 is not None else None,
                "max": round(score_values[-1], 6) if score_values else None,
            },
            "per_category": per_category_out,
            "per_signal_mode": per_signal_mode_out,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Historical backtest for latest Stage7 KEEP decisions.")
    parser.add_argument("--days", type=int, default=730, help="Lookback window in days.")
    parser.add_argument("--limit", type=int, default=5000, help="Max latest KEEP signals to evaluate.")
    parser.add_argument(
        "--invert-momentum",
        action="store_true",
        help="Invert side only for momentum signals (A/B hypothesis test).",
    )
    parser.add_argument(
        "--mode-filter",
        type=str,
        default="",
        help="Comma-separated signal modes to include (e.g. momentum,uncertainty_liquid).",
    )
    parser.add_argument(
        "--invert-uncertainty-liquid",
        action="store_true",
        help="Invert side only for uncertainty_liquid signals (A/B hypothesis test).",
    )
    args = parser.parse_args()
    mode_filter = {
        _normalize_signal_mode(x)
        for x in (args.mode_filter or "").split(",")
        if str(x).strip()
    }
    report = run(
        days=args.days,
        limit=args.limit,
        invert_momentum=bool(args.invert_momentum),
        invert_uncertainty_liquid=bool(args.invert_uncertainty_liquid),
        mode_filter=mode_filter or None,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
