from app.core.config import Settings
from app.models.models import Market
from app.services.analyzers.divergence import DivergenceDetector


def _mk_market(*, p: float, bid: float | None = None, ask: float | None = None, spread_cents: float | None = None) -> Market:
    return Market(
        platform_id=1,
        external_market_id="m",
        title="m",
        probability_yes=p,
        best_bid_yes=bid,
        best_ask_yes=ask,
        spread_cents=spread_cents,
    )


def test_executable_divergence_uses_bid_ask_when_available() -> None:
    det = DivergenceDetector(settings=Settings())
    a = _mk_market(p=0.45, bid=0.44, ask=0.46)
    b = _mk_market(p=0.52, bid=0.51, ask=0.53)
    res = det.compute_executable_divergence(a, b, position_size_usd=50.0, gas_fee_usd=0.0, bridge_fee_usd=0.0)
    assert res is not None
    assert round(res.gross_divergence, 3) == 0.07
    assert round(res.executable_divergence, 3) == 0.05
    assert res.direction == "YES"


def test_executable_divergence_negative_after_costs() -> None:
    det = DivergenceDetector(settings=Settings())
    a = _mk_market(p=0.49, spread_cents=4)
    b = _mk_market(p=0.52, spread_cents=4)
    res = det.compute_executable_divergence(a, b, position_size_usd=50.0, gas_fee_usd=2.0, bridge_fee_usd=0.5)
    assert res is not None
    assert res.net_edge_after_costs < 0


def test_executable_divergence_fallback_spread_and_no_clob() -> None:
    det = DivergenceDetector(settings=Settings())
    a = _mk_market(p=0.4)
    b = _mk_market(p=0.5)
    res = det.compute_executable_divergence(a, b, position_size_usd=100.0, gas_fee_usd=0.0, bridge_fee_usd=0.0)
    assert res is not None
    assert res.has_clob_data is False
    assert res.spread_a > 0
    assert res.spread_b > 0


def test_detector_plain_divergence_remains_available() -> None:
    det = DivergenceDetector(settings=Settings())
    a = _mk_market(p=0.1)
    b = _mk_market(p=0.25)
    assert det.divergence(a, b) == 0.15
