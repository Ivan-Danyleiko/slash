#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from statistics import median

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.models import Stage17TailPosition
from app.services.research.stage17_tail_report import payout_skew_bootstrap_ci


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage17 tail historical backtest summary from DB ledger.")
    parser.add_argument("--days", type=int, default=90, help="Lookback window in days (default: 90)")
    args = parser.parse_args()

    cutoff = datetime.now(UTC) - timedelta(days=max(1, int(args.days)))
    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(Stage17TailPosition)
                .where(Stage17TailPosition.opened_at >= cutoff)
                .where(Stage17TailPosition.status == "CLOSED")
                .order_by(Stage17TailPosition.closed_at.desc())
            )
        )

    pnls = [float(r.realized_pnl_usd or 0.0) for r in rows]
    wins = [x for x in pnls if x > 0.0]
    losses = [x for x in pnls if x <= 0.0]
    hit_rate = (len(wins) / len(rows)) if rows else 0.0
    total_pnl = float(sum(pnls))
    ci_low, ci_high = payout_skew_bootstrap_ci(pnls, n_bootstrap=1000, seed=42)

    ttr_days = []
    for r in rows:
        if r.opened_at is None or r.closed_at is None:
            continue
        o = r.opened_at if r.opened_at.tzinfo else r.opened_at.replace(tzinfo=UTC)
        c = r.closed_at if r.closed_at.tzinfo else r.closed_at.replace(tzinfo=UTC)
        ttr_days.append(max(0.0, (c - o).total_seconds() / 86400.0))

    by_variation: dict[str, dict[str, float]] = {}
    for r in rows:
        key = str(r.tail_variation or "unknown")
        b = by_variation.setdefault(key, {"closed": 0.0, "wins": 0.0, "pnl": 0.0})
        b["closed"] += 1.0
        p = float(r.realized_pnl_usd or 0.0)
        if p > 0:
            b["wins"] += 1.0
        b["pnl"] += p

    print("=== Stage17 Tail Backtest ===")
    print(f"window_days={int(args.days)}")
    print(f"closed_positions={len(rows)}")
    print(f"wins={len(wins)} losses={len(losses)}")
    print(f"hit_rate_tail={hit_rate:.4f}")
    print(f"total_realized_pnl_usd={total_pnl:.4f}")
    print(f"payout_skew_ci_low_80={ci_low:.4f}")
    print(f"payout_skew_ci_high_80={ci_high:.4f}")
    print(f"time_to_resolution_median_days={median(ttr_days):.3f}" if ttr_days else "time_to_resolution_median_days=n/a")
    if by_variation:
        print("--- by_variation ---")
        for k, v in sorted(by_variation.items()):
            closed = float(v.get("closed") or 0.0)
            wins_v = float(v.get("wins") or 0.0)
            wr = (wins_v / closed) if closed > 0 else 0.0
            print(f"{k}: closed={int(closed)} win_rate={wr:.4f} pnl={float(v.get('pnl') or 0.0):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

