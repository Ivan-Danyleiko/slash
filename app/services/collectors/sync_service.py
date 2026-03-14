from sqlalchemy.orm import Session
import structlog

from app.models.models import JobRun
from app.repositories.market_repository import MarketRepository
from app.services.collectors.manifold import ManifoldCollector
from app.services.collectors.metaculus import MetaculusCollector
from app.services.collectors.polymarket import PolymarketCollector

logger = structlog.get_logger(__name__)


class CollectorSyncService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = MarketRepository(db)
        self.collectors = [ManifoldCollector(), MetaculusCollector(), PolymarketCollector()]
        self.collector_map = {collector.platform_name.lower(): collector for collector in self.collectors}

    def _sync_collector(self, collector) -> dict[str, int | str]:
        summary: dict[str, int | str] = {
            "fetched": 0,
            "inserted": 0,
            "updated": 0,
            "errors": 0,
        }
        try:
            logger.info("collector_fetch_started", platform=collector.platform_name)
            markets = collector.fetch_markets()
            summary["fetched"] = len(markets)
            for market in markets:
                _, is_inserted = self.repo.upsert_market(market)
                if is_inserted:
                    summary["inserted"] += 1
                else:
                    summary["updated"] += 1
            self.db.commit()
            logger.info("collector_fetch_finished", platform=collector.platform_name, summary=summary)
            return summary
        except Exception as exc:  # noqa: BLE001
            self.db.rollback()
            summary["errors"] = 1
            summary["error"] = str(exc)
            logger.warning("collector_fetch_failed", platform=collector.platform_name, error=str(exc))
            return summary

    def sync_all(self, platform: str | None = None) -> dict:
        result: dict[str, dict[str, int | str] | str] = {}
        selected_collectors = self.collectors
        if platform:
            platform_key = platform.lower()
            collector = self.collector_map.get(platform_key)
            if not collector:
                return {"error": f"Unsupported platform '{platform}'. Use: manifold, metaculus, polymarket"}
            selected_collectors = [collector]

        job = JobRun(job_name="sync_all_platforms", status="RUNNING", details={})
        self.db.add(job)
        self.db.commit()
        logger.info("collector_sync_started", collectors=[c.platform_name for c in selected_collectors])

        try:
            for collector in selected_collectors:
                result[collector.platform_name] = self._sync_collector(collector)
            job.status = "SUCCESS"
            job.details = result
            self.db.commit()
            logger.info("collector_sync_finished", result=result)
            return result
        except Exception as exc:  # noqa: BLE001
            job.status = "FAILED"
            job.details = {"error": str(exc)}
            self.db.commit()
            logger.error("collector_sync_failed", error=str(exc))
            raise
