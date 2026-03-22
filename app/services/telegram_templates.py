"""MarkdownV2-formatted message templates (Ukrainian UI)."""

from __future__ import annotations

from app.services.telegram_i18n import (
    category_label,
    esc,
    esc_url,
    signal_type_ua,
    access_level_ua,
    subscription_status_ua,
)


# ─── Signal push ─────────────────────────────────────────────────────────────

def render_signal_push(
    *,
    signal_type: str,
    title: str,
    confidence: float,
    metric_label: str,
    metric_value: float,
    utility: float,
    slippage_edge: float,
    cost_impact: float,
    assumptions: str,
    disclaimer: str,
    market_url: str | None = None,
) -> str:
    type_ua = esc(signal_type_ua(signal_type))
    t = esc(title[:120])
    conf_s = esc(f"{confidence:.2f}")
    metric_s = esc(f"{metric_value:.1f}%")
    util_s = esc(f"{utility:.3f}")
    edge_s = esc(f"{slippage_edge:.3f}")
    cost_s = esc(f"{cost_impact:.3f}")
    assump_s = esc(assumptions)
    disc_s = esc(disclaimer)
    metric_label_s = esc(metric_label)

    link = f"\n[🔗 Відкрити ринок]({esc_url(market_url)})" if market_url else ""

    return (
        f"🔥 *{type_ua}*\n"
        f"{t}{link}\n\n"
        f"Впевненість: `{conf_s}` \\| {metric_label_s}: `{metric_s}`\n"
        f"Корисність: `{util_s}` \\| Перевага: `{edge_s}` \\(витрати: `{cost_s}`\\)\n"
        f"Модель: `{assump_s}`\n"
        f"_{disc_s}_"
    )


# ─── Stage17: position opened ─────────────────────────────────────────────────

def render_stage17_open(item: dict) -> str:
    category = str(item.get("tail_category") or "")
    cat_label = esc(category_label(category))
    koef = float(item.get("koef") or 0.0)
    our_pct = 100.0 * float(item.get("our_prob") or 0.0)
    mkt_pct = 100.0 * float(item.get("market_prob") or 0.0)
    bet = float(item.get("notional_usd") or 0.0)
    platform = esc(str(item.get("platform") or "Unknown"))
    days = int(item.get("days_to_resolution") or 0)
    title = esc(str(item.get("title") or "")[:120])

    return (
        f"🎯 *Нова позиція Stage17*\n"
        f"{title}\n\n"
        f"{cat_label} \\| {platform} \\| {esc(str(days))} днів\n"
        f"Коеф: `x{esc(f'{koef:.2f}')}` \\| наша: `{esc(f'{our_pct:.1f}')}%` vs ринок: `{esc(f'{mkt_pct:.1f}')}%`\n"
        f"Ставка: `\\${esc(f'{bet:.2f}')}`"
    )


# ─── Stage17: win ─────────────────────────────────────────────────────────────

def render_stage17_win(item: dict) -> str:
    koef = float(item.get("koef") or 0.0)
    profit = float(item.get("profit_usd") or 0.0)
    title = esc(str(item.get("title") or "")[:100])
    return (
        f"🎉 *Виграш Stage17\\!*\n"
        f"{title}\n\n"
        f"Коеф: `x{esc(f'{koef:.2f}')}` \\| Прибуток: `\\+\\${esc(f'{profit:.2f}')}`"
    )


# ─── Stage17: daily digest ───────────────────────────────────────────────────

def render_stage17_daily(summary: dict) -> str:
    hit_rate = summary.get("hit_rate_tail")
    roi = summary.get("roi_total")
    open_pos = summary.get("open_positions")
    avg_koef = summary.get("avg_koef")
    final = summary.get("final_decision")

    def _fmt(v) -> str:
        if v is None:
            return "n/a"
        if isinstance(v, float):
            return f"{v:.3f}"
        return str(v)

    hr_s = esc(_fmt(hit_rate))
    roi_s = esc(_fmt(roi))
    open_s = esc(str(open_pos) if open_pos is not None else "n/a")
    koef_s = esc(_fmt(avg_koef))
    final_s = esc(str(final) if final else "n/a")

    return (
        f"📊 *Stage17 — щоденний звіт*\n\n"
        f"Hit rate: `{hr_s}` \\| ROI: `{roi_s}`\n"
        f"Відкритих позицій: `{open_s}` \\| Сер\\. коеф: `{koef_s}`\n"
        f"Рішення: `{final_s}`"
    )


# ─── Bot commands ─────────────────────────────────────────────────────────────

def render_start(*, is_admin: bool = False) -> str:
    text = (
        "👋 *Вітаємо у Prediction Market Scanner\\!*\n\n"
        "Знаходимо цікаві ринки прогнозів: арбітраж, цінові розходження, нетипові ринки та ризики правил\\.\n\n"
        "*Команди:*\n"
        "/top — топ\\-5 сигналів прямо зараз\n"
        "/signals — останні сигнали\n"
        "/watchlist — ваш вотчліст\n"
        "/digest — щоденний огляд\n"
        "/me — ваш профіль та план\n"
        "/plans — доступні плани"
    )
    if is_admin:
        text += (
            "\n\n*Адмін:*\n"
            "/portfolio — баланс dry\\-run\n"
            "/positions — відкриті угоди\n"
            "/pnl — статистика P&L\n"
            "/dryrun — запустити симуляцію\n"
            "/refresh — оновити ціни"
        )
    return text


def render_help(*, is_admin: bool = False) -> str:
    text = (
        "📖 *Довідка*\n\n"
        "*Сигнали:*\n"
        "/top — топ\\-5 сигналів за скором\n"
        "/signals — стрічка останніх сигналів\n"
        "/digest — щоденний дайджест\n\n"
        "*Вотчліст:*\n"
        "/watchlist — переглянути список\n"
        "/add \\<market\\_id\\> — додати ринок\n"
        "/remove \\<market\\_id\\> — видалити ринок\n\n"
        "*Профіль:*\n"
        "/me — ваш план і статистика\n"
        "/plans — тарифи"
    )
    if is_admin:
        text += (
            "\n\n*Адмін:*\n"
            "/portfolio /positions /pnl /dryrun /simulate /refresh"
        )
    return text


def render_plans() -> str:
    return (
        "💎 *Плани*\n\n"
        "🆓 *Безкоштовний* — до 3 сигналів на день, вотчліст до 3 ринків\n\n"
        "⭐ *Pro* — до 20 сигналів на день, вотчліст до 20 ринків\n\n"
        "🚀 *Premium* — необмежено\n\n"
        "_Підключення планів — незабаром\\._"
    )


def render_me(
    *,
    username: str | None,
    access_level: str,
    subscription_status: str,
    signals_sent_today: int,
    signals_limit: int,
) -> str:
    uname = esc(username or "—")
    level = esc(access_level_ua(access_level))
    status = esc(subscription_status_ua(subscription_status))
    sent_s = esc(str(signals_sent_today))
    limit_s = esc(str(signals_limit))

    return (
        f"👤 *Ваш профіль*\n\n"
        f"Ім'я: `{uname}`\n"
        f"План: *{level}*\n"
        f"Статус: {status}\n"
        f"Сигналів сьогодні: `{sent_s}` / `{limit_s}`"
    )
