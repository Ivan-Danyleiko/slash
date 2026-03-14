from enum import Enum


class AccessLevel(str, Enum):
    FREE = "FREE"
    PRO = "PRO"
    PREMIUM = "PREMIUM"


class SubscriptionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    CANCELED = "CANCELED"


class SignalType(str, Enum):
    ARBITRAGE_CANDIDATE = "ARBITRAGE_CANDIDATE"
    DUPLICATE_MARKET = "DUPLICATE_MARKET"
    DIVERGENCE = "DIVERGENCE"
    LIQUIDITY_RISK = "LIQUIDITY_RISK"
    RULES_RISK = "RULES_RISK"
    WEIRD_MARKET = "WEIRD_MARKET"
    WATCHLIST = "WATCHLIST"
