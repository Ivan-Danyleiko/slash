import asyncio

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.enums import AccessLevel, SignalType, SubscriptionStatus
from app.models.models import Market, Signal, User
from app.services.telegram_product import TelegramProductService

settings = get_settings()
dp = Dispatcher()


def _upsert_user(message: Message) -> User:
    db = SessionLocal()
    try:
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
    finally:
        db.close()


@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    _upsert_user(message)
    await message.answer(
        "Welcome to Prediction Market Scanner\n\n"
        "This bot finds unusual prediction markets across platforms.\n\n"
        "You will receive:\n"
        "• arbitrage candidates\n"
        "• price divergences\n"
        "• duplicate markets\n"
        "• rule risks\n\n"
        "Commands:\n"
        "/top — best signals now\n"
        "/signals — latest signals\n"
        "/watchlist — track markets\n"
        "/digest — today summary"
    )


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer("/plans /signals /top /watchlist /add /remove /digest /me")


@dp.message(Command("plans"))
async def cmd_plans(message: Message) -> None:
    await message.answer("FREE, PRO, PREMIUM (payments TBD)")


@dp.message(Command("signals"))
async def cmd_signals(message: Message) -> None:
    db = SessionLocal()
    try:
        svc = TelegramProductService(db)
        user = svc.get_or_create_user(str(message.from_user.id), message.from_user.username)
        rows = svc.latest_signals(user=user, signal_type=None, page=1, page_size=7)
        if not rows:
            await message.answer("No signals yet")
            return
        allowed_rows: list[Signal] = []
        for row in rows:
            if svc.can_send_signal(user, 1):
                allowed_rows.append(row)
                svc.record_signal_sent(user, row)
            else:
                break
        if not allowed_rows:
            await message.answer("📛 Daily signal limit reached for your plan. Use /plans")
            return
        text = "\n".join(
            [
                (
                    f"*{s.signal_type.value}* | c={s.confidence_score or 0:.2f}\n"
                    f"{s.title[:80]}\n"
                    + (f"[Open Market]({market.url})" if (market := db.get(Market, s.market_id)) and market.url else "")
                )
                for s in allowed_rows
            ]
        )
        await message.answer(text, parse_mode="Markdown", disable_web_page_preview=True)
    finally:
        db.close()


@dp.message(Command("top"))
async def cmd_top(message: Message) -> None:
    db = SessionLocal()
    try:
        svc = TelegramProductService(db)
        user = svc.get_or_create_user(str(message.from_user.id), message.from_user.username)
        rows = svc.top_ranked_signals(user, limit=5)
        if not rows:
            await message.answer("No top signals yet")
            return
        allowed_rows: list[Signal] = []
        for row in rows:
            if svc.can_send_signal(user, 1):
                allowed_rows.append(row)
                svc.record_signal_sent(user, row)
            else:
                break
        if not allowed_rows:
            await message.answer("📛 Daily signal limit reached for your plan. Use /plans")
            return
        text = (
            "🔥 Top Prediction Market Signals\n\n"
            + "\n\n".join(
                [
                    (
                        f"{i+1}️⃣ *{s.signal_type.value}* | score={(s.confidence_score or 0):.2f}\n"
                        f"{s.title[:88]}\n"
                        + (f"[Open Market]({market.url})" if (market := db.get(Market, s.market_id)) and market.url else "")
                    )
                    for i, s in enumerate(allowed_rows)
                ]
            )
            if allowed_rows
            else "No top signals"
        )
        await message.answer(text, parse_mode="Markdown", disable_web_page_preview=True)
    finally:
        db.close()


@dp.message(Command("watchlist"))
async def cmd_watchlist(message: Message) -> None:
    db = SessionLocal()
    try:
        svc = TelegramProductService(db)
        user = svc.get_or_create_user(str(message.from_user.id), message.from_user.username)
        items = svc.list_watchlist(user)
        if not items:
            await message.answer("Your watchlist is empty. Use /add <market_id>")
            return
        text = "📌 Your Watchlist\n\n" + "\n\n".join(
            [f"#{i['market_id']} {i['title'][:72]}\nLast signal: {i['last_signal_type'] or 'none'}\n{i['url'] or ''}" for i in items[:10]]
        )
        await message.answer(text, parse_mode="Markdown", disable_web_page_preview=True)
    finally:
        db.close()


@dp.message(Command("add"))
async def cmd_add(message: Message) -> None:
    db = SessionLocal()
    try:
        parts = (message.text or "").split()
        if len(parts) < 2 or not parts[1].isdigit():
            await message.answer("Use: /add <market_id>")
            return
        svc = TelegramProductService(db)
        user = svc.get_or_create_user(str(message.from_user.id), message.from_user.username)
        ok, msg = svc.add_watchlist(user, int(parts[1]))
        await message.answer("✅ Added to watchlist" if ok else f"❌ {msg}")
    finally:
        db.close()


@dp.message(Command("remove"))
async def cmd_remove(message: Message) -> None:
    db = SessionLocal()
    try:
        parts = (message.text or "").split()
        if len(parts) < 2 or not parts[1].isdigit():
            await message.answer("Use: /remove <market_id>")
            return
        svc = TelegramProductService(db)
        user = svc.get_or_create_user(str(message.from_user.id), message.from_user.username)
        ok = svc.remove_watchlist(user, int(parts[1]))
        await message.answer("🗑 Removed" if ok else "Not in watchlist")
    finally:
        db.close()


@dp.message(Command("digest"))
async def cmd_digest(message: Message) -> None:
    db = SessionLocal()
    try:
        svc = TelegramProductService(db)
        user = svc.get_or_create_user(str(message.from_user.id), message.from_user.username)
        text = svc.daily_digest(user)
        await message.answer(text, parse_mode="Markdown", disable_web_page_preview=True)
    finally:
        db.close()


@dp.message(Command("me"))
async def cmd_me(message: Message) -> None:
    user = _upsert_user(message)
    await message.answer(
        f"User: {user.username or 'n/a'}\\nLevel: {user.access_level.value}\\nStatus: {user.subscription_status.value}"
    )


async def run_bot() -> None:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    bot = Bot(token=settings.telegram_bot_token)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(run_bot())
