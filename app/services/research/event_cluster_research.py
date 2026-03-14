from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
import re
from statistics import mean, pstdev
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import SignalType
from app.models.models import Market, SignalHistory

_HORIZON_TO_FIELD = {
    "1h": "probability_after_1h",
    "6h": "probability_after_6h",
    "24h": "probability_after_24h",
    "resolution": "resolved_probability",
}

_STOPWORDS = {
    "will",
    "what",
    "when",
    "where",
    "which",
    "about",
    "with",
    "from",
    "that",
    "this",
    "have",
    "been",
    "into",
    "over",
    "under",
    "after",
    "before",
    "more",
    "than",
    "year",
    "market",
    "price",
    "prediction",
}


def _normalize_horizon(horizon: str) -> str:
    key = (horizon or "").strip().lower()
    return key if key in _HORIZON_TO_FIELD else "6h"


def _parse_signal_type(signal_type: str | None) -> SignalType | None:
    if not signal_type:
        return None
    raw = signal_type.strip().upper()
    try:
        return SignalType(raw)
    except ValueError:
        return None


def _tokens(title: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", (title or "").lower())
        if len(token) >= 4 and token not in _STOPWORDS
    }


def _build_market_clusters(
    markets: dict[int, Market],
    *,
    min_shared_tokens: int = 2,
    min_jaccard: float = 0.2,
) -> list[set[int]]:
    ids = list(markets.keys())
    token_map = {mid: _tokens(markets[mid].title or "") for mid in ids}
    adjacency: dict[int, set[int]] = {mid: set() for mid in ids}
    for i in range(len(ids)):
        a = ids[i]
        tok_a = token_map[a]
        if not tok_a:
            continue
        for j in range(i + 1, len(ids)):
            b = ids[j]
            tok_b = token_map[b]
            if not tok_b:
                continue
            shared = len(tok_a & tok_b)
            if shared < min_shared_tokens:
                continue
            union = len(tok_a | tok_b) or 1
            jaccard = shared / union
            if jaccard < min_jaccard:
                continue
            adjacency[a].add(b)
            adjacency[b].add(a)

    visited: set[int] = set()
    clusters: list[set[int]] = []
    for mid in ids:
        if mid in visited:
            continue
        stack = [mid]
        component: set[int] = set()
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            component.add(node)
            stack.extend(adjacency[node] - visited)
        if len(component) >= 2:
            clusters.append(component)
    return clusters


def build_event_cluster_research_report(
    db: Session,
    *,
    days: int = 30,
    horizon: str = "6h",
    signal_type: str | None = None,
    min_cluster_size: int = 2,
    min_shared_tokens: int = 2,
    min_jaccard: float = 0.2,
    max_markets: int = 400,
) -> dict[str, Any]:
    days = max(1, min(int(days), 365))
    horizon = _normalize_horizon(horizon)
    min_cluster_size = max(2, min(int(min_cluster_size), 50))
    min_shared_tokens = max(1, min(int(min_shared_tokens), 6))
    min_jaccard = max(0.0, min(float(min_jaccard), 1.0))
    max_markets = max(50, min(int(max_markets), 5000))
    field_name = _HORIZON_TO_FIELD[horizon]
    cutoff = datetime.now(UTC) - timedelta(days=days)

    st = _parse_signal_type(signal_type)
    if signal_type and st is None:
        return {"error": f"unsupported signal_type '{signal_type}'", "supported": [x.value for x in SignalType]}

    stmt = select(SignalHistory).where(
        SignalHistory.timestamp >= cutoff,
        SignalHistory.probability_at_signal.is_not(None),
        getattr(SignalHistory, field_name).is_not(None),
    )
    if st is not None:
        stmt = stmt.where(SignalHistory.signal_type == st)
    rows = list(db.scalars(stmt.order_by(SignalHistory.timestamp.desc())))

    market_ids = list({int(r.market_id) for r in rows})[:max_markets]
    markets = {m.id: m for m in db.scalars(select(Market).where(Market.id.in_(market_ids)))}
    clusters = _build_market_clusters(
        markets,
        min_shared_tokens=min_shared_tokens,
        min_jaccard=min_jaccard,
    )

    market_to_cluster: dict[int, int] = {}
    for idx, cluster in enumerate(clusters):
        for mid in cluster:
            market_to_cluster[mid] = idx

    cluster_probs: dict[int, list[float]] = defaultdict(list)
    cluster_returns: dict[int, list[float]] = defaultdict(list)
    cluster_signal_types: dict[int, Counter[str]] = defaultdict(Counter)
    for row in rows:
        cid = market_to_cluster.get(int(row.market_id))
        if cid is None:
            continue
        prob = row.probability_at_signal
        exit_prob = getattr(row, field_name)
        if prob is None or exit_prob is None:
            continue
        cluster_probs[cid].append(float(prob))
        cluster_returns[cid].append(float(exit_prob) - float(prob))
        cluster_signal_types[cid][row.signal_type.value] += 1

    out_clusters: list[dict[str, Any]] = []
    for cid, cluster_market_ids in enumerate(clusters):
        if len(cluster_market_ids) < min_cluster_size:
            continue
        probs = cluster_probs.get(cid, [])
        rets = cluster_returns.get(cid, [])
        if not probs or not rets:
            continue
        var = (pstdev(probs) ** 2) if len(probs) > 1 else 0.0
        hit_rate = sum(1 for x in rets if x > 0) / len(rets)
        tokens = Counter()
        for mid in cluster_market_ids:
            tokens.update(_tokens((markets[mid].title if mid in markets else "") or ""))
        label = " ".join([tok for tok, _ in tokens.most_common(3)]) or f"cluster_{cid}"
        out_clusters.append(
            {
                "cluster_id": cid,
                "cluster_label": label,
                "unique_markets": len(cluster_market_ids),
                "returns_labeled": len(rets),
                "cluster_probability_variance": round(var, 6),
                "avg_return": round(mean(rets), 6),
                "hit_rate": round(hit_rate, 4),
                "signal_type_mix": dict(cluster_signal_types[cid]),
            }
        )

    out_clusters.sort(
        key=lambda c: (float(c["cluster_probability_variance"]), float(c["avg_return"])),
        reverse=True,
    )
    best = out_clusters[0] if out_clusters else None
    return {
        "period_days": days,
        "horizon": horizon,
        "signal_type": st.value if st else None,
        "min_cluster_size": min_cluster_size,
        "params": {
            "min_shared_tokens": min_shared_tokens,
            "min_jaccard": min_jaccard,
            "max_markets": max_markets,
        },
        "clusters_total": len(out_clusters),
        "best_cluster": best,
        "clusters": out_clusters,
    }


def extract_event_cluster_metrics(report: dict[str, Any]) -> dict[str, float]:
    best = report.get("best_cluster") or {}
    return {
        "clusters_total": float(report.get("clusters_total") or 0.0),
        "best_cluster_variance": float(best.get("cluster_probability_variance") or 0.0),
        "best_cluster_avg_return": float(best.get("avg_return") or 0.0),
        "best_cluster_hit_rate": float(best.get("hit_rate") or 0.0),
    }
