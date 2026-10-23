"""Versioned direction/face sidecars. Old LifeScript 0.3 remains unchanged."""
import re
from typing import Literal
from pydantic import Field, StrictBool, StrictInt, ValidationError, model_validator
from .contracts import Contract
from .life_contracts import Source, LifeValidationError, fail


def track_bindings(script):
    return {**{k: script[k] for k in ('job_id', 'source_version_id', 'audio_sha256', 'timeline_sha256',
                                     'catalog_sha256', 'sample_rate', 'total_frames')},
            'actors': [{k: a[k] for k in ('instance_id', 'role_id', 'actor_id', 'actor_revision')} for a in script['actors']]}


class DirectionCue(Contract):
    id: str = Field(min_length=1, max_length=128)
    instance_id: str
    start_frame: StrictInt = Field(ge=0)
    end_frame: StrictInt = Field(gt=0)
    enabled: StrictBool = True
    mode: Literal['target', 'left', 'right', 'hold', 'away', 'turn']
    target_instance_id: str | None = None
    source: Source | None = None
    source_text: str = ''
    rule_id: Literal['manual', 'zh_dialogue/1'] = 'manual'


class DirectionTrack(Contract):
    schema_version: Literal['legend_dialogue_track/1'] = 'legend_dialogue_track/1'
    algorithm_version: Literal['dialogue_facing/1'] = 'dialogue_facing/1'
    bindings: dict
    cues: list[DirectionCue] = Field(max_length=256)


class FaceCue(Contract):
    id: str = Field(min_length=1, max_length=128)
    instance_id: str
    type: Literal['emotion', 'blink']
    start_frame: StrictInt = Field(ge=0)
    end_frame: StrictInt = Field(gt=0)
    enabled: StrictBool = True
    emotion: Literal['neutral', 'squint', 'surprised'] | None = None

    @model_validator(mode='after')
    def emotion_channel(self):
        if (self.type == 'emotion') != (self.emotion is not None):
            raise ValueError('表情指令必须指定表情；眨眼指令不能携带表情')
        return self


class FaceTrack(Contract):
    schema_version: Literal['legend_face_track/1'] = 'legend_face_track/1'
    bindings: dict
    profile_sha256: str = Field(pattern='^[a-f0-9]{64}$')
    cues: list[FaceCue] = Field(max_length=512)


def parse_track(model, value, script, prefix):
    try:
        track = model.model_validate(value).model_dump()
    except ValidationError as exc:
        raise LifeValidationError([{'path': prefix + '.' + '.'.join(map(str, e['loc'])), 'code': 'SCHEMA', 'message': e['msg']} for e in exc.errors()]) from None
    if track['bindings'] != track_bindings(script):
        fail(prefix + '.bindings', '轨道与当前演员、素材或真实音频版本不一致')
    seen, channels = set(), {}
    actors = {a['instance_id']: a for a in script['actors']}
    for c in track['cues']:
        path = prefix + '.' + c['id']
        if c['id'] in seen or c['instance_id'] not in actors:
            fail(path, '指令 ID 重复或演员不存在')
        seen.add(c['id'])
        if not c['start_frame'] < c['end_frame'] <= script['total_frames']:
            fail(path, '区间必须为正时长且位于真实音频范围内')
        if not c['enabled']:
            continue
        channel = (c['instance_id'], c.get('type', c.get('rule_id')))
        if any(max(c['start_frame'], old['start_frame']) < min(c['end_frame'], old['end_frame']) for old in channels.get(channel, [])):
            fail(path, '同一演员的同类轨道重叠；请调整起止时间或停用冲突项')
        channels.setdefault(channel, []).append(c)
    return track


def validate_direction_track(value, script, source_keys):
    track = parse_track(DirectionTrack, value, script, 'dialogue_track')
    ids = {a['instance_id'] for a in script['actors']}
    for c in track['cues']:
        if c['mode'] in ('target', 'away'):
            if c['target_instance_id'] not in ids or c['target_instance_id'] == c['instance_id']:
                fail(c['id'], '请选择另一名已绑定的舞台演员')
        elif c['target_instance_id'] is not None:
            fail(c['id'], '此朝向模式不能携带目标演员')
        if c['source'] and tuple(c['source'][k] for k in ('episode_id', 'scene_id', 'line_id')) not in source_keys:
            fail(c['id'], '朝向指令来源句不存在')
    return track


def validate_face_track(value, script, envelope):
    track = parse_track(FaceTrack, value, script, 'face_track')
    if track['profile_sha256'] != envelope['profile_sha256']:
        fail('face_track.profile_sha256', '表情轨道与 Profile 版本不一致')
    profiles = {p['actor_id']: p for p in envelope['profile_set']['profiles']}
    ready = {d['instance_id'] for d in envelope['diagnostics'] if d['code'] == 'FACE_PROFILE_READY'}
    for c in track['cues']:
        actor = next(a for a in script['actors'] if a['instance_id'] == c['instance_id'])
        p = profiles.get(actor['actor_id'], {})
        if c['instance_id'] not in ready or not p.get('eyes'):
            fail(c['id'], '此演员版本没有核对过的表情能力')
        if c['type'] == 'emotion' and c['emotion'] != 'neutral' and c['emotion'] not in p.get('emotions', {}):
            fail(c['id'], '此演员没有该表情语义映射')
    return track


