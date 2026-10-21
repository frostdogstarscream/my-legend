"""Source-bound sound decisions. No text rewriting or implicit scene boundaries."""
from hashlib import sha256
import json
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from .contracts import AudioEventRef, Contract


MODE_OPTIONS = {'dialogue': ['speech'], 'narration': ['speech'],
    'sfx': ['one_shot','background','continue_background','change_background','stop_sound'],
    'music': ['one_shot','background','continue_background','change_background','stop_sound'],
    'action': ['action_note','stop_sound','continue_background','change_background']}


def decision_schema_rules():
    rules = [{'if': {'properties': {'kind': {'const': kind}}, 'required': ['kind']},
              'then': {'properties': {'mode': {'enum': modes}}}}
             for kind, modes in MODE_OPTIONS.items()]
    # Match the runtime mode/field contract, including omitted versus null values.
    for modes, fields in (
        (['background'], {'end': 'object'}),
        (['stop_sound'], {'stop_target': 'object'}),
        (['continue_background', 'change_background'], {'control_target': 'object'}),
        (['change_background'], {'transition_end': 'object', 'target_gain_db': 'number'}),
    ):
        rules.append({'if': {'properties': {'mode': {'enum': modes}}, 'required': ['mode']},
                      'then': {'required': list(fields), 'properties': {k: {'type': v} for k, v in fields.items()}},
                      'else': {'properties': {k: {'type': 'null'} for k in fields}}})
    rules.append({'if': {'properties': {'mode': {'not': {'const': 'background'}}}, 'required': ['mode']},
                  'then': {'properties': {'fade_out_ms': {'const': 0}}}})
    rules.append({'if': {'properties': {'mode': {'enum': ['action_note', 'stop_sound', 'continue_background', 'change_background']}}, 'required': ['mode']},
                  'then': {'properties': {'gain_db': {'const': 0}}}})
    return rules


def source_key(ref):
    return (ref.episode_id, ref.scene_id, ref.line_id)


class SoundAnchor(Contract):
    event: AudioEventRef | None
    point: Literal['start', 'end'] = 'start'
    offset_ms: int = Field(default=0, ge=-60000, le=60000)

    @model_validator(mode='after')
    def origin(self):
        if self.event is None and self.point != 'start':
            raise ValueError('ARRANGEMENT_ORIGIN_HAS_NO_END')
        return self


class SoundDecision(Contract):
    model_config = ConfigDict(json_schema_extra={'allOf': decision_schema_rules()})
    source: AudioEventRef
    kind: Literal['dialogue', 'narration', 'sfx', 'music', 'action']
    text: str = Field(min_length=1)
    identity_id: str | None = None
    mode: Literal['speech', 'one_shot', 'background', 'action_note', 'stop_sound', 'continue_background', 'change_background']
    # Evidence is a literal excerpt; reason records the model's production choice.
    evidence_quote: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=2000)
    start: SoundAnchor
    end: SoundAnchor | None = None
    stop_target: AudioEventRef | None = None
    gain_db: float = Field(default=0.0, ge=-60, le=12)
    fade_out_ms: int = Field(default=0, ge=0, le=10000)
    control_target: AudioEventRef | None = None
    transition_end: SoundAnchor | None = None
    target_gain_db: float | None = Field(default=None, ge=-60, le=12)

    @model_validator(mode='after')
    def valid_mode(self):
        if self.mode not in MODE_OPTIONS[self.kind]:
            raise ValueError('ARRANGEMENT_SOURCE_KIND_MISMATCH')
        if self.evidence_quote not in self.text:
            raise ValueError('ARRANGEMENT_EVIDENCE_NOT_LITERAL')
        if self.mode == 'background' and self.end is None:
            raise ValueError('ARRANGEMENT_BACKGROUND_END_REQUIRED')
        if self.mode != 'background' and self.end is not None:
            raise ValueError('ARRANGEMENT_END_ONLY_FOR_BACKGROUND')
        if (self.mode == 'stop_sound') != (self.stop_target is not None):
            raise ValueError('ARRANGEMENT_STOP_TARGET_REQUIRED')
        if self.fade_out_ms and self.mode != 'background':
            raise ValueError('ARRANGEMENT_ONLY_BACKGROUND_MAY_FADE')
        if self.mode in ('action_note','stop_sound','continue_background','change_background') and self.gain_db:
            raise ValueError('ARRANGEMENT_MARKER_HAS_NO_GAIN')
        if (self.mode in ('continue_background','change_background')) != (self.control_target is not None):
            raise ValueError('ARRANGEMENT_CONTROL_TARGET_REQUIRED')
        if self.mode == 'change_background':
            if self.transition_end is None or self.target_gain_db is None:
                raise ValueError('ARRANGEMENT_GAIN_TRANSITION_REQUIRED')
        elif self.transition_end is not None or self.target_gain_db is not None:
            raise ValueError('ARRANGEMENT_UNEXPECTED_GAIN_TRANSITION')
        return self


