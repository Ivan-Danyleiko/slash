import asyncio
import logging
from contextlib import contextmanager
from typing import Generator

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.enums import AccessLevel, SignalType, SubscriptionStatus
from app.models.models import Market, Signal, User
from app.services.telegram_product import TelegramProductService

logger = logging.getLogger(__name__)
settings = get_settings()
dp = Dispatcher()

# ---------------------------------------------------------------------------
# MarkdownV2 escape helper
# ---------------------------------------------------------------------------
_MV2_SPECIAL = r"\_*[]()~`>#+-=|{}.!"


def _mv2(text: str) -> str:
    """Escape text for Telegram MarkdownV2."""
    for ch in _MV2_SPECIAL:
        text = text.replace(ch, f"\\{ch}")
    return text


# ---------------------------------------------------------------------------
# DB session context manager
# ---------------------------------------------------------------------------

@contextmanager
def _db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_admin(message: Message) -> bool:
    admin_id = str(settings.telegram_chat_id or "").strip()
    if not admin_id:
        return False
    return str(message.chat.id) == admin_id or str(message.from_user.id) == admin_id


def _upsert_user(message: Message) -> User:
    with _db() as db:
        user = db.scalar(select(User).where(User.telegram_user_id == str(message.from_user.id)))
        if not user:
            user = User(
                telegram_user_id=str(message.from_user.id),
                username=message.from_user.username,
                access_level=AccessLevel.FREE,
                subscription_status=SubscriptionStatus.INACTIVE,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        return user


async def _err(message: Message, exc: Exception) -> None:
    logger.exception("Bot handler error: %s", exc)
    await message.answer("⚠️ Something went wrong. Please try again.")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    try:
        _upsert_user(message)
        text = (
            "Welcome to Prediction Market Scanner\n\n"
            "Finds unusual prediction markets across platforms.\n\n"
            "You will receive:\n"
            "• arbitrage candidates\n"
            "• price divergences\n"
            "• duplicate markets\n"
            "• rule risks\n\n"
            "Commands:\n"
            "/top — best signals now\n"
            "/signals — latest signals\n"
            "/watchlist — track markets\n"
            "/digest — today summary\n"
            "/me — your profile"
        )
        if _is_admin(message):
            text += "\n\nAdmin:\n/portfolio — dry-run balance\n/positions — open trades\n/pnl — performance stats"
        await message.answer(text)
    except Exception as exc:
        await _err(message, exc)


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = "/plans /signals /top /watchlist /add /remove /digest /me"
    if _is_admin(message):
        text += "\nAdmin: /portfolio /positions /pnl"
    await message.answer(text)


@dp.message(Command("plans"))
async def cmd_plans(message: Message) -> None:
    await message.answer("FREE, PRO, PREMIUM (payments TBD)")


@dp.message(Command("signals"))
async def cmd_signals(message: Message) -> None:
    try:
        with _db() as db:
            svc = TelegramProductService(db)
            user = svc.get_or_create_user(str(message.from_user.id), message.from_user.username)
            rows = svc.latest_signals(user=user, signal_type=None, page=1, page_size=7)
            if not rows:
                await message.answer("No signals yet")
                return
            allowed: list[Signal] = []
            for row in rows:
                if svc.can_send_signal(user, 1):
                    allowed.append(row)
                    svc.record_signal_sent(user, row)
                else:
                    break
            if not allowed:
                await message.answer("📛 Daily signal limit reached. Use /plans")
                return
            # Prefetch markets in one query
            market_ids = [s.market_id for s in allowed]
            markets = {
                m.id: m
                for m in db.scalars(select(Market).where(Market.id.in_(market_ids)))
            }
            parts = []
            for s in allowed:
                market = markets.get(s.market_id)
                sig_type = _mv2(s.signal_type.value)
                title = _mv2(s.title[:80])
                conf = _mv2(f"{s.confidence_score or 0:.2f}")
                link = f"[Open Market]({market.url})" if market and market.url else ""
                parts.append(f"*{sig_type}* | c={conf}\n{title}\n{link}")
            await message.answer("\n\n".join(parts), parse_mode="MarkdownV2", disable_web_page_preview=True)
    except Exception as exc:
        await _err(message, exc)


@dp.message(Command("top"))
async def cmd_top(message: Message) -> None:
    try:
        with _db() as db:
            svc = TelegramProductService(db)
            user = svc.get_or_create_user(str(message.from_user.id), message.from_user.username)
            rows = svc.top_ranked_signals(user, limit=5)
            if not rows:
                await message.answer("No top signals yet")
                return
            allowed: list[Signal] = []
            for row in rows:
                if svc.can_send_signal(user, 1):
                    allowed.append(row)
                    svc.record_signal_sent(user, row)
                else:
                    break
            if not allowed:
                await message.answer("📛 Daily signal limit reached. Use /plans")
                return
            market_ids = [s.market_id for s in allowed]
            markets = {
                m.id: m
                for m in db.scalars(select(Market).where(Market.id.in_(market_ids)))
            }
            nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
            parts = ["🔥 *Top Prediction Market Signals*"]
            for i, s in enumerate(allowed):
                market = markets.get(s.market_id)
                num = nums[i] if i < len(nums) else f"{i+1}\\."
                sig_type = _mv2(s.signal_type.value)
                score = _mv2(f"{s.confidence_score or 0:.2f}")
                title = _mv2(s.title[:88])
                link = f"[Open Market]({market.url})" if market and market.url else ""
                parts.append(f"{num} *{sig_type}* | score={score}\n{title}\n{link}")
            await message.answer("\n\n".join(parts), parse_mode="MarkdownV2", disable_web_page_preview=True)
    except Exception as exc:
        await _err(message, exc)


@dp.message(Command("watchlist"))
async def cmd_watchlist(message: Message) -> None:
    try:
        with _db() as db:
            svc = TelegramProductService(db)
            user = svc.get_or_create_user(str(message.from_user.id), message.from_user.username)
            items = svc.list_watchlist(user)
            if not items:
                await message.answer("Your watchlist is empty. Use /add <market\\_id>", parse_mode="MarkdownV2")
                return
            total = len(items)
            parts = [f"📌 *Your Watchlist* \\({total}\\)"]
            for item in items[:10]:
                title = _mv2(item["title"][:72])
                last_sig = _mv2(item["last_signal_type"] or "none")
                prob = item.get("probability_yes")
                prob_str = f" · {prob*100:.0f}%" if prob is not None else ""
                url = item.get("url") or ""
                link = f"[#{item['market_id']}]({url})" if url else f"\\#{item['market_id']}"
                parts.append(f"{link} {title}{_mv2(prob_str)}\n_Last: {last_sig}_")
            if total > 10:
                parts.append(f"_\\.\\.\\. \\+{total - 10} more_")
            await message.answer("\n\n".join(parts), parse_mode="MarkdownV2", disable_web_page_preview=True)
    except Exception as exc:
        await _err(message, exc)


@dp.message(Command("add"))
async def cmd_add(message: Message) -> None:
    try:
        parts = (message.text or "").split()
        if len(parts) < 2 or not parts[1].isdigit():
            await message.answer("Use: /add <market_id>")
            return
        with _db() as db:
            svc = TelegramProductService(db)
            user = svc.get_or_create_user(str(message.from_user.id), message.from_user.username)
            ok, msg = svc.add_watchlist(user, int(parts[1]))
            if ok:
                await message.answer("✅ Added to watchlist")
            else:
                # Don't leak internal messages — map to user-friendly text
                friendly = {
                    "Market not found": "❌ Market not found. Check the market ID.",
                    "Already in watchlist": "ℹ️ Already in your watchlist.",
                }.get(msg, "❌ Could not add. Check your plan limit (/plans).")
                await message.answer(friendly)
    except Exception as exc:
        await _err(message, exc)


@dp.message(Command("remove"))
async def cmd_remove(message: Message) -> None:
    try:
        parts = (message.text or "").split()
        if len(parts) < 2 or not parts[1].isdigit():
            await message.answer("Use: /remove <market_id>")
            return
        with _db() as db:
            svc = TelegramProductService(db)
            user = svc.get_or_create_user(str(message.from_user.id), message.from_user.username)
            ok = svc.remove_watchlist(user, int(parts[1]))
            await message.answer("🗑 Removed" if ok else "ℹ️ Not in your watchlist")
    except Exception as exc:
        await _err(message, exc)


@dp.message(Command("digest"))
async def cmd_digest(message: Message) -> None:
    try:
        with _db() as db:
            svc = TelegramProductService(db)
            user = svc.get_or_create_user(str(message.from_user.id), message.from_user.username)
            text = svc.daily_digest(user)
            await message.answer(text, parse_mode="MarkdownV2", disable_web_page_preview=True)
    except Exception as exc:
        await _err(message, exc)


@dp.message(Command("me"))
async def cmd_me(message: Message) -> None:
    try:
        user = _upsert_user(message)
        await message.answer(
            f"User: {user.username or 'n/a'}\n"
            f"Level: {user.access_level.value}\n"
            f"Status: {user.subscription_status.value}"
        )
    except Exception as exc:
        await _err(message, exc)


# ---------------------------------------------------------------------------
# Admin: dry-run portfolio
# ---------------------------------------------------------------------------

@dp.message(Command("portfolio"))
async def cmd_portfolio(message: Message) -> None:
    if not _is_admin(message):
        await message.answer("⛔ Admin only")
        return
    try:
        with _db() as db:
            svc = TelegramProductService(db)
            text = svc.get_dryrun_portfolio_text()
            await message.answer(text, parse_mode="MarkdownV2")
    except Exception as exc:
        await _err(message, exc)


@dp.message(Command("positions"))
async def cmd_positions(message: Message) -> None:
    if not _is_admin(message):
        await message.answer("⛔ Admin only")
        return
    try:
        with _db() as db:
            svc = TelegramProductService(db)
            text = svc.get_dryrun_positions_text()
            await message.answer(text, parse_mode="MarkdownV2", disable_web_page_preview=True)
    except Exception as exc:
        await _err(message, exc)


@dp.message(Command("pnl"))
async def cmd_pnl(message: Message) -> None:
    if not _is_admin(message):
        await message.answer("⛔ Admin only")
        return
    try:
        with _db() as db:
            svc = TelegramProductService(db)
            text = svc.get_dryrun_pnl_text()
            await message.answer(text, parse_mode="MarkdownV2")
    except Exception as exc:
        await _err(message, exc)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run_bot() -> None:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    bot = Bot(token=settings.telegram_bot_token)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(run_bot())
