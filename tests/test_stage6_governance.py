from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import Market, Platform, Signal
from app.services.research import stage6_governance as s6


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _seed_signals(db: Session) -> None:
    p = Platform(name="P", base_url="https://example.test")
    db.add(p)
    db.commit()
    db.refresh(p)
    m = Market(platform_id=p.id, external_market_id="m1", title="M1")
    db.add(m)
    db.commit()
    db.refresh(m)

    now = datetime.utcnow()
    for i in range(180):
        db.add(
            Signal(
                signal_type=SignalType.DIVERGENCE,
                market_id=m.id,
                title=f"s{i}",
                summary="x",
                execution_analysis={"expected_ev_after_costs_pct": 0.02},
                created_at=now - timedelta(days=(i % 30)),
            )
        )
    db.commit()


def test_stage6_governance_go_decision(monkeypatch) -> None:
    db = _session()
    _seed_signals(db)

    def fake_final_report(*args, **kwargs):
        return {
            "sections": {
                "signal_types_effective": {
                    "rows": [
                        {
                            "signal_type": "DIVERGENCE",
                            "decision": "KEEP",
                            "avg_return": 0.03,
                            "sharpe_like": 1.2,
                            "risk_of_ruin": 0.05,
                            "returns_labeled": 300,
                            "hit_rate": 0.58,
                        },
                        {
                            "signal_type": "RULES_RISK",
                            "decision": "KEEP",
                            "avg_return": 0.025,
                            "sharpe_like": 1.1,
                            "risk_of_ruin": 0.08,
                            "returns_labeled": 280,
                            "hit_rate": 0.56,
                        },
                    ]
                }
            }
        }

    def fake_walk(*args, **kwargs):
        return {
            "rows": [
                {"signal_type": "DIVERGENCE", "low_confidence": False, "avg_test_return": 0.01},
                {"signal_type": "RULES_RISK", "low_confidence": False, "avg_test_return": 0.01},
            ]
        }

    monkeypatch.setattr(s6, "build_stage5_final_report", fake_final_report)
    monkeypatch.setattr(s6, "build_walkforward_report", fake_walk)

    report = s6.build_stage6_governance_report(db, days=30)
    assert report["decision"] == "GO"
    assert report["checks"]["keep_types_gte_2"] is True


def test_stage6_governance_no_go_with_overfit_flags(monkeypatch) -> None:
    db = _session()
    _seed_signals(db)

    def fake_final_report(*args, **kwargs):
        return {
            "sections": {
                "signal_types_effective": {
                    "rows": [
                        {
                            "signal_type": "DIVERGENCE",
                            "decision": "MODIFY",
                            "avg_return": 0.20,
                            "sharpe_like": 3.0,
                            "risk_of_ruin": 0.25,
                            "returns_labeled": 100,
                            "hit_rate": 0.70,
                        }
                    ]
                }
            }
        }

    def fake_walk(*args, **kwargs):
        return {"rows": [{"signal_type": "DIVERGENCE", "low_confidence": True, "avg_test_return": -0.01}]}

    monkeypatch.setattr(s6, "build_stage5_final_report", fake_final_report)
    monkeypatch.setattr(s6, "build_walkforward_report", fake_walk)

    report = s6.build_stage6_governance_report(db, days=30)
    assert report["decision"] == "NO_GO"
    assert len(report["overfit_flags"]) >= 1