class AudioArrangement(Contract):
    schema_version: Literal['audio_arrangement/1.0', 'audio_arrangement/1.1'] = 'audio_arrangement/1.0'
    source_version_id: str = Field(min_length=1)
    delivery_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    sample_rate: Literal[48000] = 48000
    channels: Literal[1] = 1
    duck_gain_db: float = Field(default=-9.0, ge=-30, le=0)
    duck_attack_ms: int = Field(default=30, ge=0, le=1000)
    duck_release_ms: int = Field(default=100, ge=0, le=2000)
    events: list[SoundDecision] = Field(min_length=1, max_length=1000)

    @model_validator(mode='after')
    def valid_references(self):
        entries = {source_key(e.source): e for e in self.events}
        if len(entries) != len(self.events):
            raise ValueError('ARRANGEMENT_DUPLICATE_SOURCE')
        edges = {}
        stopped = set()
        for key, event in entries.items():
            if self.schema_version == 'audio_arrangement/1.0' and (event.mode in ('continue_background','change_background') or
                    event.kind != 'action' and event.mode == 'stop_sound'):
                raise ValueError('ARRANGEMENT_CONTROL_REQUIRES_VERSION_1_1')
            anchors = [('start', event.start), ('end', event.end)]
            if event.transition_end is not None:
                anchors.append(('transition_end',event.transition_end))
            for point, anchor in anchors:
                node = (key, point)
                if point == 'end' and anchor is None:
                    edges[node] = [(key, 'start')]
                elif anchor.event is None:
                    edges[node] = []
                else:
                    target = source_key(anchor.event)
                    if target not in entries:
                        raise ValueError('ARRANGEMENT_UNKNOWN_ANCHOR')
                    edges[node] = [(target, anchor.point)]
            if event.stop_target is not None:
                target = source_key(event.stop_target)
                if target not in entries or entries[target].mode != 'background':
                    raise ValueError('ARRANGEMENT_STOP_TARGET_INVALID')
                if target in stopped:
                    raise ValueError('ARRANGEMENT_DUPLICATE_STOP')
                stopped.add(target)
            if event.control_target is not None:
                target = source_key(event.control_target)
                if target not in entries or entries[target].mode != 'background' or list(entries).index(target) >= list(entries).index(key):
                    raise ValueError('ARRANGEMENT_CONTROL_TARGET_INVALID')
        # Iterative topological elimination avoids recursion limits for long scripts.
        resolved = set()
        while len(resolved) < len(edges):
            ready = {node for node, deps in edges.items() if node not in resolved and all(d in resolved for d in deps)}
            if not ready:
                raise ValueError('ARRANGEMENT_TIMING_CYCLE')
            resolved.update(ready)
        if not any(e.mode in ('speech', 'one_shot', 'background') for e in self.events):
            raise ValueError('ARRANGEMENT_AUDIO_REQUIRED')
        return self


def bind_arrangement(plan: AudioArrangement, delivery_bytes: bytes):
    if sha256(delivery_bytes).hexdigest() != plan.delivery_sha256:
        raise ValueError('ARRANGEMENT_DELIVERY_HASH_MISMATCH')
    delivery = json.loads(delivery_bytes)
    validate_arrangement_source(plan, delivery)
    return delivery


def validate_arrangement_source(plan, delivery):
    if delivery['story_version_id'] != plan.source_version_id:
        raise ValueError('ARRANGEMENT_SOURCE_VERSION_MISMATCH')
    originals = [(ep['episode_id'], sc['scene_id'], line)
                 for ep in delivery['episodes'] for sc in ep['scenes'] for line in sc['utterances']]
    actual = [(source_key(e.source), e.kind, e.text, e.identity_id) for e in plan.events]
    expected = [((ep, sc, line['line_id']), line['kind'], line['text'],
                 line.get('speaker_role_id') or line.get('narrator_id')) for ep, sc, line in originals]
    if actual != expected:
        raise ValueError('ARRANGEMENT_SOURCE_COVERAGE_MISMATCH')


def arrangement_bytes(plan: AudioArrangement):
    data = plan.model_dump()
    if plan.schema_version == 'audio_arrangement/1.0':
        for event in data['events']:
            for name in ('control_target','transition_end','target_gain_db'):
                event.pop(name)
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


