from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.services.agent_stage7.decision_composer import compose_stage7_decision
from app.services.agent_stage7.external_verifier import build_external_verification
from app.services.agent_stage7.stack_adapters.factory import get_stage7_adapter
from app.services.agent_stage7.stack_adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from app.services.agent_stage7.stack_adapters.openai_compatible_adapter import _safe_parse_decision, _parse_reason_codes
from app.services.agent_stage7.stack_adapters.plain_api_adapter import PlainApiAdapter
from app.services.agent_stage7.store import get_cached_stage7_decision, save_stage7_decision
from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import Market, Platform, Signal
from app.services.research.stage7_final_report import _resolve_stage7_decision
from app.services.research.stage7_harness import build_stage7_harness_report
from app.services.research.stage7_stack_scorecard import build_stage7_stack_scorecard_report


def test_stage7_stack_scorecard_has_weights_and_top_stack() -> None:
    report = build_stage7_stack_scorecard_report()
    weights = report["weights"]
    assert round(sum(float(v) for v in weights.values()), 6) == 1.0
    assert report["summary"]["top_stack"] == "langgraph"
    assert len(report["rows"]) >= 6


def test_stage7_decision_composer_is_idempotent_for_same_input() -> None:
    evidence = {
        "internal_metrics_snapshot": {"expected_ev_pct": 0.012, "confidence": 0.55, "liquidity": 0.70, "risk_flags": []},
        "external_consensus": {"polymarket_prob": 0.55, "manifold_prob": 0.52, "metaculus_median": 0.54},
        "contradictions": [],
        "resolution_ambiguity_flags": [],
        "fetched_at": "2026-03-14T00:00:00+00:00",
    }
    gate = {"profile": "balanced", "passed": True, "score": 0.91, "reasons": []}
    a = compose_stage7_decision(
        signal_id=123,
        base_decision="KEEP",
        internal_gate=gate,
        evidence_bundle=evidence,
        provider="langgraph",
        model_id="stage7_verifier",
        model_version="v1",
        prompt_template_version="stage7_prompt_v1",
        provider_fingerprint="deterministic_local",
    )
    b = compose_stage7_decision(
        signal_id=123,
        base_decision="KEEP",
        internal_gate=gate,
        evidence_bundle=evidence,
        provider="langgraph",
        model_id="stage7_verifier",
        model_version="v1",
        prompt_template_version="stage7_prompt_v1",
        provider_fingerprint="deterministic_local",
    )
    assert a["input_hash"] == b["input_hash"]
    assert a["decision"] == b["decision"]
    assert a["reason_codes"] == b["reason_codes"]


def test_stage7_harness_has_two_stacks_and_idempotency() -> None:
    report = build_stage7_harness_report(max_latency_ms=1200)
    summary = report["summary"]
    assert summary["stacks_tested"] >= 2
    by_stack = report["by_stack"]
    assert "langgraph" in by_stack
    assert "plain_llm_api" in by_stack
    assert by_stack["langgraph"]["idempotency_pass_rate"] >= 0.9
    assert by_stack["plain_llm_api"]["idempotency_pass_rate"] >= 0.9


def test_stage7_store_cache_by_input_hash() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    with session_factory() as db:
        p = Platform(name="P", base_url="https://x")
        db.add(p)
        db.flush()
        m = Market(
            platform_id=p.id,
            external_market_id="m1",
            title="T",
            created_at=datetime.now(UTC),
            fetched_at=datetime.now(UTC),
        )
        db.add(m)
        db.flush()
        s = Signal(
            signal_type=SignalType.DIVERGENCE,
            market_id=m.id,
            title="S",
            summary="x",
            created_at=datetime.now(UTC),
        )
        db.add(s)
        db.commit()

        payload = {
            "signal_id": s.id,
            "base_decision": "KEEP",
            "decision": "MODIFY",
            "confidence_adjustment": -0.07,
            "reason_codes": ["r1"],
            "evidence_bundle": {"k": 1},
            "input_hash": "abc123",
            "model_id": "stage7_verifier",
            "model_version": "v1",
            "prompt_template_version": "stage7_prompt_v1",
            "provider": "langgraph",
            "provider_fingerprint": "fp",
        }
        save_stage7_decision(db, payload=payload, llm_cost_usd=0.002, tool_snapshot_version="v1")
        cached = get_cached_stage7_decision(db, input_hash="abc123")
        assert cached is not None
        assert cached["decision"] == "MODIFY"
        assert cached["cache_hit"] is True


def test_stage7_input_hash_ignores_fetched_at_in_evidence_bundle() -> None:
    base = {
        "internal_metrics_snapshot": {"expected_ev_pct": 0.012, "confidence": 0.55, "liquidity": 0.70, "risk_flags": []},
        "external_consensus": {"polymarket_prob": 0.55, "manifold_prob": 0.52, "metaculus_median": 0.54},
        "contradictions": [],
        "resolution_ambiguity_flags": [],
    }
    gate = {"profile": "balanced", "passed": True, "score": 0.91, "reasons": []}
    a = compose_stage7_decision(
        signal_id=77,
        base_decision="KEEP",
        internal_gate=gate,
        evidence_bundle={**base, "fetched_at": "2026-03-14T00:00:00+00:00"},
        provider="langgraph",
        model_id="stage7_verifier",
        model_version="v1",
        prompt_template_version="stage7_prompt_v1",
    )
    b = compose_stage7_decision(
        signal_id=77,
        base_decision="KEEP",
        internal_gate=gate,
        evidence_bundle={**base, "fetched_at": "2026-03-15T00:00:00+00:00"},
        provider="langgraph",
        model_id="stage7_verifier",
        model_version="v1",
        prompt_template_version="stage7_prompt_v1",
    )
    assert a["input_hash"] == b["input_hash"]


