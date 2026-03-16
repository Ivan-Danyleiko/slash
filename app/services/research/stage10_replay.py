from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
import random
from types import SimpleNamespace
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.models import (
    Market,
    MarketSnapshot,
    Signal,
    SignalHistory,
    Stage7AgentDecision,
    Stage8Decision,
    Stage10ReplayRow,
)
from app.services.research.stage10_leakage_guard import detect_leakage_for_row
from app.services.research.stage10_timeline_sources import resolve_timeline_point


def _safe_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _hget(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _as_utc(dt: Any) -> datetime | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        raw = dt.strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return _as_utc(parsed)
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _latest_by_signal_id(rows: list[Any]) -> dict[int, Any]:
    out: dict[int, Any] = {}
    for row in rows:
        sid = int(_hget(row, "signal_id", 0) or 0)
        if sid <= 0:
            continue
        if sid not in out:
            out[sid] = row
    return out


def _load_signal_history_rows_compat(db: Session, *, cutoff: datetime, limit: int) -> list[Any]:
    try:
        return list(
            db.scalars(
                select(SignalHistory)
                .where(SignalHistory.timestamp >= cutoff)
                .order_by(SignalHistory.timestamp.desc())
                .limit(limit)
            )
        )
    except OperationalError:
        inspector = sa_inspect(db.get_bind())
        columns = {str(c.get("name")) for c in inspector.get_columns("signal_history")}
        wanted = [
            "id",
            "signal_id",
            "signal_type",
            "timestamp",
            "platform",
            "source_tag",
            "market_id",
            "probability_at_signal",
            "divergence",
            "liquidity",
            "volume_24h",
            "resolved_success",
            "resolved_outcome",
            "signal_direction",
        ]
        selects: list[str] = []
        for name in wanted:
            if name in columns:
                selects.append(name)
            else:
                selects.append(f"NULL as {name}")
        stmt = text(
            f"SELECT {', '.join(selects)} "  # noqa: S608
            "FROM signal_history WHERE timestamp >= :cutoff "
            "ORDER BY timestamp DESC LIMIT :limit"
        )
        rows = list(db.execute(stmt, {"cutoff": cutoff.isoformat(), "limit": int(limit)}).mappings())
        return [dict(r) for r in rows]


def _load_markets_compat(db: Session, *, market_ids: list[int]) -> dict[int, Any]:
    if not market_ids:
        return {}
    try:
        rows = list(db.scalars(select(Market).where(Market.id.in_(market_ids))))
        return {int(r.id): r for r in rows}
    except OperationalError:
        inspector = sa_inspect(db.get_bind())
        columns = {str(c.get("name")) for c in inspector.get_columns("markets")}
        wanted = [
            "id",
            "external_market_id",
            "category",
            "source_payload",
            "fetched_at",
            "title",
        ]
        selects: list[str] = []
        for name in wanted:
            if name in columns:
                selects.append(name)
            else:
                selects.append(f"NULL as {name}")
        placeholders = ",".join(f":m{i}" for i in range(len(market_ids)))
        params = {f"m{i}": int(mid) for i, mid in enumerate(market_ids)}
        stmt = text(f"SELECT {', '.join(selects)} FROM markets WHERE id IN ({placeholders})")  # noqa: S608
        out: dict[int, Any] = {}
        for row in db.execute(stmt, params).mappings():
            rid = int(row.get("id") or 0)
            if rid <= 0:
                continue
            payload = row.get("source_payload")
            if isinstance(payload, str):
                try:
                    import json

                    payload = json.loads(payload)
                except Exception:  # noqa: BLE001
                    payload = {}
            out[rid] = SimpleNamespace(
                id=rid,
                external_market_id=row.get("external_market_id"),
                category=row.get("category"),
                source_payload=payload if isinstance(payload, dict) else {},
                fetched_at=row.get("fetched_at"),
                title=row.get("title") or "",
            )
        return out


def _source_count_from_consensus(consensus: dict[str, Any] | None) -> int:
    if not isinstance(consensus, dict):
        return 0
    keys = ("polymarket_prob", "manifold_prob", "metaculus_median")
    return sum(1 for k in keys if _safe_float(consensus.get(k)) is not None)


def _normalize_core_category(raw: Any, *, title: str = "") -> str:
    text = str(raw or "").strip().lower()
    title_text = str(title or "").strip().lower()
    merged = f"{text} {title_text}".strip()
    if not text:
        text = merged

    # Exact category-field matches (common values from Polymarket/Manifold/Metaculus)
    if text in {
        "crypto", "cryptocurrency", "defi", "nfts", "bitcoin", "ethereum",
        "blockchain", "web3",
    }:
        return "crypto"
    if text in {
        "finance", "economics", "business", "stocks", "macro", "market",
        "economy", "markets", "financial", "investing",
    }:
        return "finance"
    if text in {
        "sports", "sport", "nba", "nfl", "mlb", "nhl", "soccer", "football",
        "tennis", "olympics", "esports", "gaming", "mma", "boxing",
    }:
        return "sports"
    if text in {
        "politics", "us-politics", "us-current-affairs", "elections",
        "government", "policy", "geopolitics", "world", "news",
        "current-events", "current events",
    }:
        return "politics"

    # Keyword search in merged (category + title).
    # Note: "bitcoin" contains "bit" (irrelevant) but also use full-word checks for common names.
    if any(k in merged for k in (
        "btc", "eth", "sol", "xrp", "avax", "bnb", "ada", "doge",
        "crypto", "token", "coin", "defi", "nft", "blockchain",
        "bitcoin", "ethereum", "solana", "binance", "polygon", "chainlink",
        "altcoin", "stablecoin", "celsius", "ftx", "coinbase",
    )):
        return "crypto"
    if any(k in merged for k in (
        "stock", "gdp", "cpi", "fed", "interest rate", "econom", "finance",
        "market cap", "s&p", "nasdaq", "dow jones", "dow", "hedge",
        "ipo", "revenue", "inflation", "treasury", "yield", "bond",
        "recession", "earnings", "commodity", "oil price", "gold price",
    )):
        return "finance"
    if any(k in merged for k in (
        "sport", "nba", "nfl", "mlb", "nhl", "soccer", "football",
        "match", "tournament", "olympic", "tennis", "championship",
        "league", "fifa", "mma", "boxing", "ufc", "f1", "formula 1",
        "basketball", "baseball", "hockey", "cricket", "rugby",
        "premier league", "epl", "la liga", "bundesliga", "serie a",
        "golf", "pga", "wimbledon", "super bowl", "world cup",
        "lakers", "celtics", "yankees", "patriots", "warriors",
    )):
        return "sports"
    if any(k in merged for k in (
        "elect", "president", "senate", "congress", "government",
        "politic", "policy", "vote", "ballot", "democrat", "republican",
        "prime minister", "parliament", "geopolit", "war", "nato",
        "ukraine", "russia", "china", "taiwan", "iran", "israel",
        "white house", "supreme court", "un ", "united nations",
        "trump", "biden", "harris", "macron", "xi jinping",
        "sanction", "ceasefire", "treaty", "diplomat",
    )):
        return "politics"
    return "other"


def _normalize_signal_type(raw: Any) -> str:
    val = str(raw or "").strip().upper()
    if not val:
        return ""
    if "." in val:
        val = val.split(".")[-1]
    return val


def _fallback_edge_proxy(row: dict[str, Any]) -> float:
    features = dict(row.get("features_snapshot") or {})
    divergence = _safe_float(features.get("divergence"))
    if divergence is not None and divergence > 0:
        # Conservative assumption: ~50% of the divergence gap converges.
        return float(divergence * 0.5)
    p_t = _safe_float(features.get("probability_t"))
    if p_t is not None:
        # If we only have point probability, use distance-from-50% as a weak proxy.
        # Capped to avoid over-optimistic historical sweeps.
        return float(min(0.20, abs(p_t - 0.5) * 0.5))
    return 0.0


def _row_effective_edge(row: dict[str, Any]) -> float:
    edge = float(row.get("predicted_edge_after_costs_pct") or 0.0)
    if edge != 0.0:
        return edge
    return _fallback_edge_proxy(row)


def _prediction_market_return(row: dict[str, Any], *, won: bool) -> float:
    """Asymmetric prediction-market payoff for a binary market.

    Prediction markets are zero-sum: buying YES at price p pays (1-p) on win
    and loses p on loss.

    Important: for legacy replay rows without real signal context (signal_id=0),
    asymmetric payout on inferred directions is often too noisy. For such rows
    we keep the symmetric edge proxy to avoid introducing artificial negativity.

    Falls back to the symmetric _row_effective_edge model when the probability
    is not available (e.g. the row came from the execution pipeline with a
    known edge but no raw price).
    """
    predicted_edge = float(row.get("predicted_edge_after_costs_pct") or 0.0)
    if predicted_edge != 0.0:
        # Pipeline-computed edge is already net of costs — keep it symmetric.
        return predicted_edge if won else -abs(predicted_edge)

    signal_id = int(row.get("signal_id") or 0)
    if signal_id <= 0:
        # Legacy candidate/backfill rows (no signal context): use edge proxy.
        edge = _fallback_edge_proxy(row)
        return edge if won else -abs(edge)

    features = dict(row.get("features_snapshot") or {})
    p_t = _safe_float(features.get("probability_t"))
    direction = str(row.get("signal_direction_used") or "").strip().upper()

    if p_t is not None and direction in {"YES", "NO"}:
        p = max(0.01, min(0.99, float(p_t)))
        if direction == "YES":
            return (1.0 - p) if won else -p
        else:  # NO direction: selling YES / buying NO at price (1-p)
            return p if won else -(1.0 - p)

    # Symmetric fallback when direction or probability is unknown.
    edge = _fallback_edge_proxy(row)
    return edge if won else -abs(edge)


def _infer_signal_direction(history_row: Any, signal: Signal | None) -> str | None:
    explicit = str(_hget(history_row, "signal_direction") or "").strip().upper()
    if explicit in {"YES", "NO"}:
        return explicit
    signal_type = _normalize_signal_type(
        _hget(history_row, "signal_type") or (signal.signal_type if signal is not None else ""),
    )
    if signal_type == "RULES_RISK":
        return "NO"
    if signal_type == "DIVERGENCE":
        p0 = _safe_float(_hget(history_row, "probability_at_signal"))
        related = _safe_float(_hget(history_row, "related_market_probability"))
        if p0 is not None and related is not None:
            return "YES" if p0 < related else "NO"
    if signal_type in {"ARBITRAGE_CANDIDATE", "DUPLICATE_MARKET"}:
        p0 = _safe_float(_hget(history_row, "probability_at_signal"))
        if p0 is not None:
            return "YES" if p0 < 0.5 else "NO"
    return None


def _resolve_direction_aware_success(history_row: Any, *, signal_direction: str | None) -> bool | None:
    resolved_outcome = str(_hget(history_row, "resolved_outcome") or "").strip().upper()
    if resolved_outcome == "VOID":
        return None
    if signal_direction in {"YES", "NO"} and resolved_outcome in {"YES", "NO"}:
        return resolved_outcome == signal_direction

    p0 = _safe_float(_hget(history_row, "probability_at_signal"))
    resolved_probability = _safe_float(_hget(history_row, "resolved_probability"))
    if signal_direction in {"YES", "NO"} and p0 is not None and resolved_probability is not None:
        if signal_direction == "YES":
            return resolved_probability > p0
        return resolved_probability < p0

    legacy = _hget(history_row, "resolved_success")
    if legacy is None:
        return None
    legacy_bool = bool(legacy)
    if signal_direction == "YES":
        return legacy_bool
    if signal_direction == "NO":
        return not legacy_bool
    return legacy_bool


def _bootstrap_ci(values: list[float], *, n_sims: int = 500, conf_level: float = 0.80, seed: int = 42) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    sims = max(100, min(int(n_sims), 5000))
    conf = min(0.99, max(0.50, float(conf_level)))
    alpha = 1.0 - conf
    rng = random.Random(seed)
    n = len(values)
    means: list[float] = []
    for _ in range(sims):
        sample = [values[rng.randrange(0, n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo_idx = int((alpha / 2.0) * (len(means) - 1))
    hi_idx = int((1.0 - (alpha / 2.0)) * (len(means) - 1))
    return float(means[lo_idx]), float(means[hi_idx])


def _precision_at_k(rows: list[dict[str, Any]], *, k: int) -> float:
    ranked = sorted(rows, key=lambda r: float(r.get("predicted_edge_after_costs_pct") or 0.0), reverse=True)[:k]
    labeled = [r for r in ranked if r.get("resolved_success_direction_aware") is not None]
    if not labeled:
        return 0.0
    hits = sum(1 for r in labeled if bool(r.get("resolved_success_direction_aware")))
    return float(hits / len(labeled))


def _brier_bss_ece(preds: list[float], ys: list[float]) -> tuple[float, float, float]:
    n = len(preds)
    if n <= 0:
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


def _reason_code_stability(rows: list[dict[str, Any]]) -> float:
    by_hash: dict[str, set[str]] = {}
    total = 0
    stable = 0
    for row in rows:
        ih = str(row.get("input_hash") or "").strip()
        if not ih:
            continue
        codes = set(str(x) for x in list(row.get("agent_reason_codes") or []) if str(x))
        if ih in by_hash:
            total += 1
            if by_hash[ih] == codes:
                stable += 1
        else:
            by_hash[ih] = codes
    if total == 0:
        return 1.0
    return float(stable / total)


def _scenario_sweeps(rows: list[dict[str, Any]], *, use_real_returns: bool = True) -> dict[str, Any]:
    position_sizes = [50.0, 100.0, 500.0]
    spreads = [0.01, 0.03, 0.05]
    fees = [0.02, 0.025]
    scenarios: list[dict[str, Any]] = []
    positive = 0
    keep_rows = [r for r in rows if str(r.get("agent_decision") or "") == "KEEP"]
    # Fallback: when no pipeline KEEP decisions exist (e.g. divergence-candidate rows without
    # Stage7/8 data), evaluate all resolved rows as if the signal was taken.
    if not keep_rows:
        keep_rows = [r for r in rows if r.get("resolved_success_direction_aware") is not None]
    total_keep = len(keep_rows)
    keep_with_real = sum(1 for r in keep_rows if r.get("resolved_success_direction_aware") is not None)
    real_coverage = (keep_with_real / total_keep) if total_keep else 0.0
    sweeps_using_real_returns = 0
    sweeps_using_proxy = 0
    positive_scenarios_real = 0
    positive_scenarios_proxy = 0
    for size in position_sizes:
        for spread in spreads:
            for fee in fees:
                size_penalty = 0.002 if size == 50.0 else (0.004 if size == 100.0 else 0.01)
                stress_costs = spread + fee + size_penalty
                vals: list[float] = []
                real_used = 0
                proxy_used = 0
                for row in keep_rows:
                    resolved = row.get("resolved_success_direction_aware")
                    if use_real_returns and resolved is not None:
                        realized = _prediction_market_return(row, won=bool(resolved))
                        real_used += 1
                    else:
                        # Fallback when no labeled outcome is available: normalized edge proxy.
                        edge = _row_effective_edge(row)
                        if resolved is None:
                            realized = edge
                        else:
                            realized = edge if bool(resolved) else -abs(edge)
                        proxy_used += 1
                    vals.append(realized - stress_costs)
                mean_ret = (sum(vals) / len(vals)) if vals else -stress_costs
                ok = mean_ret > 0.0
                if ok:
                    positive += 1
                if real_used > 0:
                    sweeps_using_real_returns += 1
                    if ok:
                        positive_scenarios_real += 1
                else:
                    sweeps_using_proxy += 1
                    if ok:
                        positive_scenarios_proxy += 1
                scenarios.append(
                    {
                        "position_size_usd": size,
                        "spread": spread,
                        "fee": fee,
                        "real_rows": int(real_used),
                        "proxy_rows": int(proxy_used),
                        "mean_post_cost_return": round(float(mean_ret), 6),
                        "positive": ok,
                    }
                )
    return {
        "required_positive": 12,
        "positive_scenarios": positive,
        "total_scenarios": len(scenarios),
        "passes_12_of_18": positive >= 12,
        "sweeps_using_real_returns": int(sweeps_using_real_returns),
        "sweeps_using_proxy": int(sweeps_using_proxy),
        "real_return_coverage": round(float(real_coverage), 6),
        "positive_scenarios_real": int(positive_scenarios_real),
        "positive_scenarios_proxy": int(positive_scenarios_proxy),
        "rows": scenarios,
    }


def _upsert_stage10_row(db: Session, payload: dict[str, Any]) -> Stage10ReplayRow:
    signal_history_id = int(payload.get("signal_history_id") or 0)
    existing = db.scalar(select(Stage10ReplayRow).where(Stage10ReplayRow.signal_history_id == signal_history_id))
    row = existing or Stage10ReplayRow(signal_history_id=signal_history_id)

    row.event_id = str(payload.get("event_id") or "")
    row.market_id = int(payload.get("market_id") or 0)
    signal_id = int(payload.get("signal_id") or 0)
    row.signal_id = signal_id if signal_id > 0 else None
    row.platform = str(payload.get("platform") or "")
    row.category = str(payload.get("category") or "other")
    row.replay_timestamp = payload.get("replay_timestamp")
    row.feature_observed_at_max = payload.get("feature_observed_at_max")
    row.feature_source_count = int(payload.get("feature_source_count") or 0)
    row.features_snapshot = dict(payload.get("features_snapshot") or {})
    row.policy_decision = str(payload.get("policy_decision") or "SKIP")
    row.agent_decision = str(payload.get("agent_decision") or "SKIP")
    row.execution_action = str(payload.get("execution_action") or "SHADOW_ONLY")
    row.predicted_edge_after_costs_pct = _safe_float(payload.get("predicted_edge_after_costs_pct"))
    row.cost_components = dict(payload.get("cost_components") or {})
    row.resolved_outcome = str(payload.get("resolved_outcome") or "PENDING")
    row.resolved_success_direction_aware = payload.get("resolved_success_direction_aware")
    row.trace_id = str(payload.get("trace_id") or "")
    row.input_hash = str(payload.get("input_hash") or "")
    row.model_version = str(payload.get("model_version") or "")
    row.leakage_violation = bool(payload.get("leakage_violation"))
    row.leakage_reason_codes = list(payload.get("leakage_reason_codes") or [])

    if existing is None:
        db.add(row)
    return row


def build_stage10_replay_report(
    db: Session,
    *,
    settings: Settings,
    days: int = 365,
    limit: int = 5000,
    event_target: int = 100,
    persist_rows: bool = True,
) -> dict[str, Any]:
    cutoff = datetime.now(UTC) - timedelta(days=max(1, int(days)))
    hist_rows = _load_signal_history_rows_compat(db, cutoff=cutoff, limit=max(100, int(limit)))

    signal_ids = [int(_hget(r, "signal_id") or 0) for r in hist_rows if (_hget(r, "signal_id") or 0) > 0]
    market_ids = [int(_hget(r, "market_id") or 0) for r in hist_rows if (_hget(r, "market_id") or 0) > 0]

    signals = list(db.scalars(select(Signal).where(Signal.id.in_(signal_ids)))) if signal_ids else []
    market_map = _load_markets_compat(db, market_ids=market_ids)
    if market_ids:
        try:
            snapshots = list(
                db.scalars(
                    select(MarketSnapshot)
                    .where(MarketSnapshot.market_id.in_(market_ids))
                    .order_by(MarketSnapshot.market_id.asc(), MarketSnapshot.fetched_at.asc())
                )
            )
        except OperationalError:
            snapshots = []
    else:
        snapshots = []

    signal_map = {int(s.id): s for s in signals}
    snapshots_by_market: dict[int, list[MarketSnapshot]] = defaultdict(list)
    for snap in snapshots:
        snapshots_by_market[int(snap.market_id)].append(snap)

    stage7_rows = (
        list(
            db.scalars(
                select(Stage7AgentDecision)
                .where(Stage7AgentDecision.signal_id.in_(signal_ids))
                .order_by(Stage7AgentDecision.id.desc())
            )
        )
        if signal_ids
        else []
    )
    stage8_rows = (
        list(
            db.scalars(select(Stage8Decision).where(Stage8Decision.signal_id.in_(signal_ids)).order_by(Stage8Decision.id.desc()))
        )
        if signal_ids
        else []
    )
    stage7_by_signal = _latest_by_signal_id(stage7_rows)
    stage8_by_signal = _latest_by_signal_id(stage8_rows)

    rows: list[dict[str, Any]] = []
    leakage_counts: dict[str, int] = defaultdict(int)
    categories: dict[str, int] = defaultdict(int)
    unique_events: set[str] = set()
    data_insufficient_timeline_count = 0
    timeline_source_counts: dict[str, int] = defaultdict(int)
    skipped_missing_signal_id = 0
    skipped_probability_t_missing = 0
    direction_missing_count = 0
    direction_present_count = 0
    direction_inferred_count = 0

    for h in hist_rows:
        signal_id = int(_hget(h, "signal_id") or 0)
        market_id = int(_hget(h, "market_id") or 0)
        market = market_map.get(market_id)
        signal = signal_map.get(signal_id) if signal_id else None
        s7 = stage7_by_signal.get(signal_id) if signal_id else None
        s8 = stage8_by_signal.get(signal_id) if signal_id else None

        event_id = f"{market_id}"
        if market and market.external_market_id:
            event_id = str(market.external_market_id)

        consensus = ((s7.evidence_bundle or {}).get("external_consensus") if s7 and isinstance(s7.evidence_bundle, dict) else {}) or {}
        replay_ts = _as_utc(_hget(h, "timestamp")) or datetime.now(UTC)
        timeline = resolve_timeline_point(
            market=market,
            history_row=h,
            replay_timestamp=replay_ts,
            snapshots=snapshots_by_market.get(market_id, []),
        )
        prob_t = _safe_float(timeline.probability_t)
        if prob_t is None:
            skipped_probability_t_missing += 1
            continue
        timeline_source = str(timeline.source or "none")
        timeline_sufficient = bool(timeline.sufficient)
        if not timeline_sufficient:
            data_insufficient_timeline_count += 1
        timeline_source_counts[timeline_source] += 1

        feature_source_count = _source_count_from_consensus(consensus) + (1 if timeline_sufficient else 0)

        execution = (signal.execution_analysis if signal and isinstance(signal.execution_analysis, dict) else {}) or {}
        predicted_edge = _safe_float(execution.get("expected_ev_after_costs_pct")) or 0.0
        cost_components = {
            "expected_costs_pct": _safe_float(execution.get("expected_costs_pct")) or 0.0,
            "assumptions_version": str(execution.get("assumptions_version") or "unknown"),
        }

        features_snapshot = {
            "probability_t": prob_t,
            "timeline_source": timeline_source,
            "liquidity": _safe_float(_hget(h, "liquidity")),
            "volume_24h": _safe_float(_hget(h, "volume_24h")),
            "divergence": _safe_float(_hget(h, "divergence")),
            "source_count": feature_source_count,
            "signal_type": str(_hget(h, "signal_type")),
            "data_sufficient_timeline": timeline_sufficient,
        }
        feature_keys = list(features_snapshot.keys())

        has_leakage, leakage_reasons = detect_leakage_for_row(
            replay_timestamp=replay_ts,
            feature_observed_at_max=_as_utc(timeline.observed_at) if timeline.observed_at else replay_ts,
            feature_keys=feature_keys,
            embargo_seconds=max(0, int(settings.stage10_replay_embargo_seconds)),
        )
        leakage_reasons.extend([x for x in timeline.reason_codes if x not in leakage_reasons])
        has_leakage = bool(has_leakage or (not timeline_sufficient))
        for code in leakage_reasons:
            leakage_counts[code] += 1

        policy_decision = str(s8.base_decision if s8 else (s7.base_decision if s7 else "SKIP"))
        agent_decision = str(s8.decision if s8 else (s7.decision if s7 else "SKIP"))
        execution_action = str(s8.execution_action if s8 else "SHADOW_ONLY")
        category_raw = (market.category if market and market.category else "other") or "other"
        market_title = str(getattr(market, "title", "") or "")
        category = _normalize_core_category(category_raw, title=market_title)
        categories[category] += 1
        signal_direction_explicit = str(_hget(h, "signal_direction") or "").strip().upper()
        signal_direction = _infer_signal_direction(h, signal)
        if signal_direction_explicit in {"YES", "NO"}:
            direction_present_count += 1
        elif signal_direction in {"YES", "NO"}:
            direction_inferred_count += 1
        else:
            direction_missing_count += 1

        resolved_success_direction_aware = _resolve_direction_aware_success(
            h,
            signal_direction=signal_direction,
        )

        payload = {
            "signal_history_id": int(_hget(h, "id") or 0),
            "event_id": event_id,
            "market_id": market_id,
            "signal_id": signal_id,
            "platform": str(_hget(h, "platform") or "unknown"),
            "category": category,
            "replay_timestamp": replay_ts,
            "feature_observed_at_max": _as_utc(timeline.observed_at) if timeline.observed_at else replay_ts,
            "feature_source_count": feature_source_count,
            "features_snapshot": features_snapshot,
            "policy_decision": policy_decision,
            "agent_decision": agent_decision,
            "execution_action": execution_action,
            "predicted_edge_after_costs_pct": predicted_edge,
            "cost_components": cost_components,
            "resolved_outcome": str(_hget(h, "resolved_outcome") or "PENDING"),
            "resolved_success_direction_aware": resolved_success_direction_aware,
            "signal_direction_used": signal_direction or "",
            "trace_id": str((s7.evidence_bundle or {}).get("trace_id") if s7 and isinstance(s7.evidence_bundle, dict) else ""),
            "input_hash": str(s7.input_hash if s7 else ""),
            "model_version": str(s7.model_version if s7 else ""),
            "agent_reason_codes": list(s7.reason_codes or []) if s7 and isinstance(s7.reason_codes, list) else [],
            "leakage_violation": has_leakage,
            "leakage_reason_codes": leakage_reasons,
        }
        if signal_id <= 0:
            skipped_missing_signal_id += 1
            payload["leakage_reason_codes"] = list(
                dict.fromkeys([*list(payload.get("leakage_reason_codes") or []), "missing_signal_id"])
            )
        elif persist_rows:
            _upsert_stage10_row(db, payload)
        rows.append(payload)
        unique_events.add(event_id)

    if persist_rows:
        db.commit()

    rows_total = len(rows)
    leakage_violations_count = sum(1 for r in rows if bool(r.get("leakage_violation")))
    leakage_violation_rate = (leakage_violations_count / rows_total) if rows_total else 0.0
    data_insufficient_timeline_share = (data_insufficient_timeline_count / rows_total) if rows_total else 1.0
    resolved_rows = [
        r
        for r in rows
        if r.get("resolved_success_direction_aware") is not None and str(r.get("resolved_outcome") or "").upper() != "VOID"
    ]
    post_cost_returns = [
        _prediction_market_return(r, won=bool(r.get("resolved_success_direction_aware")))
        for r in resolved_rows
    ]
    ev_mean = (sum(post_cost_returns) / len(post_cost_returns)) if post_cost_returns else 0.0
    ev_ci_low, ev_ci_high = _bootstrap_ci(post_cost_returns, n_sims=500, conf_level=0.80)

    category_returns: dict[str, list[float]] = defaultdict(list)
    for r in resolved_rows:
        cat = str(r.get("category") or "other").strip().lower() or "other"
        ret = _prediction_market_return(r, won=bool(r.get("resolved_success_direction_aware")))
        category_returns[cat].append(ret)
    core_categories = ("crypto", "finance", "sports", "politics")
    core_category_ev_ci_low_80: dict[str, float] = {}
    for cat in core_categories:
        low, _ = _bootstrap_ci(category_returns.get(cat, []), n_sims=500, conf_level=0.80)
        core_category_ev_ci_low_80[cat] = low
    core_category_positive_ev_candidates = sum(1 for cat in core_categories if core_category_ev_ci_low_80.get(cat, 0.0) > 0.0)

    preds: list[float] = []
    ys: list[float] = []
    for r in resolved_rows:
        p = _safe_float((r.get("features_snapshot") or {}).get("probability_t"))
        if p is None:
            continue
        pred = max(0.0, min(1.0, float(p)))
        y = 1.0 if bool(r.get("resolved_success_direction_aware")) else 0.0
        preds.append(pred)
        ys.append(y)
    brier_score, brier_skill_score, ece = _brier_bss_ece(preds, ys)
    long_idx = [k for k, p in enumerate(preds) if p <= 0.15]
    longshot_bias_error_0_15pct = ((sum(ys[k] - preds[k] for k in long_idx) / len(long_idx)) if long_idx else 0.0)

    sweeps = _scenario_sweeps(rows)
    reason_code_stability = _reason_code_stability(rows)
    precision_at_10 = _precision_at_k(rows, k=10)
    precision_at_25 = _precision_at_k(rows, k=25)
    precision_at_50 = _precision_at_k(rows, k=50)
    categories_core = {k: int(categories.get(k, 0)) for k in ("crypto", "finance", "sports", "politics")}

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "rows_total": rows_total,
            "events_total": len(unique_events),
            "event_target": int(event_target),
            "event_target_reached": len(unique_events) >= int(event_target),
            "leakage_violations_count": leakage_violations_count,
            "leakage_violation_rate": leakage_violation_rate,
            "data_insufficient_timeline_count": data_insufficient_timeline_count,
            "data_insufficient_timeline_share": data_insufficient_timeline_share,
            "resolved_rows_total": len(resolved_rows),
            "post_cost_ev_mean_pct": ev_mean,
            "post_cost_ev_ci_low_80": ev_ci_low,
            "post_cost_ev_ci_high_80": ev_ci_high,
            "core_category_ev_ci_low_80": core_category_ev_ci_low_80,
            "core_category_positive_ev_candidates": int(core_category_positive_ev_candidates),
            "direction_missing_count": int(direction_missing_count),
            "direction_present_count": int(direction_present_count),
            "direction_inferred_count": int(direction_inferred_count),
            "precision_at_10": precision_at_10,
            "precision_at_25": precision_at_25,
            "precision_at_50": precision_at_50,
            "brier_score": brier_score,
            "brier_skill_score": brier_skill_score,
            "ece": ece,
            "longshot_bias_error_0_15pct": longshot_bias_error_0_15pct,
            "reason_code_stability": reason_code_stability,
            "core_category_counts": categories_core,
            "core_categories_each_ge_20": all(v >= 20 for v in categories_core.values()),
            "timeline_source_counts": dict(timeline_source_counts),
            "skipped_missing_signal_id": skipped_missing_signal_id,
            "skipped_probability_t_missing": int(skipped_probability_t_missing),
        },
        "leakage_reason_counts": dict(leakage_counts),
        "scenario_sweeps": sweeps,
        "rows": rows,
    }


def extract_stage10_replay_metrics(report: dict[str, Any]) -> dict[str, float]:
    summary = dict(report.get("summary") or {})
    return {
        "stage10_rows_total": float(summary.get("rows_total") or 0.0),
        "stage10_events_total": float(summary.get("events_total") or 0.0),
        "stage10_event_target_reached": 1.0 if bool(summary.get("event_target_reached")) else 0.0,
        "stage10_leakage_violations_count": float(summary.get("leakage_violations_count") or 0.0),
        "stage10_leakage_violation_rate": float(summary.get("leakage_violation_rate") or 0.0),
        "stage10_data_insufficient_timeline_share": float(summary.get("data_insufficient_timeline_share") or 1.0),
        "stage10_post_cost_ev_ci_low_80": float(summary.get("post_cost_ev_ci_low_80") or 0.0),
        "stage10_reason_code_stability": float(summary.get("reason_code_stability") or 0.0),
        "stage10_scenario_sweeps_positive": float((report.get("scenario_sweeps") or {}).get("positive_scenarios") or 0.0),
        "stage10_core_categories_each_ge_20": 1.0 if bool(summary.get("core_categories_each_ge_20")) else 0.0,
    }
