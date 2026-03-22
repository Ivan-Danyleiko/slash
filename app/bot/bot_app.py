import asyncio
import logging
from contextlib import contextmanager
from typing import Generator

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import BotCommand, BotCommandScopeDefault, Message
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.enums import AccessLevel, SignalType, SubscriptionStatus
from app.models.models import Market, Signal, User
from app.services.dryrun.simulator import check_resolutions, refresh_mark_prices, run_simulation_cycle
from app.services.telegram_i18n import esc as _mv2, esc_url as _mv2_url, signal_type_ua as _sig_ua
from app.services.telegram_product import TelegramProductService
from app.services.telegram_templates import render_start, render_help, render_plans, render_me

logger = logging.getLogger(__name__)
settings = get_settings()
dp = Dispatcher()


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

def _admin_ids() -> set[str]:
    """Collect all configured admin Telegram IDs."""
    ids: set[str] = set()
    if settings.telegram_chat_id:
        ids.add(str(settings.telegram_chat_id).strip())
    for raw in (settings.telegram_admin_ids or "").split(","):
        uid = raw.strip()
        if uid:
            ids.add(uid)
    return ids


def _is_admin(message: Message) -> bool:
    admins = _admin_ids()
    if not admins:
        return False
    user_id = str(message.from_user.id)
    chat_id = str(message.chat.id)
    return user_id in admins or chat_id in admins


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
    await message.answer("⚠️ Щось пішло не так\\. Спробуйте ще раз\\.", parse_mode="MarkdownV2")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    try:
        _upsert_user(message)
        text = render_start(is_admin=_is_admin(message))
        await message.answer(text, parse_mode="MarkdownV2")
    except Exception as exc:
        await _err(message, exc)


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    try:
        text = render_help(is_admin=_is_admin(message))
        await message.answer(text, parse_mode="MarkdownV2")
    except Exception as exc:
        await _err(message, exc)


@dp.message(Command("plans"))
async def cmd_plans(message: Message) -> None:
    try:
        await message.answer(render_plans(), parse_mode="MarkdownV2")
    except Exception as exc:
        await _err(message, exc)


@dp.message(Command("signals"))
async def cmd_signals(message: Message) -> None:
    try:
        with _db() as db:
            svc = TelegramProductService(db)
            user = svc.get_or_create_user(str(message.from_user.id), message.from_user.username)
            rows = svc.latest_signals(user=user, signal_type=None, page=1, page_size=7)
            if not rows:
                await message.answer("Сигналів поки немає")
                return
            allowed: list[Signal] = []
            for row in rows:
                if svc.can_send_signal(user, 1):
                    allowed.append(row)
                    svc.record_signal_sent(user, row)
                else:
                    break
            if not allowed:
                await message.answer("📛 Ліміт сигналів вичерпано\\. Дивіться /plans", parse_mode="MarkdownV2")
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
                sig_type = _mv2(_sig_ua(s.signal_type.value))
                title = _mv2(s.title[:80])
                conf = _mv2(f"{s.confidence_score or 0:.0%}")
                url = market.url if market and market.url else None
                link = f"[🔗 Ринок]({_mv2_url(url)})" if url else ""
                parts.append(f"*{sig_type}* \\| {conf}\n{title}\n{link}")
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
                await message.answer("Топ\\-сигналів поки немає", parse_mode="MarkdownV2")
                return
            allowed: list[Signal] = []
            for row in rows:
                if svc.can_send_signal(user, 1):
                    allowed.append(row)
                    svc.record_signal_sent(user, row)
                else:
                    break
            if not allowed:
                await message.answer("📛 Ліміт сигналів вичерпано\\. Дивіться /plans", parse_mode="MarkdownV2")
                return
            market_ids = [s.market_id for s in allowed]
            markets = {
                m.id: m
                for m in db.scalars(select(Market).where(Market.id.in_(market_ids)))
            }
            nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
            parts = ["🔥 *Топ сигналів ринків прогнозів*"]
            for i, s in enumerate(allowed):
                market = markets.get(s.market_id)
                num = nums[i] if i < len(nums) else f"{i+1}\\."
                sig_type = _mv2(_sig_ua(s.signal_type.value))
                score = _mv2(f"{s.confidence_score or 0:.0%}")
                title = _mv2(s.title[:88])
                url = market.url if market and market.url else None
                link = f"[🔗 Ринок]({_mv2_url(url)})" if url else ""
                parts.append(f"{num} *{sig_type}* \\| {score}\n{title}\n{link}")
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
                await message.answer("Вотчліст порожній\\. Додайте: /add \\<market\\_id\\>", parse_mode="MarkdownV2")
                return
            total = len(items)
            parts = [f"📌 *Ваш вотчліст* \\({total}\\)"]
            for item in items[:10]:
                title = _mv2(item["title"][:72])
                raw_sig = item["last_signal_type"] or ""
                from app.services.telegram_i18n import signal_type_ua as _sig_ua_local
                last_sig = _mv2(_sig_ua_local(raw_sig) if raw_sig else "немає")
                prob = item.get("probability_yes")
                prob_str = f" · {prob*100:.0f}%" if prob is not None else ""
                url = item.get("url") or ""
                link = f"[\\#{item['market_id']}]({_mv2_url(url)})" if url else f"\\#{item['market_id']}"
                parts.append(f"{link} {title}{_mv2(prob_str)}\n_Сигнал: {last_sig}_")
            if total > 10:
                parts.append(f"_\\.\\.\\. \\+{total - 10} ще_")
            await message.answer("\n\n".join(parts), parse_mode="MarkdownV2", disable_web_page_preview=True)
    except Exception as exc:
        await _err(message, exc)