def test_stage7_external_verifier_uses_fuzzy_cross_platform_match() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    with session_factory() as db:
        p_poly = Platform(name="POLYMARKET", base_url="https://poly")
        p_man = Platform(name="MANIFOLD", base_url="https://man")
        p_meta = Platform(name="METACULUS", base_url="https://meta")
        db.add_all([p_poly, p_man, p_meta])
        db.flush()

        poly_market = Market(
            platform_id=p_poly.id,
            external_market_id="poly-1",
            title="Will X win the 2026 election?",
            probability_yes=0.61,
            rules_text="Resolution at our discretion if applicable.",
            created_at=datetime.now(UTC),
            fetched_at=datetime.now(UTC),
        )
        man_market = Market(
            platform_id=p_man.id,
            external_market_id="man-1",
            title="X wins presidency in 2026",
            probability_yes=0.37,
            created_at=datetime.now(UTC),
            fetched_at=datetime.now(UTC),
        )
        meta_market = Market(
            platform_id=p_meta.id,
            external_market_id="meta-1",
            title="Will X be elected president in 2026?",
            probability_yes=0.58,
            created_at=datetime.now(UTC),
            fetched_at=datetime.now(UTC),
        )
        db.add_all([poly_market, man_market, meta_market])
        db.flush()

        signal = Signal(
            signal_type=SignalType.DIVERGENCE,
            market_id=poly_market.id,
            title=poly_market.title,
            summary="candidate",
            created_at=datetime.now(UTC),
        )
        db.add(signal)
        db.commit()

        evidence = build_external_verification(
            db,
            signal=signal,
            base_row={"expected_ev_pct": 0.03, "confidence": 0.8, "liquidity": 1000, "risk_flags": []},
            settings=Settings(),
        )
        consensus = evidence["external_consensus"]
        assert consensus["polymarket_prob"] is not None
        assert consensus["manifold_prob"] is not None
        assert consensus["metaculus_median"] is not None
        assert "cross_platform_spread_ge_20pct" in evidence["contradictions"]
        assert any(flag.startswith("rules_ambiguity:") for flag in evidence["resolution_ambiguity_flags"])


def test_stage7_factory_uses_gemini_profile_when_key_present() -> None:
    settings = Settings()
    settings.stage7_agent_provider = "plain_llm_api"
    settings.stage7_agent_real_calls_enabled = True
    settings.stage7_agent_provider_profile = "gemini"
    settings.gemini_api_key = "test-key"
    adapter = get_stage7_adapter(settings)
    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert "generativelanguage.googleapis.com" in adapter.api_base_url
    assert adapter.model == settings.stage7_gemini_model


def test_stage7_factory_falls_back_to_plain_when_profile_key_missing() -> None:
    settings = Settings()
    settings.stage7_agent_provider = "plain_llm_api"
    settings.stage7_agent_real_calls_enabled = True
    settings.stage7_agent_provider_profile = "groq"
    settings.groq_api_key = ""
    adapter = get_stage7_adapter(settings)
    assert isinstance(adapter, PlainApiAdapter)


def test_stage7_safe_parse_decision_supports_wrapped_json() -> None:
    text = "Here is the result:\\n```json\\n{\"decision\":\"MODIFY\",\"reason_codes\":[\"a\",\"b\"]}\\n```"
    decision, reasons = _safe_parse_decision(text)
    assert decision == "MODIFY"
    assert reasons == ["a", "b"]


def test_stage7_parse_reason_codes_handles_string() -> None:
    # LLMs sometimes return reason_codes as a comma-separated string
    result = _parse_reason_codes("no_contradiction, good_confidence")
    assert result == ["no_contradiction", "good_confidence"]


def test_stage7_safe_parse_handles_string_reason_codes() -> None:
    # Gemini / other providers may return reason_codes as string, not list
    text = '{"decision": "KEEP", "reason_codes": "no_issues_found"}'
    decision, reasons = _safe_parse_decision(text)
    assert decision == "KEEP"
    assert reasons == ["no_issues_found"]


def test_stage7_safe_parse_handles_missing_reason_codes() -> None:
    # If reason_codes is absent but decision is valid, use adapter_no_reason_codes
    text = '{"decision": "REMOVE"}'
    decision, reasons = _safe_parse_decision(text)
    assert decision == "REMOVE"
    assert reasons == ["adapter_no_reason_codes"]


def test_stage7_final_decision_data_pending_when_insufficient_data() -> None:
    verdict = _resolve_stage7_decision(
        stage6_final_decision="NO_GO",
        shadow_days=30,
        delta_keep_rate=0.0,
        baseline_precision=0.0,
        post_hoc_precision=0.0,
        reason_code_stability=1.0,
        latency_p95_ms=100.0,
        max_latency_ms=1200,
        cost_mode="normal",
        sweeps_pass_12_of_18=True,
        ci_lower_bound_positive_80=True,
        walkforward_negative_window_share_ok=True,
        data_sufficient_for_acceptance=False,
    )
    assert verdict == "NO_GO_DATA_PENDING"
