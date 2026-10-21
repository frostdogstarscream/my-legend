"""Real PostgreSQL, explicitly scripted generation; not real-model evidence."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from test_story_operations import setup, req, generate, create, edit
from test_postgres_workflow import pg_engine
from drama_engine.workflow.repository import SqlAlchemyWorkflowRepository, WorkflowConflictError

pytestmark = pytest.mark.postgres


def test_four_operations_persist_and_restore_complete_deliveries(pg_engine):
    with Session(pg_engine) as session:
        app, _ = setup(SqlAlchemyWorkflowRepository(session))
        original = create(app)
    with Session(pg_engine) as session:
        app, _ = setup(SqlAlchemyWorkflowRepository(session))
        assert app.get_story_version(original.story_version_id) == original
        revised, _ = generate(app, edit(original))
    with Session(pg_engine) as session:
        app, _ = setup(SqlAlchemyWorkflowRepository(session))
        continued, job = generate(app, req("continue", revised, creation_preferences={"target_episodes": 2}))
    with Session(pg_engine) as session:
        app, _ = setup(SqlAlchemyWorkflowRepository(session))
        remix, _ = generate(app, req("remix", continued, creation_preferences={"target_duration_sec": 120},
            remix={"instruction": "改变事故", "retain_elements": ["广播站"], "transform_elements": ["事故"]}))
    with Session(pg_engine) as session:
        app, _ = setup(SqlAlchemyWorkflowRepository(session))
        assert app.get_story_version(continued.story_version_id) == continued
        assert continued.episodes[:2] == revised.episodes
        assert app.get_generation_job(job.job_id).status == "SUCCEEDED"
        assert remix.story_id != original.story_id
        assert remix.parent_story_version_id == continued.story_version_id
        assert [original.version_no, revised.version_no, continued.version_no, remix.version_no] == [1, 2, 3, 1]
        assert app.get_story_version(original.story_version_id) == original
        assert session.execute(text("SELECT version_num FROM alembic_version")).scalar() == "004"
        with pytest.raises(DBAPIError, match="immutable"):
            session.execute(text("UPDATE story_versions SET delivery_json='{}' WHERE story_version_id=:id"),
                            {"id": original.story_version_id})
        session.rollback()


def test_concurrent_historical_branches_allocate_unique_versions(pg_engine):
    with Session(pg_engine) as session:
        app, _ = setup(SqlAlchemyWorkflowRepository(session))
        original = create(app)
    def branch(_):
        with Session(pg_engine) as session:
            app, _ = setup(SqlAlchemyWorkflowRepository(session))
            return generate(app, req("continue", original))[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        branches = list(pool.map(branch, range(2)))
    assert sorted(b.version_no for b in branches) == [2, 3]
    assert all(b.parent_story_version_id == original.story_version_id for b in branches)


def test_atomic_claim_recovery_and_retry_survive_restart(pg_engine):
    with Session(pg_engine) as session:
        app, _ = setup(SqlAlchemyWorkflowRepository(session))
        plan = app.prepare_story_plan(req())
        job = app.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, uuid.uuid4().hex)
    def claim(_):
        with Session(pg_engine) as session:
            repo = SqlAlchemyWorkflowRepository(session)
            try:
                running = repo.claim_job(job.job_id)
                session.commit()
                return running
            except WorkflowConflictError:
                session.rollback()
                return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = [j for j in pool.map(claim, range(2)) if j is not None]
    assert len(claimed) == 1
    stale = claimed[0]
    with Session(pg_engine) as session:
        app, _ = setup(SqlAlchemyWorkflowRepository(session))
        app.recover_interrupted_job(job.job_id, stale.execution_token)
    with Session(pg_engine) as session:
        repo = SqlAlchemyWorkflowRepository(session)
        with pytest.raises(WorkflowConflictError, match="JOB_EXECUTION_FENCED"):
            repo.save_job(stale)
        session.rollback()
    with Session(pg_engine) as session:
        app, _ = setup(SqlAlchemyWorkflowRepository(session))
        app.retry_failed_job(job.job_id)
    with Session(pg_engine) as session:
        app, _ = setup(SqlAlchemyWorkflowRepository(session))
        delivery = app.generate_from_approved_plan(job.job_id)
        assert app.generate_from_approved_plan(job.job_id) == delivery
        assert len(app.repository.list_story_versions("local")) == 1


def test_failed_candidate_rolls_back_without_publishing_and_can_retry(pg_engine):
    with Session(pg_engine) as session:
        app, provider = setup(SqlAlchemyWorkflowRepository(session))
        original = create(app)
        plan = app.prepare_story_plan(edit(original))
        job = app.approve_story_plan(plan.plan_id, 1, plan.plan_fingerprint, uuid.uuid4().hex)
        provider.escape = True
        with pytest.raises(ValueError, match="SCOPE_VIOLATION"):
            app.generate_from_approved_plan(job.job_id)
    with Session(pg_engine) as session:
        app, _ = setup(SqlAlchemyWorkflowRepository(session))
        assert app.get_generation_job(job.job_id).status == "FAILED"
        assert len(app.repository.list_story_versions("local")) == 1
        app.retry_failed_job(job.job_id)
        revised = app.generate_from_approved_plan(job.job_id)
        assert revised.version_no == 2
