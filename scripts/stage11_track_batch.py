from __future__ import annotations

from datetime import UTC, datetime
import csv
import json
from io import StringIO
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.base import Base
from app.services.stage11.reports import build_stage11_track_report


def _build_client_report_csv(rows: list[dict]) -> str:
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(["client_id", "client_code", "runtime_mode", "orders", "fills", "realized_pnl_usd"])
    for row in rows:
        writer.writerow(
            [
                row.get("client_id"),
                row.get("client_code"),
                row.get("runtime_mode"),
                row.get("orders"),
                row.get("fills"),
                row.get("realized_pnl_usd"),
            ]
        )
    return out.getvalue()


def main() -> int:
    settings = get_settings()
    now = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out_dir = Path("artifacts/research")
    out_dir.mkdir(parents=True, exist_ok=True)

    out_json = out_dir / f"stage11_execution_{now}.json"
    out_csv = out_dir / f"stage11_client_report_{now}.csv"

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with session_factory() as db:
        report = build_stage11_track_report(db, settings=settings)
        report["database_url"] = "***redacted***"
        report["artifacts"] = {"json": str(out_json), "csv": str(out_csv)}
        out_json.write_text(json.dumps(report, ensure_ascii=True, indent=2, default=str), encoding="utf-8")

        client_rows = (((report.get("sections") or {}).get("client_report") or {}).get("rows") or [])
        out_csv.write_text(_build_client_report_csv(list(client_rows)), encoding="utf-8")

    print(f"stage11_execution_json={out_json}")
    print(f"stage11_client_report_csv={out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
