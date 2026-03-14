from abc import ABC, abstractmethod

from app.schemas.collector import NormalizedMarketDTO


class BaseCollector(ABC):
    platform_name: str

    @abstractmethod
    def fetch_markets(self) -> list[NormalizedMarketDTO]:
        """Fetch and normalize external markets into shared schema."""
