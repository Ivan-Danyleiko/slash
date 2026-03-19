from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.services.dryrun.reporter import build_report
from app.services.dryrun.simulator import run_simulation_cycle


def _mk_db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return session_factory()


def test_stage17_tail_block_present_in_dryrun_cycle_output() -> None:
    db = _mk_db()
    out = run_simulation_cycle(db)
    assert "tail" in out
    assert isinstance(out.get("tail"), dict)


def test_stage17_tail_report_present_in_dryrun_report() -> None:
    db = _mk_db()
    report = build_report(db)
    assert "tail_report" in report
    tail = report.get("tail_report") or {}
    assert "summary" in tail
    assert "final_decision" in tail

