from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExternalContextResult:
    contradiction: float
    reason_codes: list[str]


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def route_external_context(
    evidence_bundle: dict | None,
    *,
    max_contradiction: float,
) -> ExternalContextResult:
    reason_codes: list[str] = []
    bundle = evidence_bundle or {}
    consensus = bundle.get("external_consensus") or {}
    probs = [
        _safe_float(consensus.get("polymarket_prob")),
        _safe_float(consensus.get("manifold_prob")),
        _safe_float(consensus.get("metaculus_median")),
    ]
    values = [p for p in probs if p is not None]
    if len(values) < 2:
        return ExternalContextResult(contradiction=0.0, reason_codes=["external_consensus_insufficient"])
    spread = max(values) - min(values)
    if spread > max_contradiction:
        reason_codes.append("cross_platform_contradiction_high")
    return ExternalContextResult(contradiction=round(spread, 6), reason_codes=reason_codes)
