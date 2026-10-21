"""Thin HTTP transport; PostgreSQL is the source of truth, model work is off-request."""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..persistence.models import (
    CharacterProfileRevisionModel,
    CharacterTemplateModel,
    StoryJobModel,
    StoryPlanModel,
    StoryPlanRevisionModel,
    StoryVersionModel,
)
from ..workflow.delivery import build_character_role_map, delivery_to_readable_script
from ..workflow.repository import (
    SqlAlchemyWorkflowRepository,
    WorkflowConflictError,
    WorkflowNotFoundError,
)
from ..workflow.schemas import (
    PlanPreview,
    PlanStatus,
    StoryDelivery,
    StoryOperationRequest,
)
from ..workflow.service import StoryWorkflowService
from .config import ServiceSettings, redact
from .downstream import RenderManifest, SimulatedTTSConsumer, rendering_manifest
from .library import CharacterLibrary
from .runtime import (
    PlanningQueue,
    job_view,
    owned_job,
    owned_plan,
    remember_attempt,
    task_view,
)
from .schemas import (
    CharacterBatch,
    CharacterView,
    ErrorResponse,
    JobView,
    PlanApprovalRequest,
    PlanRevisionRequest,
    RuntimeUpdate,
    RuntimeView,
    TaskView,
)


def create_app(settings: ServiceSettings | None = None, engine=None):
    settings = settings or ServiceSettings.from_env()
    settings.validate()
    engine = engine or settings.engine()

    @asynccontextmanager
    async def lifespan(app):
        if not settings.testing:
            with engine.connect() as c:
                if (
                    c.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    != "004"
                ):
                    raise RuntimeError("SERVICE_MIGRATION_004_REQUIRED")
        yield

    errors = {code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 503)}
    app = FastAPI(
        title="StoryGenerate Product API",
        version="11.0",
        lifespan=lifespan,
        responses=errors,
    )
    app.state.engine = engine
    app.state.settings = settings
    bearer = HTTPBearer(auto_error=False)

    def auth(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials:
            for key, principal in settings.api_keys.items():
                if secrets.compare_digest(credentials.credentials, key):
                    return principal
        raise HTTPException(401, detail="AUTH_REQUIRED")

    def session():
        with Session(engine) as s:
            try:
                yield s
            except Exception:
                s.rollback()
                raise

    def service(s):
        return StoryWorkflowService(SqlAlchemyWorkflowRepository(s), None, None)

    def fail(request, status, code, message, details=None):
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "code": code,
                    "message": redact(message),
                    "request_id": getattr(
                        request.state, "request_id", uuid.uuid4().hex
                    ),
                    "details": details or [],
                }
            },
        )

    @app.middleware("http")
    async def correlation(request, call_next):
        request.state.request_id = uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(WorkflowNotFoundError)
    async def missing(req, exc):
        return fail(req, 404, "NOT_FOUND", "Resource not found")

    @app.exception_handler(WorkflowConflictError)
    async def conflict(req, exc):
        message = str(exc)
        code = message.split(":")[0]
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", code):
            code = "CONFLICT"
        return fail(req, 409, code, message)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(req, exc):
        code = str(exc.detail)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", code):
            code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(
                exc.status_code, "HTTP_ERROR"
            )
        return fail(req, exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def bad_schema(req, exc):
        details = [
            {"loc": list(e["loc"]), "type": e["type"], "message": e["msg"]}
            for e in exc.errors()
        ]
        return fail(
            req,
            422,
            "SCHEMA_VALIDATION_FAILED",
            "Request schema validation failed",
            details,
        )

    @app.exception_handler(ValueError)
    async def bad_value(req, exc):
        return fail(
            req,
            422,
            str(exc).split(":")[0]
            if re.match(r"^[A-Z_]+", str(exc))
            else "INVALID_REQUEST",
            str(exc),
        )

    @app.exception_handler(Exception)
    async def unexpected(req, exc):
        return fail(
            req,
            503,
            "SERVICE_UNAVAILABLE",
            "Service unavailable; inspect server diagnostics",
        )

    @app.get("/health", tags=["operations"])
    def health():
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        return {
            "status": "ready",
            "storage": "postgresql"
            if engine.dialect.name == "postgresql"
            else "test-only",
        }

    @app.post("/v1/plans", response_model=TaskView, status_code=202, tags=["plans"])
    def prepare(
        value: StoryOperationRequest, principal=Depends(auth), s=Depends(session)
    ):
        if len(value.request_id) > 64 or len(value.idempotency_key) > 128:
            raise ValueError("REQUEST_IDENTIFIER_TOO_LONG")
        # Scope engine request IDs to tenants because legacy story_jobs has a global unique request_id.
        value = value.model_copy(
            update={
                "request_id": hashlib.sha256(
                    (principal["principal_id"] + "\0" + value.request_id).encode()
                ).hexdigest()
            }
        )
        if value.source_selector:
            SqlAlchemyWorkflowRepository(s).resolve_source(
                value.source_selector, principal["principal_id"]
            )
        context = CharacterLibrary(s).context(value, principal["principal_id"])
        row = PlanningQueue(s).submit(
            principal["principal_id"],
            value.idempotency_key,
            "prepare",
            {
                "request": value.model_dump(mode="json", exclude_unset=True),
                "context": context.model_dump(mode="json"),
            },
        )
        s.commit()
        return task_view(row)

    @app.get("/v1/tasks/{task_id}", response_model=TaskView, tags=["tasks"])
    def get_task(task_id, principal=Depends(auth), s=Depends(session)):
        return task_view(PlanningQueue(s).get(task_id, principal["principal_id"]))

    @app.post("/v1/tasks/{task_id}/cancel", response_model=TaskView, tags=["tasks"])
    def cancel_task(task_id, principal=Depends(auth), s=Depends(session)):
        row = PlanningQueue(s).cancel(task_id, principal["principal_id"])
        s.commit()
        return task_view(row)

    @app.post(
        "/v1/tasks/{task_id}/retry",
        response_model=TaskView,
        status_code=202,
        tags=["tasks"],
    )
    def retry_task(task_id, principal=Depends(auth), s=Depends(session)):
        row = PlanningQueue(s).retry(task_id, principal["principal_id"])
        s.commit()
        return task_view(row)

    @app.get("/v1/plans/{plan_id}", response_model=PlanPreview, tags=["plans"])
    def get_plan(plan_id, principal=Depends(auth), s=Depends(session)):
        owned_plan(s, plan_id, principal["principal_id"])
        return service(s).get_story_plan(plan_id)

    @app.get(
        "/v1/plans/{plan_id}/revisions",
        response_model=list[PlanPreview],
        tags=["plans"],
    )
    def revisions(plan_id, principal=Depends(auth), s=Depends(session)):
        owned_plan(s, plan_id, principal["principal_id"])
        return [
            PlanPreview.model_validate(r.plan_json)
            for r in s.query(StoryPlanRevisionModel)
            .filter_by(plan_id=plan_id)
            .order_by(StoryPlanRevisionModel.revision)
            .all()
        ]

    @app.post(
        "/v1/plans/{plan_id}/revisions",
        response_model=TaskView,
        status_code=202,
        tags=["plans"],
    )
    def revise(
        plan_id, value: PlanRevisionRequest, principal=Depends(auth), s=Depends(session)
    ):
        owned_plan(s, plan_id, principal["principal_id"])
        plan = service(s).get_story_plan(plan_id)
        # Exact retries return the original task even after it advanced the plan revision.
        row = PlanningQueue(s).submit(
            principal["principal_id"],
            value.idempotency_key,
            "revise",
            {
                "plan_id": plan_id,
                "expected_revision": value.expected_revision,
                "feedback": value.feedback,
            },
        )
        if row.status == "QUEUED" and (
            plan.status != PlanStatus.AWAITING_APPROVAL
            or plan.plan_revision != value.expected_revision
        ):
            raise WorkflowConflictError("STALE_PLAN")
        s.commit()
        return task_view(row)

    @app.post(
        "/v1/plans/{plan_id}/approve",
        response_model=JobView,
        status_code=202,
        tags=["plans"],
    )
    def approve(
        plan_id, value: PlanApprovalRequest, principal=Depends(auth), s=Depends(session)
    ):
        owned_plan(s, plan_id, principal["principal_id"])
        plan = service(s).get_story_plan(plan_id)
        key = hashlib.sha256(
            (principal["principal_id"] + "\0" + value.idempotency_key).encode()
        ).hexdigest()
        existing = s.query(StoryJobModel).filter_by(idempotency_key=key).first()
        if not existing:
            current_row = owned_plan(s, plan_id, principal["principal_id"])
            selected = current_row.request_snapshot.get("characters", {}).get(
                "selected_character_ids", []
            )
            CharacterLibrary(s).check_snapshot_versions(plan, selected)
        job = service(s).approve_story_plan(
            plan_id, value.expected_revision, value.fingerprint, key
        )
        return job_view(s, owned_job(s, job.job_id, principal["principal_id"]))

    @app.post(
        "/v1/plans/{plan_id}/generate",
        response_model=JobView,
        status_code=202,
        tags=["plans"],
    )
    def generate(plan_id, principal=Depends(auth), s=Depends(session)):
        row = owned_plan(s, plan_id, principal["principal_id"])
        if row.status != PlanStatus.APPROVED:
            raise WorkflowConflictError("PLAN_APPROVAL_REQUIRED")
        job = s.query(StoryJobModel).filter_by(approved_plan_id=plan_id).first()
        if not job:
            raise WorkflowConflictError("PLAN_APPROVAL_REQUIRED")
        return job_view(s, owned_job(s, job.job_id, principal["principal_id"]))

    @app.get("/v1/jobs/{job_id}", response_model=JobView, tags=["jobs"])
    def get_job(job_id, principal=Depends(auth), s=Depends(session)):
        return job_view(s, owned_job(s, job_id, principal["principal_id"]))

    @app.post("/v1/jobs/{job_id}/cancel", response_model=JobView, tags=["jobs"])
    def cancel_job(job_id, principal=Depends(auth), s=Depends(session)):
        row = owned_job(s, job_id, principal["principal_id"])
        if row.status not in (
            "QUEUED",
            "RUNNING",
            "VALIDATING",
            "CANCEL_REQUESTED",
            "CANCELLED",
        ):
            raise WorkflowConflictError("JOB_NOT_CANCELLABLE")
        service(s).cancel_generation_job(job_id)
        return job_view(s, row)

    @app.post(
        "/v1/jobs/{job_id}/retry",
        response_model=JobView,
        status_code=202,
        tags=["jobs"],
    )
    def retry_job(job_id, principal=Depends(auth), s=Depends(session)):
        row = owned_job(s, job_id, principal["principal_id"])
        row.execution_usage_json = remember_attempt(
            row.execution_usage_json, row.status, row.error_code, row.error_message
        )
        service(s).retry_failed_job(job_id)
        row.lease_owner = None
        row.lease_expires_at = None
        s.commit()
        return job_view(s, row)

    def version(s, version_id, principal):
        row = (
            s.query(StoryVersionModel)
            .filter_by(story_version_id=version_id, principal_id=principal)
            .first()
        )
        if not row:
            raise WorkflowNotFoundError("VERSION_NOT_FOUND")
        return StoryDelivery.model_validate(row.delivery_json)

    @app.get("/v1/stories", tags=["stories"])
    def stories(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        principal=Depends(auth),
        s=Depends(session),
    ):
        rows = (
            s.query(StoryVersionModel)
            .filter_by(principal_id=principal["principal_id"])
            .order_by(StoryVersionModel.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [
            {
                "story_id": r.story_id,
                "story_version_id": r.story_version_id,
                "version_no": r.version_no,
                "title": r.title,
                "parent_story_version_id": r.parent_story_version_id,
                "lineage_type": r.lineage_type,
                "status": r.status,
            }
            for r in rows
        ]

    @app.get(
        "/v1/stories/{story_id}/versions",
        response_model=list[StoryDelivery],
        tags=["stories"],
    )
    def versions(story_id, principal=Depends(auth), s=Depends(session)):
        rows = (
            s.query(StoryVersionModel)
            .filter_by(principal_id=principal["principal_id"], story_id=story_id)
            .order_by(StoryVersionModel.version_no)
            .all()
        )
        if not rows:
            raise WorkflowNotFoundError("STORY_NOT_FOUND")
        return [StoryDelivery.model_validate(r.delivery_json) for r in rows]

    @app.get(
        "/v1/story-versions/{version_id}",
        response_model=StoryDelivery,
        tags=["stories"],
    )
    def get_version(version_id, principal=Depends(auth), s=Depends(session)):
        return version(s, version_id, principal["principal_id"])

    @app.get("/v1/story-versions/{version_id}/lineage", tags=["stories"])
    def lineage(version_id, principal=Depends(auth), s=Depends(session)):
        origin = version(s, version_id, principal["principal_id"])
        visited = set()
        frontier = [origin.story_version_id]
        nodes = []
        # Traverse ancestor and descendant version edges, including different Remix story IDs.
        while frontier:
            current = frontier.pop()
            if current in visited:
                continue
            visited.add(current)
            d = version(s, current, principal["principal_id"])
            nodes.append(
                {
                    "story_id": d.story_id,
                    "story_version_id": d.story_version_id,
                    "version_no": d.version_no,
                    "parent_story_version_id": d.parent_story_version_id,
                    "lineage_type": d.lineage_type,
                }
            )
            if d.parent_story_version_id:
                frontier.append(d.parent_story_version_id)
            frontier += [
                r.story_version_id
                for r in s.query(StoryVersionModel)
                .filter_by(
                    principal_id=principal["principal_id"],
                    parent_story_version_id=current,
                )
                .all()
            ]
        return {"root_requested_version_id": version_id, "versions": nodes}

    @app.get("/v1/story-versions/{version_id}/export", tags=["delivery"])
    def export(
        version_id,
        format: str = Query("json", pattern="^(json|script|characters)$"),
        principal=Depends(auth),
        s=Depends(session),
    ):
        d = version(s, version_id, principal["principal_id"])
        if format == "script":
            return PlainTextResponse(delivery_to_readable_script(d))
        if format == "characters":
            return build_character_role_map(d)
        return JSONResponse(content=d.model_dump(mode="json"))

    @app.get(
        "/v1/story-versions/{version_id}/render-manifest",
        response_model=RenderManifest,
        tags=["delivery"],
    )
    def render(version_id, principal=Depends(auth), s=Depends(session)):
        return rendering_manifest(version(s, version_id, principal["principal_id"]))

    @app.post("/v1/story-versions/{version_id}/simulate-tts", tags=["delivery"])
    def simulate(version_id, principal=Depends(auth), s=Depends(session)):
        return SimulatedTTSConsumer().consume(
            rendering_manifest(version(s, version_id, principal["principal_id"]))
        )

    @app.post(
        "/v1/characters/import", response_model=list[CharacterView], tags=["characters"]
    )
    def import_characters(
        value: CharacterBatch, principal=Depends(auth), s=Depends(session)
    ):
        if not principal.get("admin"):
            raise HTTPException(403, detail="ADMIN_REQUIRED")
        if len({c.character_id for c in value.characters}) != len(value.characters):
            raise ValueError("CHARACTER_ID_CONFLICT")
        library = CharacterLibrary(s)
        imported = {
            c.character_id: library.import_character(c)
            for c in sorted(value.characters, key=lambda c: c.character_id)
        }
        views = [imported[c.character_id] for c in value.characters]
        s.commit()
        return views

    @app.get("/v1/characters", response_model=list[CharacterView], tags=["characters"])
    def characters(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        principal=Depends(auth),
        s=Depends(session),
    ):
        ids = (
            s.query(CharacterTemplateModel.character_id)
            .filter_by(status="active")
            .distinct()
            .order_by(CharacterTemplateModel.character_id)
            .offset(offset)
            .limit(limit)
            .all()
        )
        library = CharacterLibrary(s)
        return [library.view(library.row(cid)) for (cid,) in ids]

    @app.get(
        "/v1/characters/{character_id}",
        response_model=CharacterView,
        tags=["characters"],
    )
    def character(
        character_id,
        version_number: int | None = Query(None, ge=1),
        principal=Depends(auth),
        s=Depends(session),
    ):
        library = CharacterLibrary(s)
        return library.view(library.row(character_id, version_number))

    @app.get(
        "/v1/characters/{character_id}/versions",
        response_model=list[CharacterView],
        tags=["characters"],
    )
    def character_versions(character_id, principal=Depends(auth), s=Depends(session)):
        rows = (
            s.query(CharacterTemplateModel)
            .filter_by(character_id=character_id)
            .order_by(CharacterTemplateModel.version)
            .all()
        )
        if not rows:
            raise WorkflowNotFoundError("CHARACTER_NOT_FOUND")
        return [CharacterLibrary.view(r) for r in rows]

    @app.get(
        "/v1/characters/{character_id}/runtime",
        response_model=list[RuntimeView],
        tags=["characters"],
    )
    def runtime_versions(character_id, principal=Depends(auth), s=Depends(session)):
        CharacterLibrary(s).row(character_id)
        rows = (
            s.query(CharacterProfileRevisionModel)
            .filter_by(
                principal_id=principal["principal_id"], character_id=character_id
            )
            .order_by(CharacterProfileRevisionModel.revision)
            .all()
        )
        return [
            RuntimeView(
                character_id=character_id, revision=r.revision, profile=r.profile_json
            )
            for r in rows
        ]

    @app.patch(
        "/v1/characters/{character_id}/runtime",
        response_model=RuntimeView,
        tags=["characters"],
    )
    def runtime_update(
        character_id, value: RuntimeUpdate, principal=Depends(auth), s=Depends(session)
    ):
        result = CharacterLibrary(s).update_runtime(
            principal["principal_id"],
            character_id,
            value.expected_revision,
            value.patch.model_dump(exclude_unset=True),
        )
        s.commit()
        return result

    return app
