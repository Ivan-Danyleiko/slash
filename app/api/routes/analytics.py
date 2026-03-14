from datetime import UTC, datetime, timedelta
import csv
from io import StringIO
from math import log

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.enums import SignalType
from app.db.session import get_db
from app.models.models import (
    DuplicatePairCandidate,
    DuplicateMarketPair,
    JobRun,
    LiquidityAnalysis,
    Market,
    MarketSnapshot,
    Platform,
    RulesAnalysis,
    Signal,
    SignalHistory,
    SignalQualityMetrics,
    User,
    UserEvent,
)
from app.services.analyzers.duplicate import DuplicateDetector
from app.services.research.stage5 import (
    build_divergence_decision,
    build_monte_carlo_summary,
    build_result_tables,
    build_signal_history_dataset,
    build_threshold_summary,
)
from app.services.research.ab_testing import (
    build_ab_testing_report,
    extract_ab_testing_metrics,
)
from app.services.research.data_quality import (
    build_signal_history_data_quality_report,
    extract_data_quality_metrics,
)
from app.services.research.deliverables import (
    build_build_vs_buy_time_saved_estimate,
    build_research_stack_readiness_report,
    build_stack_decision_log,
    extract_build_vs_buy_metrics,
)
from app.services.research.ethics import build_ethics_report, extract_ethics_metrics
from app.services.research.event_cluster_research import (
    build_event_cluster_research_report,
    extract_event_cluster_metrics,
)
from app.services.research.final_report import (
    build_stage5_final_report,
    extract_stage5_final_report_metrics,
)
from app.services.research.export_package import (
    build_stage5_export_decision_rows,
    build_stage5_export_package,
)
from app.services.research.provider_reliability import (
    build_provider_reliability_report,
    extract_provider_reliability_metrics,
)
from app.services.research.platform_comparison import (
    build_platform_comparison_report,
    extract_platform_comparison_metrics,
)
from app.services.research.liquidity_safety import (
    build_liquidity_safety_report,
    extract_liquidity_safety_metrics,
)
from app.services.research.ranking_research import (
    build_ranking_research_report,
    extract_ranking_research_metrics,
)
from app.services.research.readiness_gate import (
    build_stage5_readiness_gate,
    extract_stage5_readiness_gate_metrics,
)
from app.services.research.signal_type_research import (
    build_signal_type_research_report,
    extract_signal_type_research_metrics,
)
from app.services.research.signal_type_optimization import (
    build_signal_type_optimization_report,
    extract_signal_type_optimization_metrics,
)
from app.services.research.signal_lifetime import (
    build_signal_lifetime_report,
    extract_signal_lifetime_metrics,
)
from app.services.research.walkforward import (
    build_walkforward_report,
    extract_walkforward_metrics,
)
from app.services.research.stage6_governance import (
    build_stage6_governance_report,
    extract_stage6_governance_metrics,
)
from app.services.research.stage6_risk_guardrails import (
    build_stage6_risk_guardrails_report,
    extract_stage6_risk_guardrails_metrics,
)
from app.services.research.stage6_type35 import (
    build_stage6_type35_report,
    extract_stage6_type35_metrics,
)
from app.services.research.stage6_final_report import (
    build_stage6_final_report,
    extract_stage6_final_report_metrics,
)
from app.services.research.stage7_stack_scorecard import (
    build_stage7_stack_scorecard_report,
    extract_stage7_stack_scorecard_metrics,
)
from app.services.research.stage7_harness import (
    build_stage7_harness_report,
    extract_stage7_harness_metrics,
)
from app.services.research.stage7_shadow import (
    build_stage7_shadow_report,
    extract_stage7_shadow_metrics,
)
from app.services.research.stage7_final_report import (
    build_stage7_final_report,
    extract_stage7_final_report_metrics,
)
from app.services.research.tracking import read_stage5_experiments, record_stage5_experiment
from app.services.agent.policy import build_agent_decision_report
from app.services.signals.ranking import select_top_signals

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _parse_thresholds_csv(thresholds: str) -> list[float]:
    parsed_thresholds: list[float] = []
    for raw in thresholds.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid threshold '{raw}'") from exc
        if value < 0.0 or value > 1.0:
            raise HTTPException(status_code=400, detail=f"threshold '{raw}' out of [0,1] range")
        parsed_thresholds.append(value)
    return parsed_thresholds


def _parse_float_csv(values: str, *, min_value: float | None = None, max_value: float | None = None) -> list[float]:
    parsed: list[float] = []
    for raw in values.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid float value '{raw}'") from exc
        if min_value is not None and value < min_value:
            raise HTTPException(status_code=400, detail=f"value '{raw}' below min {min_value}")
        if max_value is not None and value > max_value:
            raise HTTPException(status_code=400, detail=f"value '{raw}' above max {max_value}")
        parsed.append(value)
    return parsed


def _parse_str_csv(values: str) -> list[str]:
    return [x.strip() for x in values.split(",") if x.strip()]


def _signal_diversity(signals_by_type: dict[str, int]) -> float:
    total = sum(int(v) for v in signals_by_type.values())
    if total <= 0:
        return 0.0
    probs = [int(v) / total for v in signals_by_type.values() if int(v) > 0]
    if not probs:
        return 0.0
    entropy = -sum(p * log(p) for p in probs)
    max_entropy = log(max(2, len(SignalType)))
    return min(1.0, max(0.0, entropy / max_entropy))


@router.get("/duplicates")
def duplicates(db: Session = Depends(get_db)) -> list[dict]:
    rows = list(db.scalars(select(DuplicateMarketPair).order_by(DuplicateMarketPair.similarity_score.desc()).limit(100)))
    return [
        {
            "id": r.id,
            "market_a_id": r.market_a_id,
            "market_b_id": r.market_b_id,
            "similarity_score": r.similarity_score,
            "similarity_explanation": r.similarity_explanation,
            "divergence_score": r.divergence_score,
        }
        for r in rows
    ]


@router.get("/duplicate-candidates")
def duplicate_candidates(
    stage: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
) -> list[dict]:
    stmt = select(DuplicatePairCandidate).order_by(DuplicatePairCandidate.similarity_score.desc())
    if stage:
        stmt = stmt.where(DuplicatePairCandidate.stage == stage)
    rows = list(db.scalars(stmt.limit(min(1000, max(1, limit)))))
    return [
        {
            "id": r.id,
            "market_a_id": r.market_a_id,
            "market_b_id": r.market_b_id,
            "stage": r.stage,
            "similarity_score": r.similarity_score,
            "similarity_explanation": r.similarity_explanation,
            "drop_reason": r.drop_reason,
            "created_at": r.created_at,
        }
        for r in rows
    ]


@router.get("/duplicate-drop-reasons")
def duplicate_drop_reasons(db: Session = Depends(get_db)) -> dict:
    rows = (
        db.execute(
            select(DuplicatePairCandidate.drop_reason, func.count(DuplicatePairCandidate.id))
            .where(DuplicatePairCandidate.stage == "strict_fail")
            .group_by(DuplicatePairCandidate.drop_reason)
            .order_by(func.count(DuplicatePairCandidate.id).desc())
        )
        .all()
    )
    return {
        "strict_fail_total": int(sum(int(count) for _, count in rows)),
        "reasons": {str(reason or "unknown"): int(count) for reason, count in rows},
    }


@router.get("/duplicate-shadow")
def duplicate_shadow(
    broad_threshold: float | None = None,
    broad_relaxed_fuzzy_min: float | None = None,
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    markets = list(db.scalars(select(Market)))

    broad = DuplicateDetector.with_profile(settings=settings, profile="aggressive")
    broad.min_overlap = settings.signal_duplicate_broad_min_overlap
    broad.min_jaccard = settings.signal_duplicate_broad_min_jaccard
    broad.min_weighted_overlap = settings.signal_duplicate_broad_min_weighted_overlap
    broad.anchor_idf = 0.0
    threshold = (
        float(broad_threshold)
        if isinstance(broad_threshold, (int, float))
        else float(settings.signal_duplicate_broad_threshold)
    )
    if isinstance(broad_relaxed_fuzzy_min, (int, float)):
        broad.broad_relaxed_fuzzy_min = float(broad_relaxed_fuzzy_min)
    broad_pairs = broad.find_pairs(markets, threshold)

    strict = DuplicateDetector.with_profile(settings=settings, profile="strict")
    balanced = DuplicateDetector.with_profile(settings=settings, profile="balanced")
    aggressive = DuplicateDetector.with_profile(settings=settings, profile="aggressive")

    strict_pass = 0
    balanced_pass = 0
    aggressive_pass = 0
    rescued_by_balanced: list[dict] = []
    rescued_by_aggressive: list[dict] = []
    for a, b, sim, _ in broad_pairs:
        s_ok, _, s_expl, s_drop = strict.evaluate_pair(a, b, settings.signal_duplicate_threshold)
        b_ok, _, b_expl, _ = balanced.evaluate_pair(a, b, settings.signal_duplicate_threshold)
        g_ok, _, g_expl, _ = aggressive.evaluate_pair(a, b, settings.signal_duplicate_threshold)
        strict_pass += int(s_ok)
        balanced_pass += int(b_ok)
        aggressive_pass += int(g_ok)
        if (not s_ok) and b_ok and len(rescued_by_balanced) < 20:
            rescued_by_balanced.append(
                {
                    "market_a_id": a.id,
                    "market_b_id": b.id,
                    "market_a_title": a.title,
                    "market_b_title": b.title,
                    "broad_similarity": round(sim, 2),
                    "strict_drop_reason": s_drop,
                    "strict_explanation": s_expl,
                    "balanced_explanation": b_expl,
                }
            )
        if (not s_ok) and g_ok and len(rescued_by_aggressive) < 20:
            rescued_by_aggressive.append(
                {
                    "market_a_id": a.id,
                    "market_b_id": b.id,
                    "market_a_title": a.title,
                    "market_b_title": b.title,
                    "broad_similarity": round(sim, 2),
                    "strict_drop_reason": s_drop,
                    "strict_explanation": s_expl,
                    "aggressive_explanation": g_expl,
                }
            )

    return {
        "markets_total": len(markets),
        "broad_candidates": len(broad_pairs),
        "strict_pass": strict_pass,
        "balanced_pass": balanced_pass,
        "aggressive_pass": aggressive_pass,
        "delta_balanced_vs_strict": balanced_pass - strict_pass,
        "delta_aggressive_vs_strict": aggressive_pass - strict_pass,
        "params": {
            "broad_threshold": threshold,
            "broad_relaxed_fuzzy_min": broad.broad_relaxed_fuzzy_min,
            "strict_threshold": settings.signal_duplicate_threshold,
        },
        "rescued_by_balanced_examples": rescued_by_balanced,
        "rescued_by_aggressive_examples": rescued_by_aggressive,
    }


@router.get("/liquidity-risk")
def liquidity_risk(db: Session = Depends(get_db)) -> list[dict]:
    rows = list(db.scalars(select(LiquidityAnalysis).order_by(LiquidityAnalysis.score.asc()).limit(100)))
    return [{"market_id": r.market_id, "score": r.score, "level": r.level} for r in rows]


@router.get("/rules-risk")
def rules_risk(db: Session = Depends(get_db)) -> list[dict]:
    rows = list(db.scalars(select(RulesAnalysis).order_by(RulesAnalysis.score.desc()).limit(100)))
    return [{"market_id": r.market_id, "score": r.score, "level": r.level, "flags": r.matched_flags} for r in rows]


@router.get("/divergence")
def divergence(db: Session = Depends(get_db)) -> list[dict]:
    rows = list(
        db.scalars(
            select(DuplicateMarketPair)
            .where(DuplicateMarketPair.divergence_score.is_not(None))
            .order_by(DuplicateMarketPair.divergence_score.desc())
            .limit(100)
        )
    )
    return [{"market_a_id": r.market_a_id, "market_b_id": r.market_b_id, "divergence": r.divergence_score} for r in rows]


@router.get("/kpi")
def kpi(db: Session = Depends(get_db)) -> dict:
    now = datetime.now(UTC)
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)

    dau = db.scalar(
        select(func.count(func.distinct(UserEvent.user_id))).where(
            UserEvent.created_at >= day_ago,
            UserEvent.event_type.in_(["signal_sent", "watchlist_added", "market_opened", "digest_sent"]),
        )
    )
    watchlist_usage = db.scalar(
        select(func.count()).where(UserEvent.created_at >= week_ago, UserEvent.event_type == "watchlist_added")
    )
    signals_clicked = db.scalar(
        select(func.count()).where(UserEvent.created_at >= week_ago, UserEvent.event_type == "market_opened")
    )
    retention = db.scalar(
        select(func.count()).select_from(User).where(User.last_digest_sent.is_not(None))
    )
    return {
        "dau": int(dau or 0),
        "signals_clicked_7d": int(signals_clicked or 0),
        "watchlist_added_7d": int(watchlist_usage or 0),
        "users_with_digest_sent": int(retention or 0),
    }


