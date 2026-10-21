"""Source-preserving measured timeline for multiple operations per story line."""
from hashlib import sha256
from typing import Literal

from pydantic import Field, model_validator

from .compound_audio import CompoundSourceEvent, AUDIBLE_MODES, compound_bytes
from .contracts import Contract
from .timeline import ControlledTimeline


class CompiledOperation(Contract):
    operation_id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    cue_text: str = Field(min_length=1)
    mode: Literal['speech', 'one_shot', 'background', 'action_note', 'stop_sound',
                  'continue_background', 'change_background']
    start_frame: int = Field(ge=0)
    end_frame: int = Field(ge=0)
    source_audio_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    processing: Literal['unchanged', 'looped_background', 'cropped_background', 'faded_background',
                        'looped_faded_background', 'cropped_faded_background', 'automated_background'] | None = None
    parent_audio_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    target_operation_id: str | None = Field(default=None, pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    transition_end_frame: int | None = Field(default=None, ge=0)
    from_gain_db: float | None = Field(default=None, ge=-60, le=12)
    to_gain_db: float | None = Field(default=None, ge=-60, le=12)

    @model_validator(mode='after')
    def operation_shape(self):
        if self.mode in AUDIBLE_MODES:
            if self.source_audio_sha256 is None or self.processing is None:
                raise ValueError('COMPOUND_TIMELINE_AUDIO_REQUIRED')
            if self.target_operation_id is not None or any(x is not None for x in
                    (self.transition_end_frame,self.from_gain_db,self.to_gain_db)):
                raise ValueError('COMPOUND_TIMELINE_AUDIO_CONTROL_CONFLICT')
        else:
            if any(x is not None for x in (self.source_audio_sha256,self.processing,self.parent_audio_sha256)):
                raise ValueError('COMPOUND_TIMELINE_MARKER_HAS_AUDIO')
            if (self.mode != 'action_note') != (self.target_operation_id is not None):
                raise ValueError('COMPOUND_TIMELINE_TARGET_REQUIRED')
            if self.mode == 'action_note' and any(x is not None for x in
                    (self.transition_end_frame,self.from_gain_db,self.to_gain_db)):
                raise ValueError('COMPOUND_TIMELINE_NOTE_HAS_TRANSITION')
        return self


class CompoundTimelineSource(CompoundSourceEvent):
    operations: list[CompiledOperation] = Field(min_length=1, max_length=16)


class CompoundTimeline(Contract):
    schema_version: Literal['measured_timeline/2.0'] = 'measured_timeline/2.0'
    source_version_id: str = Field(min_length=1)
    arrangement_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    sample_rate: Literal[48000] = 48000
    channels: Literal[1] = 1
    total_frames: int = Field(ge=1)
    audio_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    events: list[CompoundTimelineSource] = Field(min_length=1, max_length=1000)
    word_alignment: Literal['not_available'] = 'not_available'

    @model_validator(mode='after')
    def measured_execution(self):
        atomic_timeline(self)
        return self


def atomic_timeline(timeline):
    pairs = [(e, op) for e in timeline.events for op in e.operations]
    sources = [tuple(e.source.model_dump().values()) for e in timeline.events]
    if len(set(sources)) != len(sources):
        raise ValueError('COMPOUND_TIMELINE_DUPLICATE_SOURCE')
    refs = {op.operation_id: dict(episode_id=e.source.episode_id, scene_id=e.source.scene_id,
                                  line_id=op.operation_id) for e, op in pairs}
    if len(refs) != len(pairs) or len(pairs) > 1000:
        raise ValueError('COMPOUND_TIMELINE_OPERATION_KEYS_INVALID')
    events = []
    for sequence, (source, op) in enumerate(pairs, 1):
        common = dict(sequence=sequence, **refs[op.operation_id], kind='action' if source.sound_intent and op.mode=='action_note' else source.kind, text=op.cue_text,
                      identity_id=source.identity_id, start_frame=op.start_frame, end_frame=op.end_frame)
        if op.mode in AUDIBLE_MODES:
            events.append(dict(**common, duration_basis='measured_audio', render_role=op.mode,
                               source_audio_sha256=op.source_audio_sha256, processing=op.processing,
                               parent_audio_sha256=op.parent_audio_sha256))
        elif op.mode == 'action_note':
            events.append(dict(**common, duration_basis='non_audio_marker', marker_type=op.mode))
        else:
            if op.target_operation_id not in refs:
                raise ValueError('COMPOUND_TIMELINE_UNKNOWN_TARGET')
            events.append(dict(**common, duration_basis='sound_control', marker_type=op.mode,
                               target=refs[op.target_operation_id], transition_end_frame=op.transition_end_frame,
                               from_gain_db=op.from_gain_db, to_gain_db=op.to_gain_db))
    return ControlledTimeline(**timeline.model_dump(exclude={'schema_version', 'events'}), events=events)


def bind_compound_timeline(plan, timeline):
    if timeline.source_version_id != plan.source_version_id or timeline.arrangement_sha256 != sha256(compound_bytes(plan)).hexdigest():
        raise ValueError('COMPOUND_TIMELINE_PLAN_BINDING_MISMATCH')
    def source_rows(obj):
        return [(e.source, e.kind, e.text, e.identity_id, e.sound_intent,
                 [(o.operation_id,o.mode,o.cue_text) for o in e.operations]) for e in obj.events]
    if source_rows(timeline) != source_rows(plan):
        raise ValueError('COMPOUND_TIMELINE_SOURCE_BINDING_MISMATCH')
    for source, rendered in zip(plan.events,timeline.events):
        for choice, op in zip(source.operations,rendered.operations):
            if op.target_operation_id != (choice.control_target or choice.stop_target):
                raise ValueError('COMPOUND_TIMELINE_CONTROL_BINDING_MISMATCH')
            if choice.mode == 'change_background' and op.to_gain_db != choice.target_gain_db:
                raise ValueError('COMPOUND_TIMELINE_GAIN_BINDING_MISMATCH')
    return timeline
