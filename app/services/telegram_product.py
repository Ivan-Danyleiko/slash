from datetime import UTC, date, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy import func as sqlfunc, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.enums import AccessLevel, SignalType
from app.models.models import DryrunPortfolio, DryrunPosition, Market, Signal, User, UserEvent, WatchlistItem
from app.services.signals.ranking import rank_score, select_top_signals
from app.services.research.ab_testing import get_ab_variant_for_user
from app.services.telegram_i18n import esc as _esc_i18n


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
        # Serialize watchlist mutations per user to avoid concurrent limit bypass.
        self.db.scalar(select(User).where(User.id == user.id).with_for_update())
        existing_count = int(
            self.db.scalar(
                select(sqlfunc.count())
                .select_from(WatchlistItem)
                .where(WatchlistItem.user_id == user.id)
            )
            or 0
        )
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
        try:
            self.db.commit()
            return True, "Added"
        except IntegrityError:
            # Unique constraint (user_id, market_id) race-safe fallback.
            self.db.rollback()
            return True, "Already in watchlist"

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
        # Single query: join WatchlistItem + Market
        item_market_rows = list(
            self.db.execute(
                select(WatchlistItem, Market)
                .join(Market, Market.id == WatchlistItem.market_id)
                .where(WatchlistItem.user_id == user.id)
                .order_by(WatchlistItem.id.desc())
            )
        )
        if not item_market_rows:
            return []
        market_ids = [market.id for _, market in item_market_rows]
        # Fetch latest signal per market in one query using max(id) subquery
        latest_sig_subq = (
            select(Signal.market_id, sqlfunc.max(Signal.id).label("max_id"))
            .where(Signal.market_id.in_(market_ids))
            .group_by(Signal.market_id)
            .subquery()
        )
        latest_signals: dict[int, str] = {
            row.market_id: row.signal_type.value
            for row in self.db.scalars(
                select(Signal).join(latest_sig_subq, Signal.id == latest_sig_subq.c.max_id)
            )
        }
        return [
            {
                "market_id": market.id,
                "title": market.title,
                "probability_yes": market.probability_yes,
                "last_signal_type": latest_signals.get(market.id),
                "url": market.url,
            }
            for _, market in item_market_rows
        ]

    def load_signal_pool(self, limit: int = 200) -> list[Signal]:
        """Pre-load actionable signals once; pass the result to top_ranked_signals as `pool`."""
        pool, _ = self.load_signal_pool_with_markets(limit=limit)
        return pool

    def load_signal_pool_with_markets(self, limit: int = 200) -> tuple[list[Signal], dict[int, Market]]:
        """Pre-load actionable signals once with market map for callers that need both."""
        now = datetime.now(UTC)
        rows = list(
            self.db.execute(
                select(Signal, Market)
                .join(Market, Market.id == Signal.market_id)
                .order_by(Signal.created_at.desc())
                .limit(limit)
            )
        )
        pool: list[Signal] = []
        markets_by_id: dict[int, Market] = {}
        for signal, market in rows:
            if not self._market_is_actionable(market, now=now):
                continue
            pool.append(signal)
            markets_by_id[int(market.id)] = market
        return pool, markets_by_id

    def top_ranked_signals(
        self,
        user: User,
        limit: int = 5,
        pool: list[Signal] | None = None,
        *,
        log_variant_event: bool = True,
    ) -> list[Signal]:
        # If a pre-loaded pool is provided, skip the DB query (avoids N+1 in push jobs)
        if pool is None:
            pool = self.load_signal_pool()
        filtered: list[Signal] = pool
        settings = get_settings()
        max_allowed = min(limit, PLAN_LIMITS[user.access_level]["signals"])
        variant = get_ab_variant_for_user(user_id=user.id, settings=settings)
        if variant == settings.research_ab_control_label:
            top = sorted(filtered, key=rank_score, reverse=True)[:max_allowed]
        else:
            top = select_top_signals(filtered, limit=max_allowed, settings=settings)
        if variant and log_variant_event:
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
        """Returns MarkdownV2-formatted daily digest."""
        settings = get_settings()
        if user.last_digest_sent and user.last_digest_sent.date() == date.today():
            return "📬 Щоденний огляд вже надіслано сьогодні\\."
        top_div = list(self.db.scalars(
            select(Signal).where(Signal.divergence_score.is_not(None))
            .order_by(Signal.divergence_score.desc()).limit(3)
        ))
        weird = list(self.db.scalars(
            select(Signal).where(Signal.signal_type == SignalType.WEIRD_MARKET)
            .order_by(Signal.created_at.desc()).limit(3)
        ))
        watch = self.list_watchlist(user)[:3]
        disclaimer = self._esc(settings.research_ethics_disclaimer_text)

        lines = [
            "📬 *Щоденний огляд ринків прогнозів*",
            f"_{disclaimer}_",
            "",
            "*Топ розходжень*",
        ]
        if top_div:
            for s in top_div:
                ex = s.execution_analysis or {}
                util = float(ex.get("utility_score") or 0.0)
                title = self._esc(s.title[:64])
                div_pct = self._esc(f"{(s.divergence_score or 0)*100:.1f}%")
                lines.append(f"• {title} → {div_pct} \\| util={self._esc(f'{util:.3f}')}")
        else:
            lines.append("• Значних розходжень немає")
        lines.append("")
        lines.append("*Нетипові ринки*")
        if weird:
            for s in weird:
                lines.append(f"• {self._esc(s.title[:64])}")
        else:
            lines.append("• Нетипових ринків не виявлено")
        lines.append("")
        lines.append("*Вотчліст*")
        if watch:
            for w in watch:
                lines.append(f"• {self._esc(w['title'][:64])}")
        else:
            lines.append("• Немає сповіщень по вотчлісту")

        user.last_digest_sent = datetime.now(UTC)
        variant = get_ab_variant_for_user(user_id=user.id)
        payload = {"variant": variant} if variant else None
        self.db.add(UserEvent(user_id=user.id, event_type="digest_sent", payload_json=payload))
        self.db.commit()
        return "\n".join(lines)

    def can_send_signal(self, user: User, requested: int = 1) -> bool:
        limit = PLAN_LIMITS[user.access_level]["signals"]
        return (user.signals_sent_today + requested) <= limit

    def record_signal_sent(self, user: User, signal: Signal, *, commit: bool = True) -> None:
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
        if commit:
            self.db.commit()

    # ------------------------------------------------------------------
    # Dry-run portfolio formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _esc(text: str) -> str:
        return _esc_i18n(text)

    def _get_dryrun_portfolio(self) -> DryrunPortfolio | None:
        return self.db.scalar(select(DryrunPortfolio).where(DryrunPortfolio.name == "default").limit(1))

    def get_dryrun_portfolio_text(self) -> str:
        portfolio = self._get_dryrun_portfolio()
        if portfolio is None:
            return "💼 *Dry\\-Run Portfolio*\n\nNo portfolio yet\\. Use `/dryrun run` to start\\."

        open_positions = list(
            self.db.scalars(
                select(DryrunPosition).where(
                    DryrunPosition.portfolio_id == portfolio.id,
                    DryrunPosition.status == "OPEN",
                )
            )
        )
        closed_positions = list(
            self.db.scalars(
                select(DryrunPosition).where(
                    DryrunPosition.portfolio_id == portfolio.id,
                    DryrunPosition.status == "CLOSED",
                )
            )
        )
        wins = [p for p in closed_positions if p.realized_pnl_usd > 0]
        n_closed = len(closed_positions)
        win_rate = len(wins) / n_closed if n_closed > 0 else 0.0

        open_notional = sum(p.notional_usd + p.unrealized_pnl_usd for p in open_positions)
        total_value = portfolio.current_cash_usd + open_notional
        roi = (total_value - portfolio.initial_balance_usd) / portfolio.initial_balance_usd * 100

        roi_sign = "+" if roi >= 0 else ""
        real_sign = "+" if portfolio.total_realized_pnl_usd >= 0 else ""
        unreal_sign = "+" if portfolio.total_unrealized_pnl_usd >= 0 else ""

        # Kelly expectation
        avg_win = sum(p.realized_pnl_usd for p in wins) / len(wins) if wins else 0.0
        losses_list = [p for p in closed_positions if p.realized_pnl_usd <= 0]
        avg_loss = abs(sum(p.realized_pnl_usd for p in losses_list) / len(losses_list)) if losses_list else 0.0
        kelly_e = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win if avg_win > 0 else 0.0
        profit_prob = win_rate * 100.0

        lines = [
            "💼 *Dry\\-Run Portfolio*",
            "━━━━━━━━━━━━━━━━",
            f"💵 Cash:       `${portfolio.current_cash_usd:.2f}`",
            f"📦 In trades:  `${sum(p.notional_usd for p in open_positions):.2f}`  \\({len(open_positions)} open\\)",
            f"🏦 Total:      `${total_value:.2f}`",
            "",
            f"📈 Realized:   `{real_sign}${portfolio.total_realized_pnl_usd:.2f}`",
            f"📉 Unrealized: `{unreal_sign}${portfolio.total_unrealized_pnl_usd:.2f}`",
            f"📊 ROI:        `{roi_sign}{roi:.2f}%`",
            "",
        ]
        if n_closed > 0:
            lines += [
                f"🎯 Win rate:   `{win_rate*100:.0f}%`  \\({len(wins)}/{n_closed} closed\\)",
                f"⚡ Kelly E:    `{kelly_e:.4f}`",
                f"🎲 Profit prob: `{profit_prob:.1f}%`",
            ]
        else:
            lines.append("_No closed positions yet_")

        return "\n".join(lines)

    def get_dryrun_positions_text(self) -> str:
        portfolio = self._get_dryrun_portfolio()
        if portfolio is None:
            return "📂 No portfolio found\\."

        open_positions = list(
            self.db.execute(
                select(DryrunPosition, Market)
                .join(Market, Market.id == DryrunPosition.market_id)
                .where(
                    DryrunPosition.portfolio_id == portfolio.id,
                    DryrunPosition.status == "OPEN",
                )
                .order_by(DryrunPosition.resolution_deadline.asc().nullslast())
            )
        )

        if not open_positions:
            return "📂 *Open Positions*\n\n_No open positions\\. Run `/dryrun run` to open some\\._"

        total_invested = sum(p.notional_usd for p, _ in open_positions)
        total_max_win = sum(p.shares_count * (1.0 - p.entry_price) for p, _ in open_positions)
        total_pnl = sum(p.unrealized_pnl_usd for p, _ in open_positions)
        pnl_sign = "\\+" if total_pnl >= 0 else ""

        header = (
            f"📂 *Відкриті позиції* — {len(open_positions)}\n"
            f"Вклад: `${self._esc(f'{total_invested:.2f}')}` \\| "
            f"Макс: `\\+${self._esc(f'{total_max_win:.2f}')}` \\| "
            f"P&L: `{pnl_sign}{self._esc(f'{total_pnl:.2f}')}`"
        )

        # Mobile-friendly cards — one per position
        cards: list[str] = []
        now = datetime.now(UTC)
        for i, (pos, market) in enumerate(open_positions, 1):
            mark = pos.mark_price or pos.entry_price
            koef = 1.0 / pos.entry_price if pos.entry_price > 0 else 0.0
            max_win = pos.shares_count * (1.0 - pos.entry_price)
            ev = pos.entry_ev_pct or 0.0
            pnl_pct = (mark - pos.entry_price) / pos.entry_price * 100 if pos.entry_price > 0 else 0.0
            pnl_sign_c = "\\+" if pnl_pct >= 0 else ""
            days_left = (
                (pos.resolution_deadline - now).total_seconds() / 86400.0
                if pos.resolution_deadline else 999.0
            )
            daily_ev = ev / max(1.0, days_left)
            dl = self._esc(pos.resolution_deadline.strftime("%d.%m.%y")) if pos.resolution_deadline else "—"
            title_raw = (market.title[:60] if market else "Unknown").replace("Arbitrage candidate: ", "")
            title = self._esc(title_raw)
            direction = self._esc(str(pos.direction or "—"))
            koef_s = self._esc(f"{koef:.1f}")
            bet_s = self._esc(f"{pos.notional_usd:.2f}")
            max_win_s = self._esc(f"{max_win:.2f}")
            dev_s = self._esc(f"{daily_ev*100:.2f}")
            pnl_s = self._esc(f"{pnl_pct:.1f}")
            cards.append(
                f"*{i}\\. {title}*\n"
                f"{direction} · `x{koef_s}` · до {dl}\n"
                f"Ставка: `\\${bet_s}` · Макс: `\\+${max_win_s}`\n"
                f"EV/д: `{dev_s}%` · P&L: `{pnl_sign_c}{pnl_s}%`"
            )

        return header + "\n\n" + "\n\n".join(cards)

    def get_simulate_text(self) -> str:
        """Full candidate scan report without opening positions."""
        from app.services.dryrun.simulator import _scan_signal_candidates
        result = _scan_signal_candidates(self.db)

        accepted = result["accepted"]
        borderline = result["borderline"]
        llm_approved = result["llm_approved"]
        hard_rejected = result["hard_rejected"]
        soft_rejected = result["soft_rejected"]
        duplicates = result["duplicates"]

        llm_from_borderline = [b for b in borderline if b["signal_id"] in llm_approved]
        llm_rejected = [b for b in borderline if b["signal_id"] not in llm_approved]

        lines = [
            "🔬 *Simulate — candidate scan*",
            f"✅ Accepted: `{len(accepted)}` \\| 🤖 LLM rescued: `{len(llm_from_borderline)}` \\| ⏭ Dupes: `{duplicates}`",
            f"🚫 Hard\\-rejected: `{len(hard_rejected)}` \\| 🟡 Soft\\-rejected: `{len(soft_rejected)}` \\| 🤖 LLM\\-rejected: `{len(llm_rejected)}`",
            "",
        ]

        if accepted:
            lines.append("*✅ Would open \\(top 5 by EV/day\\):*")
            rows = ["#  Dir  Koef  EV/d    Days  Vol     Title"]
            rows.append("─" * 52)
            for i, c in enumerate(accepted[:5], 1):
                dl = f"{c['days_to_res']:.0f}d" if c["days_to_res"] < 999 else "  —"
                vol = f"${c['volume_usd']/1000:.0f}k" if c["volume_usd"] >= 1000 else f"${c['volume_usd']:.0f}"
                dev = f"{c['daily_ev']*100:.3f}%"
                title = c["title"][:22]
                rows.append(f"{i:<2} {c['direction']:<3}  {c['koef']:.1f}x  {dev:<7} {dl:<5} {vol:<7} {title}")
            lines.append("```\n" + "\n".join(rows) + "\n```")

        if llm_from_borderline:
            lines.append("*🤖 LLM врятував \\(borderline → approved\\):*")
            for c in llm_from_borderline:
                lines.append(f"• _{self._esc(c['title'][:50])}_ — `{c['filter_reason']}`")
            lines.append("")

        if llm_rejected:
            lines.append("*🤖 LLM відхилив \\(borderline → rejected\\):*")
            for c in llm_rejected[:3]:
                lines.append(f"• _{self._esc(c['title'][:50])}_ — `{c['filter_reason']}`")
            lines.append("")

        if hard_rejected:
            lines.append("*🚫 Hard\\-rejected \\(топ 3\\):*")
            for c in hard_rejected[:3]:
                lines.append(f"• _{self._esc(c['title'][:50])}_ — `{self._esc(c['reason'])}`")
            lines.append("")

        if not accepted and not llm_from_borderline:
            lines.append("_Немає кандидатів для відкриття позицій\\._")

        return "\n".join(lines)

    def get_dryrun_pnl_text(self) -> str:
        portfolio = self._get_dryrun_portfolio()
        if portfolio is None:
            return "📊 *P&L Report*\n\n_No data yet\\._"

        closed = list(
            self.db.scalars(
                select(DryrunPosition).where(
                    DryrunPosition.portfolio_id == portfolio.id,
                    DryrunPosition.status == "CLOSED",
                )
            )
        )
        open_pos = list(
            self.db.scalars(
                select(DryrunPosition).where(
                    DryrunPosition.portfolio_id == portfolio.id,
                    DryrunPosition.status == "OPEN",
                )
            )
        )

        n_closed = len(closed)
        wins = [p for p in closed if p.realized_pnl_usd > 0]
        losses = [p for p in closed if p.realized_pnl_usd <= 0]
        win_rate = len(wins) / n_closed if n_closed > 0 else 0.0
        avg_win = sum(p.realized_pnl_usd for p in wins) / len(wins) if wins else 0.0
        avg_loss = abs(sum(p.realized_pnl_usd for p in losses) / len(losses)) if losses else 0.0
        kelly_e = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win if avg_win > 0 else 0.0
        profit_prob = win_rate * 100.0

        open_notional = sum(p.notional_usd + p.unrealized_pnl_usd for p in open_pos)
        total_value = portfolio.current_cash_usd + open_notional
        roi = (total_value - portfolio.initial_balance_usd) / portfolio.initial_balance_usd * 100
        roi_sign = "+" if roi >= 0 else ""

        lines = [
            "📊 *P&L Report*",
            "━━━━━━━━━━━━━",
        ]
        if n_closed > 0:
            lines += [
                f"Closed: `{n_closed}`  ·  Won: `{len(wins)}`  ·  Lost: `{len(losses)}`",
                f"Win rate: `{win_rate*100:.1f}%`",
                f"Avg win:  `+${avg_win:.2f}`",
                f"Avg loss: `\\-${avg_loss:.2f}`",
                "",
                f"Kelly E\\(V\\):   `{kelly_e:.4f}`",
                f"Profit prob:  `{profit_prob:.1f}%`",
                "",
            ]
        else:
            lines += ["_No closed positions yet_", ""]

        lines += [
            f"ROI:          `{roi_sign}{roi:.2f}%`",
            f"Initial:      `${portfolio.initial_balance_usd:.2f}`",
            f"Current val:  `${total_value:.2f}`",
            f"Open pos:     `{len(open_pos)}`",
            "",
            "_Shadow mode · Not financial advice_",
        ]
        return "\n".join(lines)

    def record_market_opened(self, user: User, market_id: int) -> None:
        variant = get_ab_variant_for_user(user_id=user.id)
        payload = {"variant": variant} if variant else None
        self.db.add(UserEvent(user_id=user.id, event_type="market_opened", market_id=market_id, payload_json=payload))
        self.db.commit()
    @staticmethod
    def _as_utc(ts: datetime | None) -> datetime | None:
        if ts is None:
            return None
        if ts.tzinfo is None:
            return ts.replace(tzinfo=UTC)
        return ts.astimezone(UTC)

    def _market_is_actionable(self, market: Market | None, *, now: datetime) -> bool:
        if market is None:
            return False
        resolution_time = self._as_utc(market.resolution_time)
        if resolution_time and resolution_time <= now:
            return False
        status = (market.status or "").strip().lower()
        if any(token in status for token in ("resolved", "closed", "settled", "final", "ended", "cancelled", "canceled")):
            return False
        return True