@router.get("/retention")
def retention(db: Session = Depends(get_db)) -> dict:
    today = datetime.now(UTC).date()
    users = list(db.scalars(select(User)))
    d1 = 0
    d7 = 0
    for user in users:
        first_event = db.scalar(
            select(UserEvent).where(UserEvent.user_id == user.id).order_by(UserEvent.created_at.asc())
        )
        if not first_event:
            continue
        cohort_day = first_event.created_at.date()
        has_d1 = db.scalar(
            select(func.count())
            .select_from(UserEvent)
            .where(UserEvent.user_id == user.id, UserEvent.created_at >= datetime.combine(cohort_day + timedelta(days=1), datetime.min.time(), tzinfo=UTC))
        )
        has_d7 = db.scalar(
            select(func.count())
            .select_from(UserEvent)
            .where(UserEvent.user_id == user.id, UserEvent.created_at >= datetime.combine(cohort_day + timedelta(days=7), datetime.min.time(), tzinfo=UTC))
        )
        d1 += int((has_d1 or 0) > 0)
        d7 += int((has_d7 or 0) > 0)
    total = len(users)
    return {
        "cohort_users": total,
        "d1_retained_users": d1,
        "d7_retained_users": d7,
        "d1_rate": (d1 / total) if total else 0,
        "d7_rate": (d7 / total) if total else 0,
        "as_of": str(today),
    }


@router.get("/platform-distribution")
def platform_distribution(db: Session = Depends(get_db)) -> dict:
    rows = (
        db.execute(
            select(Platform.name, func.count(Market.id))
            .join(Market, Market.platform_id == Platform.id)
            .group_by(Platform.name)
            .order_by(func.count(Market.id).desc())
        )
        .all()
    )
    return {name: int(count) for name, count in rows}


@router.get("/cross-platform-pairs")
def cross_platform_pairs(db: Session = Depends(get_db)) -> dict:
    rows = (
        db.execute(
            select(Platform.id, Platform.name, func.count(Market.id))
            .join(Market, Market.platform_id == Platform.id)
            .group_by(Platform.id, Platform.name)
            .order_by(Platform.name.asc())
        )
        .all()
    )
    platforms = [{"id": int(pid), "name": name, "count": int(count)} for pid, name, count in rows]
    pairs: list[dict] = []
    total = 0
    for i in range(len(platforms)):
        for j in range(i + 1, len(platforms)):
            a = platforms[i]
            b = platforms[j]
            pair_count = int(a["count"] * b["count"])
            total += pair_count
            pairs.append(
                {
                    "platform_a": a["name"],
                    "platform_b": b["name"],
                    "potential_pairs": pair_count,
                }
            )
    return {
        "platforms": {p["name"]: p["count"] for p in platforms},
        "cross_platform_pairs_total": total,
        "cross_platform_pairs_by_platform": sorted(pairs, key=lambda x: x["potential_pairs"], reverse=True),
    }


@router.get("/quality")
def quality(days: int = 7, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    days = max(1, min(days, 60))
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=days)
    top_window = max(5, settings.top_window_size)
    fresh_cutoff = now - timedelta(hours=settings.snapshot_fresh_hours)

    stored_rows = list(
        db.scalars(
            select(SignalQualityMetrics).where(SignalQualityMetrics.date >= cutoff.date()).order_by(SignalQualityMetrics.date.desc())
        )
    )
    if stored_rows:
        metrics_by_date = [
            {
                "date": str(r.date),
                "signals_total": int(sum((r.signals_by_type or {}).values())),
                "signals_by_type": r.signals_by_type or {},
                "signals_by_mode": r.signals_by_mode or {},
                "avg_score_by_type": r.avg_score_by_type or {},
                "zero_move_arbitrage_ratio": round(float(r.zero_move_arbitrage_ratio or 0.0), 4),
                "missing_rules_share_top_window": round(float(r.missing_rules_share or 0.0), 4),
                "actionable_rate": round(float(r.actionable_rate or 0.0), 4),
                "simulated_edge_mean": round(float(r.simulated_edge_mean or 0.0), 4),
                "simulated_edge_p10": round(float(r.simulated_edge_p10 or 0.0), 4),
                "top5_utility_daily": round(float(r.top5_utility_daily or 0.0), 4),
                "signal_diversity_top_window": round(_signal_diversity(r.signals_by_type or {}), 4),
            }
            for r in stored_rows
        ]
        markets_ingested = int(stored_rows[0].markets_ingested or 0)
        markets_with_prob = int(stored_rows[0].markets_with_prob or 0)
        markets_with_rules = int(stored_rows[0].markets_with_rules or 0)
        snapshots_fresh_ratio = float(stored_rows[0].snapshots_fresh_ratio or 0.0)
        period_signals = int(sum(item["signals_total"] for item in metrics_by_date))
        avg_actionable = (
            round(sum(item["actionable_rate"] for item in metrics_by_date) / len(metrics_by_date), 4)
            if metrics_by_date
            else 0.0
        )
        avg_zero_move = (
            round(sum(item["zero_move_arbitrage_ratio"] for item in metrics_by_date) / len(metrics_by_date), 4)
            if metrics_by_date
            else 0.0
        )
        return {
            "period_days": days,
            "top_window_size": top_window,
            "snapshot_fresh_hours": settings.snapshot_fresh_hours,
            "metrics_by_date": metrics_by_date,
            "aggregates": {
                "markets_ingested": markets_ingested,
                "markets_with_prob": markets_with_prob,
                "markets_with_rules": markets_with_rules,
                "snapshots_fresh_ratio": round(snapshots_fresh_ratio, 4),
                "signals_total_period": period_signals,
                "avg_actionable_rate": avg_actionable,
                "avg_zero_move_arbitrage_ratio": avg_zero_move,
                "avg_simulated_edge_mean": round(
                    sum(item["simulated_edge_mean"] for item in metrics_by_date) / len(metrics_by_date), 4
                )
                if metrics_by_date
                else 0.0,
                "avg_signal_diversity_top_window": round(
                    sum(item["signal_diversity_top_window"] for item in metrics_by_date) / len(metrics_by_date), 4
                )
                if metrics_by_date
                else 0.0,
            },
            "source": "signal_quality_metrics",
        }

    metrics_by_date: list[dict] = []
    for idx in range(days):
        day = (now - timedelta(days=idx)).date()
        next_day = day + timedelta(days=1)
        day_start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
        day_end = datetime.combine(next_day, datetime.min.time(), tzinfo=UTC)
        rows = list(
            db.scalars(
                select(Signal)
                .where(Signal.created_at >= day_start, Signal.created_at < day_end)
                .order_by(Signal.created_at.desc())
            )
        )
        if not rows:
            continue

        by_type: dict[str, int] = {}
        by_mode: dict[str, int] = {}
        avg_score_by_type: dict[str, float] = {}
        score_buf: dict[str, list[float]] = {}
        for s in rows:
            st = s.signal_type.value
            by_type[st] = by_type.get(st, 0) + 1
            mode = s.signal_mode or "untyped"
            by_mode[mode] = by_mode.get(mode, 0) + 1
            if s.confidence_score is not None:
                score_buf.setdefault(st, []).append(float(s.confidence_score))
        for key, values in score_buf.items():
            avg_score_by_type[key] = round(sum(values) / len(values), 4)

        arb = [s for s in rows if s.signal_type == SignalType.ARBITRAGE_CANDIDATE]
        zero_move = 0
        momentum_total = 0
        for s in arb:
            mode = (s.signal_mode or "").lower()
            if mode == "momentum":
                momentum_total += 1
                mv = float((s.metadata_json or {}).get("recent_move", 0) or 0)
                if mv <= 1e-9:
                    zero_move += 1
        zero_move_ratio = (zero_move / momentum_total) if momentum_total else 0.0

        top_rows = select_top_signals(rows, limit=top_window, settings=settings)
        top_missing_rules = sum(1 for s in top_rows if (s.signal_mode or "") == "missing_rules_risk")
        missing_rules_share = (top_missing_rules / len(top_rows)) if top_rows else 0.0

        actionable = sum(1 for s in rows if (s.liquidity_score or 0.0) >= 0.6 and (s.confidence_score or 0.0) >= 0.4)
        actionable_rate = actionable / len(rows)

        metrics_by_date.append(
            {
                "date": str(day),
                "signals_total": len(rows),
                "signals_by_type": by_type,
                "signals_by_mode": by_mode,
                "avg_score_by_type": avg_score_by_type,
                "zero_move_arbitrage_ratio": round(zero_move_ratio, 4),
                "missing_rules_share_top_window": round(missing_rules_share, 4),
                "actionable_rate": round(actionable_rate, 4),
                "signal_diversity_top_window": round(_signal_diversity(by_type), 4),
            }
        )

    total_markets = int(db.scalar(select(func.count()).select_from(Market)) or 0)
    markets_with_prob = int(
        db.scalar(select(func.count()).select_from(Market).where(Market.probability_yes.is_not(None))) or 0
    )
    markets_with_rules = int(
        db.scalar(
            select(func.count()).select_from(Market).where(Market.rules_text.is_not(None), Market.rules_text != "")
        )
        or 0
    )
    markets_with_fresh_snapshot = int(
        db.scalar(
            select(func.count(func.distinct(MarketSnapshot.market_id)))
            .select_from(MarketSnapshot)
            .where(MarketSnapshot.fetched_at >= fresh_cutoff)
        )
        or 0
    )
    snapshots_fresh_ratio = (markets_with_fresh_snapshot / total_markets) if total_markets else 0.0
    period_signals = int(
        db.scalar(select(func.count()).select_from(Signal).where(Signal.created_at >= cutoff)) or 0
    )

    avg_actionable = (
        round(sum(item["actionable_rate"] for item in metrics_by_date) / len(metrics_by_date), 4)
        if metrics_by_date
        else 0.0
    )
    avg_zero_move = (
        round(sum(item["zero_move_arbitrage_ratio"] for item in metrics_by_date) / len(metrics_by_date), 4)
        if metrics_by_date
        else 0.0
    )

    return {
        "period_days": days,
        "top_window_size": top_window,
        "snapshot_fresh_hours": settings.snapshot_fresh_hours,
        "metrics_by_date": metrics_by_date,
        "aggregates": {
            "markets_ingested": total_markets,
            "markets_with_prob": markets_with_prob,
            "markets_with_rules": markets_with_rules,
            "snapshots_fresh_ratio": round(snapshots_fresh_ratio, 4),
            "signals_total_period": period_signals,
            "avg_actionable_rate": avg_actionable,
            "avg_zero_move_arbitrage_ratio": avg_zero_move,
            "avg_signal_diversity_top_window": round(
                sum(item["signal_diversity_top_window"] for item in metrics_by_date) / len(metrics_by_date), 4
            )
            if metrics_by_date
            else 0.0,
        },
    }


