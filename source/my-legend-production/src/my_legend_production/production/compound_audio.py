"""Compound sound decisions bound to unique, unchanged story lines.

This is an offline contract and lowering layer. The production planner does not
select this version until Worker, cache, playback and audit support is complete.
"""
from hashlib import sha256
import json
from typing import Literal

from pydantic import ConfigDict, Field, model_validator, model_serializer

from .audio_arrangement import AudioArrangement, MODE_OPTIONS, bind_arrangement, resolve_frames
from .contracts import AudioEventRef, Contract


OperationId = str
AUDIBLE_MODES = ('speech', 'one_shot', 'background')


class OperationAnchor(Contract):
    operation_id: str | None = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    point: Literal['start', 'end'] = 'start'
    offset_ms: int = Field(default=0, ge=-60000, le=60000)

    @model_validator(mode='after')
    def origin(self):
        if self.operation_id is None and self.point != 'start':
            raise ValueError('COMPOUND_ORIGIN_HAS_NO_END')
        return self


def operation_schema_rules():
    rules = []
    for modes, fields in (
        (['background'], {'end': 'object'}),
        (['stop_sound'], {'stop_target': 'string'}),
        (['continue_background', 'change_background'], {'control_target': 'string'}),
        (['change_background'], {'transition_end': 'object', 'target_gain_db': 'number'}),
    ):
        rules.append({'if': {'properties': {'mode': {'enum': modes}}, 'required': ['mode']},
                      'then': {'required': list(fields), 'properties': {k: {'type': v} for k, v in fields.items()}},
                      'else': {'properties': {k: {'type': 'null'} for k in fields}}})
    rules += [
        {'if': {'properties': {'mode': {'not': {'const': 'background'}}}, 'required': ['mode']},
         'then': {'properties': {'fade_out_ms': {'const': 0}}}},
        {'if': {'properties': {'mode': {'enum': ['action_note', 'stop_sound', 'continue_background', 'change_background']}}, 'required': ['mode']},
         'then': {'properties': {'gain_db': {'const': 0}}}},
    ]
    return rules


