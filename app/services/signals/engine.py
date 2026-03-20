from datetime import UTC, date, datetime, timedelta
import re

from sqlalchemy import and_, delete, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, load_only

from app.core.config import get_settings
from app.models.enums import SignalType
from app.models.models import (
    DuplicatePairCandidate,
    DuplicateMarketPair,
    LiquidityAnalysis,
    Market,
    MarketSnapshot,
    Platform,
    RulesAnalysis,
    Signal,
    SignalGenerationStats,
    SignalHistory,
    Stage7AgentDecision,
    Stage8Decision,
    Stage8Position,
)
from app.services.analyzers.divergence import DivergenceDetector
from app.services.analyzers.duplicate import DuplicateDetector
from app.services.analyzers.liquidity import LiquidityAnalyzer
from app.services.analyzers.rules_risk import RulesRiskAnalyzer
from app.services.analyzers.weird_market import WeirdMarketDetector
from app.services.signals.base_rate import BaseRateEstimator
from app.services.signals.execution import build_execution_simulator
from app.services.signals.tail_circuit_breaker import can_open_tail_by_category, check_tail_circuit_breaker
from app.services.signals.tail_classifier import classify_tail_event, tail_mispricing_ratio


class SignalEngine:
    """Deterministic analytics pipeline on top of normalized markets."""

    def __init__(self, db: Session):
        self.db = db
        self.settings = get_settings()
        self.cooldown_hours = 24
        self.execution = build_execution_simulator(db=db, settings=self.settings)

    def detect_duplicates(self) -> dict[str, int]:
        now = datetime.now(UTC)
        incremental_hours = max(1, int(getattr(self.settings, "signal_duplicate_incremental_hours", 6)))
        max_anchor_markets = max(20, int(getattr(self.settings, "signal_duplicate_max_anchor_markets", 120)))
        max_candidate_markets = max(500, int(getattr(self.settings, "signal_duplicate_max_candidate_markets", 5000)))
        max_broad_pairs = max(500, int(getattr(self.settings, "signal_duplicate_max_pairs_per_run", 6000)))
        cutoff = now - timedelta(hours=incremental_hours)

        anchor_markets = list(
            self.db.scalars(
                select(Market)
                .where(Market.fetched_at >= cutoff)
                .order_by(Market.fetched_at.desc())
                .limit(max_anchor_markets)
            )
        )
        if not anchor_markets:
            return {
                "duplicate_pairs": 0,
                "duplicate_pairs_broad": 0,
                "duplicate_pairs_strict_pass": 0,
                "duplicate_pairs_strict_fail": 0,
                "duplicate_pairs_shadow_balanced_pass": 0,
                "duplicate_pairs_shadow_aggressive_pass": 0,
                "mode": "incremental",
                "anchors_processed": 0,
                "candidates_processed": 0,
            }
        markets = list(
            self.db.scalars(
                select(Market)
                .options(
                    load_only(
                        Market.id,
                        Market.title,
                        Market.platform_id,
                        Market.resolution_time,
                        Market.fetched_at,
                    )
                )
                .order_by(Market.fetched_at.desc(), Market.id.desc())
                .limit(max_candidate_markets)
            )
        )
        strict_detector = DuplicateDetector.with_profile(profile="strict")
        balanced_detector = DuplicateDetector.with_profile(profile="balanced")
        aggressive_detector = DuplicateDetector.with_profile(profile="aggressive")

        broad_detector = DuplicateDetector.with_profile(profile="aggressive")
        broad_detector.min_overlap = self.settings.signal_duplicate_broad_min_overlap
        broad_detector.min_jaccard = self.settings.signal_duplicate_broad_min_jaccard
        broad_detector.min_weighted_overlap = self.settings.signal_duplicate_broad_min_weighted_overlap
        broad_detector.anchor_idf = 0.0
        broad_pairs = broad_detector.find_pairs_against(
            anchor_markets,
            markets,
            self.settings.signal_duplicate_broad_threshold,
            max_pairs=max_broad_pairs,
        )

        anchor_ids = [m.id for m in anchor_markets]
        self.db.execute(
            delete(DuplicateMarketPair).where(
                or_(DuplicateMarketPair.market_a_id.in_(anchor_ids), DuplicateMarketPair.market_b_id.in_(anchor_ids))
            )
        )
        self.db.execute(
            delete(DuplicatePairCandidate).where(
                or_(DuplicatePairCandidate.market_a_id.in_(anchor_ids), DuplicatePairCandidate.market_b_id.in_(anchor_ids))
            )
        )

        inserted = 0
        strict_fail = 0
        shadow_balanced_pass = 0
        shadow_aggressive_pass = 0
        for a, b, sim, explanation in broad_pairs:
            ok, strict_sim, strict_expl, drop_reason = strict_detector.evaluate_pair(
                a, b, self.settings.signal_duplicate_threshold
            )
            bal_ok, _, _, _ = balanced_detector.evaluate_pair(a, b, self.settings.signal_duplicate_threshold)
            agg_ok, _, _, _ = aggressive_detector.evaluate_pair(a, b, self.settings.signal_duplicate_threshold)
            if bal_ok:
                shadow_balanced_pass += 1
            if agg_ok:
                shadow_aggressive_pass += 1
            if ok:
                self.db.add(
                    DuplicateMarketPair(
                        market_a_id=a.id,
                        market_b_id=b.id,
                        similarity_score=strict_sim,
                        similarity_explanation=strict_expl,
                    )
                )
                self.db.add(
                    DuplicatePairCandidate(
                        market_a_id=a.id,
                        market_b_id=b.id,
                        stage="strict_pass",
                        similarity_score=strict_sim,
                        similarity_explanation=strict_expl,
                        drop_reason=None,
                    )
                )
                inserted += 1
            else:
                self.db.add(
                    DuplicatePairCandidate(
                        market_a_id=a.id,
                        market_b_id=b.id,
                        stage="strict_fail",
                        similarity_score=strict_sim or sim,
                        similarity_explanation=strict_expl or explanation,
                        drop_reason=drop_reason or "strict_threshold_not_met",
                    )
                )
                strict_fail += 1

        self.db.commit()
        return {
            "duplicate_pairs": inserted,
            "duplicate_pairs_broad": len(broad_pairs),
            "duplicate_pairs_strict_pass": inserted,
            "duplicate_pairs_strict_fail": strict_fail,
            "duplicate_pairs_shadow_balanced_pass": shadow_balanced_pass,
            "duplicate_pairs_shadow_aggressive_pass": shadow_aggressive_pass,
            "mode": "incremental",
            "anchors_processed": len(anchor_markets),
            "candidates_processed": len(markets),
            "cutoff": cutoff.isoformat(),
        }

    def analyze_rules(self) -> dict[str, int]:
        # Only re-analyze markets synced in the last 25 min (sync runs every 15 min).
        # Older markets keep their existing analysis — it's still valid.
        cutoff = datetime.now(UTC) - timedelta(minutes=25)
        markets = list(self.db.scalars(select(Market).where(Market.fetched_at >= cutoff)))
        if not markets:
            return {"rules_analyses": 0, "liquidity_analyses": 0}
        market_ids = [m.id for m in markets]
        rules = RulesRiskAnalyzer()
        weird = WeirdMarketDetector()
        liquidity = LiquidityAnalyzer()

        self.db.execute(delete(RulesAnalysis).where(RulesAnalysis.market_id.in_(market_ids)))
        self.db.execute(delete(LiquidityAnalysis).where(LiquidityAnalysis.market_id.in_(market_ids)))

        for market in markets:
            rules_result = rules.analyze(market)
            liq_result = liquidity.analyze(market)
            self.db.add(
                RulesAnalysis(
                    market_id=market.id,
                    score=rules_result["score"],
                    level=rules_result["level"],
                    matched_flags=rules_result["matched_flags"],
                    explanation=rules_result["explanation"],
                )
            )
            self.db.add(
                LiquidityAnalysis(
                    market_id=market.id,
                    score=liq_result["score"],
                    level=liq_result["level"],
                    explanation=liq_result["explanation"],
                )
            )
            weird_result = weird.analyze(market)
            if weird_result:
                self._create_signal_if_not_recent(
                    signal_type=SignalType.WEIRD_MARKET,
                    market_id=market.id,
                    related_market_id=None,
                    title=f"Weird market: {market.title}",
                    summary=", ".join(weird_result["flags"]),
                    confidence_score=weird_result["score"],
                    metadata_json={"flags": weird_result["flags"]},
                )
        self.db.commit()
        return {"rules_analyses": len(markets), "liquidity_analyses": len(markets)}

    def detect_divergence(self) -> dict[str, int]:
        divergence_detector = DivergenceDetector(settings=self.settings)
        pairs = list(self.db.scalars(select(DuplicateMarketPair)))
        flagged = 0
        flagged_gross = 0
        flagged_net = 0

        # Batch-load all needed markets in one query instead of N+1 db.get() calls.
        if pairs:
            pair_market_ids = set()
            for pair in pairs:
                pair_market_ids.add(pair.market_a_id)
                pair_market_ids.add(pair.market_b_id)
            markets_by_id: dict[int, Market] = {
                m.id: m for m in self.db.scalars(select(Market).where(Market.id.in_(pair_market_ids)))
            }
        else:
            markets_by_id = {}

        for pair in pairs:
            market_a = markets_by_id.get(pair.market_a_id)
            market_b = markets_by_id.get(pair.market_b_id)
            if not market_a or not market_b:
                continue
            gross = divergence_detector.divergence(market_a, market_b)
            exec_res = divergence_detector.compute_executable_divergence(market_a, market_b)
            if gross is not None and gross >= self.settings.signal_divergence_threshold:
                flagged_gross += 1

            if self.settings.signal_divergence_use_executable:
                threshold_value = exec_res.net_edge_after_costs if exec_res is not None else None
                pair.divergence_score = threshold_value
                if threshold_value is not None and threshold_value >= self.settings.signal_divergence_net_edge_min:
                    flagged += 1
                    flagged_net += 1
            else:
                pair.divergence_score = gross
                if gross is not None and gross >= self.settings.signal_divergence_threshold:
                    flagged += 1

        self.db.commit()
        return {
            "pairs_processed": len(pairs),
            "divergence_flagged": flagged,
            "divergence_flagged_gross": flagged_gross,
            "divergence_flagged_net": flagged_net,
            "use_executable": bool(self.settings.signal_divergence_use_executable),
        }

    def generate_signals(self) -> dict[str, int]:
        has_stage7_ref = exists(
            select(Stage7AgentDecision.id).where(Stage7AgentDecision.signal_id == Signal.id)
        )
        has_stage8_ref = exists(
            select(Stage8Decision.id).where(Stage8Decision.signal_id == Signal.id)
        )
        has_stage8_position_ref = exists(
            select(Stage8Position.id).where(Stage8Position.signal_id == Signal.id)
        )
        has_signal_history_ref = exists(
            select(SignalHistory.id).where(SignalHistory.signal_id == Signal.id)
        )
        created = 0
        stale_removed = self.db.execute(
            delete(Signal).where(
                Signal.signal_type.in_(
                    [
                        SignalType.DUPLICATE_MARKET,
                        SignalType.DIVERGENCE,
                        SignalType.ARBITRAGE_CANDIDATE,
                    ]
                ),
                ~has_stage7_ref,
                ~has_stage8_ref,
                ~has_stage8_position_ref,
                ~has_signal_history_ref,
            )
        ).rowcount or 0
        duplicate_attempted = 0
        duplicate_cooldown_blocked = 0
        duplicate_updated = 0
        duplicate_low_liquidity_skipped = 0
        divergence_attempted = 0
        divergence_below_threshold = 0
        divergence_cooldown_blocked = 0
        divergence_updated = 0
        divergence_low_liquidity_skipped = 0
        rules_attempted = 0
        rules_below_threshold = 0
        rules_cooldown_blocked = 0
        rules_updated = 0
        rules_missing_text_high_liquidity = 0
        rules_excluded_by_keyword = 0
        arbitrage_attempted = 0
        arbitrage_created = 0
        arbitrage_low_liquidity_skipped = 0
        arbitrage_no_snapshot_skipped = 0
        arbitrage_no_edge_skipped = 0
        arbitrage_excluded_by_keyword = 0
        arbitrage_low_volume_skipped = 0
        arbitrage_personal_market_skipped = 0
        tail_attempted = 0
        tail_created = 0
        tail_updated = 0
        tail_out_of_prob_range = 0
        tail_ambiguous_skipped = 0
        tail_below_mispricing = 0
        tail_by_category_limit = 0
        tail_breaker_blocked = 0
        tail_excluded_by_keyword = 0
        tail_source_filtered = 0

        pairs = list(self.db.scalars(select(DuplicateMarketPair)))
        liquidity_rows = list(self.db.scalars(select(LiquidityAnalysis)))
        liquidity_by_market = {row.market_id: row.score for row in liquidity_rows}
        rules_rows = list(self.db.scalars(select(RulesAnalysis)))
        rules_by_market = {row.market_id: row.score for row in rules_rows}
        platform_by_id = {p.id: str(p.name or "").upper() for p in self.db.scalars(select(Platform))}

        # Pre-load all markets referenced by rules_rows to avoid N+1 db.get() in the rules loop below.
        rules_market_ids = [row.market_id for row in rules_rows]
        rules_markets_by_id: dict[int, Market] = {
            m.id: m
            for m in self.db.scalars(select(Market).where(Market.id.in_(rules_market_ids)))
        } if rules_market_ids else {}
        excluded_tokens = [
            x.strip().lower() for x in self.settings.signal_arbitrage_exclude_keywords.split(",") if x.strip()
        ]
        enabled_sources = {
            x.strip().upper()
            for x in str(self.settings.signal_sources_enabled or "").split(",")
            if x.strip()
        }
        known_signal_sources = {"POLYMARKET", "MANIFOLD", "METACULUS", "KALSHI"}
        for pair in pairs:
            duplicate_attempted += 1
            pair_liquidity = self._pair_liquidity_score(
                pair.market_a_id, pair.market_b_id, liquidity_by_market
            )
            if pair_liquidity < self.settings.signal_duplicate_min_pair_liquidity:
                duplicate_low_liquidity_skipped += 1
            else:
                outcome = self._create_signal_if_not_recent(
                    signal_type=SignalType.DUPLICATE_MARKET,
                    market_id=pair.market_a_id,
                    related_market_id=pair.market_b_id,
                    title="Duplicate market candidate",
                    summary=f"Similarity={pair.similarity_score:.1f}. {pair.similarity_explanation or ''}".strip(),
                    confidence_score=min(1.0, pair.similarity_score / 100),
                    liquidity_score=pair_liquidity,
                    divergence_score=pair.divergence_score,
                    metadata_json={"similarity_score": pair.similarity_score},
                )
                if outcome == "created":
                    created += 1
                elif outcome == "updated":
                    duplicate_updated += 1
                else:
                    duplicate_cooldown_blocked += 1

            threshold = (
                self.settings.signal_divergence_net_edge_min
                if self.settings.signal_divergence_use_executable
                else self.settings.signal_divergence_threshold
            )
            if pair.divergence_score is None or pair.divergence_score < threshold:
                divergence_below_threshold += 1
                continue

            divergence_attempted += 1
            if pair_liquidity < self.settings.signal_divergence_min_pair_liquidity:
                divergence_low_liquidity_skipped += 1
                continue
            market_a = self.db.get(Market, pair.market_a_id)
            market_b = self.db.get(Market, pair.market_b_id)
            signal_direction = None
            if market_a and market_b and market_a.probability_yes is not None and market_b.probability_yes is not None:
                signal_direction = "YES" if float(market_a.probability_yes) < float(market_b.probability_yes) else "NO"
            exec_meta = None
            if market_a and market_b:
                exec_res = DivergenceDetector(settings=self.settings).compute_executable_divergence(market_a, market_b)
                if exec_res is not None:
                    exec_meta = {
                        "gross_divergence": round(exec_res.gross_divergence, 6),
                        "executable_divergence": round(exec_res.executable_divergence, 6),
                        "net_edge_after_costs": round(exec_res.net_edge_after_costs, 6),
                        "has_clob_data": bool(exec_res.has_clob_data),
                        "spread_a": round(exec_res.spread_a, 6),
                        "spread_b": round(exec_res.spread_b, 6),
                        "ask_a": round(exec_res.ask_a, 6),
                        "bid_a": round(exec_res.bid_a, 6),
                        "ask_b": round(exec_res.ask_b, 6),
                        "bid_b": round(exec_res.bid_b, 6),
                    }

            outcome = self._create_signal_if_not_recent(
                signal_type=SignalType.DIVERGENCE,
                market_id=pair.market_a_id,
                related_market_id=pair.market_b_id,
                title="Significant divergence across similar markets",
                summary=f"Divergence={pair.divergence_score:.3f}",
                confidence_score=min(1.0, pair.divergence_score * 3),
                liquidity_score=pair_liquidity,
                divergence_score=pair.divergence_score,
                signal_direction=signal_direction,
                metadata_json={
                    "threshold": threshold,
                    "threshold_mode": "net_edge_after_costs" if self.settings.signal_divergence_use_executable else "gross",
                    "signal_divergence_threshold": self.settings.signal_divergence_threshold,
                    "signal_divergence_net_edge_min": self.settings.signal_divergence_net_edge_min,
                    **(exec_meta or {}),
                },
            )
            if outcome == "created":
                created += 1
            elif outcome == "updated":
                divergence_updated += 1
            else:
                divergence_cooldown_blocked += 1

        fallback_rules_candidates: list[tuple[Market, float, float, dict]] = []
        for row in rules_rows:
            market = rules_markets_by_id.get(row.market_id)
            if not market:
                continue

            # Fallback for providers with empty rules text: only surface if liquidity is meaningfully high.
            if row.score < self.settings.signal_rules_risk_threshold:
                liq_score = liquidity_by_market.get(row.market_id, 0.0)
                missing_rules_text = not (market.rules_text or "").strip()
                high_liquidity = liq_score >= self.settings.signal_rules_missing_min_liquidity
                enough_volume = (market.volume_24h or 0.0) >= self.settings.signal_rules_missing_min_volume_24h
                if missing_rules_text and high_liquidity:
                    title_l = (market.title or "").lower()
                    if any(token in title_l for token in excluded_tokens):
                        rules_excluded_by_keyword += 1
                        continue
                    if not enough_volume:
                        rules_below_threshold += 1
                        continue
                    rules_missing_text_high_liquidity += 1
                    rules_attempted += 1
                    rr_score = max(0.25, liq_score * 0.45)
                    score_breakdown = self._score_breakdown(
                        edge=0.2,
                        liquidity=liq_score,
                        freshness=0.8,
                        confidence=rr_score,
                        risk_penalties=rr_score,
                    )
                    fallback_rules_candidates.append((market, liq_score, rr_score, score_breakdown))
                    continue

                rules_below_threshold += 1
                continue

            rules_attempted += 1
            score_breakdown = self._score_breakdown(
                edge=0.25,
                liquidity=liquidity_by_market.get(row.market_id, 0.0),
                freshness=0.8,
                confidence=row.score,
                risk_penalties=row.score,
            )
            outcome = self._create_signal_if_not_recent(
                signal_type=SignalType.RULES_RISK,
                market_id=row.market_id,
                related_market_id=None,
                title=f"Rules risk: {market.title}",
                summary=f"Flags: {', '.join(row.matched_flags or []) or 'none'}",
                confidence_score=row.score,
                liquidity_score=liquidity_by_market.get(row.market_id),
                rules_risk_score=row.score,
                signal_mode="explicit_rules_risk",
                score_breakdown_json=score_breakdown,
                execution_analysis=self.execution.simulate(
                    market=market,
                    confidence_score=row.score,
                    liquidity_score=liquidity_by_market.get(row.market_id),
                    recent_move=None,
                    signal_type=SignalType.RULES_RISK,
                ),
                signal_direction="NO",
                metadata_json={"flags": row.matched_flags or [], "signal_mode": "explicit_rules_risk"},
            )
            if outcome == "created":
                created += 1
            elif outcome == "updated":
                rules_updated += 1
            else:
                rules_cooldown_blocked += 1

        # Apply daily cap for missing-rules fallback based on quality ranking.
        fallback_rules_candidates.sort(key=lambda x: x[2], reverse=True)
        created_today_missing = self._generated_today_count(
            SignalType.RULES_RISK, "missing_rules_risk", datetime.now(UTC).date()
        )
        missing_rules_daily_cap = max(0, self.settings.signal_rules_missing_daily_cap)
        missing_rules_creatable_left = max(0, missing_rules_daily_cap - created_today_missing)
        missing_rules_created_now = 0
        for market, liq_score, rr_score, score_breakdown in fallback_rules_candidates:
            if missing_rules_creatable_left <= 0:
                break
            outcome = self._create_signal_if_not_recent(
                signal_type=SignalType.RULES_RISK,
                market_id=market.id,
                related_market_id=None,
                title=f"Rules missing on liquid market: {market.title}",
                summary="No resolution rules text provided by source; liquidity is high, so resolution ambiguity matters more.",
                confidence_score=rr_score,
                liquidity_score=liq_score,
                rules_risk_score=rr_score,
                signal_mode="missing_rules_risk",
                score_breakdown_json=score_breakdown,
                execution_analysis=self.execution.simulate(
                    market=market,
                    confidence_score=rr_score,
                    liquidity_score=liq_score,
                    recent_move=None,
                    signal_type=SignalType.RULES_RISK,
                ),
                signal_direction="NO",
                metadata_json={"flags": ["missing_rules_text", "high_liquidity"], "signal_mode": "missing_rules_risk"},
            )
            if outcome == "created":
                created += 1
                missing_rules_created_now += 1
                missing_rules_creatable_left -= 1
            elif outcome == "updated":
                rules_updated += 1
            else:
                rules_cooldown_blocked += 1

        # Load top-2 snapshots per market via window function.
        # Use 48h cutoff (not 7d) — only recent moves matter for signals.
        snap_cutoff = datetime.now(UTC) - timedelta(hours=48)
        _rn = func.row_number().over(
            partition_by=MarketSnapshot.market_id,
            order_by=MarketSnapshot.fetched_at.desc(),
        ).label("rn")
        _subq = (
            select(MarketSnapshot.id, _rn)
            .where(MarketSnapshot.fetched_at >= snap_cutoff)
            .subquery()
        )
        _top_ids = list(self.db.scalars(
            select(_subq.c.id).where(_subq.c.rn <= 2)
        ))
        snapshots = list(self.db.scalars(
            select(MarketSnapshot)
            .where(MarketSnapshot.id.in_(_top_ids))
        )) if _top_ids else []
        last_two: dict[int, list[MarketSnapshot]] = {}
        for snap in snapshots:
            last_two.setdefault(snap.market_id, []).append(snap)

        # Incremental: only markets updated in the last 48h — avoids full 31k scan.
        _arb_cutoff = datetime.now(UTC) - timedelta(hours=48)
        arbitrage_candidates: list[tuple[Market, float, float, float, str, float, float, str]] = []
        for market in self.db.scalars(
            select(Market)
            .where(Market.probability_yes.is_not(None))
            .where(Market.fetched_at >= _arb_cutoff)
            .options(load_only(
                Market.id, Market.title, Market.platform_id,
                Market.probability_yes, Market.volume_24h,
                Market.resolution_time, Market.source_payload,
                Market.fetched_at,
            ))
        ):
            arbitrage_attempted += 1
            title_l = (market.title or "").lower()
            if any(token in title_l for token in excluded_tokens):
                arbitrage_excluded_by_keyword += 1
                continue
            if " i " in f" {title_l} " or " my " in f" {title_l} ":
                arbitrage_personal_market_skipped += 1
                continue
            platform_name = str(
                (market.source_payload or {}).get("platform") or platform_by_id.get(market.platform_id) or ""
            ).upper()
            if enabled_sources and platform_name in known_signal_sources and platform_name not in enabled_sources:
                continue
            is_manifold = platform_name == "MANIFOLD"
            min_liquidity = (
                self.settings.signal_arbitrage_min_liquidity_manifold
                if is_manifold
                else self.settings.signal_arbitrage_min_liquidity
            )
            liq = liquidity_by_market.get(market.id, 0.0)
            if liq < min_liquidity:
                arbitrage_low_liquidity_skipped += 1
                continue
            min_volume = (
                self.settings.signal_arbitrage_min_volume_24h_manifold
                if is_manifold
                else self.settings.signal_arbitrage_min_volume_24h
            )
            if (market.volume_24h or 0.0) < min_volume:
                arbitrage_low_volume_skipped += 1
                continue

            pair = last_two.get(market.id, [])
            if len(pair) < 2 or pair[0].probability_yes is None or pair[1].probability_yes is None:
                arbitrage_no_snapshot_skipped += 1
                continue

            signed_move = (pair[0].probability_yes or 0.0) - (pair[1].probability_yes or 0.0)
            move = abs(signed_move)
            midpoint_distance = abs((market.probability_yes or 0.0) - 0.5)
            if (
                move < self.settings.signal_mode_momentum_min_move
                and midpoint_distance > self.settings.signal_arbitrage_midpoint_band
            ):
                arbitrage_no_edge_skipped += 1
                continue

            mode = "momentum" if move >= self.settings.signal_mode_momentum_min_move else "uncertainty_liquid"
            mode_multiplier = 1.0 if mode == "momentum" else 0.6
            confidence = mode_multiplier * min(
                1.0,
                (0.45 * liq)
                + (0.35 * min(1.0, move / max(1e-6, self.settings.signal_mode_momentum_min_move)))
                + (0.2 * max(0.0, 1 - (midpoint_distance / 0.5))),
            )
            if mode == "uncertainty_liquid":
                confidence = min(confidence, self.settings.signal_mode_uncertainty_max_score)
            snapshot_age_hours = self._hours_since(pair[0].fetched_at)
            arbitrage_candidates.append(
                (market, confidence, move, midpoint_distance, mode, snapshot_age_hours, signed_move, platform_name)
            )

        arbitrage_candidates.sort(key=lambda x: x[1], reverse=True)
        max_candidates = max(1, int(self.settings.signal_arbitrage_max_candidates))
        manifold_quota = max(0, int(self.settings.signal_arbitrage_min_manifold_candidates))
        manifold_max = max(0, int(self.settings.signal_manifold_max_per_cycle))
        manifold_quota = min(manifold_quota, max_candidates)
        selected: list[tuple[Market, float, float, float, str, float, float, str]] = []
        selected_market_ids: set[int] = set()
        selected_manifold = 0
        if manifold_quota > 0:
            for cand in arbitrage_candidates:
                market = cand[0]
                platform_name = cand[7]
                if platform_name != "MANIFOLD":
                    continue
                if market.id in selected_market_ids:
                    continue
                if manifold_max > 0 and selected_manifold >= manifold_max:
                    break
                selected.append(cand)
                selected_market_ids.add(market.id)
                selected_manifold += 1
                if len(selected) >= manifold_quota:
                    break
        if len(selected) < max_candidates:
            for cand in arbitrage_candidates:
                market = cand[0]
                platform_name = cand[7]
                if market.id in selected_market_ids:
                    continue
                if platform_name == "MANIFOLD" and manifold_max > 0 and selected_manifold >= manifold_max:
                    continue
                selected.append(cand)
                selected_market_ids.add(market.id)
                if platform_name == "MANIFOLD":
                    selected_manifold += 1
                if len(selected) >= max_candidates:
                    break

        for market, confidence, move, midpoint_distance, mode, snapshot_age_hours, signed_move, _platform_name in selected:
            rr = rules_by_market.get(market.id)
            score_breakdown = self._score_breakdown(
                edge=min(1.0, move / 0.20) if mode == "momentum" else 0.3,
                liquidity=liquidity_by_market.get(market.id, 0.0),
                freshness=max(0.0, 1 - (snapshot_age_hours / 24.0)),
                confidence=confidence,
                risk_penalties=min(1.0, (rr or 0.0) * 0.6),
            )
            outcome = self._create_signal_if_not_recent(
                signal_type=SignalType.ARBITRAGE_CANDIDATE,
                market_id=market.id,
                related_market_id=None,
                title=f"Arbitrage candidate: {market.title}",
                summary=(
                    f"Liquidity={liquidity_by_market.get(market.id, 0.0):.3f}; "
                    f"recent_move={move:.3f}; "
                    f"distance_from_50={midpoint_distance:.3f}"
                ),
                confidence_score=confidence,
                liquidity_score=liquidity_by_market.get(market.id),
                rules_risk_score=rr,
                divergence_score=move,
                signal_mode=mode,
                score_breakdown_json=score_breakdown,
                execution_analysis=self.execution.simulate(
                    market=market,
                    confidence_score=confidence,
                    liquidity_score=liquidity_by_market.get(market.id),
                    recent_move=move,
                    signal_type=SignalType.ARBITRAGE_CANDIDATE,
                ),
                signal_direction="YES" if signed_move >= 0 else "NO",
                metadata_json={
                    "signal_mode": mode,
                    "recent_move": round(move, 4),
                    "signed_recent_move": round(signed_move, 4),
                    "distance_from_50": round(midpoint_distance, 4),
                    "snapshot_age_hours": round(snapshot_age_hours, 3),
                    "rules_risk_score": rr,
                },
            )
            if outcome == "created":
                created += 1
                arbitrage_created += 1

        tail_stats = self._generate_tail_signals(
            liquidity_by_market=liquidity_by_market,
            rules_by_market=rules_by_market,
            excluded_tokens=excluded_tokens,
            enabled_sources=enabled_sources,
            known_signal_sources=known_signal_sources,
            platform_by_id=platform_by_id,
        )
        created += int(tail_stats.get("tail_created", 0))
        tail_attempted = int(tail_stats.get("tail_attempted", 0))
        tail_created = int(tail_stats.get("tail_created", 0))
        tail_updated = int(tail_stats.get("tail_updated", 0))
        tail_out_of_prob_range = int(tail_stats.get("tail_out_of_prob_range", 0))
        tail_ambiguous_skipped = int(tail_stats.get("tail_ambiguous_skipped", 0))
        tail_below_mispricing = int(tail_stats.get("tail_below_mispricing", 0))
        tail_by_category_limit = int(tail_stats.get("tail_by_category_limit", 0))
        tail_breaker_blocked = int(tail_stats.get("tail_breaker_blocked", 0))
        tail_excluded_by_keyword = int(tail_stats.get("tail_excluded_by_keyword", 0))
        tail_source_filtered = int(tail_stats.get("tail_source_filtered", 0))

        self.db.commit()
        return {
            "signals_created": created,
            "stale_duplicate_divergence_removed": stale_removed,
            "debug": {
                "pairs_total": len(pairs),
                "duplicate_attempted": duplicate_attempted,
                "duplicate_cooldown_blocked": duplicate_cooldown_blocked,
                "duplicate_updated": duplicate_updated,
                "duplicate_low_liquidity_skipped": duplicate_low_liquidity_skipped,
                "divergence_attempted": divergence_attempted,
                "divergence_below_threshold": divergence_below_threshold,
                "divergence_cooldown_blocked": divergence_cooldown_blocked,
                "divergence_updated": divergence_updated,
                "divergence_low_liquidity_skipped": divergence_low_liquidity_skipped,
                "rules_rows_total": len(rules_rows),
                "rules_attempted": rules_attempted,
                "rules_below_threshold": rules_below_threshold,
                "rules_cooldown_blocked": rules_cooldown_blocked,
                "rules_updated": rules_updated,
                "rules_missing_text_high_liquidity": rules_missing_text_high_liquidity,
                "rules_excluded_by_keyword": rules_excluded_by_keyword,
                "rules_missing_daily_cap": missing_rules_daily_cap,
                "rules_missing_created_today_before_run": created_today_missing,
                "rules_missing_created_now": missing_rules_created_now,
                "rules_missing_candidates_total": len(fallback_rules_candidates),
                "arbitrage_attempted": arbitrage_attempted,
                "arbitrage_created": arbitrage_created,
                "arbitrage_low_liquidity_skipped": arbitrage_low_liquidity_skipped,
                "arbitrage_no_snapshot_skipped": arbitrage_no_snapshot_skipped,
                "arbitrage_no_edge_skipped": arbitrage_no_edge_skipped,
                "arbitrage_excluded_by_keyword": arbitrage_excluded_by_keyword,
                "arbitrage_low_volume_skipped": arbitrage_low_volume_skipped,
                "arbitrage_personal_market_skipped": arbitrage_personal_market_skipped,
                "tail_attempted": tail_attempted,
                "tail_created": tail_created,
                "tail_updated": tail_updated,
                "tail_out_of_prob_range": tail_out_of_prob_range,
                "tail_ambiguous_skipped": tail_ambiguous_skipped,
                "tail_below_mispricing": tail_below_mispricing,
                "tail_by_category_limit": tail_by_category_limit,
                "tail_breaker_blocked": tail_breaker_blocked,
                "tail_excluded_by_keyword": tail_excluded_by_keyword,
                "tail_source_filtered": tail_source_filtered,
            },
        }

    def run(self) -> dict[str, int]:
        # detect_duplicates() is O(N²) across all markets — runs via its own
        # scheduled task every 2 hours. run() reads the pre-computed pairs from DB.
        result: dict[str, int] = {}
        result.update(self.analyze_rules())
        result.update(self.detect_divergence())
        result.update(self.capture_divergence_research_samples())
        result.update(self.generate_signals())
        return result

    def capture_divergence_research_samples(self) -> dict[str, int]:
        now = datetime.now(UTC)
        max_samples = max(0, self.settings.signal_divergence_research_max_samples_per_run)
        min_similarity = float(self.settings.signal_divergence_research_min_similarity)
        min_diff = float(self.settings.signal_divergence_research_min_diff)
        max_diff = float(self.settings.signal_divergence_research_max_diff)
        min_volume_24h = float(self.settings.signal_divergence_research_min_volume_24h)
        min_pair_liquidity = float(self.settings.signal_divergence_research_min_pair_liquidity)
        min_prob = float(self.settings.signal_divergence_research_min_prob)
        max_prob = float(self.settings.signal_divergence_research_max_prob)
        cooldown_minutes = max(1, int(self.settings.signal_divergence_research_sample_cooldown_minutes))
        cooldown_cutoff = now - timedelta(minutes=cooldown_minutes)

        if max_samples == 0:
            return {
                "research_divergence_candidates": 0,
                "research_divergence_samples_created": 0,
                "research_divergence_skipped_cooldown": 0,
                "research_divergence_skipped_low_similarity": 0,
                "research_divergence_skipped_low_diff": 0,
                "research_divergence_skipped_same_platform": 0,
                "research_divergence_skipped_missing_market": 0,
                "research_divergence_skipped_missing_probability": 0,
                "research_divergence_fallback_candidates": 0,
                "research_divergence_fallback_created": 0,
                "research_divergence_skipped_low_volume": 0,
                "research_divergence_skipped_low_pair_liquidity": 0,
                "research_divergence_skipped_prob_bounds": 0,
                "research_divergence_skipped_too_large_diff": 0,
            }

        liquidity_by_market = {
            row.market_id: row.score for row in self.db.scalars(select(LiquidityAnalysis))
        }
        platform_by_id = {p.id: p.name for p in self.db.scalars(select(Platform))}
        snap_cutoff_research = datetime.now(UTC) - timedelta(days=7)
        latest_snapshot_prob_by_market: dict[int, float] = {}
        for snap in self.db.scalars(
            select(MarketSnapshot)
            .where(MarketSnapshot.fetched_at >= snap_cutoff_research)
            .order_by(MarketSnapshot.market_id.asc(), MarketSnapshot.fetched_at.desc())
        ):
            if snap.market_id in latest_snapshot_prob_by_market:
                continue
            if snap.probability_yes is None:
                continue
            latest_snapshot_prob_by_market[snap.market_id] = float(snap.probability_yes)

        candidates = list(
            self.db.scalars(
                select(DuplicatePairCandidate)
                .where(DuplicatePairCandidate.similarity_score >= min_similarity)
                .order_by(DuplicatePairCandidate.similarity_score.desc())
                .limit(max_samples * 20)
            )
        )

        created = 0
        skipped_cooldown = 0
        skipped_low_similarity = 0
        skipped_low_diff = 0
        skipped_same_platform = 0
        skipped_missing_market = 0
        skipped_missing_probability = 0
        used_snapshot_probability = 0
        fallback_candidates = 0
        fallback_created = 0
        category_fallback_candidates = 0
        category_fallback_created = 0
        skipped_low_volume = 0
        skipped_low_pair_liquidity = 0
        skipped_prob_bounds = 0
        skipped_too_large_diff = 0
        for candidate in candidates:
            if created >= max_samples:
                break
            if (candidate.similarity_score or 0.0) < min_similarity:
                skipped_low_similarity += 1
                continue

            market_a = self.db.get(Market, candidate.market_a_id)
            market_b = self.db.get(Market, candidate.market_b_id)
            if not market_a or not market_b:
                skipped_missing_market += 1
                continue
            if market_a.platform_id == market_b.platform_id:
                skipped_same_platform += 1
                continue
            prob_a = (
                float(market_a.probability_yes)
                if market_a.probability_yes is not None
                else latest_snapshot_prob_by_market.get(market_a.id)
            )
            prob_b = (
                float(market_b.probability_yes)
                if market_b.probability_yes is not None
                else latest_snapshot_prob_by_market.get(market_b.id)
            )
            if prob_a is None or prob_b is None:
                skipped_missing_probability += 1
                continue
            if market_a.probability_yes is None or market_b.probability_yes is None:
                used_snapshot_probability += 1
            if prob_a < min_prob or prob_a > max_prob or prob_b < min_prob or prob_b > max_prob:
                skipped_prob_bounds += 1
                continue

            liq_a = liquidity_by_market.get(market_a.id, 0.0) or 0.0
            liq_b = liquidity_by_market.get(market_b.id, 0.0) or 0.0
            pair_liquidity = min(liq_a, liq_b)
            if pair_liquidity < min_pair_liquidity:
                skipped_low_pair_liquidity += 1
                continue
            pair_volume = min(float(market_a.volume_24h or 0.0), float(market_b.volume_24h or 0.0))
            if pair_volume < min_volume_24h:
                skipped_low_volume += 1
                continue

            divergence = abs(prob_a - prob_b)
            if divergence < min_diff:
                skipped_low_diff += 1
                continue
            if divergence > max_diff:
                skipped_too_large_diff += 1
                continue

            existing = self.db.scalar(
                select(SignalHistory.id).where(
                    SignalHistory.signal_type == SignalType.DIVERGENCE,
                    SignalHistory.timestamp >= cooldown_cutoff,
                    or_(
                        and_(
                            SignalHistory.market_id == market_a.id,
                            SignalHistory.related_market_id == market_b.id,
                        ),
                        and_(
                            SignalHistory.market_id == market_b.id,
                            SignalHistory.related_market_id == market_a.id,
                        ),
                    ),
                )
            )
            if existing:
                skipped_cooldown += 1
                continue

            platform_a = platform_by_id.get(market_a.platform_id, f"platform_{market_a.platform_id}")
            platform_b = platform_by_id.get(market_b.platform_id, f"platform_{market_b.platform_id}")
            platform_label = f"{platform_a}|{platform_b}"[:64]
            self.db.add(
                SignalHistory(
                    signal_id=None,
                    signal_type=SignalType.DIVERGENCE,
                    timestamp=now,
                    timestamp_bucket=self._to_hour_bucket(now),
                    platform=platform_label,
                    source_tag="local",
                    market_id=market_a.id,
                    related_market_id=market_b.id,
                    probability_at_signal=prob_a,
                    related_market_probability=prob_b,
                    divergence=divergence,
                    signal_direction="YES" if float(prob_a) < float(prob_b) else "NO",
                    liquidity=pair_liquidity,
                    volume_24h=pair_volume,
                    simulated_trade={
                        "source": "duplicate_pair_candidate",
                        "candidate_stage": candidate.stage,
                        "similarity_score": round(float(candidate.similarity_score or 0.0), 3),
                    },
                )
            )
            created += 1

        if created < max_samples:
            self.db.flush()
            fallback = self._capture_divergence_research_samples_from_markets(
                now=now,
                min_diff=min_diff,
                max_diff=max_diff,
                min_volume_24h=min_volume_24h,
                min_pair_liquidity=min_pair_liquidity,
                min_prob=min_prob,
                max_prob=max_prob,
                max_samples=max_samples - created,
                cooldown_cutoff=cooldown_cutoff,
                liquidity_by_market=liquidity_by_market,
                platform_by_id=platform_by_id,
            )
            created += fallback["created"]
            fallback_candidates = fallback["candidates"]
            fallback_created = fallback["created"]
            skipped_cooldown += fallback["skipped_cooldown"]
            skipped_low_diff += fallback["skipped_low_diff"]
            skipped_same_platform += fallback["skipped_same_platform"]
            skipped_low_volume += fallback["skipped_low_volume"]
            skipped_low_pair_liquidity += fallback["skipped_low_pair_liquidity"]
            skipped_prob_bounds += fallback["skipped_prob_bounds"]
            skipped_too_large_diff += fallback["skipped_too_large_diff"]
            if created < max_samples and fallback_created == 0:
                category_fallback = self._capture_divergence_research_samples_from_category(
                    now=now,
                    min_diff=min_diff,
                    max_diff=max_diff,
                    min_volume_24h=min_volume_24h,
                    min_pair_liquidity=min_pair_liquidity,
                    min_prob=min_prob,
                    max_prob=max_prob,
                    max_samples=max_samples - created,
                    cooldown_cutoff=cooldown_cutoff,
                    liquidity_by_market=liquidity_by_market,
                    platform_by_id=platform_by_id,
                )
                created += category_fallback["created"]
                category_fallback_candidates = category_fallback["candidates"]
                category_fallback_created = category_fallback["created"]
                skipped_cooldown += category_fallback["skipped_cooldown"]
                skipped_low_diff += category_fallback["skipped_low_diff"]
                skipped_same_platform += category_fallback["skipped_same_platform"]
                skipped_low_volume += category_fallback["skipped_low_volume"]
                skipped_low_pair_liquidity += category_fallback["skipped_low_pair_liquidity"]
                skipped_prob_bounds += category_fallback["skipped_prob_bounds"]
                skipped_too_large_diff += category_fallback["skipped_too_large_diff"]

        self.db.commit()
        return {
            "research_divergence_candidates": len(candidates),
            "research_divergence_samples_created": created,
            "research_divergence_skipped_cooldown": skipped_cooldown,
            "research_divergence_skipped_low_similarity": skipped_low_similarity,
            "research_divergence_skipped_low_diff": skipped_low_diff,
            "research_divergence_skipped_same_platform": skipped_same_platform,
            "research_divergence_skipped_missing_market": skipped_missing_market,
            "research_divergence_skipped_missing_probability": skipped_missing_probability,
            "research_divergence_used_snapshot_probability": used_snapshot_probability,
            "research_divergence_fallback_candidates": fallback_candidates,
            "research_divergence_fallback_created": fallback_created,
            "research_divergence_category_fallback_candidates": category_fallback_candidates,
            "research_divergence_category_fallback_created": category_fallback_created,
            "research_divergence_skipped_low_volume": skipped_low_volume,
            "research_divergence_skipped_low_pair_liquidity": skipped_low_pair_liquidity,
            "research_divergence_skipped_prob_bounds": skipped_prob_bounds,
            "research_divergence_skipped_too_large_diff": skipped_too_large_diff,
        }

    def _capture_divergence_research_samples_from_markets(
        self,
        *,
        now: datetime,
        min_diff: float,
        max_diff: float,
        min_volume_24h: float,
        min_pair_liquidity: float,
        min_prob: float,
        max_prob: float,
        max_samples: int,
        cooldown_cutoff: datetime,
        liquidity_by_market: dict[int, float],
        platform_by_id: dict[int, str],
    ) -> dict[str, int]:
        if max_samples <= 0:
            return {
                "candidates": 0,
                "created": 0,
                "skipped_cooldown": 0,
                "skipped_low_diff": 0,
                "skipped_same_platform": 0,
                "skipped_low_volume": 0,
                "skipped_low_pair_liquidity": 0,
                "skipped_prob_bounds": 0,
                "skipped_too_large_diff": 0,
            }

        # Limit to recently updated markets + load only needed fields.
        _div_cutoff = datetime.now(UTC) - timedelta(hours=48)
        _div_limit = max(500, int(getattr(self.settings, "signal_divergence_research_max_markets", 5000)))
        markets = list(self.db.scalars(
            select(Market)
            .where(Market.probability_yes.is_not(None))
            .where(Market.fetched_at >= _div_cutoff)
            .options(load_only(Market.id, Market.title, Market.platform_id, Market.probability_yes,
                               Market.volume_24h, Market.fetched_at))
            .order_by(Market.fetched_at.desc())
            .limit(_div_limit)
        ))
        stopwords = {
            "will", "what", "when", "where", "which", "about", "with", "from", "that", "this", "have", "been",
            "into", "over", "under", "after", "before", "more", "than", "into", "year", "market",
        }
        tokenized: list[tuple[Market, set[str]]] = []
        for market in markets:
            tokens = {
                t
                for t in re.findall(r"[a-z0-9]+", (market.title or "").lower())
                if len(t) >= 4 and t not in stopwords
            }
            if not tokens:
                continue
            tokenized.append((market, tokens))

        # Build per-platform buckets — we only want cross-platform pairs, so
        # skip intra-platform combinations upfront instead of after comparison.
        buckets_by_platform: dict[str, dict[int, list[tuple[Market, set[str]]]]] = {}
        for market, tokens in tokenized:
            for token in list(tokens)[:6]:
                by_plat = buckets_by_platform.setdefault(token, {})
                by_plat.setdefault(market.platform_id, []).append((market, tokens))

        seen_pairs: set[tuple[int, int]] = set()
        candidates = 0
        created = 0
        skipped_cooldown = 0
        skipped_low_diff = 0
        skipped_same_platform = 0
        skipped_low_volume = 0
        skipped_low_pair_liquidity = 0
        skipped_prob_bounds = 0
        skipped_too_large_diff = 0
        min_shared_tokens = max(1, int(self.settings.signal_divergence_research_fallback_min_shared_tokens))
        min_jaccard = max(0.0, float(self.settings.signal_divergence_research_fallback_min_jaccard))
        for token, by_plat in buckets_by_platform.items():
            platform_ids = list(by_plat.keys())
            if len(platform_ids) < 2:
                # All markets for this token are on the same platform — nothing to pair.
                skipped_same_platform += sum(
                    len(v) * (len(v) - 1) // 2 for v in by_plat.values() if len(v) >= 2
                )
                continue
            # Only generate cross-platform pairs — same-platform divergence is not meaningful.
            for pi in range(len(platform_ids)):
                for pj in range(pi + 1, len(platform_ids)):
                    items_a = by_plat[platform_ids[pi]][:80]
                    items_b = by_plat[platform_ids[pj]][:80]
                    for market_a, tokens_a in items_a:
                        for market_b, tokens_b in items_b:
                            pair_key = tuple(sorted((market_a.id, market_b.id)))
                            if pair_key in seen_pairs:
                                continue
                            seen_pairs.add(pair_key)
                            shared_tokens = len(tokens_a & tokens_b)
                            if shared_tokens < min_shared_tokens:
                                continue
                            union = len(tokens_a | tokens_b) or 1
                            jaccard = shared_tokens / union
                            if jaccard < min_jaccard:
                                continue
                            prob_a = float(market_a.probability_yes or 0.0)
                            prob_b = float(market_b.probability_yes or 0.0)
                            if prob_a < min_prob or prob_a > max_prob or prob_b < min_prob or prob_b > max_prob:
                                skipped_prob_bounds += 1
                                continue
                            pair_liquidity = min(
                                liquidity_by_market.get(market_a.id, 0.0) or 0.0,
                                liquidity_by_market.get(market_b.id, 0.0) or 0.0,
                            )
                            if pair_liquidity < min_pair_liquidity:
                                skipped_low_pair_liquidity += 1
                                continue
                            pair_volume = min(float(market_a.volume_24h or 0.0), float(market_b.volume_24h or 0.0))
                            if pair_volume < min_volume_24h:
                                skipped_low_volume += 1
                                continue
                            divergence = abs(prob_a - prob_b)
                            if divergence < min_diff:
                                skipped_low_diff += 1
                                continue
                            if divergence > max_diff:
                                skipped_too_large_diff += 1
                                continue
                            candidates += 1
                            existing = self.db.scalar(
                                select(SignalHistory.id).where(
                                    SignalHistory.signal_type == SignalType.DIVERGENCE,
                                    SignalHistory.timestamp >= cooldown_cutoff,
                                    or_(
                                        and_(
                                            SignalHistory.market_id == market_a.id,
                                            SignalHistory.related_market_id == market_b.id,
                                        ),
                                        and_(
                                            SignalHistory.market_id == market_b.id,
                                            SignalHistory.related_market_id == market_a.id,
                                        ),
                                    ),
                                )
                            )
                            if existing:
                                skipped_cooldown += 1
                                continue
                            platform_a = platform_by_id.get(market_a.platform_id, f"platform_{market_a.platform_id}")
                            platform_b = platform_by_id.get(market_b.platform_id, f"platform_{market_b.platform_id}")
                            self.db.add(
                                SignalHistory(
                                    signal_id=None,
                                    signal_type=SignalType.DIVERGENCE,
                                    timestamp=now,
                                    timestamp_bucket=self._to_hour_bucket(now),
                                    platform=f"{platform_a}|{platform_b}"[:64],
                                    source_tag="local",
                                    market_id=market_a.id,
                                    related_market_id=market_b.id,
                                    probability_at_signal=prob_a,
                                    related_market_probability=prob_b,
                                    divergence=divergence,
                                    signal_direction="YES" if float(prob_a) < float(prob_b) else "NO",
                                    liquidity=pair_liquidity,
                                    volume_24h=pair_volume,
                                    simulated_trade={
                                        "source": "cross_platform_title_overlap",
                                        "shared_tokens": shared_tokens,
                                        "token_jaccard": round(jaccard, 4),
                                    },
                                )
                            )
                            created += 1
                            if created >= max_samples:
                                return {
                                    "candidates": candidates,
                                    "created": created,
                                    "skipped_cooldown": skipped_cooldown,
                                    "skipped_low_diff": skipped_low_diff,
                                    "skipped_same_platform": skipped_same_platform,
                                    "skipped_low_volume": skipped_low_volume,
                                    "skipped_low_pair_liquidity": skipped_low_pair_liquidity,
                                    "skipped_prob_bounds": skipped_prob_bounds,
                                    "skipped_too_large_diff": skipped_too_large_diff,
                                }

        return {
            "candidates": candidates,
            "created": created,
            "skipped_cooldown": skipped_cooldown,
            "skipped_low_diff": skipped_low_diff,
            "skipped_same_platform": skipped_same_platform,
            "skipped_low_volume": skipped_low_volume,
            "skipped_low_pair_liquidity": skipped_low_pair_liquidity,
            "skipped_prob_bounds": skipped_prob_bounds,
            "skipped_too_large_diff": skipped_too_large_diff,
        }

    def _capture_divergence_research_samples_from_category(
        self,
        *,
        now: datetime,
        min_diff: float,
        max_diff: float,
        min_volume_24h: float,
        min_pair_liquidity: float,
        min_prob: float,
        max_prob: float,
        max_samples: int,
        cooldown_cutoff: datetime,
        liquidity_by_market: dict[int, float],
        platform_by_id: dict[int, str],
    ) -> dict[str, int]:
        if max_samples <= 0:
            return {
                "candidates": 0,
                "created": 0,
                "skipped_cooldown": 0,
                "skipped_low_diff": 0,
                "skipped_same_platform": 0,
                "skipped_low_volume": 0,
                "skipped_low_pair_liquidity": 0,
                "skipped_prob_bounds": 0,
                "skipped_too_large_diff": 0,
            }

        markets = list(
            self.db.scalars(
                select(Market).where(
                    Market.probability_yes.is_not(None),
                    Market.category.is_not(None),
                    Market.category != "",
                )
            )
        )
        by_category: dict[str, list[Market]] = {}
        for market in markets:
            key = (market.category or "").strip().lower()
            if not key:
                continue
            by_category.setdefault(key, []).append(market)

        candidates = 0
        created = 0
        skipped_cooldown = 0
        skipped_low_diff = 0
        skipped_same_platform = 0
        skipped_low_volume = 0
        skipped_low_pair_liquidity = 0
        skipped_prob_bounds = 0
        skipped_too_large_diff = 0
        for items in by_category.values():
            if len(items) < 2:
                continue
            for i in range(len(items)):
                if created >= max_samples:
                    break
                for j in range(i + 1, len(items)):
                    if created >= max_samples:
                        break
                    market_a = items[i]
                    market_b = items[j]
                    if market_a.platform_id == market_b.platform_id:
                        skipped_same_platform += 1
                        continue
                    prob_a = float(market_a.probability_yes or 0.0)
                    prob_b = float(market_b.probability_yes or 0.0)
                    if prob_a < min_prob or prob_a > max_prob or prob_b < min_prob or prob_b > max_prob:
                        skipped_prob_bounds += 1
                        continue

                    pair_liquidity = min(
                        liquidity_by_market.get(market_a.id, 0.0) or 0.0,
                        liquidity_by_market.get(market_b.id, 0.0) or 0.0,
                    )
                    if pair_liquidity < min_pair_liquidity:
                        skipped_low_pair_liquidity += 1
                        continue
                    pair_volume = min(float(market_a.volume_24h or 0.0), float(market_b.volume_24h or 0.0))
                    if pair_volume < min_volume_24h:
                        skipped_low_volume += 1
                        continue
                    divergence = abs(prob_a - prob_b)
                    if divergence < min_diff:
                        skipped_low_diff += 1
                        continue
                    if divergence > max_diff:
                        skipped_too_large_diff += 1
                        continue
                    candidates += 1

                    existing = self.db.scalar(
                        select(SignalHistory.id).where(
                            SignalHistory.signal_type == SignalType.DIVERGENCE,
                            SignalHistory.timestamp >= cooldown_cutoff,
                            or_(
                                and_(
                                    SignalHistory.market_id == market_a.id,
                                    SignalHistory.related_market_id == market_b.id,
                                ),
                                and_(
                                    SignalHistory.market_id == market_b.id,
                                    SignalHistory.related_market_id == market_a.id,
                                ),
                            ),
                        )
                    )
                    if existing:
                        skipped_cooldown += 1
                        continue

                    platform_a = platform_by_id.get(market_a.platform_id, f"platform_{market_a.platform_id}")
                    platform_b = platform_by_id.get(market_b.platform_id, f"platform_{market_b.platform_id}")
                    self.db.add(
                SignalHistory(
                    signal_id=None,
                    signal_type=SignalType.DIVERGENCE,
                    timestamp=now,
                    timestamp_bucket=self._to_hour_bucket(now),
                    platform=f"{platform_a}|{platform_b}"[:64],
                    source_tag="local",
                    market_id=market_a.id,
                    related_market_id=market_b.id,
                            probability_at_signal=prob_a,
                            related_market_probability=prob_b,
                            divergence=divergence,
                            signal_direction="YES" if float(prob_a) < float(prob_b) else "NO",
                            liquidity=pair_liquidity,
                            volume_24h=pair_volume,
                            simulated_trade={
                                "source": "cross_platform_category_fallback",
                                "category": market_a.category,
                            },
                        )
                    )
                    created += 1

        return {
            "candidates": candidates,
            "created": created,
            "skipped_cooldown": skipped_cooldown,
            "skipped_low_diff": skipped_low_diff,
            "skipped_same_platform": skipped_same_platform,
            "skipped_low_volume": skipped_low_volume,
            "skipped_low_pair_liquidity": skipped_low_pair_liquidity,
            "skipped_prob_bounds": skipped_prob_bounds,
            "skipped_too_large_diff": skipped_too_large_diff,
        }

    def _generate_tail_signals(
        self,
        *,
        liquidity_by_market: dict[int, float],
        rules_by_market: dict[int, float],
        excluded_tokens: list[str],
        enabled_sources: set[str],
        known_signal_sources: set[str],
        platform_by_id: dict[int, str],
    ) -> dict[str, int]:
        if not bool(self.settings.signal_tail_enabled):
            return {
                "tail_attempted": 0,
                "tail_created": 0,
                "tail_updated": 0,
                "tail_out_of_prob_range": 0,
                "tail_ambiguous_skipped": 0,
                "tail_below_mispricing": 0,
                "tail_by_category_limit": 0,
                "tail_breaker_blocked": 0,
                "tail_excluded_by_keyword": 0,
                "tail_source_filtered": 0,
            }

        created = 0
        updated = 0
        attempted = 0
        out_of_prob_range = 0
        ambiguous_skipped = 0
        below_mispricing = 0
        by_category_limit = 0
        breaker_blocked = 0
        excluded_by_keyword = 0
        source_filtered = 0
        unknown_category_skipped = 0
        max_candidates = max(1, int(self.settings.signal_tail_max_candidates))
        min_mispricing = max(0.0, float(self.settings.signal_tail_min_mispricing_ratio))
        min_our_prob = max(0.0, float(self.settings.signal_tail_min_our_prob))
        max_market_prob = min(1.0, max(0.0, float(self.settings.signal_tail_max_prob)))
        min_koef = max(1.0, float(self.settings.signal_tail_min_koef))
        max_koef = max(min_koef, float(self.settings.signal_tail_max_koef))
        max_days = max(1, int(self.settings.signal_tail_max_days_to_resolution))
        min_volume = max(0.0, float(self.settings.signal_tail_min_volume_usd))
        ref_balance = max(1.0, float(self.settings.signal_tail_reference_balance_usd))
        raw_notional = ref_balance * max(0.0, float(self.settings.signal_tail_notional_pct))
        # Hard risk cap: single tail position cannot exceed 5% reference balance.
        notional_usd = min(max(0.05, raw_notional), ref_balance * 0.05)

        blocked, _reason = check_tail_circuit_breaker(
            self.db,
            settings=self.settings,
            balance_usd=ref_balance,
            api_status={"degraded": False},
        )
        if blocked:
            return {
                "tail_attempted": 0,
                "tail_created": 0,
                "tail_updated": 0,
                "tail_out_of_prob_range": 0,
                "tail_ambiguous_skipped": 0,
                "tail_below_mispricing": 0,
                "tail_by_category_limit": 0,
                "tail_breaker_blocked": max_candidates,
                "tail_excluded_by_keyword": 0,
                "tail_source_filtered": 0,
            }

        estimator = BaseRateEstimator(db=self.db, settings=self.settings)
        tail_markets = list(
            self.db.scalars(
                select(Market)
                .where(Market.probability_yes.is_not(None))
                .where(Market.probability_yes >= float(self.settings.signal_tail_min_prob))
                .where(Market.probability_yes <= float(self.settings.signal_tail_max_prob))
                .where(
                    func.coalesce(
                        func.nullif(Market.volume_24h, 0),
                        func.nullif(Market.notional_value_dollars, 0),
                        func.nullif(Market.liquidity_value, 0),
                        0.0,
                    )
                    >= min_volume
                )
                .where(Market.resolution_time.is_not(None))
                .where(Market.resolution_time <= (datetime.now(UTC) + timedelta(days=max_days)))
                .where(
                    or_(
                        Market.status.is_(None),
                        ~Market.status.in_(["resolved", "closed", "settled", "final", "ended"]),
                    )
                )
                .order_by(Market.probability_yes.asc(), Market.fetched_at.desc())
                .limit(max_candidates * 20)
            )
        )

        for market in tail_markets:
            if created >= max_candidates:
                break
            attempted += 1

            title_l = (market.title or "").lower()
            if any(token in title_l for token in excluded_tokens):
                excluded_by_keyword += 1
                continue

            platform_name = str(
                (market.source_payload or {}).get("platform") or platform_by_id.get(market.platform_id) or ""
            ).upper()
            if enabled_sources and platform_name in known_signal_sources and platform_name not in enabled_sources:
                source_filtered += 1
                continue

            tail = classify_tail_event(market, settings=self.settings)
            if tail is None:
                out_of_prob_range += 1
                continue
            if not bool(tail.get("eligible")):
                ambiguous_skipped += 1
                continue

            tail_category = str(tail.get("tail_category") or "unknown")
            tail_strategy = str(tail.get("tail_strategy") or "unknown")
            if tail_category not in {
                "crypto",
                "crypto_level",
                "price_target",
                "geopolitical_event",
                "election",
                "sports_match",
                "earnings_surprise",
                "regulatory",
                "company_valuation",
            }:
                unknown_category_skipped += 1
                continue
            base_rate = estimator.estimate(market, tail_category=tail_category, strategy=tail_strategy)
            market_prob = float(tail.get("market_prob") or market.probability_yes or 0.5)
            our_prob = float(base_rate.get("our_prob") or market_prob)
            koef = 1.0 / max(1e-6, market_prob)
            if market_prob > max_market_prob:
                below_mispricing += 1
                continue
            if koef < min_koef or koef > max_koef:
                below_mispricing += 1
                continue
            if our_prob < min_our_prob:
                below_mispricing += 1
                continue
            # For YES tail bets we need our_prob > market_prob (market underprices the event).
            if our_prob <= market_prob:
                below_mispricing += 1
                continue
            mispricing_ratio = tail_mispricing_ratio(market_prob=market_prob, our_prob=our_prob)
            if (our_prob <= (market_prob * min_mispricing)) or (mispricing_ratio < min_mispricing):
                below_mispricing += 1
                continue

            allowed, _cat_reason = can_open_tail_by_category(
                self.db,
                settings=self.settings,
                category=tail_category,
                notional_usd=notional_usd,
                balance_usd=ref_balance,
            )
            if not allowed:
                by_category_limit += 1
                continue

            signal_direction = "YES"
            source_name = str(base_rate.get("source") or "")
            if source_name.startswith("external_") or source_name.startswith("historical_"):
                tail_variation = "tail_base_rate"
            elif tail_strategy == "llm_evaluate":
                tail_variation = "tail_narrative_fade"
            else:
                tail_variation = "tail_stability"

            liq_score = float(liquidity_by_market.get(market.id, 0.0) or 0.0)
            rr_score = float(rules_by_market.get(market.id, 0.0) or 0.0)
            score_breakdown = self._score_breakdown(
                edge=min(1.0, mispricing_ratio / 5.0),
                liquidity=liq_score,
                freshness=max(0.0, 1 - (self._hours_since(market.fetched_at) / 24.0)),
                confidence=float(base_rate.get("confidence") or 0.0),
                risk_penalties=min(1.0, rr_score * 0.5),
            )
            metadata = {
                "signal_mode": tail_variation,
                "tail_category": tail_category,
                "tail_strategy": tail_strategy,
                "tail_market_prob": round(market_prob, 6),
                "tail_our_prob": round(our_prob, 6),
                "tail_koef": round(koef, 6),
                "tail_days_to_resolution": float(tail.get("days_to_resolution") or 0.0),
                "tail_mispricing_ratio": round(mispricing_ratio, 6),
                "tail_base_rate_source": source_name,
                "tail_base_rate_reasoning": str(base_rate.get("reasoning") or ""),
                "tail_direction_rule": "deterministic",
                "reason_codes": [
                    *(tail.get("reason_codes") or []),
                    f"tail_variation:{tail_variation}",
                    f"tail_source:{source_name}",
                ],
            }
            outcome = self._create_signal_if_not_recent(
                signal_type=SignalType.TAIL_EVENT_CANDIDATE,
                market_id=market.id,
                related_market_id=None,
                title=f"Tail event candidate: {market.title}",
                summary=(
                    f"tail_category={tail_category}; strategy={tail_strategy}; "
                    f"market_prob={market_prob:.4f}; our_prob={our_prob:.4f}; "
                    f"mispricing_ratio={mispricing_ratio:.2f}"
                ),
                confidence_score=float(base_rate.get("confidence") or 0.0),
                liquidity_score=liq_score,
                rules_risk_score=rr_score,
                divergence_score=mispricing_ratio,
                signal_mode=tail_variation,
                score_breakdown_json=score_breakdown,
                execution_analysis=self.execution.simulate(
                    market=market,
                    confidence_score=float(base_rate.get("confidence") or 0.0),
                    liquidity_score=liq_score,
                    recent_move=None,
                    signal_type=SignalType.TAIL_EVENT_CANDIDATE,
                ),
                signal_direction=signal_direction,
                metadata_json=metadata,
            )
            if outcome == "created":
                created += 1
            elif outcome == "updated":
                updated += 1
            else:
                breaker_blocked += 1

        return {
            "tail_attempted": attempted,
            "tail_created": created,
            "tail_updated": updated,
            "tail_out_of_prob_range": out_of_prob_range,
            "tail_ambiguous_skipped": ambiguous_skipped,
            "tail_below_mispricing": below_mispricing,
            "tail_by_category_limit": by_category_limit,
            "tail_breaker_blocked": breaker_blocked,
            "tail_excluded_by_keyword": excluded_by_keyword,
            "tail_source_filtered": source_filtered,
            "tail_unknown_category_skipped": unknown_category_skipped,
        }

    def _create_signal_if_not_recent(
        self,
        *,
        signal_type: SignalType,
        market_id: int,
        related_market_id: int | None,
        title: str,
        summary: str,
        confidence_score: float | None,
        liquidity_score: float | None = None,
        rules_risk_score: float | None = None,
        divergence_score: float | None = None,
        signal_mode: str | None = None,
        score_breakdown_json: dict | None = None,
        drop_reason: str | None = None,
        execution_analysis: dict | None = None,
        metadata_json: dict | None = None,
        signal_direction: str | None = None,
    ) -> str:
        run_now = datetime.now(UTC)
        existing = self.db.scalar(
            select(Signal).where(
                Signal.signal_type == signal_type,
                Signal.market_id == market_id,
                Signal.related_market_id.is_(related_market_id) if related_market_id is None else Signal.related_market_id == related_market_id,
            )
        )
        if existing:
            existing.title = title
            existing.summary = summary
            existing.confidence_score = confidence_score
            existing.liquidity_score = liquidity_score
            existing.rules_risk_score = rules_risk_score
            existing.divergence_score = divergence_score
            existing.signal_mode = signal_mode
            existing.score_breakdown_json = score_breakdown_json
            existing.drop_reason = drop_reason
            existing.execution_analysis = execution_analysis
            existing.metadata_json = metadata_json
            existing.signal_direction = signal_direction
            existing.updated_at = run_now
            return "updated"
        created_signal = Signal(
            signal_type=signal_type,
            market_id=market_id,
            related_market_id=related_market_id,
            title=title,
            summary=summary,
            confidence_score=confidence_score,
            liquidity_score=liquidity_score,
            rules_risk_score=rules_risk_score,
            divergence_score=divergence_score,
            signal_mode=signal_mode,
            score_breakdown_json=score_breakdown_json,
            drop_reason=drop_reason,
            execution_analysis=execution_analysis,
            metadata_json=metadata_json,
            signal_direction=signal_direction,
            updated_at=run_now,
        )
        self.db.add(created_signal)
        self.db.flush()
        self._capture_signal_history(created_signal, run_now)
        self._increment_generation_stat(signal_type, signal_mode, run_now.date(), delta=1)
        return "created"

    def _capture_signal_history(self, signal: Signal, ts: datetime) -> None:
        market = self.db.get(Market, signal.market_id)
        if not market:
            return
        related_market = self.db.get(Market, signal.related_market_id) if signal.related_market_id else None
        platform_name = None
        platform = self.db.get(Platform, market.platform_id)
        if platform:
            platform_name = platform.name
        self.db.add(
            SignalHistory(
                signal_id=signal.id,
                signal_type=signal.signal_type,
                timestamp=ts,
                timestamp_bucket=self._to_hour_bucket(ts),
                platform=platform_name,
                source_tag="local",
                market_id=signal.market_id,
                related_market_id=signal.related_market_id,
                probability_at_signal=market.probability_yes,
                related_market_probability=related_market.probability_yes if related_market else None,
                divergence=signal.divergence_score,
                liquidity=signal.liquidity_score,
                volume_24h=market.volume_24h,
                signal_direction=signal.signal_direction,
                simulated_trade=signal.execution_analysis,
            )
        )

    @staticmethod
    def _to_hour_bucket(ts: datetime) -> datetime:
        ts_utc = ts.astimezone(UTC) if ts.tzinfo else ts.replace(tzinfo=UTC)
        return ts_utc.replace(minute=0, second=0, microsecond=0)

    @staticmethod
    def _pair_liquidity_score(market_a_id: int, market_b_id: int, liquidity_by_market: dict[int, float]) -> float:
        liq_a = liquidity_by_market.get(market_a_id, 0.0) or 0.0
        liq_b = liquidity_by_market.get(market_b_id, 0.0) or 0.0
        return min(liq_a, liq_b)

    @staticmethod
    def _hours_since(ts: datetime | None) -> float:
        if ts is None:
            return 24.0
        ref = ts.astimezone(UTC) if ts.tzinfo else ts.replace(tzinfo=UTC)
        return max(0.0, (datetime.now(UTC) - ref).total_seconds() / 3600.0)

    @staticmethod
    def _score_breakdown(
        *,
        edge: float,
        liquidity: float,
        freshness: float,
        confidence: float,
        execution_safety: float | None = None,
        risk_penalties: float,
    ) -> dict:
        exec_safety = float(execution_safety if execution_safety is not None else confidence)
        score_total = (
            (0.35 * edge)
            + (0.25 * liquidity)
            + (0.20 * exec_safety)
            + (0.10 * freshness)
            + (0.10 * confidence)
            - risk_penalties
        )
        return {
            "edge": round(edge, 4),
            "liquidity": round(liquidity, 4),
            "execution_safety": round(exec_safety, 4),
            "freshness": round(freshness, 4),
            "confidence": round(confidence, 4),
            "risk_penalties": round(risk_penalties, 4),
            "score_total": round(score_total, 4),
        }

    def _generated_today_count(self, signal_type: SignalType, signal_mode: str | None, day: date) -> int:
        mode_key = signal_mode or ""
        row = self.db.scalar(
            select(SignalGenerationStats).where(
                SignalGenerationStats.date == day,
                SignalGenerationStats.signal_type == signal_type,
                SignalGenerationStats.signal_mode == mode_key,
            )
        )
        return int(row.count) if row else 0

    def _increment_generation_stat(
        self, signal_type: SignalType, signal_mode: str | None, day: date, delta: int = 1
    ) -> None:
        if delta <= 0:
            return
        mode_key = signal_mode or ""
        now = datetime.now(UTC)
        stmt = pg_insert(SignalGenerationStats).values(
            date=day,
            signal_type=signal_type,
            signal_mode=mode_key,
            count=delta,
            updated_at=now,
        )
        self.db.execute(
            stmt.on_conflict_do_update(
                constraint="uq_signal_generation_stats_key",
                set_={
                    "count": SignalGenerationStats.count + delta,
                    "updated_at": now,
                },
            )
        )