@dp.message(Command("add"))
async def cmd_add(message: Message) -> None:
    try:
        parts = (message.text or "").split()
        if len(parts) < 2 or not parts[1].isdigit():
            await message.answer("Використання: /add \\<market\\_id\\>", parse_mode="MarkdownV2")
            return
        with _db() as db:
            svc = TelegramProductService(db)
            user = svc.get_or_create_user(str(message.from_user.id), message.from_user.username)
            ok, msg = svc.add_watchlist(user, int(parts[1]))
            if ok:
                await message.answer("✅ Додано до вотчлісту")
            else:
                friendly = {
                    "Market not found": "❌ Ринок не знайдено\\. Перевірте ID\\.",
                    "Already in watchlist": "ℹ️ Вже у вотчлісті\\.",
                }.get(msg, "❌ Не вдалось додати\\. Перевірте ліміт плану \\(/plans\\)\\.")
                await message.answer(friendly, parse_mode="MarkdownV2")
    except Exception as exc:
        await _err(message, exc)


@dp.message(Command("remove"))
async def cmd_remove(message: Message) -> None:
    try:
        parts = (message.text or "").split()
        if len(parts) < 2 or not parts[1].isdigit():
            await message.answer("Використання: /remove \\<market\\_id\\>", parse_mode="MarkdownV2")
            return
        with _db() as db:
            svc = TelegramProductService(db)
            user = svc.get_or_create_user(str(message.from_user.id), message.from_user.username)
            ok = svc.remove_watchlist(user, int(parts[1]))
            await message.answer("🗑 Видалено" if ok else "ℹ️ Не у вотчлісті")
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
        from app.services.telegram_product import PLAN_LIMITS
        user = _upsert_user(message)
        signals_limit = PLAN_LIMITS[user.access_level]["signals"]
        text = render_me(
            username=user.username,
            access_level=user.access_level.value,
            subscription_status=user.subscription_status.value,
            signals_sent_today=user.signals_sent_today,
            signals_limit=signals_limit,
        )
        await message.answer(text, parse_mode="MarkdownV2")
    except Exception as exc:
        await _err(message, exc)


# ---------------------------------------------------------------------------
# Admin: dry-run portfolio
# ---------------------------------------------------------------------------

@dp.message(Command("portfolio"))
async def cmd_portfolio(message: Message) -> None:
    if not _is_admin(message):
        await message.answer("⛔ Тільки для адміністраторів")
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
        await message.answer("⛔ Тільки для адміністраторів")
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
        await message.answer("⛔ Тільки для адміністраторів")
        return
    try:
        with _db() as db:
            svc = TelegramProductService(db)
            text = svc.get_dryrun_pnl_text()
            await message.answer(text, parse_mode="MarkdownV2")
    except Exception as exc:
        await _err(message, exc)


# ---------------------------------------------------------------------------
# Admin: dry-run actions
# ---------------------------------------------------------------------------

