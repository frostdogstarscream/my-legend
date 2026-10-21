"""Version-bound playback references; URLs never carry credentials or external hosts."""
from math import isclose
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from .contracts import ArtifactRef, Contract
from .repository import ProductionError
from .timeline import read_timeline, arrangement_binding_matches, has_arrangement


class PlaybackEntry(Contract):
    artifact: ArtifactRef
    content_path: str


class PlaybackManifest(Contract):
    schema_version: Literal["production_playback/1.0"] = "production_playback/1.0"
    job_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    work_id: str = Field(min_length=1, max_length=128)
    source_version_id: str = Field(min_length=1, max_length=128)
    script: PlaybackEntry
    audio: PlaybackEntry | None = None
    timeline: PlaybackEntry | None = None
    video: PlaybackEntry | None = None
    poster: PlaybackEntry | None = None
    sample_rate: int | None = Field(default=None, ge=1)
    total_frames: int | None = Field(default=None, ge=1)
    duration_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    visual_profile: Literal["standing_performance/1", "pixel_performance/1", "basic_storyboard/1"] | None = None
    human_review: Literal["pending", "not_required"] = "pending"
    publication: Literal["not_requested"] = "not_requested"
    delivery_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_bindings(self):
        kinds = {"script": ("StoryDelivery", "application/json"), "audio": ("AudioMix", "audio/wav"),
                 "timeline": ("MeasuredTimeline", "application/json"), "video": ("Video", "video/mp4"),
                 "poster": ("Poster", "image/png")}
        for name, (kind, media_type) in kinds.items():
            entry = getattr(self, name)
            if entry is None:
                continue
            if entry.artifact.source_version_id != self.source_version_id:
                raise ValueError("SOURCE_VERSION_MISMATCH")
            if (entry.artifact.kind, entry.artifact.media_type) != (kind, media_type):
                raise ValueError("PLAYBACK_ARTIFACT_KIND_MISMATCH")
            expected = f"/api/v1/production-jobs/{self.job_id}/artifacts/{entry.artifact.sha256}"
            if entry.content_path != expected:
                raise ValueError("PLAYBACK_CONTENT_PATH_MISMATCH")
        if (self.audio is None) != (self.timeline is None):
            raise ValueError("PLAYBACK_AUDIO_TIMELINE_REQUIRED")
        timing = (self.sample_rate, self.total_frames, self.duration_seconds)
        if self.audio is not None:
            if any(value is None for value in timing):
                raise ValueError("PLAYBACK_TIMING_REQUIRED")
            if not isclose(self.duration_seconds, self.total_frames / self.sample_rate, rel_tol=0, abs_tol=1e-9):
                raise ValueError("PLAYBACK_DURATION_MISMATCH")
        elif any(value is not None for value in timing):
            raise ValueError("PLAYBACK_TIMING_WITHOUT_AUDIO")
        if self.video is not None and (self.audio is None or self.poster is None or self.visual_profile is None):
            raise ValueError("PLAYBACK_VIDEO_DEPENDENCIES_REQUIRED")
        if self.video is None and (self.poster is not None or self.visual_profile is not None):
            raise ValueError("PLAYBACK_VISUAL_WITHOUT_VIDEO")
        return self


def playback_view(job, store):
    """Read committed results without advancing stages or writing derived artifacts."""
    refs = {value["kind"]: ArtifactRef.model_validate(value) for value in job["artifacts"]}
    view = {"schema_version": "production_playback_view/1.0", "job_id": job["id"],
            "job_status": job["status"], "available_outputs": [], "manifest": None}
    if "StoryDelivery" not in refs:
        return view
    version = job["source_version_id"]
    def entry(kind):
        ref = refs[kind]
        return {"artifact": ref.model_dump(),
                "content_path": f"/api/v1/production-jobs/{job['id']}/artifacts/{ref.sha256}"}
    value = {"job_id": job["id"], "work_id": job["work_id"], "source_version_id": version,
             "script": entry("StoryDelivery")}
    source_warnings = store.json(refs["StoryDelivery"]).get('quality_report', {}).get('warnings', [])
    if (job.get('request', {}).get('review_policy') == 'listen_first'
            or any('LISTEN_FIRST_SEMANTIC_REVIEW_SKIPPED' in w for w in source_warnings)):
        value['delivery_warnings'] = ['试听稿：未执行内容审批，剧情、声音完整性和听感由你试听后确认。']
    try:
        if "AudioMix" in refs:
            if "MeasuredTimeline" not in refs:
                raise ProductionError("PLAYBACK_AUDIO_TIMELINE_REQUIRED")
            timing = read_timeline(store.json(refs["MeasuredTimeline"]))
            execution = refs.get('AudioExecutionArrangement') or refs.get('AudioArrangement')
            if not arrangement_binding_matches(timing, execution):
                raise ProductionError('PLAYBACK_ARRANGEMENT_BINDING_MISMATCH')
            if has_arrangement(timing):
                store.verify(execution)
            if timing.source_version_id != version or timing.audio_sha256 != refs["AudioMix"].sha256:
                raise ProductionError("PLAYBACK_TIMELINE_BINDING_MISMATCH")
            value.update(audio=entry("AudioMix"), timeline=entry("MeasuredTimeline"),
                         sample_rate=timing.sample_rate, total_frames=timing.total_frames,
                         duration_seconds=timing.total_frames / timing.sample_rate)
            omissions = []
            for kind in ('AudioArrangementEvidence', 'AudioReport'):
                if kind in refs:
                    omissions.extend(store.json(refs[kind]).get('omissions', []))
            if omissions:
                value['delivery_warnings'] = value.get('delivery_warnings', []) + [
                    f"音频优先交付：{len(omissions)} 项声音或控制操作已省略，对白与旁白保留；关键音效及听感仍待核对。"
                ] + [f"省略：{o['text']}" for o in omissions]
        if "Video" in refs:
            if "PlaybackManifest" not in refs:
                raise ProductionError("PLAYBACK_VIDEO_MANIFEST_REQUIRED")
            saved = PlaybackManifest.model_validate(store.json(refs["PlaybackManifest"]))
            if saved.job_id != job["id"] or saved.work_id != job["work_id"] or saved.source_version_id != version:
                raise ProductionError("PLAYBACK_SOURCE_BINDING_MISMATCH")
            for name in ("script", "audio", "timeline", "video", "poster"):
                item = getattr(saved, name)
                if item is None or refs.get(item.artifact.kind) != item.artifact:
                    raise ProductionError("PLAYBACK_ARTIFACT_BINDING_MISMATCH")
            if any(getattr(saved, key) != value.get(key) for key in ("sample_rate", "total_frames", "duration_seconds")):
                raise ProductionError("PLAYBACK_TIMELINE_BINDING_MISMATCH")
            value = saved.model_dump(mode="json", exclude_none=True)
        manifest = PlaybackManifest.model_validate(value)
    except ValidationError:
        raise ProductionError("PLAYBACK_MANIFEST_INVALID") from None
    view["manifest"] = manifest.model_dump(mode="json", exclude_none=True)
    view["available_outputs"] = [name for name, item in (("script", manifest.script), ("audio", manifest.audio),
                                                        ("visual", manifest.video)) if item is not None]
    return view
