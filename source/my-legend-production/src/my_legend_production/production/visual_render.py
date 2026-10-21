import json
from pathlib import Path
import time

from .contracts import ArtifactRef, StageResult
from .repository import ProductionError
from .timeline import MeasuredTimeline
from .visual_contracts import VisualContext
from .visual_execution import compile_track


class VisualRenderStage:
    def __init__(self, paths_file, catalog_loader):
        self.paths_file, self.catalog_loader = Path(paths_file), catalog_loader

    def __call__(self, ctx):
        refs = {a["kind"]: ArtifactRef.model_validate(a) for a in ctx.stage["artifacts"]}
        context = VisualContext.model_validate(ctx.store.json(refs["VisualContext"]))
        plan = ctx.store.json(refs["VisualPlan"])
        if plan["context_sha256"] != refs["VisualContext"].sha256 or not plan["execution_ready"]:
            raise ProductionError("VISUAL_PLAN_BINDING_INVALID")
        catalog = self.catalog_loader(ctx.request.visual_profile)
        if catalog["catalog_sha256"] != plan["catalog"]["catalog_sha256"]:
            raise ProductionError("VISUAL_ASSET_CHANGED")
        if (not plan['catalog'].get('execution_contract_sha256') or
                plan['catalog']['execution_contract_sha256'] != catalog.get('execution_contract_sha256')):
            raise ProductionError('VISUAL_EXECUTION_VERSION_CHANGED')
        raw_timeline = ctx.store.json(refs["MeasuredTimeline"])
        if raw_timeline.get('schema_version') == 'measured_timeline/1.1':
            raise ProductionError('VISUAL_TIMELINE_VERSION_UNSUPPORTED')
        timeline = MeasuredTimeline.model_validate(raw_timeline)
        if timeline.audio_sha256 != refs["AudioMix"].sha256:
            raise ProductionError("AUDIO_OUTPUT_HASH_MISMATCH")
        if time.time() >= ctx.stage["deadline"].timestamp():
            raise ProductionError("DEADLINE_EXCEEDED")
        track = compile_track(ctx.store.json(refs["StoryDelivery"]), context, timeline, catalog,
                              json.loads(self.paths_file.read_text(encoding="utf-8")), check=ctx.check_active)
        return StageResult(status="succeeded", source_version_id=context.source_version_id, technical_quality="passed",
            human_review="pending", artifacts=[ctx.store.put_json("VisualTrack", context.source_version_id, track)])
