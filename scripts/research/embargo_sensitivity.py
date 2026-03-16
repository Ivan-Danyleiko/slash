#!/usr/bin/env python3
from __future__ import annotations

import json

from app.db.session import SessionLocal
from app.services.research.walkforward import build_walkforward_report


def _negative_share(report: dict) -> float | None:
    windows = []
    for row in report.get("rows") or []:
        windows.extend(row.get("windows") or [])
    vals = []
    for w in windows:
        test = w.get("test") or {}
        if int(test.get("n") or 0) <= 0:
            continue
        vals.append(float(test.get("avg_return") or 0.0) < 0.0)
    if not vals:
        return None
    return sum(1 for x in vals if x) / len(vals)


def main() -> None:
    embargos = [0, 6, 12, 24, 48]
    out = []
    with SessionLocal() as db:
        for emb in embargos:
            report = build_walkforward_report(
                db,
                days=365,
                horizon="6h",
                train_days=30,
                test_days=14,
                step_days=14,
                embargo_hours=emb,
                min_samples_per_window=10,
            )
            out.append(
                {
                    "embargo_hours": emb,
                    "negative_window_share": _negative_share(report),
                    "types": len(report.get("rows") or []),
                }
            )
    print(json.dumps({"embargo_sensitivity": out}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
