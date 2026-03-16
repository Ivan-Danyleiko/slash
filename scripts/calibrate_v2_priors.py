#!/usr/bin/env python3
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.models import Market, SignalHistory
from app.services.research.stage10_replay import _bootstrap_ci


def _direction_aware_return(row: SignalHistory, after: float) -> float | None:
    if row.probability_at_signal is None:
        return None
    p0 = float(row.probability_at_signal)
    p1 = float(after)
    direction = str(row.signal_direction or "").strip().upper()
    raw = p1 - p0
    return -raw if direction == "NO" else raw


def _recommended_prior(mean_return: float, ci_80_low: float) -> float:
    if ci_80_low > 0:
        return mean_return
    if mean_return > 0:
        return mean_return * 0.5
    return 0.005


def main() -> None:
    lookback_days = 90
    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
    out: dict[str, dict] = {}
    by_category: dict[str, list[float]] = defaultdict(list)

    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(SignalHistory).where(
                    SignalHistory.timestamp >= cutoff,
                    SignalHistory.probability_after_6h.is_not(None),
                    SignalHistory.probability_at_signal.is_not(None),
                )
            )
        )

        market_by_id = {
            m.id: m
            for m in db.scalars(select(Market).where(Market.id.in_({r.market_id for r in rows})))
        }

        for row in rows:
            ret = _direction_aware_return(row, float(row.probability_after_6h))
            if ret is None:
                continue
            category = str((market_by_id.get(row.market_id).category if market_by_id.get(row.market_id) else "other") or "other").strip().lower()
            by_category[category].append(ret)

    for cat, vals in sorted(by_category.items()):
        if not vals:
            continue
        mean_ret = sum(vals) / len(vals)
        ci_low, ci_high = _bootstrap_ci(vals, n_sims=500, conf_level=0.80)
        hits = sum(1 for v in vals if v > 0)
        rec = _recommended_prior(mean_ret, ci_low)
        out[cat] = {
            "n_samples": len(vals),
            "mean_return": round(mean_ret, 6),
            "hit_rate": round(hits / len(vals), 6),
            "ci_80_low": round(ci_low, 6),
            "ci_80_high": round(ci_high, 6),
            "recommended_prior": round(max(0.0, rec), 6),
        }

    env_map = {
        "SIGNAL_EXECUTION_V2_PRIOR_CRYPTO": out.get("crypto", {}).get("recommended_prior", 0.02),
        "SIGNAL_EXECUTION_V2_PRIOR_FINANCE": out.get("finance", {}).get("recommended_prior", 0.02),
        "SIGNAL_EXECUTION_V2_PRIOR_SPORTS": out.get("sports", {}).get("recommended_prior", 0.015),
        "SIGNAL_EXECUTION_V2_PRIOR_POLITICS": out.get("politics", {}).get("recommended_prior", 0.02),
        "SIGNAL_EXECUTION_V2_PRIOR_DEFAULT": out.get("other", {}).get("recommended_prior", 0.015),
    }

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "lookback_days": lookback_days,
        "categories": out,
        "recommended_env": env_map,
    }

    artifact = Path("artifacts") / f"v2_prior_calibration_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
