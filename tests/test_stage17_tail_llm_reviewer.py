from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.base import Base
from app.models.models import Market, Platform, Signal
from app.models.enums import SignalType
from app.services.stage17.tail_llm_reviewer import review_tail_narrative


def _mk_db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return session_factory()


def test_tail_llm_reviewer_returns_cached_result_when_real_calls_disabled() -> None:
    db = _mk_db()
    p = Platform(name="POLYMARKET")
    db.add(p)
    db.flush()
    market = Market(
        platform_id=p.id,
        external_market_id="m",
        title="Will team win championship?",
        description="Sports narrative event",
        rules_text="Official scoreboard determines outcome.",
        probability_yes=0.07,
    )
    db.add(market)
    db.flush()
    signal = Signal(
        signal_type=SignalType.TAIL_EVENT_CANDIDATE,
        market_id=market.id,
        title="Tail sports",
        summary="",
        signal_direction="NO",
    )
    db.add(signal)
    db.commit()

    settings = get_settings().model_copy(
        update={
            "stage7_agent_real_calls_enabled": False,
            "signal_tail_llm_prompt_version": "tail_v1",
        }
    )
    a = review_tail_narrative(
        settings=settings,
        signal=signal,
        market=market,
        tail_category="sports_outcome",
        market_prob=0.07,
        our_prob=0.03,
    )
    b = review_tail_narrative(
        settings=settings,
        signal=signal,
        market=market,
        tail_category="sports_outcome",
        market_prob=0.07,
        our_prob=0.03,
    )
    # With stage7_agent_real_calls_enabled=False, the fallback returns SKIP (safe default).
    assert a["decision"] == "SKIP"
    assert a["direction"] in {"YES", "NO"}
    assert a["input_hash"] == b["input_hash"]
    assert b["cache_hit"] is True
    assert len(str(a["prompt_version_hash"])) == 8