@dp.message(Command("dryrun"))
async def cmd_dryrun(message: Message) -> None:
    if not _is_admin(message):
        await message.answer("⛔ Тільки для адміністраторів")
        return
    try:
        await message.answer("⏳ Запускаємо симуляцію\\.\\.\\.", parse_mode="MarkdownV2")
        with _db() as db:
            result = run_simulation_cycle(db)
            db.commit()
            refresh_mark_prices(db)
            db.commit()
            svc = TelegramProductService(db)
            portfolio_text = svc.get_dryrun_portfolio_text()
        opened = result.get("opened", 0)
        skipped = result.get("skipped", 0)
        cash = result.get("cash_remaining_usd", 0)
        summary = (
            f"✅ *Симуляцію завершено*\n"
            f"Відкрито: `{opened}` · Пропущено: `{skipped}` · Залишок: `\\${_mv2(f'{cash:.2f}')}`\n\n"
            + portfolio_text
        )
        await message.answer(summary, parse_mode="MarkdownV2")
    except Exception as exc:
        await _err(message, exc)


@dp.message(Command("simulate"))
async def cmd_simulate(message: Message) -> None:
    if not _is_admin(message):
        await message.answer("⛔ Тільки для адміністраторів")
        return
    try:
        await message.answer("🔬 Сканування кандидатів\\.\\.\\.", parse_mode="MarkdownV2")
        with _db() as db:
            svc = TelegramProductService(db)
            text = svc.get_simulate_text()
        await message.answer(text, parse_mode="MarkdownV2", disable_web_page_preview=True)
    except Exception as exc:
        await _err(message, exc)


@dp.message(Command("refresh"))
async def cmd_refresh(message: Message) -> None:
    if not _is_admin(message):
        await message.answer("⛔ Тільки для адміністраторів")
        return
    try:
        with _db() as db:
            res_result = check_resolutions(db)
            db.commit()
            mark_result = refresh_mark_prices(db)
            db.commit()
            svc = TelegramProductService(db)
            portfolio_text = svc.get_dryrun_portfolio_text()
        resolved = res_result.get("resolved_closed", 0)
        updated = mark_result.get("prices_updated", 0)
        sl_closed = mark_result.get("stop_loss_closed", 0)
        unreal = mark_result.get("total_unrealized_usd", 0)
        summary = (
            f"🔄 *Ціни оновлено*\n"
            f"Оновлено: `{updated}` · Резолюцій: `{resolved}` · Стоп\\-лос: `{sl_closed}`\n"
            f"Нереалізований P&L: `{_mv2(f'${unreal:+.2f}')}`\n\n"
            + portfolio_text
        )
        await message.answer(summary, parse_mode="MarkdownV2")
    except Exception as exc:
        await _err(message, exc)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

_USER_COMMANDS = [
    BotCommand(command="start", description="Вітання та список команд"),
    BotCommand(command="top", description="Топ-5 сигналів прямо зараз"),
    BotCommand(command="signals", description="Останні сигнали"),
    BotCommand(command="watchlist", description="Ваш вотчліст"),
    BotCommand(command="digest", description="Щоденний огляд"),
    BotCommand(command="me", description="Ваш профіль та план"),
    BotCommand(command="plans", description="Доступні плани"),
]

_ADMIN_COMMANDS = _USER_COMMANDS + [
    BotCommand(command="portfolio", description="Dry-run: баланс портфелю"),
    BotCommand(command="positions", description="Відкриті позиції"),
    BotCommand(command="pnl", description="Звіт P&L"),
    BotCommand(command="dryrun", description="Запустити симуляцію"),
    BotCommand(command="simulate", description="Сканування кандидатів (без відкриття)"),
    BotCommand(command="refresh", description="Оновити ціни та резолюції"),
]


async def run_bot() -> None:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    bot = Bot(token=settings.telegram_bot_token)
    # Register command menu (shows up as the / button in Telegram)
    await bot.set_my_commands(_USER_COMMANDS, scope=BotCommandScopeDefault())
    # Set full admin command list for each admin
    from aiogram.types import BotCommandScopeChat
    for admin_id in _admin_ids():
        try:
            await bot.set_my_commands(_ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=int(admin_id)))
        except Exception:  # noqa: BLE001
            pass
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(run_bot())