class SoundOperation(Contract):
    model_config = ConfigDict(json_schema_extra={'allOf': operation_schema_rules()})
    operation_id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    mode: Literal['speech', 'one_shot', 'background', 'action_note', 'stop_sound',
                  'continue_background', 'change_background']
    # This excerpt is also the material prompt, not a rewritten whole source line.
    cue_text: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=2000)
    start: OperationAnchor
    end: OperationAnchor | None = None
    stop_target: str | None = Field(default=None, pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    control_target: str | None = Field(default=None, pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    transition_end: OperationAnchor | None = None
    target_gain_db: float | None = Field(default=None, ge=-60, le=12)
    gain_db: float = Field(default=0.0, ge=-60, le=12)
    fade_out_ms: int = Field(default=0, ge=0, le=10000)


class CompoundSourceEvent(Contract):
    model_config = ConfigDict(json_schema_extra={'allOf': [
        {'if': {'properties': {'kind': {'const': kind}}, 'required': ['kind'],
                'not': {'properties': {'sound_intent': {'enum': ['control_only','omitted_for_delivery']}}, 'required': ['sound_intent']}},
         'then': {'properties': {'operations': {'items': {'properties': {'mode': {'enum': modes}}},
                    **({'minItems': 1, 'maxItems': 1} if kind in ('dialogue', 'narration') else {})}}}}
        for kind, modes in MODE_OPTIONS.items()]})
    source: AudioEventRef
    kind: Literal['dialogue', 'narration', 'sfx', 'music', 'action']
    text: str = Field(min_length=1)
    identity_id: str | None = None
    sound_intent: Literal['control_only','omitted_for_delivery'] | None = None
    operations: list[SoundOperation] = Field(min_length=1, max_length=16)

    @model_serializer(mode='wrap')
    def compatible_dump(self, handler):
        data = handler(self)
        if self.sound_intent is None:
            data.pop('sound_intent', None)
        return data

    @model_validator(mode='after')
    def preserve_line(self):
        if any(op.cue_text not in self.text or not op.cue_text.strip() for op in self.operations):
            raise ValueError('COMPOUND_CUE_NOT_LITERAL')
        modes = MODE_OPTIONS[self.kind]
        if self.sound_intent in ('control_only','omitted_for_delivery'):
            if self.kind not in ('sfx', 'music', 'action'):
                raise ValueError('COMPOUND_CONTROL_INTENT_KIND_INVALID')
            modes = ('action_note', 'stop_sound', 'continue_background')
            if self.sound_intent == 'omitted_for_delivery':
                modes = tuple(set(MODE_OPTIONS[self.kind]) | {'action_note'})
        if any(op.mode not in modes for op in self.operations):
            raise ValueError('COMPOUND_SOURCE_KIND_MISMATCH')
        if self.kind in ('dialogue', 'narration'):
            if len(self.operations) != 1 or self.operations[0].cue_text != self.text:
                raise ValueError('COMPOUND_SPEECH_MUST_BE_COMPLETE')
        elif self.identity_id is not None:
            raise ValueError('COMPOUND_NON_SPEECH_IDENTITY_INVALID')
        return self


class CompoundArrangement(Contract):
    schema_version: Literal['audio_arrangement/2.0'] = 'audio_arrangement/2.0'
    source_version_id: str = Field(min_length=1)
    delivery_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    sample_rate: Literal[48000] = 48000
    channels: Literal[1] = 1
    duck_gain_db: float = Field(default=-9.0, ge=-30, le=0)
    duck_attack_ms: int = Field(default=30, ge=0, le=1000)
    duck_release_ms: int = Field(default=100, ge=0, le=2000)
    events: list[CompoundSourceEvent] = Field(min_length=1, max_length=1000)

    @model_validator(mode='after')
    def executable_operations(self):
        sources = [(e.source.episode_id, e.source.scene_id, e.source.line_id) for e in self.events]
        if len(set(sources)) != len(sources):
            raise ValueError('COMPOUND_DUPLICATE_SOURCE')
        # Reuse the proven DAG, mode, target and gain validation on explicit operation keys.
        lower_operations(self)
        return self


def lower_operations(plan):
    """Return internal atomic choices and op -> original source provenance.

    Internal line_id is an operation key, never a modified StoryDelivery line.
    The result MUST NOT be published as a source-bound AudioArrangement 1.x.
    """
    pairs = [(event, op) for event in plan.events for op in event.operations]
    if len(pairs) > 1000:
        raise ValueError('COMPOUND_OPERATION_LIMIT_EXCEEDED')
    ids = [op.operation_id for _, op in pairs]
    if len(set(ids)) != len(ids):
        raise ValueError('COMPOUND_DUPLICATE_OPERATION')
    refs = {op.operation_id: dict(episode_id=event.source.episode_id, scene_id=event.source.scene_id,
                                  line_id=op.operation_id) for event, op in pairs}
    operations = {op.operation_id: op for _, op in pairs}
    def reference(key):
        if key not in refs:
            raise ValueError('COMPOUND_UNKNOWN_OPERATION')
        return refs[key]
    def anchor(value):
        return None if value is None else dict(event=reference(value.operation_id) if value.operation_id else None,
                                               point=value.point, offset_ms=value.offset_ms)
    rows, provenance = [], {}
    for index, (event, op) in enumerate(pairs):
        target = op.control_target or op.stop_target
        if target is not None:
            reference(target)
            if ids.index(target) >= index or operations[target].mode != 'background':
                raise ValueError('COMPOUND_CONTROL_TARGET_INVALID')
        rows.append(dict(source=refs[op.operation_id], kind='action' if event.sound_intent and op.mode=='action_note' else event.kind, text=op.cue_text,
                         identity_id=event.identity_id, mode=op.mode, evidence_quote=op.cue_text, reason=op.reason,
                         start=anchor(op.start), end=anchor(op.end),
                         stop_target=reference(op.stop_target) if op.stop_target else None,
                         control_target=reference(op.control_target) if op.control_target else None,
                         transition_end=anchor(op.transition_end), target_gain_db=op.target_gain_db,
                         gain_db=op.gain_db, fade_out_ms=op.fade_out_ms))
        provenance[op.operation_id] = event.source.model_dump()
    atomic = AudioArrangement(schema_version='audio_arrangement/1.1',
        **plan.model_dump(exclude={'schema_version', 'events'}), events=rows)
    return atomic, provenance


def bind_compound(plan: CompoundArrangement, delivery_bytes: bytes):
    # Both versions retain precisely the same unique source envelope.
    return bind_arrangement(plan, delivery_bytes)


def compound_bytes(plan: CompoundArrangement):
    return json.dumps(plan.model_dump(), ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def resolve_compound_frames(plan, measured_operation_frames, *, max_output_seconds=1800):
    atomic, _ = lower_operations(plan)
    needed = {e.source.line_id for e in atomic.events if e.mode in AUDIBLE_MODES}
    if set(measured_operation_frames) != needed:
        raise ValueError('COMPOUND_MEASURED_ASSETS_MISMATCH')
    durations = {(e.source.episode_id, e.source.scene_id, e.source.line_id): measured_operation_frames[e.source.line_id]
                 for e in atomic.events if e.mode in AUDIBLE_MODES}
    frames = resolve_frames(atomic, durations, max_output_seconds=max_output_seconds)
    return {(key[2], point): value for (key, point), value in frames.items()}


def read_audio_arrangement(value):
    if isinstance(value,(str,bytes,bytearray)):
        value=json.loads(value)
    model=CompoundArrangement if value.get('schema_version')=='audio_arrangement/2.0' else AudioArrangement
    return model.model_validate(value)