@router.get("/signal-history")
def signal_history_stats(days: int = 7, db: Session = Depends(get_db)) -> dict:
    days = max(1, min(days, 90))
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=days)
    total = int(
        db.scalar(select(func.count()).select_from(SignalHistory).where(SignalHistory.timestamp >= cutoff)) or 0
    )
    recent_rows = list(db.scalars(select(SignalHistory).where(SignalHistory.timestamp >= cutoff)))
    labeled_15m = sum(
        1
        for row in recent_rows
        if isinstance(row.simulated_trade, dict) and row.simulated_trade.get("probability_after_15m") is not None
    )
    labeled_30m = sum(
        1
        for row in recent_rows
        if isinstance(row.simulated_trade, dict) and row.simulated_trade.get("probability_after_30m") is not None
    )
    labeled_1h = int(
        db.scalar(
            select(func.count())
            .select_from(SignalHistory)
            .where(SignalHistory.timestamp >= cutoff, SignalHistory.probability_after_1h.is_not(None))
        )
        or 0
    )
    labeled_6h = int(
        db.scalar(
            select(func.count())
            .select_from(SignalHistory)
            .where(SignalHistory.timestamp >= cutoff, SignalHistory.probability_after_6h.is_not(None))
        )
        or 0
    )
    labeled_24h = int(
        db.scalar(
            select(func.count())
            .select_from(SignalHistory)
            .where(SignalHistory.timestamp >= cutoff, SignalHistory.probability_after_24h.is_not(None))
        )
        or 0
    )
    resolved_labeled = int(
        db.scalar(
            select(func.count())
            .select_from(SignalHistory)
            .where(SignalHistory.timestamp >= cutoff, SignalHistory.resolved_success.is_not(None))
        )
        or 0
    )
    resolved_positive = int(
        db.scalar(
            select(func.count())
            .select_from(SignalHistory)
            .where(SignalHistory.timestamp >= cutoff, SignalHistory.resolved_success.is_(True))
        )
        or 0
    )
    by_type_rows = (
        db.execute(
            select(SignalHistory.signal_type, func.count(SignalHistory.id))
            .where(SignalHistory.timestamp >= cutoff)
            .group_by(SignalHistory.signal_type)
            .order_by(func.count(SignalHistory.id).desc())
        )
        .all()
    )
    return {
        "period_days": days,
        "total_rows": total,
        "labeled_rows": {
            "15m": labeled_15m,
            "30m": labeled_30m,
            "1h": labeled_1h,
            "6h": labeled_6h,
            "24h": labeled_24h,
            "resolution": resolved_labeled,
        },
        "coverage": {
            "15m": round((labeled_15m / total), 4) if total else 0.0,
            "30m": round((labeled_30m / total), 4) if total else 0.0,
            "1h": round((labeled_1h / total), 4) if total else 0.0,
            "6h": round((labeled_6h / total), 4) if total else 0.0,
            "24h": round((labeled_24h / total), 4) if total else 0.0,
            "resolution": round((resolved_labeled / total), 4) if total else 0.0,
        },
        "resolution_success_rate": round((resolved_positive / resolved_labeled), 4) if resolved_labeled else 0.0,
        "by_signal_type": {str(st.value): int(cnt) for st, cnt in by_type_rows},
    }


@router.get("/research/signals")
def research_signals(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    signal_type: str | None = Query(default=None),
    min_divergence: float | None = Query(default=None, ge=0.0, le=1.0),
    limit: int = Query(default=1000, ge=1, le=10000),
    db: Session = Depends(get_db),
) -> dict:
    result = build_signal_history_dataset(
        db,
        days=days,
        horizon=horizon,
        signal_type=signal_type,
        min_divergence=min_divergence,
        limit=limit,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result)
    return result


@router.get("/research/signals.csv")
def research_signals_csv(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    signal_type: str | None = Query(default=None),
    min_divergence: float | None = Query(default=None, ge=0.0, le=1.0),
    limit: int = Query(default=5000, ge=1, le=20000),
    db: Session = Depends(get_db),
) -> Response:
    result = build_signal_history_dataset(
        db,
        days=days,
        horizon=horizon,
        signal_type=signal_type,
        min_divergence=min_divergence,
        limit=limit,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result)
    rows = result["rows"]
    buffer = StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "id",
            "timestamp",
            "signal_type",
            "platform",
            "market_id",
            "related_market_id",
            "probability_at_signal",
            "divergence",
            "liquidity",
            "volume_24h",
            "horizon",
            "probability_at_horizon",
            "return_pct",
            "is_hit",
            "resolved_success",
        ],
    )
    writer.writeheader()
    writer.writerows(rows)
    filename = f"stage5_signals_{days}d_{horizon}.csv"
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename=\"{filename}\"'},
    )


@router.get("/research/divergence-thresholds")
def research_divergence_thresholds(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    signal_type: str = Query(default=SignalType.DIVERGENCE.value),
    thresholds: str = Query(default="0.03,0.05,0.08,0.10,0.15"),
    db: Session = Depends(get_db),
) -> dict:
    parsed_thresholds = _parse_thresholds_csv(thresholds)
    result = build_threshold_summary(
        db,
        days=days,
        horizon=horizon,
        thresholds=parsed_thresholds,
        signal_type=signal_type,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result)
    return result


@router.get("/research/progress")
def research_progress(
    target_samples: int = Query(default=500, ge=50, le=50000),
    lookback_days: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_db),
) -> dict:
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=lookback_days)
    total_divergence = int(
        db.scalar(
            select(func.count())
            .select_from(SignalHistory)
            .where(SignalHistory.signal_type == SignalType.DIVERGENCE)
        )
        or 0
    )
    lookback_divergence = int(
        db.scalar(
            select(func.count())
            .select_from(SignalHistory)
            .where(
                SignalHistory.signal_type == SignalType.DIVERGENCE,
                SignalHistory.timestamp >= cutoff,
            )
        )
        or 0
    )
    avg_per_day = (lookback_divergence / lookback_days) if lookback_days > 0 else 0.0
    remaining = max(0, target_samples - total_divergence)
    eta_days = (remaining / avg_per_day) if avg_per_day > 0 else None
    return {
        "target_samples": target_samples,
        "current_samples": total_divergence,
        "remaining_samples": remaining,
        "lookback_days": lookback_days,
        "samples_last_window": lookback_divergence,
        "avg_samples_per_day": round(avg_per_day, 3),
        "eta_days_to_target": round(eta_days, 2) if eta_days is not None else None,
        "as_of": now.isoformat(),
    }


