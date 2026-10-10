"""Database layer using SQLAlchemy + aiosqlite for async SQLite."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


DB_PATH = Path(__file__).parent.parent.parent / "data" / "chaos_platform.db"


class Base(DeclarativeBase):
    pass


class ExperimentRow(Base):
    __tablename__ = "experiments"

    id = Column(String, primary_key=True)
    spec_json = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="planned")
    parent_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    baseline_json = Column(Text, nullable=True)
    during_json = Column(Text, nullable=True)
    after_json = Column(Text, nullable=True)
    events_json = Column(Text, default="[]")
    abort_reason = Column(String, nullable=True)


class ReportRow(Base):
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    experiment_id = Column(String, nullable=False)
    verdict = Column(String, nullable=False)
    report_json = Column(Text, nullable=False)
    model = Column(String, default="rule-based")
    created_at = Column(DateTime, default=datetime.utcnow)


class BaselineRow(Base):
    __tablename__ = "baselines"

    id = Column(String, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    snapshot_json = Column(Text, nullable=False)


class MetricSampleRow(Base):
    __tablename__ = "metric_samples"

    id = Column(Integer, primary_key=True, autoincrement=True)
    experiment_id = Column(String, nullable=True)
    phase = Column(String, nullable=False)  # before, during, after, baseline
    timestamp = Column(DateTime, default=datetime.utcnow)
    metric = Column(String, nullable=False)
    value = Column(Float, nullable=False)


class EventRow(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    experiment_id = Column(String, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    type = Column(String, nullable=False)
    detail = Column(Text, default="")


# ---------------------------------------------------------------------------
# Database manager
# ---------------------------------------------------------------------------

class Database:
    """Synchronous database manager for SQLite."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{self.db_path}", echo=False)
        Base.metadata.create_all(self.engine)
        self._session_factory = sessionmaker(bind=self.engine)

    def session(self) -> Session:
        return self._session_factory()

    # -- Experiments --------------------------------------------------------

    def save_experiment(self, exp_id: str, spec_json: str, status: str = "planned",
                        parent_id: str | None = None) -> ExperimentRow:
        with self.session() as s:
            row = ExperimentRow(
                id=exp_id,
                spec_json=spec_json,
                status=status,
                parent_id=parent_id,
                created_at=datetime.utcnow(),
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            return row

    def update_experiment(self, exp_id: str, **kwargs) -> None:
        with self.session() as s:
            row = s.query(ExperimentRow).filter_by(id=exp_id).first()
            if row:
                for k, v in kwargs.items():
                    setattr(row, k, v)
                s.commit()

    def get_experiment(self, exp_id: str) -> Optional[ExperimentRow]:
        with self.session() as s:
            return s.query(ExperimentRow).filter_by(id=exp_id).first()

    def list_experiments(self, limit: int = 50) -> list[ExperimentRow]:
        with self.session() as s:
            return s.query(ExperimentRow).order_by(ExperimentRow.created_at.desc()).limit(limit).all()

    def get_active_experiment(self) -> Optional[ExperimentRow]:
        """Return the currently running experiment, if any.
        
        'validated' is intentionally excluded — a validated-but-not-yet-started
        experiment must not block a new submission.
        """
        active_statuses = ["baselining", "injecting", "observing", "recovering"]
        with self.session() as s:
            return s.query(ExperimentRow).filter(
                ExperimentRow.status.in_(active_statuses)
            ).first()

    # -- Baselines ----------------------------------------------------------

    def save_baseline(self, baseline_id: str, snapshot_json: str) -> BaselineRow:
        with self.session() as s:
            row = BaselineRow(
                id=baseline_id,
                timestamp=datetime.utcnow(),
                snapshot_json=snapshot_json,
            )
            s.add(row)
            s.commit()
            return row

    def get_latest_baseline(self) -> Optional[BaselineRow]:
        with self.session() as s:
            return s.query(BaselineRow).order_by(BaselineRow.timestamp.desc()).first()

    # -- Reports ------------------------------------------------------------

    def save_report(self, experiment_id: str, verdict: str, report_json: str,
                    model: str = "rule-based") -> ReportRow:
        with self.session() as s:
            row = ReportRow(
                experiment_id=experiment_id,
                verdict=verdict,
                report_json=report_json,
                model=model,
            )
            s.add(row)
            s.commit()
            return row

    def get_report(self, experiment_id: str) -> Optional[ReportRow]:
        with self.session() as s:
            return s.query(ReportRow).filter_by(experiment_id=experiment_id).first()

    # -- Events -------------------------------------------------------------

    def add_event(self, experiment_id: str, event_type: str, detail: str = "") -> None:
        with self.session() as s:
            row = EventRow(
                experiment_id=experiment_id,
                timestamp=datetime.utcnow(),
                type=event_type,
                detail=detail,
            )
            s.add(row)
            s.commit()

    def get_events(self, experiment_id: str) -> list[EventRow]:
        with self.session() as s:
            return s.query(EventRow).filter_by(experiment_id=experiment_id).order_by(EventRow.timestamp).all()

    # -- Metric samples -----------------------------------------------------

    def add_metric_sample(self, experiment_id: str | None, phase: str,
                          metric: str, value: float) -> None:
        with self.session() as s:
            row = MetricSampleRow(
                experiment_id=experiment_id,
                phase=phase,
                metric=metric,
                value=value,
            )
            s.add(row)
            s.commit()

    # -- History queries ----------------------------------------------------

    def get_experiment_history(self, service: str, exp_type: str,
                               limit: int = 10) -> list[ExperimentRow]:
        """Get recent experiments targeting a specific service + type."""
        with self.session() as s:
            rows = s.query(ExperimentRow).order_by(
                ExperimentRow.created_at.desc()
            ).limit(200).all()
            # Filter in Python since spec_json is a blob
            results = []
            for row in rows:
                spec = json.loads(row.spec_json)
                if (spec.get("target", {}).get("service") == service and
                        spec.get("type") == exp_type):
                    results.append(row)
                    if len(results) >= limit:
                        break
            return results
