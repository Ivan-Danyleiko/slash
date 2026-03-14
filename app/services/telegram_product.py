from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.enums import AccessLevel, SignalType
from app.models.models import Market, Signal, User, UserEvent, WatchlistItem
from app.services.signals.ranking import rank_score, select_top_signals
from app.services.research.ab_testing import get_ab_variant_for_user


PLAN_LIMITS = {
    AccessLevel.FREE: {"signals": 3, "watchlist": 3},
    AccessLevel.PRO: {"signals": 20, "watchlist": 20},
    AccessLevel.PREMIUM: {"signals": 10_000, "watchlist": 10_000},
}


class TelegramProductService:
    def __init__(self, db: Session):
        self.db = db

    def get_or_create_user(self, telegram_user_id: str, username: str | None = None) -> User:
        user = self.db.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
        if user:
            return user
        user = User(telegram_user_id=telegram_user_id, username=username)
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def add_watchlist(self, user: User, market_id: int) -> tuple[bool, str]:
        limits = PLAN_LIMITS[user.access_level]
        existing_count = len(list(self.db.scalars(select(WatchlistItem).where(WatchlistItem.user_id == user.id))))
        if existing_count >= limits["watchlist"]:
            return False, f"Watchlist limit reached for {user.access_level.value}"
        existing = self.db.scalar(
            select(WatchlistItem).where(WatchlistItem.user_id == user.id, WatchlistItem.market_id == market_id)
        )
        if existing:
            return True, "Already in watchlist"
        if not self.db.get(Market, market_id):
            return False, "Market not found"
        self.db.add(WatchlistItem(user_id=user.id, market_id=market_id))
        variant = get_ab_variant_for_user(user_id=user.id)
        payload = {"variant": variant} if variant else None
        self.db.add(UserEvent(user_id=user.id, event_type="watchlist_added", market_id=market_id, payload_json=payload))
        self.db.commit()
        return True, "Added"

    def remove_watchlist(self, user: User, market_id: int) -> bool:
        item = self.db.scalar(
            select(WatchlistItem).where(WatchlistItem.user_id == user.id, WatchlistItem.market_id == market_id)
        )
        if not item:
            return False
        self.db.delete(item)
        self.db.commit()
        return True

    def list_watchlist(self, user: User) -> list[dict]:
        rows = list(self.db.scalars(select(WatchlistItem).where(WatchlistItem.user_id == user.id).order_by(WatchlistItem.id.desc())))
        out: list[dict] = []
        for row in rows:
            market = self.db.get(Market, row.market_id)
            if not market:
                continue
            last_signal = self.db.scalar(select(Signal).where(Signal.market_id == market.id).order_by(Signal.created_at.desc()))
            out.append(
                {
                    "market_id": market.id,
                    "title": market.title,
                    "probability_yes": market.probability_yes,
                    "last_signal_type": last_signal.signal_type.value if last_signal else None,
                    "url": market.url,
                }
            )
        return out

    def top_ranked_signals(self, user: User, limit: int = 5) -> list[Signal]:
        rows = list(self.db.scalars(select(Signal).order_by(Signal.created_at.desc()).limit(200)))
        settings = get_settings()
        max_allowed = min(limit, PLAN_LIMITS[user.access_level]["signals"])
        variant = get_ab_variant_for_user(user_id=user.id, settings=settings)
        if variant == settings.research_ab_control_label:
            top = sorted(rows, key=rank_score, reverse=True)[:max_allowed]
        else:
            top = select_top_signals(rows, limit=max_allowed, settings=settings)
        if variant:
            self.db.add(
                UserEvent(
                    user_id=user.id,
                    event_type="ab_variant_exposure",
                    payload_json={
                        "experiment": settings.research_ab_experiment_name,
                        "variant": variant,
                        "signals_returned": len(top),
                    },
                )
            )
            self.db.commit()
        return top

    def latest_signals(self, user: User, signal_type: str | None, page: int, page_size: int = 5) -> list[Signal]:
        stmt = select(Signal).order_by(Signal.created_at.desc())
        if signal_type:
            stmt = stmt.where(Signal.signal_type == signal_type)
        offset = max(0, page - 1) * page_size
        rows = list(self.db.scalars(stmt.offset(offset).limit(page_size)))
        return rows

    def daily_digest(self, user: User) -> str:
        settings = get_settings()
        if user.last_digest_sent and user.last_digest_sent.date() == date.today():
            return "📬 Daily digest already sent today."
        top_div = list(self.db.scalars(select(Signal).where(Signal.divergence_score.is_not(None)).order_by(Signal.divergence_score.desc()).limit(3)))
        weird = list(
            self.db.scalars(
                select(Signal)
                .where(Signal.signal_type == SignalType.WEIRD_MARKET)
                .order_by(Signal.created_at.desc())
                .limit(3)
            )
        )
        watch = self.list_watchlist(user)[:3]

        lines = [
            "📬 *Prediction Markets Daily Digest*",
            f"_Disclaimer: {settings.research_ethics_disclaimer_text}_",
            "",
            "*Top Divergences*",
        ]
        if top_div:
            for s in top_div:
                ex = s.execution_analysis or {}
                util = float(ex.get("utility_score") or 0.0)
                assump = str(ex.get("assumptions_version") or "n/a")
                lines.append(
                    f"• {s.title[:64]} → {(s.divergence_score or 0)*100:.1f}% | util={util:.3f} | {assump}"
                )
        else:
            lines.append("• No significant divergences today")
        lines.append("")
        lines.append("*Top Weird Markets*")
        if weird:
            for s in weird:
                lines.append(f"• {s.title[:64]}")
        else:
            lines.append("• No weird markets detected")
        lines.append("")
        lines.append("*Watchlist Alerts*")
        if watch:
            for w in watch:
                lines.append(f"• {w['title'][:64]}")
        else:
            lines.append("• No watchlist alerts")

        user.last_digest_sent = datetime.now(UTC)
        variant = get_ab_variant_for_user(user_id=user.id)
        payload = {"variant": variant} if variant else None
        self.db.add(UserEvent(user_id=user.id, event_type="digest_sent", payload_json=payload))
        self.db.commit()
        return "\n".join(lines)

    def can_send_signal(self, user: User, requested: int = 1) -> bool:
        limit = PLAN_LIMITS[user.access_level]["signals"]
        return (user.signals_sent_today + requested) <= limit

    def record_signal_sent(self, user: User, signal: Signal) -> None:
        user.signals_sent_today += 1
        variant = get_ab_variant_for_user(user_id=user.id)
        payload = {"variant": variant} if variant else None
        self.db.add(
            UserEvent(
                user_id=user.id,
                event_type="signal_sent",
                market_id=signal.market_id,
                payload_json=payload,
            )
        )
        self.db.commit()

    def record_market_opened(self, user: User, market_id: int) -> None:
        variant = get_ab_variant_for_user(user_id=user.id)
        payload = {"variant": variant} if variant else None
        self.db.add(UserEvent(user_id=user.id, event_type="market_opened", market_id=market_id, payload_json=payload))
        self.db.commit()