class ArrangementTimingError(ValueError):
    def __init__(self, code, **details):
        super().__init__(code)
        self.details = details


def resolve_frames(plan: AudioArrangement, durations: dict, *, max_output_seconds=1800):
    """Durations are measured frames, never model-estimated seconds."""
    entries = {source_key(e.source): e for e in plan.events}
    needed = {key for key, e in entries.items() if e.mode in ('speech', 'one_shot', 'background')}
    if set(durations) != needed or any(type(n) is not int or n <= 0 for n in durations.values()):
        raise ValueError('ARRANGEMENT_MEASURED_ASSETS_MISMATCH')
    pending = {(key, point) for key in entries for point in ('start', 'end')}
    pending.update((key,'transition_end') for key,e in entries.items() if e.transition_end is not None)
    frames = {}
    while pending:
        progressed = False
        for node in list(pending):
            key, point = node
            e = entries[key]
            anchor = getattr(e,point)
            if anchor is None:
                dep = (key, 'start')
                if dep not in frames:
                    continue
                value = frames[dep] + (durations[key] if e.mode in ('speech', 'one_shot') else 0)
            else:
                dep = (source_key(anchor.event), anchor.point) if anchor.event else None
                if dep is not None and dep not in frames:
                    continue
                value = (frames[dep] if dep else 0) + round(anchor.offset_ms * plan.sample_rate / 1000)
            if not 0 <= value <= max_output_seconds * plan.sample_rate:
                raise ValueError('ARRANGEMENT_FRAME_OUT_OF_BOUNDS')
            frames[node] = value
            pending.remove(node)
            progressed = True
        if not progressed:
            raise ValueError('ARRANGEMENT_TIMING_CYCLE')
    last_speech_end = 0
    for key, e in entries.items():
        start, end = frames[key, 'start'], frames[key, 'end']
        if e.mode in ('speech', 'one_shot', 'background') and end <= start:
            raise ArrangementTimingError('ARRANGEMENT_EMPTY_AUDIO_INTERVAL', operation_id=key[2],
                                         start_frame=start, end_frame=end, sample_rate=plan.sample_rate)
        if e.fade_out_ms * plan.sample_rate / 1000 > end - start:
            raise ArrangementTimingError('ARRANGEMENT_FADE_EXCEEDS_BACKGROUND', operation_id=key[2],
                                         start_frame=start, end_frame=end, sample_rate=plan.sample_rate)
        if e.mode == 'speech':
            if start < last_speech_end:
                raise ArrangementTimingError('ARRANGEMENT_SPEECH_OVERLAP_UNSUPPORTED', operation_id=key[2],
                    start_frame=start, previous_speech_end=last_speech_end, sample_rate=plan.sample_rate)
            last_speech_end = end
        if e.stop_target is not None and frames[source_key(e.stop_target), 'end'] != start:
            raise ValueError('ARRANGEMENT_STOP_NOT_EXECUTED')
        if e.control_target is not None:
            target = source_key(e.control_target)
            if not frames[target,'start'] <= start < frames[target,'end']:
                raise ValueError('ARRANGEMENT_CONTROL_OUTSIDE_BACKGROUND')
            if e.mode == 'change_background' and not start <= frames[key,'transition_end'] <= frames[target,'end']:
                raise ValueError('ARRANGEMENT_GAIN_TRANSITION_OUTSIDE_BACKGROUND')
    gain_controls(plan, frames)  # Also rejects reversed/overlapping/no-op gain transitions.
    return frames


def gain_controls(plan, frames):
    backgrounds = {source_key(e.source): e for e in plan.events if e.mode == 'background'}
    curves = {key:[] for key in backgrounds}
    for e in plan.events:
        if e.mode != 'change_background':
            continue
        target = source_key(e.control_target);key = source_key(e.source)
        previous = curves[target]
        begin,end = frames[key,'start'],frames[key,'transition_end']
        initial = previous[-1]['to_gain_db'] if previous else backgrounds[target].gain_db
        if previous and previous[-1]['end_frame'] > begin:
            raise ValueError('ARRANGEMENT_GAIN_TRANSITIONS_OVERLAP')
        if initial == e.target_gain_db:
            raise ValueError('ARRANGEMENT_GAIN_TRANSITION_HAS_NO_CHANGE')
        previous.append(dict(source=e.source.model_dump(),start_frame=begin,end_frame=end,
                             from_gain_db=initial,to_gain_db=e.target_gain_db))
    return curves
