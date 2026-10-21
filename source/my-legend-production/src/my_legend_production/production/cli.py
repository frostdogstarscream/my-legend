"""Independent migration, API, Worker, and contract export entry points."""
import argparse
import json
import os
from pathlib import Path
import secrets

from fastapi import FastAPI, HTTPException

from .api import register_production_routes
from .artifacts import ArtifactStore
from .contracts import ProductionRequest, StageResult, RunEvidence, ResumeRequest, ReconcileRequest, RemakeAudioRequest
from .repository import ProductionRepository
from .settings import ProductionSettings
from .worker import ProductionWorker
from .timeline import MeasuredTimeline, ArrangedTimeline, ControlledTimeline
from .audio_arrangement import AudioArrangement
from .audio_plan import SoundArrangementReview
from .compound_audio import CompoundArrangement
from .compound_audio_plan import CompoundArrangementReview
from .compound_timeline import CompoundTimeline
from .registry import enabled_modules, handlers
from .visual_contracts import VisualContext
from .playback import PlaybackManifest
from .sound_bridge import SoundProductionPackage


def services(settings=None):
    settings = settings or ProductionSettings.environment()
    return ProductionRepository(settings.dsn, settings.schema), ArtifactStore(settings.root / "artifacts")


def capabilities():
    enabled = enabled_modules()
    return {"schema_version": "production_capabilities/1.0", "execution": "background_worker",
            "outputs": (["script"] if "story" in enabled else []) + (["audio"] if {"story", "audio"} <= enabled else [])
                       + (["visual"] if {"story", "audio", "visual_plan", "visual_render", "integrate"} <= enabled else []),
            "modes": ["automatic", "reviewed"],
            "review_policies": ["standard", "listen_first"],
            "sound_workflows": ['sound_bridge/1'] if {'story','sound_plan','audio'} <= enabled else [],
            "target_duration_seconds": [90, 120, 150, 180, 300],
            "audio_revision": {"configured": {"story", "audio"} <= enabled, "requires_artifact": "AudioCache",
                               "scope": "same_frozen_script", "controls": ["regenerate_events", "regenerate_operations", "voice_bindings"],
                               "preserves_arrangement": True,
                               "preserves_original_job": True, "rebuilds_timeline": True},
            "visual_profiles": ["standing_performance/1", "pixel_performance/1", "basic_storyboard/1"],
            "modules": {name: {"configured": name in enabled} for name in ("story", "sound_plan", "audio", "visual_plan", "visual_render", "integrate")},
            "human_approval_automatic": False, "external_publication": False}


def make_app(repository=None, store=None):
    if repository is None:
        repository, store = services()
    token = os.environ["MY_LEGEND_SERVICE_TOKEN"]
    app = FastAPI(title="我的传说独立生产 API")

    def owner(request):
        if not secrets.compare_digest(request.headers.get("authorization", ""), "Bearer " + token):
            raise HTTPException(401, "UNAUTHORIZED")
        return "local-developer"

    register_production_routes(app, repository, store, owner, capabilities)
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["migrate", "serve", "worker", "export-schemas"])
    parser.add_argument("--port", type=int, default=8898)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "export-schemas":
        destination = args.output or Path(__file__).parent / "schemas"
        destination.mkdir(parents=True, exist_ok=True)
        for model in (ProductionRequest, StageResult, RunEvidence, ResumeRequest, ReconcileRequest, RemakeAudioRequest, MeasuredTimeline, ArrangedTimeline, ControlledTimeline, AudioArrangement, SoundArrangementReview, CompoundArrangement, CompoundArrangementReview, CompoundTimeline, VisualContext, PlaybackManifest, SoundProductionPackage):
            (destination / (model.__name__ + ".schema.json")).write_text(
                json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("Production contracts exported.")
        return
    repository, store = services()
    if args.command == "migrate":
        repository.migrate()
        print("Production namespace migration complete.")
    elif args.command == "serve":
        import uvicorn
        uvicorn.run(make_app(repository, store), host="127.0.0.1", port=args.port)
    else:
        ProductionWorker(repository, store, handlers(ProductionSettings.environment())).run()


if __name__ == "__main__":
    main()
