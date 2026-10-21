"""Durable queues: planning transport tasks + existing approved story_jobs."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from ..persistence.models import ServicePlanTaskModel, StoryJobModel, StoryPlanModel
from ..workflow.repository import (
    SqlAlchemyWorkflowRepository,
    WorkflowConflictError,
    WorkflowNotFoundError,
)
from ..workflow.schemas import WorkflowJobStatus
from .config import redact
from .library import advisory
from .schemas import JobView, TaskView

ACTIVE = ("RUNNING", "VALIDATING", "CANCEL_REQUESTED")


def db_now(session):
    # PostgreSQL now() is the transaction start: LLM calls may outlive a lease.
    clock = (
        func.clock_timestamp()
        if session.bind.dialect.name == "postgresql"
        else func.current_timestamp()
    )
    return session.execute(__import__("sqlalchemy").select(clock)).scalar_one()


def usage(calls):
    unique = {c["call_id"]: c for c in calls if c.get("call_id")}
    reported = [c for c in unique.values() if c.get("usage_source") == "reported"]
    return {
        "calls": len(unique),
        "input_tokens": sum(c.get("input_tokens", 0) for c in reported),
        "output_tokens": sum(c.get("output_tokens", 0) for c in reported),
        "reasoning_tokens_in_output": sum(
            c.get("reasoning_tokens", 0) for c in reported
        ),
        "cache_read_tokens": sum(c.get("cache_read_tokens", 0) for c in reported),
        "estimated_input_tokens": sum(
            c.get("input_tokens", 0)
            for c in unique.values()
            if c.get("usage_source") == "estimated"
        ),
        "unknown_usage_calls": sum(
            c.get("usage_source") == "unavailable" for c in unique.values()
        ),
        "model_request_seconds": round(
            sum(c.get("latency_ms", 0) for c in unique.values()) / 1000, 3
        ),
        "by_stage": {
            node: sum(
                c.get("input_tokens", 0) + c.get("output_tokens", 0)
                for c in reported
                if c.get("node") == node
            )
            for node in {c.get("node", "unknown") for c in unique.values()}
        },
        "call_records": redact(list(unique.values())),
    }


def merge_usage(previous, calls):
    previous = previous or {}
    data = usage(previous.get("call_records", []) + list(calls))
    data["current_attempt_call_ids"] = [c["call_id"] for c in calls if c.get("call_id")]
    data["attempt_history"] = previous.get("attempt_history", [])
    if previous.get("inflight_usage_unknown"):
        data["inflight_usage_unknown"] = True
    for key in ("attempt_started_at", "attempt_completed_at", "attempt_wall_seconds"):
        if key in previous:
            data[key] = previous[key]
    return data


def remember_attempt(previous, status, code, message):
    previous = previous or {}
    current = set(previous.get("current_attempt_call_ids", []))
    metrics = usage(
        [c for c in previous.get("call_records", []) if c["call_id"] in current]
    )
    return {
        **previous,
        "current_attempt_call_ids": [],
        "attempt_history": [
            *previous.get("attempt_history", []),
            {
                "status": status,
                "error_code": code,
                "error_message": redact(message),
                "usage": metrics,
                "inflight_usage_unknown": bool(previous.get("inflight_usage_unknown")),
                "wall_seconds": previous.get("attempt_wall_seconds"),
            },
        ],
    }


def finish_usage(data, now):
    from datetime import datetime

    data = dict(data or {})
    data["attempt_completed_at"] = now.isoformat()
    if data.get("attempt_started_at"):
        data["attempt_wall_seconds"] = round(
            (now - datetime.fromisoformat(data["attempt_started_at"])).total_seconds(),
            3,
        )
    return data


def task_view(row):
    return TaskView(
        task_id=row.task_id,
        kind=row.kind,
        status=row.status,
        progress_pct=row.progress_pct,
        stage=row.stage,
        result=row.result_json or {},
        usage=row.usage_json or {},
        error_code=row.error_code,
        error_message=redact(row.error_message),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def job_view(session, row):
    job = SqlAlchemyWorkflowRepository(session).get_job(row.job_id)
    states = {
        "SUCCEEDED": "SUCCEEDED",
        "FAILED": "FAILED",
        "CANCELLED": "CANCELLED",
        "QUEUED": "NOT_GENERATED",
    }
    return JobView(
        job_id=job.job_id,
        request_id=job.request_id,
        status=job.status,
        stage=job.stage,
        progress_pct=job.progress_pct,
        approved_plan_id=job.approved_plan_id,
        approved_plan_revision=job.approved_plan_revision,
        result_story_version_id=job.result_story_version_id,
        error_code=job.error_code,
        error_message=redact(job.error_message),
        usage=row.execution_usage_json or {},
        story_state=states.get(job.status, "GENERATING"),
    )


def owned_plan(session, plan_id, principal):
    row = (
        session.query(StoryPlanModel)
        .filter_by(plan_id=plan_id, principal_id=principal)
        .first()
    )
    if row is None:
        raise WorkflowNotFoundError("PLAN_NOT_FOUND")
    return row


def owned_job(session, job_id, principal):
    row = (
        session.query(StoryJobModel)
        .join(StoryPlanModel, StoryJobModel.approved_plan_id == StoryPlanModel.plan_id)
        .filter(
            StoryJobModel.job_id == job_id, StoryPlanModel.principal_id == principal
        )
        .first()
    )
    if row is None:
        raise WorkflowNotFoundError("JOB_NOT_FOUND")
    return row


class PlanningQueue:
    def __init__(self, session):
        self.session = session

    def get(self, task_id, principal):
        row = (
            self.session.query(ServicePlanTaskModel)
            .filter_by(task_id=task_id, principal_id=principal)
            .first()
        )
        if not row:
            raise WorkflowNotFoundError("TASK_NOT_FOUND")
        return row

    def submit(self, principal, key, kind, payload):
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "kind": kind,
                    "payload": {k: v for k, v in payload.items() if k != "context"},
                },
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        advisory(self.session, "service-task:" + principal + ":" + key)
        row = (
            self.session.query(ServicePlanTaskModel)
            .filter_by(principal_id=principal, idempotency_key=key)
            .first()
        )
        if row:
            if row.request_fingerprint != fingerprint:
                raise WorkflowConflictError("IDEMPOTENCY_KEY_CONFLICT")
            return row
        if kind == "prepare":
            request_id = payload["request"]["request_id"]
            advisory(self.session, "service-request:" + principal + ":" + request_id)
            existing = (
                self.session.query(ServicePlanTaskModel)
                .filter_by(principal_id=principal, kind="prepare")
                .filter(
                    ServicePlanTaskModel.payload_json["request"][
                        "request_id"
                    ].as_string()
                    == request_id
                )
                .first()
            )
            if existing:
                raise WorkflowConflictError("REQUEST_ID_CONFLICT")
        row = ServicePlanTaskModel(
            task_id=uuid.uuid4().hex,
            principal_id=principal,
            idempotency_key=key,
            request_fingerprint=fingerprint,
            kind=kind,
            payload_json=payload,
            status="QUEUED",
            result_json={},
            usage_json={},
        )
        self.session.add(row)
        self.session.flush()
        return row

    def cancel(self, task_id, principal):
        row = (
            self.session.query(ServicePlanTaskModel)
            .filter_by(task_id=task_id, principal_id=principal)
            .with_for_update()
            .first()
        )
        if not row:
            raise WorkflowNotFoundError("TASK_NOT_FOUND")
        if row.status == "QUEUED":
            row.status = "CANCELLED"
            row.stage = "cancelled"
        elif row.status == "RUNNING":
            row.status = "CANCEL_REQUESTED"
        elif row.status != "CANCELLED":
            raise WorkflowConflictError("TASK_NOT_CANCELLABLE")
        row.updated_at = db_now(self.session)
        self.session.flush()
        return row

    def retry(self, task_id, principal):
        row = (
            self.session.query(ServicePlanTaskModel)
            .filter_by(task_id=task_id, principal_id=principal)
            .with_for_update()
            .first()
        )
        if not row:
            raise WorkflowNotFoundError("TASK_NOT_FOUND")
        if row.status != "FAILED":
            raise WorkflowConflictError("TASK_NOT_RETRYABLE")
        row.usage_json = remember_attempt(
            row.usage_json, row.status, row.error_code, row.error_message
        )
        row.status = "QUEUED"
        row.stage = "queued"
        row.progress_pct = 0
        row.execution_token = None
        row.lease_owner = None
        row.lease_expires_at = None
        row.error_code = None
        row.error_message = None
        row.updated_at = db_now(self.session)
        self.session.flush()
        return row


def recover_expired(session):
    """Explicit retry after failure; never silently repeat a billed model call."""
    now = db_now(session)
    recovered = []
    tasks = (
        session.query(ServicePlanTaskModel)
        .filter(
            ServicePlanTaskModel.status.in_(ACTIVE),
            ServicePlanTaskModel.lease_expires_at < now,
        )
        .with_for_update(skip_locked=True)
        .all()
    )
    for row in tasks:
        row.status = "CANCELLED" if row.status == "CANCEL_REQUESTED" else "FAILED"
        row.stage = "interrupted"
        row.error_code = "WORKER_INTERRUPTED"
        row.error_message = "Worker lease expired; explicit retry required; in-flight usage may be unknown."
        row.execution_token = None
        row.lease_owner = None
        row.lease_expires_at = None
        row.updated_at = now
        row.usage_json = {**(row.usage_json or {}), "inflight_usage_unknown": True}
        row.usage_json = finish_usage(row.usage_json, now)
        recovered.append(row.task_id)
    rows = (
        session.query(StoryJobModel)
        .filter(
            StoryJobModel.approved_plan_id.is_not(None),
            StoryJobModel.status.in_(ACTIVE),
            StoryJobModel.lease_owner.is_not(None),
            StoryJobModel.lease_expires_at < now,
        )
        .with_for_update(skip_locked=True)
        .all()
    )
    repo = SqlAlchemyWorkflowRepository(session)
    for row in rows:
        job = repo.get_job(row.job_id)
        cancelled = job.status == "CANCEL_REQUESTED"
        failed = repo.fence_job(job.job_id, job.execution_token)
        if cancelled:
            repo.save_job(
                failed.model_copy(
                    update={"status": WorkflowJobStatus.CANCELLED, "stage": "cancelled"}
                )
            )
        row.lease_owner = None
        row.lease_expires_at = None
        row.execution_usage_json = {
            **(row.execution_usage_json or {}),
            "inflight_usage_unknown": True,
        }
        row.finished_at = now
        row.execution_usage_json = finish_usage(row.execution_usage_json, now)
        recovered.append(row.job_id)
    session.flush()
    return recovered


class WorkerRepository(SqlAlchemyWorkflowRepository):
    """Engine transactions also fence leases; creation still uses the same engine."""

    def __init__(
        self, session, settings, owner, task_id=None, token=None, provider=None
    ):
        super().__init__(session)
        self.settings = settings
        self.owner = owner
        self.task_id = task_id
        self.token = token
        self.provider = provider

    def task(self):
        row = (
            self.session.query(ServicePlanTaskModel)
            .filter_by(task_id=self.task_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if (
            not row
            or row.execution_token != self.token
            or row.status not in ("RUNNING", "CANCEL_REQUESTED")
            or row.lease_expires_at <= db_now(self.session)
        ):
            raise WorkflowConflictError("TASK_EXECUTION_FENCED")
        if row.status == "CANCEL_REQUESTED":
            raise WorkflowConflictError("TASK_CANCELLED")
        return row

    def create_plan(self, request, context, preview):
        task = self.task()
        result = super().create_plan(request, context, preview)
        self.finish_task(task, preview)
        return result

    def save_revision(self, preview, feedback=""):
        task = self.task()
        result = super().save_revision(preview, feedback)
        self.finish_task(task, preview)
        return result

    def finish_task(self, task, preview):
        task.status = "SUCCEEDED"
        task.stage = "completed"
        task.progress_pct = 100
        task.updated_at = db_now(self.session)
        task.result_json = {
            "plan_id": preview.plan_id,
            "plan_revision": preview.plan_revision,
        }
        task.usage_json = merge_usage(
            task.usage_json, getattr(self.provider, "call_history", [])
        )
        task.usage_json = finish_usage(task.usage_json, task.updated_at)
        task.lease_owner = None
        task.lease_expires_at = None

    def lock_job(self, job_id):
        job = super().lock_job(job_id)
        if job and job.status in ACTIVE:
            row = self.session.query(StoryJobModel).filter_by(job_id=job_id).first()
            if (
                row.lease_owner != self.owner
                or not row.lease_expires_at
                or row.lease_expires_at <= db_now(self.session)
            ):
                raise WorkflowConflictError("JOB_EXECUTION_FENCED")
        return job

    def claim_job(self, job_id):
        running = super().claim_job(job_id)
        row = self.session.query(StoryJobModel).filter_by(job_id=job_id).first()
        row.started_at = db_now(self.session)
        row.finished_at = None
        row.execution_usage_json = {
            **(row.execution_usage_json or {}),
            "attempt_started_at": row.started_at.isoformat(),
        }
        row.execution_usage_json.pop("attempt_completed_at", None)
        row.execution_usage_json.pop("attempt_wall_seconds", None)
        row.lease_owner = self.owner
        row.lease_expires_at = db_now(self.session) + timedelta(
            seconds=self.settings.lease_seconds
        )
        self.session.flush()
        return running
