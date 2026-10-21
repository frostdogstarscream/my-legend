"""Measured event timeline shared by independent audio and visual modules."""
import json
from typing import Annotated, Literal
from pydantic import Field, model_validator
from .contracts import Contract, ArtifactRef, AudioEventRef


class TimelineEvent(Contract):
    sequence: int = Field(ge=1)
    episode_id: str
    scene_id: str
    line_id: str
    kind: Literal["dialogue", "narration", "sfx", "action", "music"]
    text: str
    identity_id: str | None = None
    start_frame: int = Field(ge=0)
    end_frame: int = Field(ge=1)
    duration_basis: Literal["measured_audio", "planned_silent_hold", "visual_estimate"]
    source_audio_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class MeasuredTimeline(Contract):
    schema_version: Literal["measured_timeline/1.0"] = "measured_timeline/1.0"
    source_version_id: str
    sample_rate: int = Field(ge=8000, le=192000)
    channels: int = Field(ge=1, le=2)
    total_frames: int = Field(ge=1)
    audio_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    events: list[TimelineEvent] = Field(min_length=1)
    word_alignment: Literal["not_available"] = "not_available"

    @model_validator(mode="after")
    def valid_events(self):
        seen = set()
        for i, event in enumerate(self.events, 1):
            identity = (event.episode_id, event.scene_id, event.line_id)
            if identity in seen or event.sequence != i:
                raise ValueError("TIMELINE_SOURCE_ORDER_INVALID")
            seen.add(identity)
            if not event.start_frame < event.end_frame <= self.total_frames:
                raise ValueError("TIMELINE_BOUNDS_INVALID")
            if event.kind in ("dialogue", "narration", "sfx", "music") and event.duration_basis != "measured_audio":
                raise ValueError("AUDIO_EVENT_MUST_BE_MEASURED")
        return self


class ArrangedAudioEvent(TimelineEvent):
    kind: Literal['dialogue', 'narration', 'sfx', 'music']
    duration_basis: Literal['measured_audio']
    render_role: Literal['speech', 'one_shot', 'background']
    processing: Literal['unchanged', 'looped_background', 'cropped_background', 'faded_background',
                        'looped_faded_background', 'cropped_faded_background'] = 'unchanged'
    parent_audio_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')

    @model_validator(mode='after')
    def valid_audio_role(self):
        if (self.kind in ('dialogue', 'narration')) != (self.render_role == 'speech'):
            raise ValueError('TIMELINE_RENDER_ROLE_INVALID')
        if self.processing != 'unchanged' and (self.render_role != 'background' or not self.parent_audio_sha256):
            raise ValueError('TIMELINE_PROCESSING_PROVENANCE_REQUIRED')
        if self.processing == 'unchanged' and self.parent_audio_sha256 is not None:
            raise ValueError('TIMELINE_UNCHANGED_PARENT_INVALID')
        return self


class NonAudioMarker(Contract):
    sequence: int = Field(ge=1)
    episode_id: str
    scene_id: str
    line_id: str
    kind: Literal['action']
    text: str
    identity_id: None = None
    start_frame: int = Field(ge=0)
    end_frame: int = Field(ge=0)
    duration_basis: Literal['non_audio_marker']
    source_audio_sha256: None = None
    marker_type: Literal['action_note', 'stop_sound']
    target: AudioEventRef | None = None

    @model_validator(mode='after')
    def valid_marker(self):
        if self.start_frame != self.end_frame:
            raise ValueError('TIMELINE_MARKER_MUST_BE_INSTANT')
        if (self.marker_type == 'stop_sound') != (self.target is not None):
            raise ValueError('TIMELINE_MARKER_TARGET_INVALID')
        return self


class ArrangedTimeline(Contract):
    schema_version: Literal['measured_timeline/1.1'] = 'measured_timeline/1.1'
    source_version_id: str
    arrangement_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    sample_rate: int = Field(ge=8000, le=192000)
    channels: int = Field(ge=1, le=2)
    total_frames: int = Field(ge=1)
    audio_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    events: list[Annotated[ArrangedAudioEvent | NonAudioMarker, Field(discriminator='duration_basis')]] = Field(min_length=1)
    word_alignment: Literal['not_available'] = 'not_available'

    @model_validator(mode='after')
    def valid_arrangement(self):
        by_source = {}
        speech = []
        for i, event in enumerate(self.events, 1):
            identity = (event.episode_id, event.scene_id, event.line_id)
            if identity in by_source or event.sequence != i:
                raise ValueError('TIMELINE_SOURCE_ORDER_INVALID')
            by_source[identity] = event
            if event.end_frame > self.total_frames:
                raise ValueError('TIMELINE_BOUNDS_INVALID')
            if isinstance(event, ArrangedAudioEvent):
                if event.start_frame >= event.end_frame:
                    raise ValueError('TIMELINE_BOUNDS_INVALID')
                if event.render_role == 'speech':
                    speech.append(event)
        if not any(isinstance(e, ArrangedAudioEvent) for e in self.events):
            raise ValueError('TIMELINE_AUDIO_REQUIRED')
        # First arranged-audio profile does not promise simultaneous dialogue.
        if any(left.end_frame > right.start_frame for left, right in zip(speech, speech[1:])):
            raise ValueError('TIMELINE_SPEECH_OVERLAP_UNSUPPORTED')
        stopped = set()
        for event in self.events:
            if isinstance(event, NonAudioMarker) and event.marker_type == 'stop_sound':
                identity = (event.target.episode_id, event.target.scene_id, event.target.line_id)
                target = by_source.get(identity)
                if not isinstance(target, ArrangedAudioEvent) or target.render_role != 'background':
                    raise ValueError('TIMELINE_STOP_TARGET_INVALID')
                if target.end_frame != event.start_frame:
                    raise ValueError('TIMELINE_STOP_NOT_EXECUTED')
                if identity in stopped:
                    raise ValueError('TIMELINE_DUPLICATE_STOP')
                stopped.add(identity)
        return self