@router.get("/research/agent-decisions")
def research_agent_decisions(
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    return build_agent_decision_report(
        db,
        settings=settings,
        limit=limit,
        lookback_days=days,
    )


@router.get("/research/divergence-decision")
def research_divergence_decision(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    thresholds: str = Query(default="0.03,0.05,0.08,0.10,0.15"),
    min_labeled_returns: int = Query(default=30, ge=1, le=100000),
    keep_ev_min: float = Query(default=0.01, ge=-1.0, le=1.0),
    keep_hit_rate_min: float = Query(default=0.52, ge=0.0, le=1.0),
    modify_ev_min: float = Query(default=0.005, ge=-1.0, le=1.0),
    keep_sharpe_like_min: float = Query(default=0.5, ge=-10.0, le=10.0),
    keep_risk_of_ruin_max: float = Query(default=0.10, ge=0.0, le=1.0),
    monte_carlo_sims: int = Query(default=1000, ge=1, le=20000),
    monte_carlo_trades: int = Query(default=100, ge=1, le=1000),
    monte_carlo_position_size_usd: float = Query(default=100.0, ge=1.0, le=100000.0),
    db: Session = Depends(get_db),
) -> dict:
    parsed_thresholds = _parse_thresholds_csv(thresholds)

    result = build_divergence_decision(
        db,
        days=days,
        horizon=horizon,
        thresholds=parsed_thresholds,
        min_labeled_returns=min_labeled_returns,
        keep_ev_min=keep_ev_min,
        keep_hit_rate_min=keep_hit_rate_min,
        keep_sharpe_like_min=keep_sharpe_like_min,
        keep_risk_of_ruin_max=keep_risk_of_ruin_max,
        modify_ev_min=modify_ev_min,
        monte_carlo_sims=monte_carlo_sims,
        monte_carlo_trades=monte_carlo_trades,
        monte_carlo_position_size_usd=monte_carlo_position_size_usd,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result)
    return result


@router.post("/research/divergence-decision/track")
def research_divergence_decision_track(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    thresholds: str = Query(default="0.03,0.05,0.08,0.10,0.15"),
    min_labeled_returns: int = Query(default=30, ge=1, le=100000),
    keep_ev_min: float = Query(default=0.01, ge=-1.0, le=1.0),
    keep_hit_rate_min: float = Query(default=0.52, ge=0.0, le=1.0),
    modify_ev_min: float = Query(default=0.005, ge=-1.0, le=1.0),
    keep_sharpe_like_min: float = Query(default=0.5, ge=-10.0, le=10.0),
    keep_risk_of_ruin_max: float = Query(default=0.10, ge=0.0, le=1.0),
    monte_carlo_sims: int = Query(default=1000, ge=1, le=20000),
    monte_carlo_trades: int = Query(default=100, ge=1, le=1000),
    monte_carlo_position_size_usd: float = Query(default=100.0, ge=1.0, le=100000.0),
    run_name: str = Query(default="stage5_divergence_decision"),
    db: Session = Depends(get_db),
) -> dict:
    parsed_thresholds = _parse_thresholds_csv(thresholds)
    decision = build_divergence_decision(
        db,
        days=days,
        horizon=horizon,
        thresholds=parsed_thresholds,
        min_labeled_returns=min_labeled_returns,
        keep_ev_min=keep_ev_min,
        keep_hit_rate_min=keep_hit_rate_min,
        keep_sharpe_like_min=keep_sharpe_like_min,
        keep_risk_of_ruin_max=keep_risk_of_ruin_max,
        modify_ev_min=modify_ev_min,
        monte_carlo_sims=monte_carlo_sims,
        monte_carlo_trades=monte_carlo_trades,
        monte_carlo_position_size_usd=monte_carlo_position_size_usd,
    )
    if "error" in decision:
        raise HTTPException(status_code=400, detail=decision)

    best = decision.get("best_threshold_metrics") or {}
    risk_metrics = (decision.get("risk_metrics") or {}).get("monte_carlo", {})
    tracking = record_stage5_experiment(
        run_name=run_name,
        params={
            "days": days,
            "horizon": horizon,
            "thresholds": thresholds,
            "min_labeled_returns": min_labeled_returns,
            "keep_ev_min": keep_ev_min,
            "keep_hit_rate_min": keep_hit_rate_min,
            "keep_sharpe_like_min": keep_sharpe_like_min,
            "keep_risk_of_ruin_max": keep_risk_of_ruin_max,
            "modify_ev_min": modify_ev_min,
            "monte_carlo_sims": monte_carlo_sims,
            "monte_carlo_trades": monte_carlo_trades,
            "monte_carlo_position_size_usd": monte_carlo_position_size_usd,
        },
        metrics={
            "avg_return": float(best.get("avg_return", 0.0)),
            "hit_rate": float(best.get("hit_rate", 0.0)),
            "returns_labeled": float(best.get("returns_labeled", 0.0)),
            "risk_of_ruin": float(risk_metrics.get("risk_of_ruin", 0.0)),
            "expected_return_pct_mc": float(risk_metrics.get("expected_return_pct", 0.0)),
            "max_drawdown_mean_mc": float(risk_metrics.get("max_drawdown_mean", 0.0)),
        },
        tags={
            "decision": str(decision.get("decision")),
            "recommended_threshold": str(decision.get("recommended_threshold")),
        },
    )
    return {"decision": decision, "tracking": tracking}


@router.get("/research/experiments")
def research_experiments(
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict:
    return read_stage5_experiments(limit=limit)


@router.get("/research/data-quality")
def research_data_quality(
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=10000, ge=1, le=100000),
    db: Session = Depends(get_db),
) -> dict:
    return build_signal_history_data_quality_report(db, days=days, limit=limit)


@router.post("/research/data-quality/track")
def research_data_quality_track(
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=10000, ge=1, le=100000),
    run_name: str = Query(default="stage5_data_quality"),
    db: Session = Depends(get_db),
) -> dict:
    report = build_signal_history_data_quality_report(db, days=days, limit=limit)
    tracking = record_stage5_experiment(
        run_name=run_name,
        params={
            "days": days,
            "limit": limit,
            "report_type": "data_quality",
        },
        metrics=extract_data_quality_metrics(report),
        tags={"passed": str(report.get("passed", False))},
    )
    return {"report": report, "tracking": tracking}


@router.get("/research/provider-reliability")
def research_provider_reliability(
    days: int = Query(default=7, ge=1, le=365),
    limit_runs: int = Query(default=1000, ge=1, le=10000),
    db: Session = Depends(get_db),
) -> dict:
    return build_provider_reliability_report(db, days=days, limit_runs=limit_runs)


@router.post("/research/provider-reliability/track")
def research_provider_reliability_track(
    days: int = Query(default=7, ge=1, le=365),
    limit_runs: int = Query(default=1000, ge=1, le=10000),
    run_name: str = Query(default="stage5_provider_reliability"),
    db: Session = Depends(get_db),
) -> dict:
    report = build_provider_reliability_report(db, days=days, limit_runs=limit_runs)
    tracking = record_stage5_experiment(
        run_name=run_name,
        params={"days": days, "limit_runs": limit_runs, "report_type": "provider_reliability"},
        metrics=extract_provider_reliability_metrics(report),
        tags={"platforms_total": str(report.get("platforms_total", 0))},
    )
    return {"report": report, "tracking": tracking}


