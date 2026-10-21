"""Assemble verified same-version media and publish references through the owning job."""
from hashlib import sha256
import json
from pathlib import Path
import time

from ..worker import save
from .contracts import ArtifactRef, StageResult
from .repository import ProductionError
from .playback import PlaybackManifest
from .process_tree import run_process_tree, media_environment


class IntegrateStage:
    def __init__(self, root, paths_file, python, *, runner=None):
        self.root, self.paths_file, self.python = Path(root), Path(paths_file), Path(python)
        self.runner = runner

    def launch(self, root, timeout, *, check=None):
        environment = media_environment()
        with (root / "render.log").open("ab") as log:
            return run_process_tree([str(self.python), "-m", "my_legend_production.production.integrate_job",
                "--request", str(root / "request.json"), "--root", str(root)], env=environment,
                log=log, timeout=timeout, cancel_file=root.parent.parent / "cancel.requested", check=check)

    def __call__(self, ctx):
        refs = {a["kind"]: ArtifactRef.model_validate(a) for a in ctx.stage["artifacts"]}
        track = ctx.store.json(refs["VisualTrack"])
        plan = ctx.store.json(refs["VisualPlan"])
        version = refs["AudioMix"].source_version_id
        if any(refs[k].source_version_id != version for k in ("MeasuredTimeline", "VisualTrack", "VisualPlan", "StoryDelivery")):
            raise ProductionError("SOURCE_VERSION_MISMATCH")
        paths = json.loads(self.paths_file.read_text(encoding="utf-8"))
        root = self.root / "jobs" / ctx.stage["job_id"] / "integrate" / ctx.stage["lease_token"]
        root.mkdir(parents=True, exist_ok=True)
        save(root / "request.json", {"inputs": {kind: {"path": str(ctx.store.verify(refs[kind])), "sha256": refs[kind].sha256}
            for kind in ("AudioMix", "MeasuredTimeline", "VisualTrack")}, "ffmpeg": paths["ffmpeg"],
            "assets": str(Path(paths["visual_root"]) / "pixel-story-mobile/packages/story_player/assets"),
            "asset_hashes": plan["catalog"]["catalog"]["file_hashes"],
            "synthetic_audio": bool(ctx.store.json(refs["AudioReport"]).get("fixture_only", False)) if "AudioReport" in refs else True})
        remaining = ctx.stage["deadline"].timestamp() - time.time()
        if remaining <= 0:
            raise ProductionError("DEADLINE_EXCEEDED")
        code = self.runner(root, remaining) if self.runner else self.launch(root, remaining, check=ctx.check_active)
        if code:
            raise ProductionError("MEDIA_INTEGRATION_FAILED")
        report = json.loads((root / "result.json").read_text(encoding="utf-8"))
        video = (root / "video.mp4").read_bytes()
        if sha256(video).hexdigest() != report["sha256"] or report["decode_validation"] != "passed":
            raise ProductionError("VIDEO_VERIFICATION_FAILED")
        if report["source_version_id"] != version or report["audio_sha256"] != refs["AudioMix"].sha256:
            raise ProductionError("SOURCE_VERSION_MISMATCH")
        vref = ctx.store.put("Video", version, video, "video/mp4")
        poster = ctx.store.put("Poster", version, (root / "poster.png").read_bytes(), "image/png")
        def entry(ref):
            return {"artifact": ref.model_dump(), "content_path": "/api/v1/production-jobs/" + ctx.stage["job_id"] + "/artifacts/" + ref.sha256}
        manifest = PlaybackManifest.model_validate({"schema_version": "production_playback/1.0", "job_id": ctx.stage["job_id"], "work_id": ctx.stage["work_id"], "source_version_id": version,
            "duration_seconds": track["total_frames"] / track["sample_rate"], "sample_rate": track["sample_rate"],
            "total_frames": track["total_frames"], "script": entry(refs["StoryDelivery"]), "audio": entry(refs["AudioMix"]),
            "video": entry(vref), "poster": entry(poster), "timeline": entry(refs["MeasuredTimeline"]),
            "visual_profile": ctx.request.visual_profile, "human_review": "pending", "publication": "not_requested"}).model_dump(mode="json", exclude_none=True)
        return StageResult(status="succeeded", source_version_id=version, technical_quality="passed", human_review="pending",
            artifacts=[vref, poster, ctx.store.put_json("PlaybackManifest", version, manifest),
                       ctx.store.put_json("RenderReport", version, report)])
