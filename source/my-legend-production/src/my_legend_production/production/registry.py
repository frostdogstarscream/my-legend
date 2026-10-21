"""Explicit deployment module selection; API preflight and Worker share this registry."""
import os
import json
from pathlib import Path

from .repository import ProductionError


def enabled_modules():
    modules = {name.strip() for name in os.environ.get("MY_LEGEND_PRODUCTION_MODULES", "").split(",") if name.strip()}
    if modules - {"story", "sound_plan", "audio", "visual_plan", "visual_render", "integrate"}:
        raise ProductionError("UNSUPPORTED_PRODUCTION_MODULE_CONFIG")
    return modules


def handlers(settings):
    from .story import StoryClient, StoryStage
    from .audio import AudioStage
    modules = enabled_modules()
    result = {}
    if "story" in modules:
        result["story"] = StoryStage(StoryClient(settings.story_url, os.environ["MY_LEGEND_STORY_TOKEN"]))
    if modules & {'audio','sound_plan'}:
        from .audio_model import AudioModel
        from .audio_simple import SimpleAudioPlanner
        model = AudioModel(os.environ['MY_LEGEND_AUDIO_MODEL_BASE_URL'],os.environ['MY_LEGEND_AUDIO_MODEL_KEY'],
                           os.environ['MY_LEGEND_AUDIO_MODEL_ID'])
        if 'audio' in modules:
            result["audio"] = AudioStage(settings.root, Path(os.environ["MY_LEGEND_WORKSPACE"]),planner=SimpleAudioPlanner(model))
        if 'sound_plan' in modules:
            from .sound_bridge import SoundBridgeStage
            result['sound_plan'] = SoundBridgeStage(model)
    if "visual_plan" in modules:
        from .visual_catalog import catalog_from_paths
        from .visual_model import VisualModel
        from .visual_plan import VisualPlanStage
        from .visual_execution import compile_preview
        model = VisualModel(os.environ["MY_LEGEND_VISUAL_MODEL_BASE_URL"], os.environ["MY_LEGEND_VISUAL_MODEL_KEY"],
                            os.environ["MY_LEGEND_VISUAL_MODEL_ID"])
        paths = Path(os.environ["MY_LEGEND_WORKFLOW_PATHS"])
        result["visual_plan"] = VisualPlanStage(model, lambda profile: catalog_from_paths(paths, profile),
            execution_preview=lambda delivery,context,catalog,check: compile_preview(delivery,context,catalog,
                json.loads(paths.read_text(encoding='utf-8')),check=check))
    if "visual_render" in modules:
        from .visual_catalog import catalog_from_paths
        from .visual_render import VisualRenderStage
        paths = Path(os.environ["MY_LEGEND_WORKFLOW_PATHS"])
        result["visual_render"] = VisualRenderStage(paths, lambda profile: catalog_from_paths(paths, profile))
    if "integrate" in modules:
        from .integrate import IntegrateStage
        result["integrate"] = IntegrateStage(settings.root, os.environ["MY_LEGEND_WORKFLOW_PATHS"], os.environ["MY_LEGEND_MEDIA_PYTHON"])
    return result