@router.get("/research/provider-contract-checks")
def research_provider_contract_checks(
    limit: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    rows = list(
        db.scalars(
            select(JobRun)
            .where(JobRun.job_name == "provider_contract_checks")
            .order_by(JobRun.started_at.desc())
            .limit(limit)
        )
    )
    if not rows:
        return {"runs_total": 0, "latest": None, "history": []}
    latest = rows[0]
    return {
        "runs_total": len(rows),
        "latest": {
            "status": latest.status,
            "started_at": latest.started_at,
            "finished_at": latest.finished_at,
            "details": latest.details or {},
        },
        "history": [
            {
                "status": row.status,
                "started_at": row.started_at,
                "finished_at": row.finished_at,
                "checks_failed": int(((row.details or {}).get("checks_failed") or 0)),
            }
            for row in rows
        ],
    }


@router.get("/research/stack-decision-log")
def research_stack_decision_log() -> dict:
    return build_stack_decision_log()


@router.get("/research/stack-readiness")
def research_stack_readiness() -> dict:
    return build_research_stack_readiness_report()


@router.get("/research/build-vs-buy-estimate")
def research_build_vs_buy_estimate() -> dict:
    return build_build_vs_buy_time_saved_estimate()


@router.post("/research/build-vs-buy-estimate/track")
def research_build_vs_buy_estimate_track(
    run_name: str = Query(default="stage5_build_vs_buy_estimate"),
) -> dict:
    report = build_build_vs_buy_time_saved_estimate()
    tracking = record_stage5_experiment(
        run_name=run_name,
        params={"report_type": "build_vs_buy_estimate"},
        metrics=extract_build_vs_buy_metrics(report),
        tags={"adoption_ratio": str(report.get("adoption_ratio", 0.0))},
    )
    return {"report": report, "tracking": tracking}


@router.get("/research/ab-testing")
def research_ab_testing(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> dict:
    return build_ab_testing_report(db, days=days)


@router.post("/research/ab-testing/track")
def research_ab_testing_track(
    days: int = Query(default=30, ge=1, le=365),
    run_name: str = Query(default="stage5_ab_testing"),
    db: Session = Depends(get_db),
) -> dict:
    report = build_ab_testing_report(db, days=days)
    tracking = record_stage5_experiment(
        run_name=run_name,
        params={"days": days, "report_type": "ab_testing"},
        metrics=extract_ab_testing_metrics(report),
        tags={
            "ab_enabled": str(report.get("ab_enabled", False)),
            "meets_ctr_goal_20pct": str((report.get("comparative") or {}).get("meets_ctr_goal_20pct", False)),
        },
    )
    return {"report": report, "tracking": tracking}


@router.get("/research/ethics")
def research_ethics(
    top_window: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    return build_ethics_report(db, top_window=top_window)


@router.post("/research/ethics/track")
def research_ethics_track(
    top_window: int = Query(default=50, ge=1, le=500),
    run_name: str = Query(default="stage5_ethics"),
    db: Session = Depends(get_db),
) -> dict:
    report = build_ethics_report(db, top_window=top_window)
    tracking = record_stage5_experiment(
        run_name=run_name,
        params={"top_window": top_window, "report_type": "ethics"},
        metrics=extract_ethics_metrics(report),
        tags={"passed": str(report.get("passed", False))},
    )
    return {"report": report, "tracking": tracking}


@router.get("/research/ranking-formulas")
def research_ranking_formulas(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    top_k: int = Query(default=50, ge=1, le=500),
    min_samples: int = Query(default=20, ge=1, le=10000),
    db: Session = Depends(get_db),
) -> dict:
    return build_ranking_research_report(
        db,
        days=days,
        horizon=horizon,
        top_k=top_k,
        min_samples=min_samples,
    )


@router.post("/research/ranking-formulas/track")
def research_ranking_formulas_track(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    top_k: int = Query(default=50, ge=1, le=500),
    min_samples: int = Query(default=20, ge=1, le=10000),
    run_name: str = Query(default="stage5_ranking_formulas"),
    db: Session = Depends(get_db),
) -> dict:
    report = build_ranking_research_report(
        db,
        days=days,
        horizon=horizon,
        top_k=top_k,
        min_samples=min_samples,
    )
    tracking = record_stage5_experiment(
        run_name=run_name,
        params={
            "days": days,
            "horizon": horizon,
            "top_k": top_k,
            "min_samples": min_samples,
            "report_type": "ranking_formulas",
        },
        metrics=extract_ranking_research_metrics(report),
        tags={"best_formula": str(report.get("best_formula"))},
    )
    return {"report": report, "tracking": tracking}


@router.get("/research/platform-comparison")
def research_platform_comparison(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    signal_type: str | None = Query(default=None),
    min_samples: int = Query(default=10, ge=1, le=10000),
    db: Session = Depends(get_db),
) -> dict:
    result = build_platform_comparison_report(
        db,
        days=days,
        horizon=horizon,
        signal_type=signal_type,
        min_samples=min_samples,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result)
    return result


@router.get("/research/market-categories")
def research_market_categories(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    signal_type: str | None = Query(default=None),
    min_samples: int = Query(default=10, ge=1, le=10000),
    db: Session = Depends(get_db),
) -> dict:
    horizon_l = (horizon or "6h").strip().lower()
    field = {
        "1h": "probability_after_1h",
        "6h": "probability_after_6h",
        "24h": "probability_after_24h",
        "resolution": "resolved_probability",
    }.get(horizon_l)
    if field is None:
        raise HTTPException(status_code=400, detail={"error": f"unsupported horizon '{horizon}'"})

    st = None
    if signal_type:
        try:
            st = SignalType(signal_type.strip().upper())
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": f"unsupported signal_type '{signal_type}'", "supported": [x.value for x in SignalType]},
            ) from exc

    cutoff = datetime.now(UTC) - timedelta(days=days)
    stmt = (
        select(SignalHistory, Market)
        .join(Market, Market.id == SignalHistory.market_id)
        .where(SignalHistory.timestamp >= cutoff)
    )
    if st is not None:
        stmt = stmt.where(SignalHistory.signal_type == st)

    rows = db.execute(stmt).all()
    by_cat: dict[str, dict[str, float | int]] = {}
    for hist, market in rows:
        category = (market.category or "uncategorized").strip().lower() or "uncategorized"
        bucket = by_cat.setdefault(
            category,
            {"sample_size": 0, "returns_labeled": 0, "returns_sum": 0.0, "hits": 0, "avg_liquidity": 0.0},
        )
        bucket["sample_size"] = int(bucket["sample_size"]) + 1
        liq = float(hist.liquidity or 0.0)
        bucket["avg_liquidity"] = float(bucket["avg_liquidity"]) + liq
        p0 = hist.probability_at_signal
        ph = getattr(hist, field)
        if p0 is None or ph is None:
            continue
        ret = float(ph) - float(p0)
        bucket["returns_labeled"] = int(bucket["returns_labeled"]) + 1
        bucket["returns_sum"] = float(bucket["returns_sum"]) + ret
        if ret > 0:
            bucket["hits"] = int(bucket["hits"]) + 1

    out: list[dict] = []
    for category, m in by_cat.items():
        sample_size = int(m["sample_size"])
        returns_labeled = int(m["returns_labeled"])
        if sample_size < min_samples:
            continue
        avg_return = (float(m["returns_sum"]) / returns_labeled) if returns_labeled else 0.0
        hit_rate = (int(m["hits"]) / returns_labeled) if returns_labeled else 0.0
        avg_liq = float(m["avg_liquidity"]) / sample_size if sample_size else 0.0
        out.append(
            {
                "category": category,
                "sample_size": sample_size,
                "returns_labeled": returns_labeled,
                "avg_return": round(avg_return, 6),
                "hit_rate": round(hit_rate, 4),
                "avg_liquidity": round(avg_liq, 4),
            }
        )

    out.sort(key=lambda x: (x["avg_return"], x["hit_rate"], x["returns_labeled"]), reverse=True)
    return {
        "period_days": days,
        "horizon": horizon_l,
        "signal_type": st.value if st else None,
        "min_samples": min_samples,
        "categories_total": len(out),
        "rows": out,
    }


@router.post("/research/platform-comparison/track")
def research_platform_comparison_track(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    signal_type: str | None = Query(default=None),
    min_samples: int = Query(default=10, ge=1, le=10000),
    run_name: str = Query(default="stage5_platform_comparison"),
    db: Session = Depends(get_db),
) -> dict:
    report = build_platform_comparison_report(
        db,
        days=days,
        horizon=horizon,
        signal_type=signal_type,
        min_samples=min_samples,
    )
    if "error" in report:
        raise HTTPException(status_code=400, detail=report)
    tracking = record_stage5_experiment(
        run_name=run_name,
        params={
            "days": days,
            "horizon": horizon,
            "signal_type": signal_type,
            "min_samples": min_samples,
            "report_type": "platform_comparison",
        },
        metrics=extract_platform_comparison_metrics(report),
        tags={"best_platform": str(report.get("best_platform"))},
    )
    return {"report": report, "tracking": tracking}


@router.get("/research/signal-types")
def research_signal_types(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    signal_types: str | None = Query(default=None, description="CSV of SignalType values"),
    min_labeled_returns: int = Query(default=30, ge=1, le=100000),
    keep_ev_min: float = Query(default=0.01, ge=-1.0, le=1.0),
    keep_hit_rate_min: float = Query(default=0.52, ge=0.0, le=1.0),
    keep_sharpe_like_min: float = Query(default=0.5, ge=-10.0, le=10.0),
    keep_risk_of_ruin_max: float = Query(default=0.10, ge=0.0, le=1.0),
    modify_ev_min: float = Query(default=0.005, ge=-1.0, le=1.0),
    db: Session = Depends(get_db),
) -> dict:
    report = build_signal_type_research_report(
        db,
        days=days,
        horizon=horizon,
        signal_types=signal_types,
        min_labeled_returns=min_labeled_returns,
        keep_ev_min=keep_ev_min,
        keep_hit_rate_min=keep_hit_rate_min,
        keep_sharpe_like_min=keep_sharpe_like_min,
        keep_risk_of_ruin_max=keep_risk_of_ruin_max,
        modify_ev_min=modify_ev_min,
    )
    if "error" in report:
        raise HTTPException(status_code=400, detail=report)
    return report


@router.post("/research/signal-types/track")
def research_signal_types_track(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    signal_types: str | None = Query(default=None, description="CSV of SignalType values"),
    min_labeled_returns: int = Query(default=30, ge=1, le=100000),
    keep_ev_min: float = Query(default=0.01, ge=-1.0, le=1.0),
    keep_hit_rate_min: float = Query(default=0.52, ge=0.0, le=1.0),
    keep_sharpe_like_min: float = Query(default=0.5, ge=-10.0, le=10.0),
    keep_risk_of_ruin_max: float = Query(default=0.10, ge=0.0, le=1.0),
    modify_ev_min: float = Query(default=0.005, ge=-1.0, le=1.0),
    run_name: str = Query(default="stage5_signal_type_research"),
    db: Session = Depends(get_db),
) -> dict:
    report = build_signal_type_research_report(
        db,
        days=days,
        horizon=horizon,
        signal_types=signal_types,
        min_labeled_returns=min_labeled_returns,
        keep_ev_min=keep_ev_min,
        keep_hit_rate_min=keep_hit_rate_min,
        keep_sharpe_like_min=keep_sharpe_like_min,
        keep_risk_of_ruin_max=keep_risk_of_ruin_max,
        modify_ev_min=modify_ev_min,
    )
    if "error" in report:
        raise HTTPException(status_code=400, detail=report)
    tracking = record_stage5_experiment(
        run_name=run_name,
        params={
            "days": days,
            "horizon": horizon,
            "signal_types": signal_types,
            "min_labeled_returns": min_labeled_returns,
            "keep_ev_min": keep_ev_min,
            "keep_hit_rate_min": keep_hit_rate_min,
            "keep_sharpe_like_min": keep_sharpe_like_min,
            "keep_risk_of_ruin_max": keep_risk_of_ruin_max,
            "modify_ev_min": modify_ev_min,
            "report_type": "signal_type_research",
        },
        metrics=extract_signal_type_research_metrics(report),
        tags={"decision_counts": str(report.get("decision_counts"))},
    )
    return {"report": report, "tracking": tracking}


@router.get("/research/signal-types/optimize")
def research_signal_types_optimize(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    signal_type: str = Query(default=SignalType.DIVERGENCE.value),
    source_tags: str = Query(default="all"),
    divergence_thresholds: str = Query(default="0,0.03,0.05,0.08,0.1,0.15"),
    liquidity_thresholds: str = Query(default="0,0.1,0.25,0.5"),
    volume_thresholds: str = Query(default="0,50,100,250,500"),
    min_labeled_returns: int = Query(default=30, ge=1, le=100000),
    keep_ev_min: float = Query(default=0.01, ge=-1.0, le=1.0),
    keep_hit_rate_min: float = Query(default=0.52, ge=0.0, le=1.0),
    keep_sharpe_like_min: float = Query(default=0.5, ge=-10.0, le=10.0),
    keep_risk_of_ruin_max: float = Query(default=0.10, ge=0.0, le=1.0),
    modify_ev_min: float = Query(default=0.005, ge=-1.0, le=1.0),
    monte_carlo_sims: int = Query(default=500, ge=1, le=20000),
    monte_carlo_trades: int = Query(default=100, ge=1, le=1000),
    monte_carlo_position_size_usd: float = Query(default=100.0, ge=1.0, le=100000.0),
    max_candidates: int = Query(default=25, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    report = build_signal_type_optimization_report(
        db,
        days=days,
        horizon=horizon,
        signal_type=signal_type,
        source_tags=_parse_str_csv(source_tags),
        divergence_thresholds=_parse_float_csv(divergence_thresholds, min_value=0.0, max_value=1.0),
        liquidity_thresholds=_parse_float_csv(liquidity_thresholds, min_value=0.0),
        volume_thresholds=_parse_float_csv(volume_thresholds, min_value=0.0),
        min_labeled_returns=min_labeled_returns,
        keep_ev_min=keep_ev_min,
        keep_hit_rate_min=keep_hit_rate_min,
        keep_sharpe_like_min=keep_sharpe_like_min,
        keep_risk_of_ruin_max=keep_risk_of_ruin_max,
        modify_ev_min=modify_ev_min,
        monte_carlo_sims=monte_carlo_sims,
        monte_carlo_trades=monte_carlo_trades,
        monte_carlo_position_size_usd=monte_carlo_position_size_usd,
        max_candidates=max_candidates,
    )
    if "error" in report:
        raise HTTPException(status_code=400, detail=report)
    return report


@router.post("/research/signal-types/optimize/track")
def research_signal_types_optimize_track(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    signal_type: str = Query(default=SignalType.DIVERGENCE.value),
    source_tags: str = Query(default="all"),
    divergence_thresholds: str = Query(default="0,0.03,0.05,0.08,0.1,0.15"),
    liquidity_thresholds: str = Query(default="0,0.1,0.25,0.5"),
    volume_thresholds: str = Query(default="0,50,100,250,500"),
    min_labeled_returns: int = Query(default=30, ge=1, le=100000),
    keep_ev_min: float = Query(default=0.01, ge=-1.0, le=1.0),
    keep_hit_rate_min: float = Query(default=0.52, ge=0.0, le=1.0),
    keep_sharpe_like_min: float = Query(default=0.5, ge=-10.0, le=10.0),
    keep_risk_of_ruin_max: float = Query(default=0.10, ge=0.0, le=1.0),
    modify_ev_min: float = Query(default=0.005, ge=-1.0, le=1.0),
    monte_carlo_sims: int = Query(default=500, ge=1, le=20000),
    monte_carlo_trades: int = Query(default=100, ge=1, le=1000),
    monte_carlo_position_size_usd: float = Query(default=100.0, ge=1.0, le=100000.0),
    max_candidates: int = Query(default=25, ge=1, le=200),
    run_name: str = Query(default="stage5_signal_type_optimization"),
    db: Session = Depends(get_db),
) -> dict:
    report = build_signal_type_optimization_report(
        db,
        days=days,
        horizon=horizon,
        signal_type=signal_type,
        source_tags=_parse_str_csv(source_tags),
        divergence_thresholds=_parse_float_csv(divergence_thresholds, min_value=0.0, max_value=1.0),
        liquidity_thresholds=_parse_float_csv(liquidity_thresholds, min_value=0.0),
        volume_thresholds=_parse_float_csv(volume_thresholds, min_value=0.0),
        min_labeled_returns=min_labeled_returns,
        keep_ev_min=keep_ev_min,
        keep_hit_rate_min=keep_hit_rate_min,
        keep_sharpe_like_min=keep_sharpe_like_min,
        keep_risk_of_ruin_max=keep_risk_of_ruin_max,
        modify_ev_min=modify_ev_min,
        monte_carlo_sims=monte_carlo_sims,
        monte_carlo_trades=monte_carlo_trades,
        monte_carlo_position_size_usd=monte_carlo_position_size_usd,
        max_candidates=max_candidates,
    )
    if "error" in report:
        raise HTTPException(status_code=400, detail=report)
    tracking = record_stage5_experiment(
        run_name=run_name,
        params={
            "days": days,
            "horizon": horizon,
            "signal_type": signal_type,
            "source_tags": source_tags,
            "divergence_thresholds": divergence_thresholds,
            "liquidity_thresholds": liquidity_thresholds,
            "volume_thresholds": volume_thresholds,
            "min_labeled_returns": min_labeled_returns,
            "report_type": "signal_type_optimization",
        },
        metrics=extract_signal_type_optimization_metrics(report),
        tags={
            "decision": str(report.get("decision")),
            "best_source_tag": str((report.get("best_candidate") or {}).get("source_tag")),
        },
    )
    return {"report": report, "tracking": tracking}


@router.get("/research/event-clusters")
def research_event_clusters(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    signal_type: str | None = Query(default=None),
    min_cluster_size: int = Query(default=2, ge=2, le=50),
    min_shared_tokens: int = Query(default=2, ge=1, le=6),
    min_jaccard: float = Query(default=0.2, ge=0.0, le=1.0),
    max_markets: int = Query(default=400, ge=50, le=5000),
    db: Session = Depends(get_db),
) -> dict:
    report = build_event_cluster_research_report(
        db,
        days=days,
        horizon=horizon,
        signal_type=signal_type,
        min_cluster_size=min_cluster_size,
        min_shared_tokens=min_shared_tokens,
        min_jaccard=min_jaccard,
        max_markets=max_markets,
    )
    if "error" in report:
        raise HTTPException(status_code=400, detail=report)
    return report


@router.post("/research/event-clusters/track")
def research_event_clusters_track(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    signal_type: str | None = Query(default=None),
    min_cluster_size: int = Query(default=2, ge=2, le=50),
    min_shared_tokens: int = Query(default=2, ge=1, le=6),
    min_jaccard: float = Query(default=0.2, ge=0.0, le=1.0),
    max_markets: int = Query(default=400, ge=50, le=5000),
    run_name: str = Query(default="stage5_event_clusters"),
    db: Session = Depends(get_db),
) -> dict:
    report = build_event_cluster_research_report(
        db,
        days=days,
        horizon=horizon,
        signal_type=signal_type,
        min_cluster_size=min_cluster_size,
        min_shared_tokens=min_shared_tokens,
        min_jaccard=min_jaccard,
        max_markets=max_markets,
    )
    if "error" in report:
        raise HTTPException(status_code=400, detail=report)
    tracking = record_stage5_experiment(
        run_name=run_name,
        params={
            "days": days,
            "horizon": horizon,
            "signal_type": signal_type,
            "min_cluster_size": min_cluster_size,
            "min_shared_tokens": min_shared_tokens,
            "min_jaccard": min_jaccard,
            "max_markets": max_markets,
            "report_type": "event_clusters",
        },
        metrics=extract_event_cluster_metrics(report),
        tags={"clusters_total": str(report.get("clusters_total", 0))},
    )
    return {"report": report, "tracking": tracking}


@router.get("/research/signal-lifetime")
def research_signal_lifetime(
    days: int = Query(default=30, ge=1, le=365),
    signal_type: str | None = Query(default=None),
    close_ratio_threshold: float = Query(default=0.5, ge=0.1, le=0.95),
    min_initial_divergence: float = Query(default=0.02, ge=0.001, le=1.0),
    min_samples: int = Query(default=10, ge=1, le=10000),
    include_subhour: bool = Query(default=True),
    subhour_grace_minutes: int = Query(default=20, ge=1, le=120),
    architecture_min_subhour_coverage: float = Query(default=0.20, ge=0.0, le=1.0),
    db: Session = Depends(get_db),
) -> dict:
    report = build_signal_lifetime_report(
        db,
        days=days,
        signal_type=signal_type,
        close_ratio_threshold=close_ratio_threshold,
        min_initial_divergence=min_initial_divergence,
        min_samples=min_samples,
        include_subhour=include_subhour,
        subhour_grace_minutes=subhour_grace_minutes,
        architecture_min_subhour_coverage=architecture_min_subhour_coverage,
    )
    if "error" in report:
        raise HTTPException(status_code=400, detail=report)
    return report


@router.post("/research/signal-lifetime/track")
def research_signal_lifetime_track(
    days: int = Query(default=30, ge=1, le=365),
    signal_type: str | None = Query(default=None),
    close_ratio_threshold: float = Query(default=0.5, ge=0.1, le=0.95),
    min_initial_divergence: float = Query(default=0.02, ge=0.001, le=1.0),
    min_samples: int = Query(default=10, ge=1, le=10000),
    include_subhour: bool = Query(default=True),
    subhour_grace_minutes: int = Query(default=20, ge=1, le=120),
    architecture_min_subhour_coverage: float = Query(default=0.20, ge=0.0, le=1.0),
    run_name: str = Query(default="stage5_signal_lifetime"),
    db: Session = Depends(get_db),
) -> dict:
    report = build_signal_lifetime_report(
        db,
        days=days,
        signal_type=signal_type,
        close_ratio_threshold=close_ratio_threshold,
        min_initial_divergence=min_initial_divergence,
        min_samples=min_samples,
        include_subhour=include_subhour,
        subhour_grace_minutes=subhour_grace_minutes,
        architecture_min_subhour_coverage=architecture_min_subhour_coverage,
    )
    if "error" in report:
        raise HTTPException(status_code=400, detail=report)
    tracking = record_stage5_experiment(
        run_name=run_name,
        params={
            "days": days,
            "signal_type": signal_type,
            "close_ratio_threshold": close_ratio_threshold,
            "min_initial_divergence": min_initial_divergence,
            "min_samples": min_samples,
            "include_subhour": include_subhour,
            "subhour_grace_minutes": subhour_grace_minutes,
            "architecture_min_subhour_coverage": architecture_min_subhour_coverage,
            "report_type": "signal_lifetime",
        },
        metrics=extract_signal_lifetime_metrics(report),
        tags={"signal_type_filter": str(report.get("signal_type_filter"))},
    )
    return {"report": report, "tracking": tracking}


@router.get("/research/walkforward")
def research_walkforward(
    days: int = Query(default=90, ge=14, le=365),
    horizon: str = Query(default="6h"),
    signal_type: str | None = Query(default=None),
    train_days: int = Query(default=30, ge=1, le=180),
    test_days: int = Query(default=14, ge=1, le=90),
    step_days: int = Query(default=14, ge=1, le=90),
    embargo_hours: int = Query(default=24, ge=0, le=24 * 7),
    min_samples_per_window: int = Query(default=100, ge=10, le=100000),
    bootstrap_sims: int = Query(default=500, ge=100, le=5000),
    db: Session = Depends(get_db),
) -> dict:
    report = build_walkforward_report(
        db,
        days=days,
        horizon=horizon,
        signal_type=signal_type,
        train_days=train_days,
        test_days=test_days,
        step_days=step_days,
        embargo_hours=embargo_hours,
        min_samples_per_window=min_samples_per_window,
        bootstrap_sims=bootstrap_sims,
    )
    if "error" in report:
        raise HTTPException(status_code=400, detail=report)
    return report


@router.post("/research/walkforward/track")
def research_walkforward_track(
    days: int = Query(default=90, ge=14, le=365),
    horizon: str = Query(default="6h"),
    signal_type: str | None = Query(default=None),
    train_days: int = Query(default=30, ge=1, le=180),
    test_days: int = Query(default=14, ge=1, le=90),
    step_days: int = Query(default=14, ge=1, le=90),
    embargo_hours: int = Query(default=24, ge=0, le=24 * 7),
    min_samples_per_window: int = Query(default=100, ge=10, le=100000),
    bootstrap_sims: int = Query(default=500, ge=100, le=5000),
    run_name: str = Query(default="stage6_walkforward"),
    db: Session = Depends(get_db),
) -> dict:
    report = build_walkforward_report(
        db,
        days=days,
        horizon=horizon,
        signal_type=signal_type,
        train_days=train_days,
        test_days=test_days,
        step_days=step_days,
        embargo_hours=embargo_hours,
        min_samples_per_window=min_samples_per_window,
        bootstrap_sims=bootstrap_sims,
    )
    if "error" in report:
        raise HTTPException(status_code=400, detail=report)
    tracking = record_stage5_experiment(
        run_name=run_name,
        params={
            "days": days,
            "horizon": horizon,
            "signal_type": signal_type,
            "train_days": train_days,
            "test_days": test_days,
            "step_days": step_days,
            "embargo_hours": embargo_hours,
            "min_samples_per_window": min_samples_per_window,
            "bootstrap_sims": bootstrap_sims,
            "report_type": "walkforward",
        },
        metrics=extract_walkforward_metrics(report),
        tags={"signal_type_filter": str(report.get("signal_type_filter"))},
    )
    return {"report": report, "tracking": tracking}


@router.get("/research/liquidity-safety")
def research_liquidity_safety(
    days: int = Query(default=30, ge=1, le=365),
    signal_type: str | None = Query(default=None),
    position_sizes: str = Query(default="50,100,500"),
    max_slippage_pct: float = Query(default=0.015, ge=0.001, le=0.5),
    min_samples: int = Query(default=10, ge=1, le=10000),
    db: Session = Depends(get_db),
) -> dict:
    report = build_liquidity_safety_report(
        db,
        days=days,
        signal_type=signal_type,
        position_sizes=position_sizes,
        max_slippage_pct=max_slippage_pct,
        min_samples=min_samples,
    )
    if "error" in report:
        raise HTTPException(status_code=400, detail=report)
    return report


@router.post("/research/liquidity-safety/track")
def research_liquidity_safety_track(
    days: int = Query(default=30, ge=1, le=365),
    signal_type: str | None = Query(default=None),
    position_sizes: str = Query(default="50,100,500"),
    max_slippage_pct: float = Query(default=0.015, ge=0.001, le=0.5),
    min_samples: int = Query(default=10, ge=1, le=10000),
    run_name: str = Query(default="stage5_liquidity_safety"),
    db: Session = Depends(get_db),
) -> dict:
    report = build_liquidity_safety_report(
        db,
        days=days,
        signal_type=signal_type,
        position_sizes=position_sizes,
        max_slippage_pct=max_slippage_pct,
        min_samples=min_samples,
    )
    if "error" in report:
        raise HTTPException(status_code=400, detail=report)
    tracking = record_stage5_experiment(
        run_name=run_name,
        params={
            "days": days,
            "signal_type": signal_type,
            "position_sizes": position_sizes,
            "max_slippage_pct": max_slippage_pct,
            "min_samples": min_samples,
            "report_type": "liquidity_safety",
        },
        metrics=extract_liquidity_safety_metrics(report),
        tags={"signal_type_filter": str(report.get("signal_type_filter"))},
    )
    return {"report": report, "tracking": tracking}


@router.get("/research/final-report")
def research_final_report(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    min_labeled_returns: int = Query(default=30, ge=1, le=100000),
    db: Session = Depends(get_db),
) -> dict:
    return build_stage5_final_report(
        db,
        days=days,
        horizon=horizon,
        min_labeled_returns=min_labeled_returns,
    )


@router.post("/research/final-report/track")
def research_final_report_track(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    min_labeled_returns: int = Query(default=30, ge=1, le=100000),
    run_name: str = Query(default="stage5_final_report"),
    db: Session = Depends(get_db),
) -> dict:
    report = build_stage5_final_report(
        db,
        days=days,
        horizon=horizon,
        min_labeled_returns=min_labeled_returns,
    )
    tracking = record_stage5_experiment(
        run_name=run_name,
        params={
            "days": days,
            "horizon": horizon,
            "min_labeled_returns": min_labeled_returns,
            "report_type": "final_report",
        },
        metrics=extract_stage5_final_report_metrics(report),
        tags={"readiness": str(report.get("readiness"))},
    )
    return {"report": report, "tracking": tracking}


@router.get("/research/export-package")
def research_export_package(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    min_labeled_returns: int = Query(default=30, ge=1, le=100000),
    experiments_limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict:
    return build_stage5_export_package(
        db,
        days=days,
        horizon=horizon,
        min_labeled_returns=min_labeled_returns,
        experiments_limit=experiments_limit,
    )


@router.get("/research/export-package.csv")
def research_export_package_csv(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    min_labeled_returns: int = Query(default=30, ge=1, le=100000),
    experiments_limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> Response:
    package = build_stage5_export_package(
        db,
        days=days,
        horizon=horizon,
        min_labeled_returns=min_labeled_returns,
        experiments_limit=experiments_limit,
    )
    rows = build_stage5_export_decision_rows(package)
    buffer = StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "signal_type",
            "decision",
            "returns_labeled",
            "avg_return",
            "hit_rate",
            "sharpe_like",
            "risk_of_ruin",
            "reason",
        ],
    )
    writer.writeheader()
    writer.writerows(rows)
    filename = f"stage5_export_package_{days}d_{horizon}.csv"
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename=\"{filename}\"'},
    )


@router.get("/research/readiness-gate")
def research_readiness_gate(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    min_labeled_returns: int = Query(default=30, ge=1, le=100000),
    min_actionable_types: int = Query(default=1, ge=0, le=100),
    max_insufficient_types: int = Query(default=3, ge=0, le=100),
    require_best_platform: bool = Query(default=True),
    min_clusters: int = Query(default=1, ge=0, le=10000),
    min_lifetime_types_ok: int = Query(default=1, ge=0, le=100),
    min_liquidity_types_ok: int = Query(default=1, ge=0, le=100),
    db: Session = Depends(get_db),
) -> dict:
    return build_stage5_readiness_gate(
        db,
        days=days,
        horizon=horizon,
        min_labeled_returns=min_labeled_returns,
        min_actionable_types=min_actionable_types,
        max_insufficient_types=max_insufficient_types,
        require_best_platform=require_best_platform,
        min_clusters=min_clusters,
        min_lifetime_types_ok=min_lifetime_types_ok,
        min_liquidity_types_ok=min_liquidity_types_ok,
    )


@router.post("/research/readiness-gate/track")
def research_readiness_gate_track(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    min_labeled_returns: int = Query(default=30, ge=1, le=100000),
    min_actionable_types: int = Query(default=1, ge=0, le=100),
    max_insufficient_types: int = Query(default=3, ge=0, le=100),
    require_best_platform: bool = Query(default=True),
    min_clusters: int = Query(default=1, ge=0, le=10000),
    min_lifetime_types_ok: int = Query(default=1, ge=0, le=100),
    min_liquidity_types_ok: int = Query(default=1, ge=0, le=100),
    run_name: str = Query(default="stage5_readiness_gate"),
    db: Session = Depends(get_db),
) -> dict:
    report = build_stage5_readiness_gate(
        db,
        days=days,
        horizon=horizon,
        min_labeled_returns=min_labeled_returns,
        min_actionable_types=min_actionable_types,
        max_insufficient_types=max_insufficient_types,
        require_best_platform=require_best_platform,
        min_clusters=min_clusters,
        min_lifetime_types_ok=min_lifetime_types_ok,
        min_liquidity_types_ok=min_liquidity_types_ok,
    )
    tracking = record_stage5_experiment(
        run_name=run_name,
        params={
            "days": days,
            "horizon": horizon,
            "min_labeled_returns": min_labeled_returns,
            "min_actionable_types": min_actionable_types,
            "max_insufficient_types": max_insufficient_types,
            "require_best_platform": require_best_platform,
            "min_clusters": min_clusters,
            "min_lifetime_types_ok": min_lifetime_types_ok,
            "min_liquidity_types_ok": min_liquidity_types_ok,
            "report_type": "readiness_gate",
        },
        metrics=extract_stage5_readiness_gate_metrics(report),
        tags={"status": str(report.get("status"))},
    )
    return {"report": report, "tracking": tracking}


@router.get("/research/stage6-governance")
def research_stage6_governance(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    min_labeled_returns: int = Query(default=30, ge=1, le=100000),
    walkforward_days: int = Query(default=90, ge=14, le=365),
    walkforward_train_days: int = Query(default=30, ge=1, le=180),
    walkforward_test_days: int = Query(default=14, ge=1, le=90),
    walkforward_step_days: int = Query(default=14, ge=1, le=90),
    walkforward_embargo_hours: int = Query(default=24, ge=0, le=24 * 7),
    walkforward_min_samples: int = Query(default=100, ge=10, le=100000),
    db: Session = Depends(get_db),
) -> dict:
    return build_stage6_governance_report(
        db,
        days=days,
        horizon=horizon,
        min_labeled_returns=min_labeled_returns,
        walkforward_days=walkforward_days,
        walkforward_train_days=walkforward_train_days,
        walkforward_test_days=walkforward_test_days,
        walkforward_step_days=walkforward_step_days,
        walkforward_embargo_hours=walkforward_embargo_hours,
        walkforward_min_samples=walkforward_min_samples,
    )


@router.post("/research/stage6-governance/track")
def research_stage6_governance_track(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    min_labeled_returns: int = Query(default=30, ge=1, le=100000),
    walkforward_days: int = Query(default=90, ge=14, le=365),
    walkforward_train_days: int = Query(default=30, ge=1, le=180),
    walkforward_test_days: int = Query(default=14, ge=1, le=90),
    walkforward_step_days: int = Query(default=14, ge=1, le=90),
    walkforward_embargo_hours: int = Query(default=24, ge=0, le=24 * 7),
    walkforward_min_samples: int = Query(default=100, ge=10, le=100000),
    run_name: str = Query(default="stage6_governance"),
    db: Session = Depends(get_db),
) -> dict:
    report = build_stage6_governance_report(
        db,
        days=days,
        horizon=horizon,
        min_labeled_returns=min_labeled_returns,
        walkforward_days=walkforward_days,
        walkforward_train_days=walkforward_train_days,
        walkforward_test_days=walkforward_test_days,
        walkforward_step_days=walkforward_step_days,
        walkforward_embargo_hours=walkforward_embargo_hours,
        walkforward_min_samples=walkforward_min_samples,
    )
    tracking = record_stage5_experiment(
        run_name=run_name,
        params={
            "days": days,
            "horizon": horizon,
            "min_labeled_returns": min_labeled_returns,
            "walkforward_days": walkforward_days,
            "walkforward_train_days": walkforward_train_days,
            "walkforward_test_days": walkforward_test_days,
            "walkforward_step_days": walkforward_step_days,
            "walkforward_embargo_hours": walkforward_embargo_hours,
            "walkforward_min_samples": walkforward_min_samples,
            "report_type": "stage6_governance",
        },
        metrics=extract_stage6_governance_metrics(report),
        tags={
            "decision": str(report.get("decision")),
            "overfit_flags": str(len(report.get("overfit_flags") or [])),
        },
    )
    return {"report": report, "tracking": tracking}


@router.get("/research/stage6-risk-guardrails")
def research_stage6_risk_guardrails(
    days: int = Query(default=7, ge=1, le=60),
    horizon: str = Query(default="6h"),
    signal_type: str = Query(default=SignalType.DIVERGENCE.value),
    nav_usd: float = Query(default=10000.0, ge=100.0, le=100000000.0),
    rollback_min_samples: int = Query(default=30, ge=10, le=100000),
    rollback_pvalue_threshold: float = Query(default=0.10, ge=0.001, le=0.5),
    rollback_cooldown_days: int = Query(default=7, ge=1, le=60),
    db: Session = Depends(get_db),
) -> dict:
    report = build_stage6_risk_guardrails_report(
        db,
        days=days,
        horizon=horizon,
        signal_type=signal_type,
        nav_usd=nav_usd,
        rollback_min_samples=rollback_min_samples,
        rollback_pvalue_threshold=rollback_pvalue_threshold,
        rollback_cooldown_days=rollback_cooldown_days,
    )
    if "error" in report:
        raise HTTPException(status_code=400, detail=report)
    return report


@router.post("/research/stage6-risk-guardrails/track")
def research_stage6_risk_guardrails_track(
    days: int = Query(default=7, ge=1, le=60),
    horizon: str = Query(default="6h"),
    signal_type: str = Query(default=SignalType.DIVERGENCE.value),
    nav_usd: float = Query(default=10000.0, ge=100.0, le=100000000.0),
    rollback_min_samples: int = Query(default=30, ge=10, le=100000),
    rollback_pvalue_threshold: float = Query(default=0.10, ge=0.001, le=0.5),
    rollback_cooldown_days: int = Query(default=7, ge=1, le=60),
    run_name: str = Query(default="stage6_risk_guardrails"),
    db: Session = Depends(get_db),
) -> dict:
    report = build_stage6_risk_guardrails_report(
        db,
        days=days,
        horizon=horizon,
        signal_type=signal_type,
        nav_usd=nav_usd,
        rollback_min_samples=rollback_min_samples,
        rollback_pvalue_threshold=rollback_pvalue_threshold,
        rollback_cooldown_days=rollback_cooldown_days,
    )
    if "error" in report:
        raise HTTPException(status_code=400, detail=report)
    tracking = record_stage5_experiment(
        run_name=run_name,
        params={
            "days": days,
            "horizon": horizon,
            "signal_type": signal_type,
            "nav_usd": nav_usd,
            "rollback_min_samples": rollback_min_samples,
            "rollback_pvalue_threshold": rollback_pvalue_threshold,
            "rollback_cooldown_days": rollback_cooldown_days,
            "report_type": "stage6_risk_guardrails",
        },
        metrics=extract_stage6_risk_guardrails_metrics(report),
        tags={
            "circuit_breaker_level": str(report.get("circuit_breaker_level")),
            "rollback_triggered": str(((report.get("rollback") or {}).get("triggered"))),
        },
    )
    return {"report": report, "tracking": tracking}


@router.get("/research/stage6-type35")
def research_stage6_type35(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    min_labeled_returns: int = Query(default=30, ge=1, le=100000),
    keep_ev_min: float = Query(default=0.01, ge=-1.0, le=1.0),
    keep_hit_rate_min: float = Query(default=0.52, ge=0.0, le=1.0),
    keep_sharpe_like_min: float = Query(default=0.5, ge=-10.0, le=10.0),
    keep_risk_of_ruin_max: float = Query(default=0.10, ge=0.0, le=1.0),
    modify_ev_min: float = Query(default=0.005, ge=-1.0, le=1.0),
    min_subhour_coverage: float = Query(default=0.20, ge=0.0, le=1.0),
    db: Session = Depends(get_db),
) -> dict:
    return build_stage6_type35_report(
        db,
        days=days,
        horizon=horizon,
        min_labeled_returns=min_labeled_returns,
        keep_ev_min=keep_ev_min,
        keep_hit_rate_min=keep_hit_rate_min,
        keep_sharpe_like_min=keep_sharpe_like_min,
        keep_risk_of_ruin_max=keep_risk_of_ruin_max,
        modify_ev_min=modify_ev_min,
        min_subhour_coverage=min_subhour_coverage,
    )


@router.post("/research/stage6-type35/track")
def research_stage6_type35_track(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    min_labeled_returns: int = Query(default=30, ge=1, le=100000),
    keep_ev_min: float = Query(default=0.01, ge=-1.0, le=1.0),
    keep_hit_rate_min: float = Query(default=0.52, ge=0.0, le=1.0),
    keep_sharpe_like_min: float = Query(default=0.5, ge=-10.0, le=10.0),
    keep_risk_of_ruin_max: float = Query(default=0.10, ge=0.0, le=1.0),
    modify_ev_min: float = Query(default=0.005, ge=-1.0, le=1.0),
    min_subhour_coverage: float = Query(default=0.20, ge=0.0, le=1.0),
    run_name: str = Query(default="stage6_type35"),
    db: Session = Depends(get_db),
) -> dict:
    report = build_stage6_type35_report(
        db,
        days=days,
        horizon=horizon,
        min_labeled_returns=min_labeled_returns,
        keep_ev_min=keep_ev_min,
        keep_hit_rate_min=keep_hit_rate_min,
        keep_sharpe_like_min=keep_sharpe_like_min,
        keep_risk_of_ruin_max=keep_risk_of_ruin_max,
        modify_ev_min=modify_ev_min,
        min_subhour_coverage=min_subhour_coverage,
    )
    tracking = record_stage5_experiment(
        run_name=run_name,
        params={
            "days": days,
            "horizon": horizon,
            "min_labeled_returns": min_labeled_returns,
            "keep_ev_min": keep_ev_min,
            "keep_hit_rate_min": keep_hit_rate_min,
            "keep_sharpe_like_min": keep_sharpe_like_min,
            "keep_risk_of_ruin_max": keep_risk_of_ruin_max,
            "modify_ev_min": modify_ev_min,
            "min_subhour_coverage": min_subhour_coverage,
            "report_type": "stage6_type35",
        },
        metrics=extract_stage6_type35_metrics(report),
        tags={"decision_counts": str(report.get("decision_counts"))},
    )
    return {"report": report, "tracking": tracking}


@router.get("/research/stage6-final-report")
def research_stage6_final_report(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    min_labeled_returns: int = Query(default=30, ge=1, le=100000),
    db: Session = Depends(get_db),
) -> dict:
    return build_stage6_final_report(
        db,
        days=days,
        horizon=horizon,
        min_labeled_returns=min_labeled_returns,
    )


@router.post("/research/stage6-final-report/track")
def research_stage6_final_report_track(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    min_labeled_returns: int = Query(default=30, ge=1, le=100000),
    run_name: str = Query(default="stage6_final_report"),
    db: Session = Depends(get_db),
) -> dict:
    report = build_stage6_final_report(
        db,
        days=days,
        horizon=horizon,
        min_labeled_returns=min_labeled_returns,
    )
    tracking = record_stage5_experiment(
        run_name=run_name,
        params={
            "days": days,
            "horizon": horizon,
            "min_labeled_returns": min_labeled_returns,
            "report_type": "stage6_final_report",
        },
        metrics=extract_stage6_final_report_metrics(report),
        tags={
            "final_decision": str(report.get("final_decision")),
            "recommended_action": str(report.get("recommended_action")),
        },
    )
    return {"report": report, "tracking": tracking}


@router.get("/research/stage7/stack-scorecard")
def research_stage7_stack_scorecard(
    include_harness: bool = Query(default=True),
    max_latency_ms: int = Query(default=1200, ge=1, le=100000),
) -> dict:
    harness = build_stage7_harness_report(max_latency_ms=max_latency_ms) if include_harness else None
    return build_stage7_stack_scorecard_report(
        harness_by_stack=(harness or {}).get("by_stack"),
    )


@router.post("/research/stage7/stack-scorecard/track")
def research_stage7_stack_scorecard_track(
    run_name: str = Query(default="stage7_stack_scorecard"),
    include_harness: bool = Query(default=True),
    max_latency_ms: int = Query(default=1200, ge=1, le=100000),
) -> dict:
    harness = build_stage7_harness_report(max_latency_ms=max_latency_ms) if include_harness else None
    report = build_stage7_stack_scorecard_report(
        harness_by_stack=(harness or {}).get("by_stack"),
    )
    tracking = record_stage5_experiment(
        run_name=run_name,
        params={
            "report_type": "stage7_stack_scorecard",
            "include_harness": include_harness,
            "max_latency_ms": max_latency_ms,
        },
        metrics=extract_stage7_stack_scorecard_metrics(report),
        tags={"top_stack": str((report.get("summary") or {}).get("top_stack") or "")},
    )
    return {"report": report, "tracking": tracking}


@router.get("/research/stage7/harness")
def research_stage7_harness(
    max_latency_ms: int = Query(default=1200, ge=1, le=100000),
) -> dict:
    return build_stage7_harness_report(max_latency_ms=max_latency_ms)


@router.post("/research/stage7/harness/track")
def research_stage7_harness_track(
    max_latency_ms: int = Query(default=1200, ge=1, le=100000),
    run_name: str = Query(default="stage7_harness"),
) -> dict:
    report = build_stage7_harness_report(max_latency_ms=max_latency_ms)
    tracking = record_stage5_experiment(
        run_name=run_name,
        params={"report_type": "stage7_harness", "max_latency_ms": max_latency_ms},
        metrics=extract_stage7_harness_metrics(report),
        tags={"all_pass_rate_gte_80pct": str((report.get("summary") or {}).get("all_pass_rate_gte_80pct"))},
    )
    return {"report": report, "tracking": tracking}


@router.get("/research/stage7/shadow")
def research_stage7_shadow(
    lookback_days: int = Query(default=14, ge=1, le=90),
    limit: int = Query(default=300, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    return build_stage7_shadow_report(
        db,
        settings=settings,
        lookback_days=lookback_days,
        limit=limit,
    )


@router.post("/research/stage7/shadow/track")
def research_stage7_shadow_track(
    lookback_days: int = Query(default=14, ge=1, le=90),
    limit: int = Query(default=300, ge=1, le=2000),
    run_name: str = Query(default="stage7_shadow"),
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    report = build_stage7_shadow_report(
        db,
        settings=settings,
        lookback_days=lookback_days,
        limit=limit,
    )
    tracking = record_stage5_experiment(
        run_name=run_name,
        params={"lookback_days": lookback_days, "limit": limit, "report_type": "stage7_shadow"},
        metrics=extract_stage7_shadow_metrics(report),
        tags={"provider": settings.stage7_agent_provider},
    )
    return {"report": report, "tracking": tracking}


@router.get("/research/stage7/final-report")
def research_stage7_final_report(
    lookback_days: int = Query(default=14, ge=1, le=90),
    limit: int = Query(default=300, ge=1, le=2000),
    stage6_days: int = Query(default=30, ge=1, le=365),
    stage6_horizon: str = Query(default="6h"),
    stage6_min_labeled_returns: int = Query(default=30, ge=1, le=100000),
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    return build_stage7_final_report(
        db,
        settings=settings,
        lookback_days=lookback_days,
        limit=limit,
        stage6_days=stage6_days,
        stage6_horizon=stage6_horizon,
        stage6_min_labeled_returns=stage6_min_labeled_returns,
    )


@router.post("/research/stage7/final-report/track")
def research_stage7_final_report_track(
    lookback_days: int = Query(default=14, ge=1, le=90),
    limit: int = Query(default=300, ge=1, le=2000),
    stage6_days: int = Query(default=30, ge=1, le=365),
    stage6_horizon: str = Query(default="6h"),
    stage6_min_labeled_returns: int = Query(default=30, ge=1, le=100000),
    run_name: str = Query(default="stage7_final_report"),
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    report = build_stage7_final_report(
        db,
        settings=settings,
        lookback_days=lookback_days,
        limit=limit,
        stage6_days=stage6_days,
        stage6_horizon=stage6_horizon,
        stage6_min_labeled_returns=stage6_min_labeled_returns,
    )
    tracking = record_stage5_experiment(
        run_name=run_name,
        params={
            "lookback_days": lookback_days,
            "limit": limit,
            "stage6_days": stage6_days,
            "stage6_horizon": stage6_horizon,
            "stage6_min_labeled_returns": stage6_min_labeled_returns,
            "report_type": "stage7_final_report",
        },
        metrics=extract_stage7_final_report_metrics(report),
        tags={
            "final_decision": str(report.get("final_decision")),
            "recommended_action": str(report.get("recommended_action")),
        },
    )
    return {"report": report, "tracking": tracking}


@router.get("/research/monte-carlo")
def research_monte_carlo(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    signal_type: str = Query(default=SignalType.DIVERGENCE.value),
    min_divergence: float | None = Query(default=None, ge=0.0, le=1.0),
    n_sims: int = Query(default=1000, ge=1, le=20000),
    trades_per_sim: int = Query(default=100, ge=1, le=1000),
    initial_capital: float = Query(default=1000.0, ge=1.0, le=10000000.0),
    position_size_usd: float = Query(default=100.0, ge=1.0, le=100000.0),
    ruin_drawdown_threshold: float = Query(default=0.5, ge=0.05, le=0.99),
    seed: int = Query(default=42, ge=0, le=10000000),
    db: Session = Depends(get_db),
) -> dict:
    result = build_monte_carlo_summary(
        db,
        days=days,
        horizon=horizon,
        signal_type=signal_type,
        min_divergence=min_divergence,
        n_sims=n_sims,
        trades_per_sim=trades_per_sim,
        initial_capital=initial_capital,
        position_size_usd=position_size_usd,
        ruin_drawdown_threshold=ruin_drawdown_threshold,
        seed=seed,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result)
    return result


@router.get("/research/result-tables")
def research_result_tables(
    days: int = Query(default=30, ge=1, le=365),
    horizon: str = Query(default="6h"),
    min_samples: int = Query(default=10, ge=1, le=10000),
    db: Session = Depends(get_db),
) -> dict:
    return build_result_tables(
        db,
        days=days,
        horizon=horizon,
        min_samples=min_samples,
    )
