"""Lossless rendering contract; simulation never asserts audio/playback success."""

import hashlib
from typing import Any, Literal

from pydantic import Field, model_validator

from ..workflow.delivery import build_character_role_map, validate_delivery_integrity
from ..workflow.schemas import StoryDelivery
from .schemas import APIModel


class RenderTask(APIModel):
    task_id: str
    sequence: int = Field(ge=1)
    episode_id: str
    episode_index: int
    scene_id: str
    line_id: str
    kind: Literal["speech", "cue"]
    source_kind: Literal["dialogue", "narration", "sfx", "music", "action"]
    speaker_role_id: str | None = None
    narrator_id: str | None = None
    performer_id: str | None = None
    text: str
    performance: dict[str, Any]
    asset_refs: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_route(self):
        if self.kind == "speech" and (
            self.source_kind not in ("dialogue", "narration") or not self.performer_id
        ):
            raise ValueError("SPEECH_PERFORMER_REQUIRED")
        if self.kind == "cue" and self.source_kind in ("dialogue", "narration"):
            raise ValueError("INVALID_CUE_ROUTE")
        return self


class RenderManifest(APIModel):
    contract_version: Literal["1.0"] = "1.0"
    delivery_format_version: str
    story_version_id: str
    story_state: Literal["SUCCEEDED"] = "SUCCEEDED"
    audio_state: Literal["NOT_SYNTHESIZED"] = "NOT_SYNTHESIZED"
    playback_state: Literal["NOT_REPORTED"] = "NOT_REPORTED"
    role_map: dict[str, Any]
    tasks: list[RenderTask]

    @model_validator(mode="after")
    def order(self):
        if [t.sequence for t in self.tasks] != list(
            range(1, len(self.tasks) + 1)
        ) or len({t.task_id for t in self.tasks}) != len(self.tasks):
            raise ValueError("INVALID_RENDER_SEQUENCE")
        return self


def rendering_manifest(delivery: StoryDelivery):
    report = validate_delivery_integrity(delivery)
    if not report.valid or delivery.status != "READY":
        raise ValueError("DELIVERY_NOT_READY")
    assets = {
        c.performer_id: c.profile.get("asset_refs", {})
        for c in delivery.character_snapshot
        if c.performer_id
    }
    tasks = []
    for episode in delivery.episodes:
        for scene in episode.scenes:
            for line in scene.utterances:
                raw = line.model_dump(mode="json")
                uid = hashlib.sha256(
                    f"{delivery.story_version_id}:{episode.episode_id}:{scene.scene_id}:{line.line_id}".encode()
                ).hexdigest()
                tasks.append(
                    RenderTask(
                        task_id=uid,
                        sequence=len(tasks) + 1,
                        episode_id=episode.episode_id,
                        episode_index=episode.index,
                        scene_id=scene.scene_id,
                        line_id=line.line_id,
                        kind="speech"
                        if line.kind in ("dialogue", "narration")
                        else "cue",
                        source_kind=line.kind,
                        speaker_role_id=line.speaker_role_id,
                        narrator_id=line.narrator_id,
                        performer_id=line.performer_id,
                        text=line.text,
                        performance={
                            k: raw[k]
                            for k in (
                                "emotion",
                                "tone_instruction",
                                "emphasis",
                                "speech_rate",
                                "pitch",
                                "volume",
                                "pauses",
                                "delivery_note",
                                "audio_cue_ref",
                            )
                        },
                        asset_refs=assets.get(line.performer_id, {}),
                    )
                )
    return RenderManifest(
        delivery_format_version=delivery.delivery_format_version,
        story_version_id=delivery.story_version_id,
        role_map=build_character_role_map(delivery),
        tasks=tasks,
    )


class SimulatedTTSConsumer:
    def consume(self, manifest: RenderManifest):
        manifest = RenderManifest.model_validate(manifest.model_dump(mode="json"))
        return {
            "story_version_id": manifest.story_version_id,
            "simulation_only": True,
            "contract_state": "SIMULATION_VERIFIED",
            "story_state": "SUCCEEDED",
            "audio_state": "NOT_SYNTHESIZED",
            "playback_state": "NOT_REPORTED",
            "speech_count": sum(t.kind == "speech" for t in manifest.tasks),
            "cue_count": sum(t.kind == "cue" for t in manifest.tasks),
            "assembly_sequence": [
                {
                    "task_id": t.task_id,
                    "sequence": t.sequence,
                    "placeholder_ref": f"simulation://{t.task_id}",
                }
                for t in manifest.tasks
            ],
        }
