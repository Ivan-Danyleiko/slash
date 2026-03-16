#!/usr/bin/env python3
from __future__ import annotations

import json

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.enums import SignalType
from app.models.models import SignalHistory


def main() -> None:
    buckets = {
        "15m": [],
        "30m": [],
        "1h": [],
        "6h": [],
        "24h": [],
    }
    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(SignalHistory).where(
                    SignalHistory.signal_type == SignalType.DIVERGENCE,
                    SignalHistory.probability_at_signal.is_not(None),
                )
            )
        )
    for r in rows:
        p0 = float(r.probability_at_signal or 0.0)
        trade = dict(r.simulated_trade or {})
        p15 = trade.get("probability_after_15m")
        p30 = trade.get("probability_after_30m")
        p1 = r.probability_after_1h
        p6 = r.probability_after_6h
        p24 = r.probability_after_24h
        for key, val in (("15m", p15), ("30m", p30), ("1h", p1), ("6h", p6), ("24h", p24)):
            if val is None:
                continue
            buckets[key].append(float(val) - p0)

    out = {}
    for k, vals in buckets.items():
        if not vals:
            out[k] = {"n": 0, "avg_move": 0.0}
            continue
        out[k] = {"n": len(vals), "avg_move": round(sum(vals) / len(vals), 6)}
    print(json.dumps({"divergence_decay": out}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
