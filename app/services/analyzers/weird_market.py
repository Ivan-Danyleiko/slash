from app.models.models import Market


class WeirdMarketDetector:
    def analyze(self, market: Market) -> dict | None:
        flags: list[str] = []
        if market.probability_yes is not None and not (0 <= market.probability_yes <= 1):
            flags.append("yes_probability_out_of_range")
        if market.probability_no is not None and not (0 <= market.probability_no <= 1):
            flags.append("no_probability_out_of_range")
        if market.probability_yes is not None and market.probability_no is not None:
            if abs((market.probability_yes + market.probability_no) - 1.0) > 0.1:
                flags.append("yes_no_sum_inconsistency")
        if not flags:
            return None
        return {"flags": flags, "score": min(1.0, 0.2 * len(flags))}
