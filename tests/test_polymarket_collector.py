from app.services.collectors.polymarket import PolymarketCollector


def test_extract_probability_yes_from_outcome_prices_list() -> None:
    row = {
        "outcomes": ["Yes", "No"],
        "outcomePrices": ["0.63", "0.37"],
    }
    prob = PolymarketCollector._extract_probability_yes(row)
    assert prob == 0.63


def test_extract_probability_yes_from_outcome_prices_json_string() -> None:
    row = {
        "outcomes": '["YES", "NO"]',
        "outcomePrices": '["0.58", "0.42"]',
    }
    prob = PolymarketCollector._extract_probability_yes(row)
    assert prob == 0.58
