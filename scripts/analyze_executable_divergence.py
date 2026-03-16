#!/usr/bin/env python3
from __future__ import annotations

from collections import Counter

from sqlalchemy import select

from app.core.config import Settings
from app.db.session import SessionLocal
from app.models.models import DuplicateMarketPair, Market
from app.services.analyzers.divergence import DivergenceDetector


def main() -> None:
    settings = Settings()
    detector = DivergenceDetector(settings=settings)
    with SessionLocal() as db:
        pairs = list(db.scalars(select(DuplicateMarketPair)))
        stats = Counter()
        for pair in pairs:
            a = db.get(Market, pair.market_a_id)
            b = db.get(Market, pair.market_b_id)
            if not a or not b:
                stats["missing_market"] += 1
                continue
            res = detector.compute_executable_divergence(a, b)
            if res is None:
                stats["missing_probability"] += 1
                continue
            stats["total"] += 1
            if res.gross_divergence >= settings.signal_divergence_threshold:
                stats["profitable_gross"] += 1
            if res.net_edge_after_costs >= settings.signal_divergence_net_edge_min:
                stats["profitable_net"] += 1
            if res.gross_divergence >= settings.signal_divergence_threshold and res.net_edge_after_costs < settings.signal_divergence_net_edge_min:
                stats["false_positive_gross"] += 1

    total = max(1, stats["total"])
    print(
        {
            "pairs_total": len(pairs),
            "pairs_evaluable": stats["total"],
            "profitable_gross": stats["profitable_gross"],
            "profitable_net": stats["profitable_net"],
            "false_positive_gross": stats["false_positive_gross"],
            "false_positive_rate": round(stats["false_positive_gross"] / total, 6),
        }
    )


if __name__ == "__main__":
    main()
