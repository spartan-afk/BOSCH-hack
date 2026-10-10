"""Tests for app.db.database - SQLite Database manager."""
import os
import tempfile
from datetime import datetime
from pathlib import Path
import pytest

from app.db.database import Database, ExperimentRow, ReportRow, EventRow, MetricSampleRow
from app.models import ExperimentReport, ExperimentSpec, ExperimentTarget, ExperimentType, Verdict


@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_chaos.db"
    return Database(db_file)


def test_save_and_get_experiment(temp_db):
    exp_id = "exp-001"
    spec_json = '{"name": "test"}'
    row = temp_db.save_experiment(exp_id, spec_json, status="planned")
    assert row.id == exp_id
    assert row.status == "planned"

    fetched = temp_db.get_experiment(exp_id)
    assert fetched is not None
    assert fetched.id == exp_id
    assert fetched.spec_json == spec_json


def test_update_experiment(temp_db):
    exp_id = "exp-002"
    temp_db.save_experiment(exp_id, '{"name": "test"}', status="planned")
    temp_db.update_experiment(exp_id, status="injecting", abort_reason="manual")

    fetched = temp_db.get_experiment(exp_id)
    assert fetched.status == "injecting"
    assert fetched.abort_reason == "manual"


def test_list_experiments_and_active(temp_db):
    temp_db.save_experiment("exp-1", "{}", status="analyzed")
    temp_db.save_experiment("exp-2", "{}", status="injecting")

    all_exps = temp_db.list_experiments()
    assert len(all_exps) == 2

    active = temp_db.get_active_experiment()
    assert active is not None
    assert active.id == "exp-2"


def test_save_and_get_report(temp_db):
    saved_row = temp_db.save_report(
        experiment_id="exp-003",
        verdict=Verdict.PASSED.value,
        report_json='{"summary": "Test passed"}',
        model="rule-based"
    )
    assert saved_row.id is not None
    assert saved_row.experiment_id == "exp-003"
    assert saved_row.verdict == "passed"

    fetched = temp_db.get_report("exp-003")
    assert fetched is not None
    assert fetched.verdict == "passed"


def test_log_and_get_events(temp_db):
    temp_db.add_event("exp-004", "TEST_EVENT", "Something happened")
    events = temp_db.get_events("exp-004")
    assert len(events) == 1
    assert events[0].type == "TEST_EVENT"
    assert events[0].detail == "Something happened"
