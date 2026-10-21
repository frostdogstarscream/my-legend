"""Persistence adapters for the approved-plan workflow."""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import func, text
from ..core.settings import get_settings
from ..config import PROMPT_VERSION, RuleLibrary

from ..persistence.models import (
    CharacterProfileRevisionModel,
    PlaybackEventModel,
    StoryJobModel,
    StoryPlanModel,
    StoryPlanRevisionModel,
    StoryVersionModel,
)
from .schemas import (
    ApprovedPlanSnapshot,
    GenerationJob,
    OperationContext,
    PlanPreview,
    PlanStatus,
    ResolvedSourceReference,
    SourceSelector,
    StoryDelivery,
    StoryOperationRequest,
    WorkflowJobStatus,
    utcnow,
)


class WorkflowConflictError(RuntimeError):
    """Optimistic-lock, idempotency or stale-plan conflict."""


class WorkflowNotFoundError(KeyError):
    """Requested workflow entity does not exist."""


class InMemoryWorkflowRepository:
    """Thread-safe reference repository used by unit tests and local demos."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._plans: dict[str, PlanPreview] = {}
        self._requests: dict[str, dict[str, Any]] = {}
        self._principals: dict[str, str] = {}
        self._revisions: dict[tuple[str, int], PlanPreview] = {}
        self._jobs: dict[str, GenerationJob] = {}
        self._idempotency: dict[str, str] = {}
        self._versions: dict[str, StoryDelivery] = {}
        self._version_principals: dict[str, str] = {}
        self._profiles: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._playback_events: list[dict[str, Any]] = []

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def create_plan(
        self,
        request: StoryOperationRequest,
        context: OperationContext,
        preview: PlanPreview,
    ) -> PlanPreview:
        with self._lock:
            self._plans[preview.plan_id] = preview
            self._requests[preview.plan_id] = request.model_dump(mode="json")
            self._principals[preview.plan_id] = context.principal_id
            self._revisions[(preview.plan_id, preview.plan_revision)] = preview
            return preview

    def get_plan(self, plan_id: str) -> PlanPreview | None:
        with self._lock:
            return self._plans.get(plan_id)

    def get_plan_request(self, plan_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._requests.get(plan_id)

    def save_revision(self, preview: PlanPreview, feedback: str = "") -> PlanPreview:
        with self._lock:
            current = self._plans.get(preview.plan_id)
            if current is None:
                raise WorkflowNotFoundError(preview.plan_id)
            if preview.plan_revision != current.plan_revision + 1:
                raise WorkflowConflictError("revision must increment exactly once")
            self._plans[preview.plan_id] = preview
            self._revisions[(preview.plan_id, preview.plan_revision)] = preview
            return preview

    def approve_and_create_job(
        self,
        plan_id: str,
        expected_revision: int,
        fingerprint: str,
        idempotency_key: str,
    ) -> GenerationJob:
        with self._lock:
            existing_id = self._idempotency.get(idempotency_key)
            if existing_id:
                existing = self._jobs[existing_id]
                if (existing.approved_plan_id, existing.approved_plan_revision) != (plan_id, expected_revision):
                    raise WorkflowConflictError("IDEMPOTENCY_KEY_CONFLICT")
                return existing
            plan = self._plans.get(plan_id)
            request = self._requests.get(plan_id)
            if plan is None or request is None:
                raise WorkflowNotFoundError(plan_id)
            current_revisions = self.get_current_profile_revisions(plan_id, list(plan.character_profile_revision_map))
            current_revisions = {
                character_id: current_revisions[character_id] if current_revisions[character_id] is not None else planned
                for character_id, planned in plan.character_profile_revision_map.items()
            }
            _validate_approval(plan, expected_revision, fingerprint, current_revisions)
            _validate_source_version(self, request, plan, self._principals[plan_id])
            approved = plan.model_copy(
                update={
                    "status": PlanStatus.APPROVED,
                    "requires_confirmation": False,
                    "allowed_actions": [],
                    "updated_at": utcnow(),
                }
            )
            self._plans[plan_id] = approved
            snapshot = ApprovedPlanSnapshot(
                plan_id=plan_id,
                plan_revision=expected_revision,
                plan_fingerprint=fingerprint,
                request=request,
                preview=approved,
                model_mapping_snapshot=_model_mapping_snapshot(),
            )
            job = GenerationJob(
                job_id=uuid.uuid4().hex,
                request_id=request["request_id"],
                idempotency_key=idempotency_key,
                approved_plan_id=plan_id,
                approved_plan_revision=expected_revision,
                input_snapshot=snapshot,
            )
            self._jobs[job.job_id] = job
            self._idempotency[idempotency_key] = job.job_id
            return job

    def get_job(self, job_id: str) -> GenerationJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def save_job(self, job: GenerationJob) -> GenerationJob:
        with self._lock:
            if job.job_id not in self._jobs:
                raise WorkflowNotFoundError(job.job_id)
            if self._jobs[job.job_id].execution_token != job.execution_token:
                raise WorkflowConflictError("JOB_EXECUTION_FENCED")
            self._jobs[job.job_id] = job.model_copy(update={"updated_at": utcnow()})
            return self._jobs[job.job_id]

    def claim_job(self, job_id: str) -> GenerationJob:
        with self._lock:
            job = self.get_job(job_id)
            if job is None:
                raise WorkflowNotFoundError(job_id)
            if job.status != WorkflowJobStatus.QUEUED:
                raise WorkflowConflictError(f"JOB_NOT_RUNNABLE: {job.status}")
            running = job.model_copy(update={"status": WorkflowJobStatus.RUNNING,
                "stage": "generation", "progress_pct": 10, "execution_token": uuid.uuid4().hex})
            self._jobs[job_id] = running
            return running

    def lock_job(self, job_id: str):
        return self.get_job(job_id)

    def fence_job(self, job_id: str, token: str) -> GenerationJob:
        with self._lock:
            job = self.get_job(job_id)
            if job is None or job.execution_token != token or job.status not in (
                WorkflowJobStatus.RUNNING, WorkflowJobStatus.VALIDATING, WorkflowJobStatus.CANCEL_REQUESTED
            ):
                raise WorkflowConflictError("JOB_RECOVERY_CONFLICT")
            failed = job.model_copy(update={"status": WorkflowJobStatus.FAILED,
                "stage": "interrupted", "error_code": "WORKER_INTERRUPTED", "execution_token": None})
            self._jobs[job_id] = failed
            return failed

    def save_story_version(self, delivery: StoryDelivery, principal_id: str = "local") -> StoryDelivery:
        with self._lock:
            if delivery.story_version_id in self._versions:
                raise WorkflowConflictError("story versions are immutable")
            delivery = delivery.model_copy(update={"version_no": 1 + max(
                (v.version_no for v in self._versions.values() if v.story_id == delivery.story_id), default=0)})
            self._versions[delivery.story_version_id] = delivery
            self._version_principals[delivery.story_version_id] = self._principals.get(delivery.approved_plan_id, principal_id)
            return delivery

    def get_story_version(self, story_version_id: str) -> StoryDelivery | None:
        with self._lock:
            return self._versions.get(story_version_id)

    def list_story_versions(self, principal_id: str, series_id: str | None = None) -> list[StoryDelivery]:
        with self._lock:
            values = [item for item in self._versions.values() if self._version_principals[item.story_version_id] == principal_id]
            if series_id:
                values = [item for item in values if item.series_id == series_id]
            return list(reversed(values))

    def resolve_source(self, selector: SourceSelector, principal_id: str) -> ResolvedSourceReference:
        with self._lock:
            delivery: StoryDelivery | None = None
            reason = selector.type
            position = selector.playback_position_ms
            if selector.type == "by_id":
                delivery = self._versions.get(selector.story_version_id or "")
                if delivery and self._version_principals.get(delivery.story_version_id) != principal_id:
                    delivery = None
            elif selector.type == "last_played":
                events = [
                    e for e in self._playback_events
                    if e["principal_id"] == principal_id
                    and self._version_principals.get(e["story_version_id"]) == principal_id
                ]
                if selector.series_id:
                    events = [
                        e for e in events
                        if self._versions.get(e["story_version_id"])
                        and self._versions[e["story_version_id"]].series_id == selector.series_id
                    ]
                if events:
                    event = max(events, key=lambda e: e["occurred_at"])
                    delivery = self._versions.get(event["story_version_id"])
                    position = event["playback_position_ms"]
            else:
                versions = self.list_story_versions(principal_id, selector.series_id)
                if selector.story_id:
                    versions = [item for item in versions if item.story_id == selector.story_id]
                if selector.type == "series_latest" and not selector.series_id:
                    raise WorkflowConflictError("series_latest requires series_id")
                delivery = versions[0] if versions else None
            if delivery is None:
                raise WorkflowNotFoundError("SOURCE_NOT_FOUND")
            return _source_from_delivery(delivery, reason, selector.episode_id, position)

    def update_character_profile(
        self, principal_id: str, character_id: str, expected_revision: int, patch: dict[str, Any]
    ) -> dict[str, Any]:
        with self._lock:
            key = (principal_id, character_id)
            versions = self._profiles.setdefault(key, [])
            current_revision = versions[-1]["revision"] if versions else 0
            if expected_revision != current_revision:
                raise WorkflowConflictError("CHARACTER_VERSION_CONFLICT")
            profile = dict(versions[-1]["profile"] if versions else {})
            profile.update(patch)
            record = {"character_id": character_id, "revision": current_revision + 1, "profile": profile}
            versions.append(record)
            return record

    def get_character_profile_revision(self, principal_id: str, character_id: str) -> int | None:
        with self._lock:
            values = self._profiles.get((principal_id, character_id), [])
            return values[-1]["revision"] if values else None

    def get_current_profile_revisions(self, plan_id: str, character_ids: list[str]) -> dict[str, int | None]:
        principal_id = self._principals.get(plan_id, "local")
        return {item: self.get_character_profile_revision(principal_id, item) for item in character_ids}

    def record_playback_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            if self._version_principals.get(event["story_version_id"]) != event["principal_id"]:
                raise WorkflowNotFoundError("STORY_VERSION_NOT_FOUND")
            payload = dict(event)
            payload.setdefault("occurred_at", utcnow())
            self._playback_events.append(payload)


class SqlAlchemyWorkflowRepository:
    """PostgreSQL production repository; also supports SQLite contract tests."""

    def __init__(self, session: Session):
        self.session = session

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()

    def create_plan(self, request, context: OperationContext, preview: PlanPreview) -> PlanPreview:
        row = StoryPlanModel(
            plan_id=preview.plan_id,
            request_id=request.request_id,
            principal_id=context.principal_id,
            intent=request.intent,
            status=preview.status,
            current_revision=preview.plan_revision,
            request_snapshot=request.model_dump(mode="json"),
            source_reference=preview.source_reference.model_dump(mode="json") if preview.source_reference else {},
        )
        self.session.add(row)
        self._add_revision(preview)
        self.session.flush()
        return preview

    def _add_revision(self, preview: PlanPreview, feedback: str = "") -> None:
        self.session.add(StoryPlanRevisionModel(
            plan_id=preview.plan_id,
            revision=preview.plan_revision,
            status=preview.status,
            plan_json=preview.model_dump(mode="json"),
            character_snapshot=[c.model_dump(mode="json") for c in preview.character_snapshots],
            source_snapshot=preview.source_reference.model_dump(mode="json") if preview.source_reference else {},
            fingerprint=preview.plan_fingerprint,
            feedback=feedback,
        ))

    def get_plan(self, plan_id: str) -> PlanPreview | None:
        plan = self.session.query(StoryPlanModel).filter_by(plan_id=plan_id).first()
        if plan is None:
            return None
        revision = self.session.query(StoryPlanRevisionModel).filter_by(
            plan_id=plan_id, revision=plan.current_revision
        ).first()
        return PlanPreview.model_validate(revision.plan_json) if revision else None

    def get_plan_request(self, plan_id: str) -> dict[str, Any] | None:
        row = self.session.query(StoryPlanModel).filter_by(plan_id=plan_id).first()
        return dict(row.request_snapshot) if row else None

    def save_revision(self, preview: PlanPreview, feedback: str = "") -> PlanPreview:
        row = self.session.query(StoryPlanModel).filter_by(plan_id=preview.plan_id).with_for_update().first()
        if row is None:
            raise WorkflowNotFoundError(preview.plan_id)
        if preview.plan_revision != row.current_revision + 1:
            raise WorkflowConflictError("revision must increment exactly once")
        row.current_revision = preview.plan_revision
        row.status = preview.status
        row.updated_at = utcnow()
        self._add_revision(preview, feedback)
        self.session.flush()
        return preview

    def approve_and_create_job(
        self, plan_id: str, expected_revision: int, fingerprint: str,
        idempotency_key: str, current_profile_revisions: dict[str, int] | None = None,
    ) -> GenerationJob:
        if self.session.bind.dialect.name == "postgresql":
            self.session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                                 {"key": "story-approval:" + idempotency_key})
        with self.session.begin_nested():
            existing = self.session.query(StoryJobModel).filter_by(idempotency_key=idempotency_key).first()
            if existing:
                if (existing.approved_plan_id, existing.approved_plan_revision) != (plan_id, expected_revision):
                    raise WorkflowConflictError("IDEMPOTENCY_KEY_CONFLICT")
                return GenerationJob.model_validate(existing.input_snapshot["job"])
            row = self.session.query(StoryPlanModel).filter_by(plan_id=plan_id).with_for_update().first()
            if row is None:
                raise WorkflowNotFoundError(plan_id)
            revision = self.session.query(StoryPlanRevisionModel).filter_by(
                plan_id=plan_id, revision=row.current_revision
            ).first()
            preview = PlanPreview.model_validate(revision.plan_json)
            profile_revisions = {}
            for character_id, planned in preview.character_profile_revision_map.items():
                profile = self.session.query(CharacterProfileRevisionModel).filter_by(
                    principal_id=row.principal_id, character_id=character_id
                ).order_by(CharacterProfileRevisionModel.revision.desc()).with_for_update().first()
                profile_revisions[character_id] = profile.revision if profile else planned
            _validate_approval(preview, expected_revision, fingerprint, profile_revisions)
            request = dict(row.request_snapshot)
            _validate_source_version(self, request, preview, row.principal_id)
            approved = preview.model_copy(update={
                "status": PlanStatus.APPROVED,
                "requires_confirmation": False,
                "allowed_actions": [],
                "updated_at": utcnow(),
            })
            revision.status = PlanStatus.APPROVED.value
            revision.plan_json = approved.model_dump(mode="json")
            row.status = PlanStatus.APPROVED.value
            snapshot = ApprovedPlanSnapshot(
                plan_id=plan_id, plan_revision=expected_revision,
                plan_fingerprint=fingerprint, request=request, preview=approved,
                model_mapping_snapshot=_model_mapping_snapshot(),
            )
            job = GenerationJob(
                job_id=uuid.uuid4().hex, request_id=request["request_id"],
                idempotency_key=idempotency_key, approved_plan_id=plan_id,
                approved_plan_revision=expected_revision, input_snapshot=snapshot,
            )
            job_payload = job.model_dump(mode="json")
            self.session.add(StoryJobModel(
                job_id=job.job_id,
                request_id=request["request_id"],
                status="QUEUED",
                current_stage="queued",
                input_json={"idea": request["user_instruction"]},
                idempotency_key=idempotency_key,
                approved_plan_id=plan_id,
                approved_plan_revision=expected_revision,
                input_snapshot={"job": job_payload},
                story_mode=approved.resolved_preferences.story_mode,
                content_form=approved.resolved_preferences.content_form,
                total_chapters=approved.resolved_preferences.target_episodes,
            ))
            self.session.flush()
            return job

    def get_job(self, job_id: str) -> GenerationJob | None:
        row = self.session.query(StoryJobModel).filter_by(job_id=job_id).populate_existing().first()
        return GenerationJob.model_validate(row.input_snapshot["job"]) if row and row.input_snapshot else None

    def save_job(self, job: GenerationJob) -> GenerationJob:
        row = self.session.query(StoryJobModel).filter_by(job_id=job.job_id).populate_existing().with_for_update().first()
        if row is None:
            raise WorkflowNotFoundError(job.job_id)
        current = GenerationJob.model_validate(row.input_snapshot["job"])
        if current.execution_token != job.execution_token:
            raise WorkflowConflictError("JOB_EXECUTION_FENCED")
        row.status = job.status
        row.current_stage = job.stage
        row.progress_pct = job.progress_pct
        row.result_story_version_id = job.result_story_version_id
        row.error_code = job.error_code
        row.error_message = job.error_message
        row.updated_at = utcnow()
        row.input_snapshot = {"job": job.model_dump(mode="json")}
        self.session.flush()
        return job

    def lock_job(self, job_id: str):
        row = self.session.query(StoryJobModel).filter_by(job_id=job_id).populate_existing().with_for_update().first()
        return GenerationJob.model_validate(row.input_snapshot["job"]) if row else None

    def claim_job(self, job_id: str) -> GenerationJob:
        job = self.lock_job(job_id)
        if job is None:
            raise WorkflowNotFoundError(job_id)
        if job.status != WorkflowJobStatus.QUEUED:
            raise WorkflowConflictError(f"JOB_NOT_RUNNABLE: {job.status}")
        running = job.model_copy(update={"status": WorkflowJobStatus.RUNNING,
            "stage": "generation", "progress_pct": 10, "execution_token": uuid.uuid4().hex})
        # Token changes are only permitted while the row is exclusively held.
        row = self.session.query(StoryJobModel).filter_by(job_id=job_id).first()
        row.input_snapshot = {"job": running.model_dump(mode="json")}
        return self.save_job(running)

    def fence_job(self, job_id: str, token: str) -> GenerationJob:
        job = self.lock_job(job_id)
        if job is None or job.execution_token != token or job.status not in (
            WorkflowJobStatus.RUNNING, WorkflowJobStatus.VALIDATING, WorkflowJobStatus.CANCEL_REQUESTED
        ):
            raise WorkflowConflictError("JOB_RECOVERY_CONFLICT")
        failed = job.model_copy(update={"status": WorkflowJobStatus.FAILED,
            "stage": "interrupted", "error_code": "WORKER_INTERRUPTED", "execution_token": None})
        row = self.session.query(StoryJobModel).filter_by(job_id=job_id).first()
        row.input_snapshot = {"job": failed.model_dump(mode="json")}
        return self.save_job(failed)

    def save_story_version(self, delivery: StoryDelivery, principal_id: str = "local") -> StoryDelivery:
        existing = self.session.query(StoryVersionModel).filter_by(
            story_version_id=delivery.story_version_id
        ).first()
        if existing:
            raise WorkflowConflictError("story versions are immutable")
        plan = self.session.query(StoryPlanModel).filter_by(plan_id=delivery.approved_plan_id).first()
        if plan is None:
            raise WorkflowNotFoundError(delivery.approved_plan_id)
        if self.session.bind.dialect.name == "postgresql":
            self.session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:story_id, 0))"),
                                 {"story_id": delivery.story_id})
        latest = self.session.query(func.max(StoryVersionModel.version_no)).filter_by(story_id=delivery.story_id).scalar()
        delivery = delivery.model_copy(update={"version_no": (latest or 0) + 1})
        self.session.add(StoryVersionModel(
            story_version_id=delivery.story_version_id,
            story_id=delivery.story_id,
            version_no=delivery.version_no,
            parent_story_version_id=delivery.parent_story_version_id,
            lineage_type=delivery.lineage_type,
            series_id=delivery.series_id,
            principal_id=plan.principal_id,
            approved_plan_id=delivery.approved_plan_id,
            approved_plan_revision=delivery.approved_plan_revision,
            status=delivery.status,
            ready_for_playback=1 if delivery.ready_for_playback else 0,
            title=delivery.title,
            summary=delivery.summary,
            delivery_json=delivery.model_dump(mode="json"),
            continuity_json=delivery.continuity_state,
        ))
        self.session.flush()
        return delivery

    def get_story_version(self, story_version_id: str) -> StoryDelivery | None:
        row = self.session.query(StoryVersionModel).filter_by(story_version_id=story_version_id).first()
        return StoryDelivery.model_validate(row.delivery_json) if row else None

    def list_story_versions(self, principal_id: str, series_id: str | None = None) -> list[StoryDelivery]:
        query = self.session.query(StoryVersionModel).filter_by(principal_id=principal_id)
        if series_id:
            query = query.filter_by(series_id=series_id)
        return [StoryDelivery.model_validate(row.delivery_json) for row in query.order_by(StoryVersionModel.id.desc()).all()]

    def resolve_source(self, selector: SourceSelector, principal_id: str) -> ResolvedSourceReference:
        row = None
        position = selector.playback_position_ms
        if selector.type == "by_id":
            row = self.session.query(StoryVersionModel).filter_by(
                story_version_id=selector.story_version_id, principal_id=principal_id
            ).first()
        elif selector.type == "last_played":
            event_query = self.session.query(PlaybackEventModel).join(
                StoryVersionModel,
                PlaybackEventModel.story_version_id == StoryVersionModel.story_version_id,
            ).filter(
                PlaybackEventModel.principal_id == principal_id,
                StoryVersionModel.principal_id == principal_id,
            )
            if selector.series_id:
                event_query = event_query.filter(StoryVersionModel.series_id == selector.series_id)
            event = event_query.order_by(PlaybackEventModel.occurred_at.desc(), PlaybackEventModel.id.desc()).first()
            if event:
                row = self.session.query(StoryVersionModel).filter_by(
                    story_version_id=event.story_version_id, principal_id=principal_id
                ).first()
                position = event.playback_position_ms
        else:
            if selector.type == "series_latest" and not selector.series_id:
                raise WorkflowConflictError("series_latest requires series_id")
            query = self.session.query(StoryVersionModel).filter_by(principal_id=principal_id)
            if selector.series_id:
                query = query.filter_by(series_id=selector.series_id)
            if selector.story_id:
                query = query.filter_by(story_id=selector.story_id)
            row = query.order_by(StoryVersionModel.id.desc()).first()
        if row is None:
            raise WorkflowNotFoundError("SOURCE_NOT_FOUND")
        delivery = StoryDelivery.model_validate(row.delivery_json)
        return _source_from_delivery(delivery, selector.type, selector.episode_id, position)

    def update_character_profile(self, principal_id, character_id, expected_revision, patch):
        latest = self.session.query(CharacterProfileRevisionModel).filter_by(
            principal_id=principal_id, character_id=character_id
        ).order_by(CharacterProfileRevisionModel.revision.desc()).with_for_update().first()
        current = latest.revision if latest else 0
        if current != expected_revision:
            raise WorkflowConflictError("CHARACTER_VERSION_CONFLICT")
        profile = dict(latest.profile_json if latest else {})
        profile.update(patch)
        record = CharacterProfileRevisionModel(
            principal_id=principal_id, character_id=character_id,
            revision=current + 1, profile_json=profile,
        )
        self.session.add(record)
        self.session.flush()
        return {"character_id": character_id, "revision": current + 1, "profile": profile}

    def get_character_profile_revision(self, principal_id: str, character_id: str) -> int | None:
        latest = self.session.query(CharacterProfileRevisionModel).filter_by(
            principal_id=principal_id, character_id=character_id
        ).order_by(CharacterProfileRevisionModel.revision.desc()).first()
        return latest.revision if latest else None

    def get_current_profile_revisions(self, plan_id: str, character_ids: list[str]) -> dict[str, int | None]:
        plan = self.session.query(StoryPlanModel).filter_by(plan_id=plan_id).first()
        if plan is None:
            raise WorkflowNotFoundError(plan_id)
        return {
            item: self.get_character_profile_revision(plan.principal_id, item)
            for item in character_ids
        }

    def record_playback_event(self, event: dict[str, Any]) -> None:
        version = self.session.query(StoryVersionModel).filter_by(
            story_version_id=event["story_version_id"], principal_id=event["principal_id"]
        ).first()
        if version is None:
            raise WorkflowNotFoundError("STORY_VERSION_NOT_FOUND")
        self.session.add(PlaybackEventModel(**event))
        self.session.flush()


def _validate_approval(
    plan: PlanPreview, expected_revision: int, fingerprint: str,
    current_profile_revisions: dict[str, int] | None,
) -> None:
    if plan.status != PlanStatus.AWAITING_APPROVAL:
        raise WorkflowConflictError("PLAN_NOT_APPROVABLE")
    if plan.plan_revision != expected_revision:
        raise WorkflowConflictError("STALE_PLAN")
    if plan.plan_fingerprint != fingerprint:
        raise WorkflowConflictError("STALE_PLAN")
    if plan.rule_versions and plan.rule_versions != _rule_versions():
        raise WorkflowConflictError("STALE_PLAN")
    if current_profile_revisions is not None:
        for character_id, approved_revision in plan.character_profile_revision_map.items():
            if current_profile_revisions.get(character_id) != approved_revision:
                raise WorkflowConflictError("STALE_PLAN")


def _model_mapping_snapshot() -> dict[str, str]:
    settings = get_settings()
    return {
        "FAST": settings.llm_fast_model,
        "BALANCED": settings.llm_balanced_model,
        "STRONG": settings.llm_strong_model,
        "LONG": settings.llm_long_model,
    }


def _rule_versions() -> dict[str, str]:
    lib = RuleLibrary.load()
    return {
        "formula": lib.formula_version,
        "timetravel": lib.timetravel_version,
        "continuity": lib.continuity_version,
        "prompt": PROMPT_VERSION,
    }


def _validate_source_version(repository, request: dict[str, Any], plan: PlanPreview, principal_id: str) -> None:
    if plan.source_reference is None:
        return
    selector = SourceSelector.model_validate(request["source_selector"])
    if selector.type == "by_id":
        return
    try:
        current = repository.resolve_source(selector, principal_id)
    except WorkflowNotFoundError as exc:
        raise WorkflowConflictError("STALE_PLAN") from exc
    if current.story_version_id != plan.source_reference.story_version_id:
        raise WorkflowConflictError("STALE_PLAN")


def _source_from_delivery(
    delivery: StoryDelivery, reason: str, episode_id: str | None, position: int | None,
) -> ResolvedSourceReference:
    if not delivery.ready_for_playback or delivery.status != "READY":
        raise WorkflowConflictError("SOURCE_NOT_READY")
    if episode_id and not any(ep.episode_id == episode_id for ep in delivery.episodes):
        raise WorkflowConflictError("SOURCE_EPISODE_NOT_FOUND")
    content_snapshot = {
        "delivery": delivery.model_dump(mode="json"),
        "approved_outline": delivery.outline.model_dump(mode="json"),
        "episodes": [{
            "episode_id": episode.episode_id,
            "index": episode.index,
            "title": episode.title,
            "summary": episode.episode_summary,
            "fact_delta": episode.fact_delta,
            "relationship_delta": episode.relationship_delta,
            "new_open_threads": episode.new_open_threads,
            "closed_threads": episode.closed_threads,
        } for episode in delivery.episodes],
    }
    if episode_id:
        selected = next((episode for episode in delivery.episodes if episode.episode_id == episode_id), None)
        if selected:
            content_snapshot["selected_episode"] = selected.model_dump(mode="json")
    return ResolvedSourceReference(
        story_id=delivery.story_id,
        story_version_id=delivery.story_version_id,
        version_no=delivery.version_no,
        episode_id=episode_id,
        series_id=delivery.series_id,
        resolved_reason=reason,
        last_played_position_ms=position,
        story_summary=delivery.summary,
        continuity_snapshot=delivery.continuity_state,
        source_content_snapshot=content_snapshot,
    )
