"""Task submission and read-only status endpoints; execution belongs to the Worker."""
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import FileResponse

from .artifacts import ArtifactStore
from .contracts import ArtifactRef, ProductionRequest, ResumeRequest, ReconcileRequest, RemakeAudioRequest, stage_graph
from .repository import ProductionError
from .playback import playback_view


def register_production_routes(app, repository, artifacts: ArtifactStore, owner, capabilities, reconciler=None):
    router = APIRouter(prefix="/api/v1")

    def translate(operation):
        try:
            return {"code": 0, "message": "ok", "data": operation()}
        except ProductionError as exc:
            status = 404 if exc.code == "JOB_NOT_FOUND" else 409
            raise HTTPException(status, exc.code) from None

    @router.post("/production-jobs", status_code=202)
    def create(body: ProductionRequest, request: Request):
        principal = owner(request)
        modules = capabilities().get("modules")
        if modules is not None:
            required = stage_graph(body)
            missing = [name for name in required if not modules.get(name, {}).get("configured")]
            if missing:
                raise HTTPException(409, {"code": "MODULE_NOT_CONFIGURED", "modules": missing})
        return translate(lambda: repository.get(repository.create(principal, body), principal))

    @router.get("/production-jobs")
    def listing(request: Request, limit: int = Query(default=30, ge=1, le=100),
                offset: int = Query(default=0, ge=0, le=10000), work_id: str | None = None):
        return translate(lambda: repository.list_jobs(owner(request), limit=limit, offset=offset, work_id=work_id))

    @router.post("/production-jobs/{job_id}/remake-audio", status_code=202)
    def remake_audio(job_id: str, body: RemakeAudioRequest, request: Request):
        principal = owner(request)
        from .audio_remake import remake_request
        source = translate(lambda: repository.get(job_id, principal))["data"]
        derived = translate(lambda: remake_request(source, body, artifacts))["data"]
        return create(derived, request)

    @router.get("/production-jobs/{job_id}")
    def get(job_id: str, request: Request):
        return translate(lambda: repository.get(job_id, owner(request)))

    @router.get("/production-jobs/{job_id}/artifacts")
    def results(job_id: str, request: Request):
        return translate(lambda: repository.get(job_id, owner(request))["artifacts"])

    @router.get("/production-jobs/{job_id}/playback")
    def playback(job_id: str, request: Request):
        return translate(lambda: playback_view(repository.get(job_id, owner(request)), artifacts))

    @router.get("/production-jobs/{job_id}/evidence")
    def evidence(job_id: str, request: Request):
        return translate(lambda: repository.execution_evidence(job_id, owner(request)))

    @router.get("/production-jobs/{job_id}/artifacts/{digest}")
    def artifact(job_id: str, digest: str, request: Request):
        job = translate(lambda: repository.get(job_id, owner(request)))["data"]
        matching = [a for a in job["artifacts"] if a["sha256"] == digest]
        if not matching:
            raise HTTPException(404, "ARTIFACT_NOT_FOUND")
        ref = ArtifactRef.model_validate(matching[0])
        path = translate(lambda: artifacts.verify(ref))["data"]
        return FileResponse(path, media_type=ref.media_type, headers={"Cache-Control": "private, no-store"})

    @router.post("/production-jobs/{job_id}/cancel")
    def cancel(job_id: str, request: Request):
        principal = owner(request)
        translate(lambda: repository.cancel(job_id, principal))
        job_root = artifacts.root.parent / 'jobs' / job_id
        job_root.mkdir(parents=True, exist_ok=True)
        (job_root / 'cancel.requested').touch()
        return translate(lambda: repository.get(job_id, principal))

    @router.post("/production-jobs/{job_id}/resume")
    def resume(job_id: str, body: ResumeRequest, request: Request):
        principal = owner(request)
        translate(lambda: repository.resume(job_id, principal, body.expected_revision, body.resolution))
        return translate(lambda: repository.get(job_id, principal))

    @router.post("/production-jobs/{job_id}/reconcile")
    def reconcile(job_id: str, body: ReconcileRequest, request: Request):
        principal = owner(request)
        from .reconciliation import ProductionReconciler
        service = reconciler or ProductionReconciler(repository)
        return translate(lambda: service.run(job_id, principal, body.expected_revision))

    @router.get("/production-capabilities")
    def options(request: Request):
        owner(request)
        return {"code": 0, "message": "ok", "data": capabilities()}

    app.include_router(router)