class AutomatedAudioEvent(ArrangedAudioEvent):
    processing: Literal['unchanged', 'looped_background', 'cropped_background', 'faded_background',
                        'looped_faded_background', 'cropped_faded_background', 'automated_background'] = 'unchanged'


class SoundControlMarker(Contract):
    sequence: int = Field(ge=1)
    episode_id: str
    scene_id: str
    line_id: str
    kind: Literal['sfx','music','action']
    text: str
    identity_id: None = None
    start_frame: int = Field(ge=0)
    end_frame: int = Field(ge=0)
    duration_basis: Literal['sound_control']
    source_audio_sha256: None = None
    marker_type: Literal['continue_background','change_background','stop_sound']
    target: AudioEventRef
    transition_end_frame: int | None = Field(default=None, ge=0)
    from_gain_db: float | None = Field(default=None, ge=-60, le=12)
    to_gain_db: float | None = Field(default=None, ge=-60, le=12)

    @model_validator(mode='after')
    def valid_control(self):
        if self.start_frame != self.end_frame:
            raise ValueError('TIMELINE_CONTROL_MUST_BE_INSTANT')
        values=(self.transition_end_frame,self.from_gain_db,self.to_gain_db)
        if self.marker_type == 'change_background':
            if any(v is None for v in values) or self.transition_end_frame < self.start_frame or self.from_gain_db == self.to_gain_db:
                raise ValueError('TIMELINE_GAIN_TRANSITION_INVALID')
        elif any(v is not None for v in values):
            raise ValueError('TIMELINE_UNEXPECTED_GAIN_TRANSITION')
        return self


class ControlledTimeline(ArrangedTimeline):
    schema_version: Literal['measured_timeline/1.2'] = 'measured_timeline/1.2'
    events: list[Annotated[AutomatedAudioEvent | NonAudioMarker | SoundControlMarker,
                           Field(discriminator='duration_basis')]] = Field(min_length=1)

    @model_validator(mode='after')
    def valid_controls(self):
        by_key={(e.episode_id,e.scene_id,e.line_id):e for e in self.events}
        stopped={(e.target.episode_id,e.target.scene_id,e.target.line_id) for e in self.events
                 if isinstance(e,NonAudioMarker) and e.marker_type=='stop_sound'}
        transitions={}
        for e in self.events:
            if not isinstance(e,SoundControlMarker):
                continue
            key=(e.target.episode_id,e.target.scene_id,e.target.line_id)
            target=by_key.get(key)
            if not isinstance(target,AutomatedAudioEvent) or target.render_role!='background' or target.sequence>=e.sequence:
                raise ValueError('TIMELINE_CONTROL_TARGET_INVALID')
            if e.marker_type=='stop_sound':
                if target.end_frame!=e.start_frame or key in stopped:
                    raise ValueError('TIMELINE_STOP_NOT_EXECUTED')
                stopped.add(key)
            elif not target.start_frame<=e.start_frame<target.end_frame:
                raise ValueError('TIMELINE_CONTROL_OUTSIDE_BACKGROUND')
            if e.marker_type=='change_background':
                if e.transition_end_frame>target.end_frame or target.processing!='automated_background':
                    raise ValueError('TIMELINE_GAIN_NOT_EXECUTED')
                previous=transitions.get(key)
                if previous and (previous.transition_end_frame>e.start_frame or previous.to_gain_db!=e.from_gain_db):
                    raise ValueError('TIMELINE_GAIN_TRANSITION_CONFLICT')
                transitions[key]=e
        if any(isinstance(e,AutomatedAudioEvent) and e.processing=='automated_background' and
               (e.episode_id,e.scene_id,e.line_id) not in transitions for e in self.events):
            raise ValueError('TIMELINE_AUTOMATION_CONTROLS_REQUIRED')
        return self


def read_timeline(value) -> MeasuredTimeline | ArrangedTimeline | ControlledTimeline:
    if isinstance(value, (str, bytes, bytearray)):
        value = json.loads(value)
    from .compound_timeline import CompoundTimeline
    model = {'measured_timeline/1.1':ArrangedTimeline,'measured_timeline/1.2':ControlledTimeline,
             'measured_timeline/2.0':CompoundTimeline}.get(
        value.get('schema_version'),MeasuredTimeline) if isinstance(value,dict) else MeasuredTimeline
    return model.model_validate(value)


def arrangement_binding_matches(timeline, ref: ArtifactRef | None):
    return not has_arrangement(timeline) or (ref is not None and
        ref.kind in ('AudioArrangement','AudioExecutionArrangement') and ref.source_version_id == timeline.source_version_id and
        ref.sha256 == timeline.arrangement_sha256 and ref.media_type == 'application/json')


def has_arrangement(timeline):
    from .compound_timeline import CompoundTimeline
    return isinstance(timeline, (ArrangedTimeline, CompoundTimeline))


def timeline_source_rows(timeline):
    from .compound_timeline import CompoundTimeline
    if isinstance(timeline, CompoundTimeline):
        return [(e.source.episode_id, e.source.scene_id, e.source.line_id, e.kind, e.text) for e in timeline.events]
    return [(e.episode_id, e.scene_id, e.line_id, e.kind, e.text) for e in timeline.events]
