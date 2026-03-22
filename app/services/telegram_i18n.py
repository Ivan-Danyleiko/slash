"""UA string constants and MarkdownV2 escape helpers for Telegram."""

from app.models.enums import AccessLevel, SignalType, SubscriptionStatus

# ─── MarkdownV2 escape ────────────────────────────────────────────────────────

_MV2_SPECIAL = r"\_*[]()~`>#+-=|{}.!"


def esc(text: str) -> str:
    """Escape text for Telegram MarkdownV2."""
    out = str(text)
    for ch in _MV2_SPECIAL:
        out = out.replace(ch, f"\\{ch}")
    return out


def esc_url(url: str) -> str:
    """Escape URL inside a MarkdownV2 link — only ) needs escaping."""
    return str(url).replace(")", "\\)")


# ─── Signal types → UA ───────────────────────────────────────────────────────

_SIGNAL_TYPE_UA: dict[str, str] = {
    SignalType.ARBITRAGE_CANDIDATE.value: "Арбітраж",
    SignalType.TAIL_EVENT_CANDIDATE.value: "Хвостова подія",
    SignalType.DUPLICATE_MARKET.value: "Дублікат ринку",
    SignalType.DIVERGENCE.value: "Цінове розходження",
    SignalType.LIQUIDITY_RISK.value: "Ліквідність",
    SignalType.RULES_RISK.value: "Ризик правил",
    SignalType.WEIRD_MARKET.value: "Нетиповий ринок",
    SignalType.WATCHLIST.value: "Вотчліст",
}


def signal_type_ua(value: str) -> str:
    return _SIGNAL_TYPE_UA.get(value, value)


# ─── Stage17 categories ──────────────────────────────────────────────────────

_CATEGORY_EMOJI: dict[str, str] = {
    "price_target": "💰",
    "crypto_level": "💰",
    "sports_match": "🏆",
    "geopolitical_event": "🌍",
    "election": "🗳️",
    "earnings_surprise": "📈",
    "regulatory": "⚖️",
    "company_valuation": "🏢",
}

_CATEGORY_UA: dict[str, str] = {
    "price_target": "цінова ціль",
    "crypto_level": "крипто рівень",
    "sports_match": "спортивна подія",
    "geopolitical_event": "геополітика",
    "election": "вибори",
    "earnings_surprise": "звітність",
    "regulatory": "регуляторика",
    "company_valuation": "оцінка компанії",
}


def category_label(cat: str) -> str:
    emoji = _CATEGORY_EMOJI.get(cat, "🔥")
    label = _CATEGORY_UA.get(cat, cat)
    return f"{emoji} {label}"


# ─── Access level / subscription status → UA ─────────────────────────────────

_ACCESS_LEVEL_UA: dict[str, str] = {
    AccessLevel.FREE.value: "Безкоштовний",
    AccessLevel.PRO.value: "Pro",
    AccessLevel.PREMIUM.value: "Premium",
}

_SUBSCRIPTION_STATUS_UA: dict[str, str] = {
    SubscriptionStatus.ACTIVE.value: "Активна",
    SubscriptionStatus.INACTIVE.value: "Неактивна",
    SubscriptionStatus.CANCELED.value: "Скасована",
}


def access_level_ua(value: str) -> str:
    return _ACCESS_LEVEL_UA.get(value, value)


def subscription_status_ua(value: str) -> str:
    return _SUBSCRIPTION_STATUS_UA.get(value, value)
