from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from ..llm.openai_compat import build_provider_from_env
from ..persistence.models import ServicePlanTaskModel, StoryJobModel
from ..workflow.adapters import ApprovedPlanLLMExecutor, LLMPlanGenerator
from ..workflow.repository import SqlAlchemyWorkflowRepository
from ..workflow.schemas import OperationContext
from ..workflow.service import GenerationCancelled, StoryWorkflowService
from ..templates.postgres_repository import PostgresStoryTemplateRepository
from ..templates.resolver import StoryTemplateResolver
from .config import redact
from .library import CharacterLibrary
from .runtime import (
    ACTIVE,
    WorkerRepository,
    db_now,
    merge_usage,
    finish_usage,
    recover_expired,
    usage,
)


def save_evidence(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(redact(value), ensure_ascii=False, indent=2), encoding="utf-8"
    )


class DurableWorker:
    def __init__(
        self,
        engine,
        settings,
        provider_factory=build_provider_from_env,
        planner_factory=LLMPlanGenerator,
        executor_factory=ApprovedPlanLLMExecutor,
        evidence_dir=None,
    ):
        self.engine = engine
        self.settings = settings
        self.owner = uuid.uuid4().hex
        self.provider_factory = provider_factory
        self.planner_factory = planner_factory
        self.executor_factory = executor_factory
        self.evidence_dir = (
            Path(evidence_dir or os.environ["STORY_EVIDENCE_DIR"])
            if evidence_dir or os.environ.get("STORY_EVIDENCE_DIR")
            else None
        )

    def heartbeat(self, kind, resource_id, token, stage=None, calls=None):
        with Session(self.engine) as session:
            model = ServicePlanTaskModel if kind == "task" else StoryJobModel
            key = (
                {"task_id": resource_id} if kind == "task" else {"job_id": resource_id}
            )
            row = session.query(model).filter_by(**key).with_for_update().first()
            now = db_now(session)
            current_token = (
                row.execution_token
                if kind == "task" and row
                else (row.input_snapshot["job"].get("execution_token") if row else None)
            )
            if (
                not row
                or current_token != token
                or row.lease_owner != self.owner
                or row.status not in ACTIVE
                or row.lease_expires_at <= now
            ):
                return False
            row.lease_expires_at = now + timedelta(seconds=self.settings.lease_seconds)
            row.updated_at = now
            if calls is not None:
                if kind == "task":
                    row.usage_json = merge_usage(row.usage_json, calls)
                else:
                    row.execution_usage_json = merge_usage(
                        row.execution_usage_json, calls
                    )
            if stage:
                count = len(calls or [])
                pct = min(85, 10 + count * 5)
                if kind == "task":
                    row.stage = stage
                    row.progress_pct = max(row.progress_pct, pct)
                else:
                    job = dict(row.input_snapshot["job"])
                    job.update(stage=stage, progress_pct=max(job["progress_pct"], pct))
                    row.input_snapshot = {"job": job}
                    row.current_stage = stage
                    row.progress_pct = job["progress_pct"]
            session.commit()
            return True

    def provider(self, kind, resource_id, token):
        provider = self.provider_factory()
        if not hasattr(provider, "complete"):
            return provider  # Explicit test providers only.
        original = provider.complete

        def complete(spec, system, user, deadline=None):
            self.ensure_active(kind, resource_id, token)
            self.heartbeat(
                kind,
                resource_id,
                token,
                spec.role,
                getattr(provider, "call_history", []),
            )
            capture_id = uuid.uuid4().hex
            record = {
                "capture_id": capture_id,
                "role": spec.role,
                "model": spec.model,
                "system": system,
                "user": user,
                "status": "pending",
                "started_at": __import__("datetime")
                .datetime.now(__import__("datetime").timezone.utc)
                .isoformat(),
            }
            path = (
                self.evidence_dir / resource_id / token / f"call_{capture_id}.json"
                if self.evidence_dir
                else None
            )
            start = len(getattr(provider, "call_history", []))
            if path:
                save_evidence(path, record)
            try:
                result = original(spec, system, user, deadline)
                record.update(status="completed", response_text=result.text)
                return result
            except Exception as exc:
                record.update(status="failed", error_type=type(exc).__name__)
                raise
            finally:
                record["calls"] = getattr(provider, "call_history", [])[start:]
                if path:
                    save_evidence(path, record)
                self.heartbeat(
                    kind,
                    resource_id,
                    token,
                    spec.role,
                    getattr(provider, "call_history", []),
                )

        provider.complete = complete
        return provider

    def ensure_active(self, kind, resource_id, token):
        """Cancellation stops future requests; an already sent request may finish."""
        from ..workflow.repository import WorkflowConflictError

        with Session(self.engine) as s:
            model = ServicePlanTaskModel if kind == "task" else StoryJobModel
            row = (
                s.query(model)
                .filter_by(
                    **(
                        {"task_id": resource_id}
                        if kind == "task"
                        else {"job_id": resource_id}
                    )
                )
                .with_for_update()
                .first()
            )
            actual = (
                row.execution_token
                if kind == "task" and row
                else (row.input_snapshot["job"].get("execution_token") if row else None)
            )
            if (
                not row
                or actual != token
                or row.lease_owner != self.owner
                or row.status not in ACTIVE
                or row.lease_expires_at <= db_now(s)
            ):
                raise WorkflowConflictError("WORKER_EXECUTION_FENCED")
            if row.status == "CANCEL_REQUESTED":
                if kind == "task":
                    raise WorkflowConflictError("TASK_CANCELLED")
                repo = SqlAlchemyWorkflowRepository(s)
                job = repo.get_job(resource_id)
                repo.save_job(
                    job.model_copy(update={"status": "CANCELLED", "stage": "cancelled"})
                )
                s.commit()
                raise GenerationCancelled("JOB_CANCELLED_BETWEEN_MODEL_REQUESTS")

    def run_once(self):
        with Session(self.engine) as session:
            recover_expired(session)
            session.commit()
            row = (
                session.query(ServicePlanTaskModel)
                .filter_by(status="QUEUED")
                .order_by(ServicePlanTaskModel.created_at)
                .with_for_update(skip_locked=True)
                .first()
            )
            if row:
                row.status = "RUNNING"
                row.stage = "planning"
                row.progress_pct = 10
                row.execution_token = uuid.uuid4().hex
                row.usage_json = {
                    **(row.usage_json or {}),
                    "attempt_started_at": db_now(session).isoformat(),
                }
                row.lease_owner = self.owner
                row.lease_expires_at = db_now(session) + timedelta(
                    seconds=self.settings.lease_seconds
                )
                task_id, token, kind, payload = (
                    row.task_id,
                    row.execution_token,
                    row.kind,
                    row.payload_json,
                )
                session.commit()
                self.run_task(task_id, token, kind, payload)
                return True
        # The engine atomically claims under this row lock, then commits before model work.
        with Session(self.engine) as session:
            row = (
                session.query(StoryJobModel)
                .filter(
                    StoryJobModel.status == "QUEUED",
                    StoryJobModel.approved_plan_id.is_not(None),
                )
                .order_by(StoryJobModel.created_at)
                .with_for_update(skip_locked=True)
                .first()
            )
            if not row:
                return False
            self.run_job(session, row.job_id)
            return True

    def pulse(self, kind, resource_id, token, stop):
        while not stop.wait(self.settings.heartbeat_seconds):
            try:
                if not self.heartbeat(kind, resource_id, token):
                    return
            except Exception:
                # DB outage doesn't grant extra lease time: publication guard still fails closed.
                return

    def run_task(self, task_id, token, kind, payload):
        stop = threading.Event()
        thread = threading.Thread(
            target=self.pulse, args=("task", task_id, token, stop), daemon=True
        )
        thread.start()
        provider = None
        try:
            provider = self.provider("task", task_id, token)
            with Session(self.engine) as session:
                repo = WorkerRepository(
                    session, self.settings, self.owner, task_id, token, provider
                )
                service = StoryWorkflowService(
                    repo,
                    self.planner_factory(provider),
                    self.executor_factory(provider),
                    template_resolver=StoryTemplateResolver(
                        PostgresStoryTemplateRepository(session)
                    ),
                )
                if kind == "prepare":
                    service.prepare_story_plan(
                        payload["request"],
                        OperationContext.model_validate(payload["context"]),
                    )
                else:
                    service.revise_story_plan(
                        payload["plan_id"],
                        payload["expected_revision"],
                        payload["feedback"],
                    )
        except Exception as exc:
            with Session(self.engine) as session:
                row = (
                    session.query(ServicePlanTaskModel)
                    .filter_by(task_id=task_id)
                    .with_for_update()
                    .first()
                )
                if row.execution_token == token and row.status in ACTIVE:
                    row.status = (
                        "CANCELLED" if row.status == "CANCEL_REQUESTED" else "FAILED"
                    )
                    row.stage = "failed" if row.status == "FAILED" else "cancelled"
                    row.error_code = "WORKER_ERROR" if row.status == "FAILED" else None
                    row.error_message = (
                        redact(str(exc)) if row.status == "FAILED" else None
                    )
                    row.usage_json = merge_usage(
                        row.usage_json, getattr(provider, "call_history", [])
                    )
                    row.usage_json = finish_usage(row.usage_json, db_now(session))
                    row.lease_owner = None
                    row.lease_expires_at = None
                    session.commit()
        finally:
            stop.set()
            thread.join(timeout=self.settings.heartbeat_seconds + 1)

    def run_job(self, session, job_id):
        worker = self
        provider = None
        running_token = None
        stop = threading.Event()
        thread = None

        class Execute:
            def execute(self, snapshot):
                nonlocal provider, running_token, thread
                running_token = (
                    SqlAlchemyWorkflowRepository(session)
                    .get_job(job_id)
                    .execution_token
                )
                # Release read transaction before heartbeat/network work.
                session.commit()
                thread = threading.Thread(
                    target=worker.pulse,
                    args=("job", job_id, running_token, stop),
                    daemon=True,
                )
                thread.start()
                provider = worker.provider("job", job_id, running_token)
                # Expose only the already-bound provider to the publication policy.
                # The wrapper otherwise hides listen-first policy from the service.
                self.provider = provider
                candidate = worker.executor_factory(provider).execute(snapshot)
                if worker.evidence_dir:
                    save_evidence(
                        worker.evidence_dir / job_id / running_token / "candidate.json",
                        candidate.model_dump(mode="json"),
                    )
                return candidate

        try:
            repo = WorkerRepository(session, self.settings, self.owner)
            StoryWorkflowService(repo, None, Execute()).generate_from_approved_plan(
                job_id
            )
        except Exception:
            session.rollback()  # Engine already persists actionable failure, or next poll fences expired lease.
        finally:
            stop.set()
            if thread:
                thread.join(timeout=self.settings.heartbeat_seconds + 1)
            session.rollback()
            row = (
                session.query(StoryJobModel)
                .filter_by(job_id=job_id)
                .populate_existing()
                .with_for_update()
                .first()
            )
            job = SqlAlchemyWorkflowRepository(session).get_job(job_id)
            if (
                job
                and job.execution_token == running_token
                and row.lease_owner == self.owner
            ):
                row.execution_usage_json = merge_usage(
                    row.execution_usage_json, getattr(provider, "call_history", [])
                )
                if row.status not in ACTIVE:
                    row.lease_owner = None
                    row.lease_expires_at = None
                    row.finished_at = db_now(session)
                    row.execution_usage_json = finish_usage(
                        row.execution_usage_json, row.finished_at
                    )
                if job.error_message:
                    SqlAlchemyWorkflowRepository(session).save_job(
                        job.model_copy(
                            update={"error_message": redact(job.error_message)}
                        )
                    )
                session.commit()

    def run(self, stop=None):
        stop = stop or threading.Event()
        while not stop.is_set():
            if not self.run_once():
                stop.wait(self.settings.poll_seconds)
