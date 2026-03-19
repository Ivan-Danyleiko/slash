from datetime import timedelta
from math import log
import re

from rapidfuzz.fuzz import ratio, token_set_ratio

from app.core.config import Settings, get_settings
from app.models.models import Market
from app.utils.text import normalize_title


class DuplicateDetector:
    NOISE_WORDS = {
        "will",
        "be",
        "the",
        "a",
        "an",
        "by",
        "before",
        "after",
        "end",
        "of",
        "who",
        "win",
        "wins",
        "won",
        "majority",
        "party",
        "election",
        "elections",
        "presidential",
        "senate",
        "house",
        "control",
        "close",
        "above",
        "below",
        "at",
        "in",
        "on",
    }
    GENERIC_TOPIC_WORDS = {
        "military",
        "conflict",
        "direct",
        "engage",
        "significant",
        "forces",
        "war",
        "global",
        "world",
        "question",
        "market",
        "event",
        "news",
    }
    GEO_WORDS = {
        "usa",
        "us",
        "uk",
        "eu",
        "china",
        "russia",
        "ukraine",
        "france",
        "germany",
        "poland",
        "italy",
        "spain",
        "israel",
        "iran",
        "india",
        "pakistan",
        "taiwan",
        "japan",
        "korea",
        "north",
        "south",
        "canada",
        "mexico",
        "brazil",
        "argentina",
        "turkey",
        "syria",
        "iraq",
        "afghanistan",
        "philippines",
        "vietnam",
        "thailand",
        "australia",
    }
    ASSET_WORDS = {
        "btc",
        "eth",
        "sol",
        "oil",
        "crude",
        "wti",
        "gold",
        "silver",
        "nasdaq",
        "sp500",
        "s&p",
        "spy",
        "dxy",
        "treasury",
        "bond",
        "bonds",
    }
    TOKEN_ALIASES = {
        "bitcoin": "btc",
        "ethereum": "eth",
        "solana": "sol",
        "gold": "gold",
        "silver": "silver",
        "crude": "oil",
        "wti": "oil",
        "nasdaq": "nasdaq",
        "spy": "sp500",
        "trump": "donald_trump",
        "donald": "donald_trump",
        "biden": "joe_biden",
        "joe": "joe_biden",
        "republican": "gop",
        "republicans": "gop",
        "democrat": "dem",
        "democrats": "dem",
        "us": "usa",
    }
    PHRASE_ALIASES = (
        (re.compile(r"\bs\s*&\s*p\s*500\b"), "sp500"),
        (re.compile(r"\bsp\s*500\b"), "sp500"),
        (re.compile(r"\bunited states\b"), "usa"),
        (re.compile(r"\bu\.?s\.?a?\.?\b"), "usa"),
        (re.compile(r"\bnew york\b"), "ny"),
        (re.compile(r"\bwhite house\b"), "us_executive"),
    )
    MONTH_WORDS = {
        "jan",
        "january",
        "feb",
        "february",
        "mar",
        "march",
        "apr",
        "april",
        "may",
        "jun",
        "june",
        "jul",
        "july",
        "aug",
        "august",
        "sep",
        "sept",
        "september",
        "oct",
        "october",
        "nov",
        "november",
        "dec",
        "december",
    }
    OFFICE_WORDS = {
        "president",
        "senate",
        "senator",
        "house",
        "governor",
        "mayor",
        "prime",
        "minister",
        "parliament",
        "court",
        "supreme",
    }
    US_STATE_WORDS = {
        "alaska",
        "california",
        "texas",
        "florida",
        "newyork",
        "ohio",
        "georgia",
        "pennsylvania",
        "michigan",
        "wisconsin",
        "arizona",
        "nevada",
    }
    PERSON_ALIASES = {
        "donald_trump": "person:donald_trump",
        "joe_biden": "person:joe_biden",
        "zelensky": "person:zelensky",
        "putin": "person:putin",
        "netanyahu": "person:netanyahu",
        "musk": "person:musk",
        "samaltman": "person:samaltman",
    }

    def __init__(self, settings: Settings | None = None, *, profile: str = "strict") -> None:
        settings = settings or get_settings()
        self.resolution_window_days = settings.signal_duplicate_resolution_window_days
        self.min_overlap = settings.signal_duplicate_min_overlap
        self.min_jaccard = settings.signal_duplicate_min_jaccard
        self.min_weighted_overlap = settings.signal_duplicate_min_weighted_overlap
        self.anchor_idf = settings.signal_duplicate_anchor_idf
        self.broad_relaxed_fuzzy_min = float(getattr(settings, "signal_duplicate_broad_relaxed_fuzzy_min", 88.0))
        self.profile = profile

    @classmethod
    def with_profile(cls, settings: Settings | None = None, profile: str = "strict") -> "DuplicateDetector":
        detector = cls(settings=settings, profile=profile)
        if profile == "balanced":
            detector.min_overlap = max(1, detector.min_overlap - 1)
            detector.min_jaccard = max(0.2, detector.min_jaccard * 0.8)
            detector.min_weighted_overlap = max(3.0, detector.min_weighted_overlap * 0.75)
            detector.anchor_idf = max(2.5, detector.anchor_idf * 0.8)
        elif profile == "aggressive":
            detector.min_overlap = 1
            detector.min_jaccard = max(0.05, detector.min_jaccard * 0.25)
            detector.min_weighted_overlap = max(1.0, detector.min_weighted_overlap * 0.4)
            detector.anchor_idf = max(1.8, detector.anchor_idf * 0.65)
        return detector

    def _canonical_text(self, title: str) -> str:
        text = normalize_title(title)
        for pattern, replacement in self.PHRASE_ALIASES:
            text = pattern.sub(replacement, text)
        return text

    def _compact_title(self, title: str) -> str:
        base = self._canonical_text(title)
        tokens = [token for token in base.split() if token not in self.NOISE_WORDS]
        return " ".join(tokens)

    def _meaningful_tokens(self, title: str) -> set[str]:
        compact = self._compact_title(title)
        mapped: set[str] = set()
        for token in compact.split():
            canon = self.TOKEN_ALIASES.get(token, token)
            if len(canon) >= 3:
                mapped.add(canon)
        return mapped

    def _title_quality_ok(self, title: str) -> bool:
        tokens = self._meaningful_tokens(title)
        return len(tokens) >= 3

    @staticmethod
    def _extract_years(text: str) -> set[int]:
        years: set[int] = set()
        for m in re.findall(r"\b(19\d{2}|20\d{2})\b", text):
            try:
                years.add(int(m))
            except ValueError:
                continue
        return years

    def _extract_date_markers(self, text: str) -> set[str]:
        markers: set[str] = set()
        for year in self._extract_years(text):
            markers.add(f"y:{year}")
        for q, year in re.findall(r"\b(q[1-4])\s*(20\d{2})\b", text):
            markers.add(f"{year}{q.lower()}")
        tokens = text.split()
        for idx, token in enumerate(tokens[:-1]):
            if token in self.MONTH_WORDS and re.fullmatch(r"20\d{2}", tokens[idx + 1]):
                markers.add(f"m:{token[:3]}-{tokens[idx + 1]}")
        return markers

    def _extract_assets(self, title: str) -> set[str]:
        base = self._canonical_text(title)
        tokens = set(base.split())
        canonical_tokens = {self.TOKEN_ALIASES.get(token, token) for token in tokens}
        out = canonical_tokens & self.ASSET_WORDS
        if "s" in tokens and "&" in tokens and "p" in tokens:
            out.add("sp500")
        return out

    def _extract_entities(self, title: str) -> set[str]:
        tokens = self._meaningful_tokens(title)
        out: set[str] = set()
        for token in tokens:
            person = self.PERSON_ALIASES.get(token)
            if person:
                out.add(person)
            if token in self.OFFICE_WORDS:
                out.add(f"office:{token}")
            if token in self.US_STATE_WORDS:
                out.add(f"state:{token}")
            if token in self.GEO_WORDS:
                out.add(f"geo:{token}")
        return out

    def _comparable(self, a: Market, b: Market) -> bool:
        if a.platform_id == b.platform_id:
            return False
        if not self._title_quality_ok(a.title) or not self._title_quality_ok(b.title):
            return False
        # Category labels are noisy across platforms; do not hard-filter by category.
        if a.resolution_time and b.resolution_time:
            return abs(a.resolution_time - b.resolution_time) <= timedelta(days=self.resolution_window_days)
        return True

    def evaluate_pair(self, a: Market, b: Market, threshold: float) -> tuple[bool, float, str, str | None]:
        """
        Return:
        - pass_strict: bool
        - similarity: float
        - explanation: str
        - drop_reason: str | None
        """
        if not self._comparable(a, b):
            return False, 0.0, "not comparable", "not_comparable"

        norm_a = self._canonical_text(a.title)
        norm_b = self._canonical_text(b.title)
        compact_a = self._compact_title(a.title)
        compact_b = self._compact_title(b.title)
        tok_a = self._meaningful_tokens(a.title)
        tok_b = self._meaningful_tokens(b.title)
        overlap_tokens = tok_a & tok_b
        overlap = len(overlap_tokens)
        if overlap < self.min_overlap:
            return False, 0.0, f"shared_meaningful_tokens={overlap}", "low_overlap"

        # Local idf approximation for pair-level strict checks.
        pair_tokens = tok_a | tok_b
        idf = {t: log((1 + 2) / (1 + (1 if t in tok_a else 0) + (1 if t in tok_b else 0))) + 1 for t in pair_tokens}
        union = pair_tokens
        jaccard = overlap / max(1, len(union))
        weighted_overlap = sum(idf.get(tok, 1.0) for tok in overlap_tokens)
        has_anchor = any(
            idf.get(tok, 1.0) >= self.anchor_idf and tok not in self.GENERIC_TOPIC_WORDS
            for tok in overlap_tokens
        )
        if weighted_overlap < self.min_weighted_overlap and jaccard < self.min_jaccard:
            return (
                False,
                0.0,
                f"jaccard={jaccard:.2f}; weighted_overlap={weighted_overlap:.2f}",
                "low_weighted_overlap",
            )

        geo_a = tok_a & self.GEO_WORDS
        geo_b = tok_b & self.GEO_WORDS
        if geo_a and geo_b and not (geo_a & geo_b):
            return False, 0.0, f"geo_a={sorted(geo_a)}; geo_b={sorted(geo_b)}", "geo_mismatch"

        years_a = self._extract_years(norm_a)
        years_b = self._extract_years(norm_b)
        if years_a and years_b and not (years_a & years_b):
            min_gap = min(abs(ya - yb) for ya in years_a for yb in years_b)
            if min_gap > 1:
                return False, 0.0, f"years_a={sorted(years_a)}; years_b={sorted(years_b)}", "year_mismatch"
        date_markers_a = self._extract_date_markers(norm_a)
        date_markers_b = self._extract_date_markers(norm_b)
        if date_markers_a and date_markers_b and not (date_markers_a & date_markers_b):
            return (
                False,
                0.0,
                f"date_markers_a={sorted(date_markers_a)}; date_markers_b={sorted(date_markers_b)}",
                "date_mismatch",
            )

        assets_a = self._extract_assets(a.title)
        assets_b = self._extract_assets(b.title)
        if assets_a and assets_b and not (assets_a & assets_b):
            return (
                False,
                0.0,
                f"assets_a={sorted(assets_a)}; assets_b={sorted(assets_b)}",
                "asset_mismatch",
            )

        entities_a = self._extract_entities(a.title)
        entities_b = self._extract_entities(b.title)
        if entities_a and entities_b and not (entities_a & entities_b):
            # For strict mode keep high precision and reject entity mismatch.
            if self.profile == "strict":
                return (
                    False,
                    0.0,
                    f"entities_a={sorted(entities_a)}; entities_b={sorted(entities_b)}",
                    "entity_mismatch",
                )
            # Balanced mode: reject only when both titles are clearly entity-specific.
            entity_specific_a = any(x.startswith("person:") or x.startswith("state:") for x in entities_a)
            entity_specific_b = any(x.startswith("person:") or x.startswith("state:") for x in entities_b)
            if self.profile == "balanced" and entity_specific_a and entity_specific_b:
                return (
                    False,
                    0.0,
                    f"entities_a={sorted(entities_a)}; entities_b={sorted(entities_b)}",
                    "entity_mismatch",
                )

        if self.profile != "aggressive" and not has_anchor and jaccard < (self.min_jaccard + 0.1):
            return (
                False,
                0.0,
                f"jaccard={jaccard:.2f}; shared_meaningful_tokens={overlap}",
                "no_anchor",
            )

        sim = max(
            ratio(norm_a, norm_b),
            ratio(compact_a, compact_b),
            token_set_ratio(norm_a, norm_b),
            token_set_ratio(compact_a, compact_b),
        )
        explanation = (
            "title fuzzy match; "
            f"shared_meaningful_tokens={overlap}; "
            f"jaccard={jaccard:.2f}; "
            f"weighted_overlap={weighted_overlap:.2f}"
        )
        if sim < threshold:
            return False, float(sim), explanation, "strict_threshold_not_met"
        return True, float(sim), explanation, None

    def find_pairs(self, markets: list[Market], threshold: float) -> list[tuple[Market, Market, float, str]]:
        pairs: list[tuple[Market, Market, float, str]] = []
        normalized = {m.id: self._canonical_text(m.title) for m in markets}
        compact = {m.id: self._compact_title(m.title) for m in markets}
        tokens = {m.id: self._meaningful_tokens(m.title) for m in markets}
        title_quality_ok = {m.id: len(tokens[m.id]) >= 3 for m in markets}
        doc_freq: dict[str, int] = {}
        total_docs = max(1, len(markets))
        for token_set in tokens.values():
            for token in token_set:
                doc_freq[token] = doc_freq.get(token, 0) + 1
        idf = {token: log((1 + total_docs) / (1 + freq)) + 1 for token, freq in doc_freq.items()}

        for idx, a in enumerate(markets):
            for b in markets[idx + 1 :]:
                if a.platform_id == b.platform_id:
                    continue
                if not title_quality_ok.get(a.id, False) or not title_quality_ok.get(b.id, False):
                    continue
                if a.resolution_time and b.resolution_time:
                    if abs(a.resolution_time - b.resolution_time) > timedelta(days=self.resolution_window_days):
                        continue
                overlap_tokens = tokens[a.id] & tokens[b.id]
                overlap = len(overlap_tokens)
                if overlap < self.min_overlap:
                    if self.profile == "aggressive":
                        fallback_sim = max(
                            ratio(compact[a.id], compact[b.id]),
                            token_set_ratio(compact[a.id], compact[b.id]),
                        )
                        if fallback_sim >= self.broad_relaxed_fuzzy_min:
                            pairs.append(
                                (
                                    a,
                                    b,
                                    float(fallback_sim),
                                    f"aggressive relaxed fuzzy fallback; compact_similarity={fallback_sim:.1f}",
                                )
                            )
                    continue
                union = tokens[a.id] | tokens[b.id]
                jaccard = overlap / max(1, len(union))
                weighted_overlap = sum(idf.get(tok, 1.0) for tok in overlap_tokens)
                has_anchor = any(
                    idf.get(tok, 1.0) >= self.anchor_idf and tok not in self.GENERIC_TOPIC_WORDS
                    for tok in overlap_tokens
                )
                if weighted_overlap < self.min_weighted_overlap and jaccard < self.min_jaccard:
                    continue

                geo_a = tokens[a.id] & self.GEO_WORDS
                geo_b = tokens[b.id] & self.GEO_WORDS
                if self.profile != "aggressive" and geo_a and geo_b and not (geo_a & geo_b):
                    continue

                if self.profile != "aggressive" and not has_anchor and jaccard < (self.min_jaccard + 0.1):
                    continue

                sim = max(
                    ratio(normalized[a.id], normalized[b.id]),
                    ratio(compact[a.id], compact[b.id]),
                    token_set_ratio(normalized[a.id], normalized[b.id]),
                    token_set_ratio(compact[a.id], compact[b.id]),
                )
                if sim >= threshold:
                    explanation = (
                        "title fuzzy match; "
                        f"shared_meaningful_tokens={overlap}; "
                        f"jaccard={jaccard:.2f}; "
                        f"weighted_overlap={weighted_overlap:.2f}"
                    )
                    pairs.append((a, b, float(sim), explanation))
        return pairs

    def find_pairs_against(
        self,
        anchors: list[Market],
        candidates: list[Market],
        threshold: float,
        *,
        max_pairs: int = 20000,
    ) -> list[tuple[Market, Market, float, str]]:
        """
        Incremental variant: compare only anchor markets against candidate universe.
        """
        pairs: list[tuple[Market, Market, float, str]] = []
        all_markets = {m.id: m for m in [*anchors, *candidates]}
        normalized = {m_id: self._canonical_text(m.title) for m_id, m in all_markets.items()}
        compact = {m_id: self._compact_title(m.title) for m_id, m in all_markets.items()}
        tokens = {m_id: self._meaningful_tokens(m.title) for m_id, m in all_markets.items()}
        title_quality_ok = {m_id: len(tok) >= 3 for m_id, tok in tokens.items()}
        candidate_by_id = {int(m.id): m for m in candidates}
        candidate_token_index: dict[str, set[int]] = {}
        for m in candidates:
            mid = int(m.id)
            for token in tokens.get(mid, set()):
                candidate_token_index.setdefault(token, set()).add(mid)
        doc_freq: dict[str, int] = {}
        total_docs = max(1, len(all_markets))
        for token_set in tokens.values():
            for token in token_set:
                doc_freq[token] = doc_freq.get(token, 0) + 1
        idf = {token: log((1 + total_docs) / (1 + freq)) + 1 for token, freq in doc_freq.items()}

        seen: set[tuple[int, int]] = set()
        for a in anchors:
            candidate_overlap_counts: dict[int, int] = {}
            for token in tokens.get(int(a.id), set()):
                for candidate_id in candidate_token_index.get(token, set()):
                    candidate_overlap_counts[candidate_id] = candidate_overlap_counts.get(candidate_id, 0) + 1
            for candidate_id, shared_count in candidate_overlap_counts.items():
                if shared_count < self.min_overlap:
                    continue
                b = candidate_by_id.get(candidate_id)
                if b is None:
                    continue
                if a.id == b.id:
                    continue
                lo, hi = (a.id, b.id) if a.id < b.id else (b.id, a.id)
                key = (lo, hi)
                if key in seen:
                    continue
                seen.add(key)
                if len(pairs) >= max_pairs:
                    return pairs
                if a.platform_id == b.platform_id:
                    continue
                if not title_quality_ok.get(a.id, False) or not title_quality_ok.get(b.id, False):
                    continue
                if a.resolution_time and b.resolution_time:
                    if abs(a.resolution_time - b.resolution_time) > timedelta(days=self.resolution_window_days):
                        continue
                overlap_tokens = tokens[a.id] & tokens[b.id]
                overlap = len(overlap_tokens)
                if overlap < self.min_overlap:
                    if self.profile == "aggressive":
                        fallback_sim = max(
                            ratio(compact[a.id], compact[b.id]),
                            token_set_ratio(compact[a.id], compact[b.id]),
                        )
                        if fallback_sim >= self.broad_relaxed_fuzzy_min:
                            pairs.append(
                                (
                                    all_markets[lo],
                                    all_markets[hi],
                                    float(fallback_sim),
                                    f"aggressive relaxed fuzzy fallback; compact_similarity={fallback_sim:.1f}",
                                )
                            )
                    continue
                union = tokens[a.id] | tokens[b.id]
                jaccard = overlap / max(1, len(union))
                weighted_overlap = sum(idf.get(tok, 1.0) for tok in overlap_tokens)
                has_anchor = any(
                    idf.get(tok, 1.0) >= self.anchor_idf and tok not in self.GENERIC_TOPIC_WORDS
                    for tok in overlap_tokens
                )
                if weighted_overlap < self.min_weighted_overlap and jaccard < self.min_jaccard:
                    continue
                geo_a = tokens[a.id] & self.GEO_WORDS
                geo_b = tokens[b.id] & self.GEO_WORDS
                if self.profile != "aggressive" and geo_a and geo_b and not (geo_a & geo_b):
                    continue
                if self.profile != "aggressive" and not has_anchor and jaccard < (self.min_jaccard + 0.1):
                    continue
                sim = max(
                    ratio(normalized[a.id], normalized[b.id]),
                    ratio(compact[a.id], compact[b.id]),
                    token_set_ratio(normalized[a.id], normalized[b.id]),
                    token_set_ratio(compact[a.id], compact[b.id]),
                )
                if sim < threshold:
                    continue
                explanation = (
                    "title fuzzy match; "
                    f"shared_meaningful_tokens={overlap}; "
                    f"jaccard={jaccard:.2f}; "
                    f"weighted_overlap={weighted_overlap:.2f}"
                )
                pairs.append((all_markets[lo], all_markets[hi], float(sim), explanation))
        return pairs