def compile_direction_track(script, delivery, timeline, lines):
    from .life_preview import NON_EXECUTION
    track = {'schema_version': 'legend_dialogue_track/1', 'algorithm_version': 'dialogue_facing/1',
             'bindings': track_bindings(script), 'cues': []}
    diagnostics = []
    actors = script['actors']
    aliases = {a['instance_id']: {a['name']} for a in actors}
    for c in delivery.get('character_snapshot', []):
        a = next((a for a in actors if a['role_id'] == c['role_id']), None)
        if a:
            aliases[a['instance_id']].update(x for x in [c.get('canon', {}).get('name'), *c.get('canon', {}).get('aliases', [])] if isinstance(x, str) and x)
    pattern = re.compile('背对|转过身|转身|面向|面对|转向|朝左|朝右')
    for event in timeline['events']:
        ref = event.get('source', event)
        key = tuple(ref[k] for k in ('episode_id', 'scene_id', 'line_id'))
        line = lines.get(key)
        if not line or line['kind'] not in ('action', 'narration', 'dialogue'):
            continue
        for op in event.get('operations', [event]):
            if op.get('mode', op.get('render_role', 'speech')) in ('stop_sound', 'change_background', 'continue_background'):
                continue
            text = op.get('cue_text') or line['text']
            prior_subject, prior_sentence = '', -1
            for match in pattern.finditer(text):
                before = re.split('[。；;！!？?\n]', text[:match.start()])[-1]
                sentence_start = match.start() - len(before)
                clause = re.split('[，,]', before)[-1]
                suffix = re.split('[，,。；;！!？?\n]', text[match.end():])[0]
                if NON_EXECUTION.search(before) or re.search(r'(?:不|未|要|想|会)\s*$', before) or re.search(r'[？?]|(?:吗|么)[。！!\s]*$', text):
                    diagnostics.append({'code': 'FACING_NON_EXECUTION', 'message': '排除否定、意图、转述或疑问中的朝向', 'text': text})
                    continue
                hits = [(clause.rfind(n), iid) for iid, names in aliases.items() for n in names if n in clause]
                iid = max(hits, default=(-1, ''))[1]
                if not iid and prior_sentence == sentence_start and not re.search('你|他|她|它', clause):
                    iid = prior_subject
                if re.search(r'我\s*$', clause) or not iid and line['kind'] == 'dialogue' and not re.search('你|他|她|它', clause):
                    iid = next((a['instance_id'] for a in actors if a['role_id'] == line.get('speaker_role_id')), '')
                if not iid or len({h[1] for h in hits}) > 1:
                    diagnostics.append({'code': 'FACING_AMBIGUOUS_ACTOR', 'message': '朝向主体不明确，请人工指定', 'text': text})
                    continue
                prior_subject, prior_sentence = iid, sentence_start
                word = match.group()
                mode = 'away' if word == '背对' else 'turn' if word in ('转身', '转过身') else 'left' if word == '朝左' else 'right' if word == '朝右' else 'target'
                target = None
                if mode in ('target', 'away'):
                    targets = {other for other, names in aliases.items() if other != iid and any(n in suffix for n in names)}
                    if not targets and len(actors) == 2 and (not suffix.strip(' ，,。') or re.match('对方|他|她', suffix)):
                        targets = {a['instance_id'] for a in actors if a['instance_id'] != iid}
                    if len(targets) != 1:
                        diagnostics.append({'code': 'FACING_AMBIGUOUS_TARGET', 'message': '面向或背对目标不明确，请人工指定', 'text': text})
                        continue
                    target = targets.pop()
                start = op['start_frame']
                if start >= script['total_frames']:
                    continue
                if mode == 'turn' and any((c['instance_id'], c['start_frame']) == (iid, start) for c in track['cues']):
                    diagnostics.append({'code': 'FACING_SHARED_ANCHOR', 'message': '同一时间锚已有朝向，后续转身缺少独立时间；请人工调整区间', 'text': text})
                    continue
                # Same source/time with "转身背对" resolves to its final explicit direction.
                track['cues'] = [c for c in track['cues'] if (c['instance_id'], c['start_frame']) != (iid, start)]
                track['cues'].append({'id': f'facing_{len(track["cues"])}_{start}_{iid}', 'instance_id': iid,
                    'start_frame': start, 'end_frame': script['total_frames'], 'enabled': True, 'mode': mode,
                    'target_instance_id': target, 'source': dict(zip(('episode_id', 'scene_id', 'line_id'), key)),
                    'source_text': text, 'rule_id': 'zh_dialogue/1'})
    for a in actors:
        cues = sorted([c for c in track['cues'] if c['instance_id'] == a['instance_id']], key=lambda c: c['start_frame'])
        for c, following in zip(cues, cues[1:]):
            c['end_frame'] = following['start_frame']
    return track, diagnostics
