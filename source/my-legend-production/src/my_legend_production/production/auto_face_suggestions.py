"""Read-only, deterministic face proposals. No model, audio or production writes.

The optional classifier is an injection seam for bounded test/provider adapters;
the public route only exposes offline_rules and never instantiates a provider.
"""
from copy import deepcopy
import re
from typing import Literal, Protocol

from pydantic import Field, StrictBool, StrictInt

from .contracts import Contract
from .face_preview import face_envelope
from .life_contracts import canonical_hash
from .performance_tracks import track_bindings, validate_face_track
from .repository import ProductionError

ENGINE = 'zh_explicit_face/1'
SOURCE = ('episode_id', 'scene_id', 'line_id')
PATTERNS = {'surprised': r'大吃一惊|惊讶|睁大(?:了)?眼睛', 'squint': r'眯起(?:了)?眼睛|眯眼|挤眼'}
SIGNAL = re.compile('|'.join(PATTERNS.values()))
UNCERTAIN = re.compile(r'不|未|没|无须|明天|将|会让|一定会|准备|打算|想要|希望|如果|假如|要是|可能|也许|仿佛|似乎|看起来|听说|据说|曾经|昨天|说|告诉|以为|回忆|[“”「」‘’\"\'？?]|吗|是否')
UNSUPPORTED = re.compile(r'开心|高兴|笑了|笑起来|生气|愤怒|难过|害怕|愣住|惊呆|震惊')
MAX_LINES, MAX_CHARS, MAX_OPS = 512, 32768, 2048


class FaceSuggestionRequest(Contract):
    profile: Literal['life_v1'] = 'life_v1'
    source_preview_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    mode: Literal['offline_rules'] = 'offline_rules'
    max_suggestions: StrictInt = Field(default=32, ge=1, le=64)
    include_diagnostics: StrictBool = True


class IntentDecision(Contract):
    """No timing fields: even an injected classifier cannot invent timestamps."""
    role_id: str = Field(min_length=1, max_length=128)
    emotion: Literal['surprised', 'squint']
    quote: str = Field(min_length=1, max_length=160)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)


class FaceClassifier(Protocol):
    version: str

    def classify(self, payload: dict) -> dict | None: ...


def overlap(a, b):
    return max(a['start_frame'], b['start_frame']) < min(a['end_frame'], b['end_frame'])


def subject(text, match, line, actors, aliases):
    # Resolve a grammatical prefix, never a name mentioned after the expression.
    prefix = text[:match.start()]
    clause = re.split(r'[，,。；;！!\n]', prefix)[-1]
    mentioned = {a['instance_id'] for a in actors if any(n in clause for n in aliases[a['role_id']])}
    if len(mentioned) == 1:
        iid = mentioned.pop()
        # "甲看着她惊讶" is ambiguous: a named observer is not an expression subject.
        if re.search(r'他|她|它|你|对方', clause):
            return None
        return next(a for a in actors if a['instance_id'] == iid)
    if mentioned:
        return None
    if re.fullmatch(r'\s*我(?:感到|十分|非常|突然|有些|显得|地|很)*\s*', SIGNAL.sub('', clause)) and line['kind'] == 'dialogue':
        return next((a for a in actors if a['role_id'] == line.get('speaker_role_id')), None)
    if re.fullmatch(r'\s*[她他它](?:感到|十分|非常|突然|有些|显得|地|很)*\s*', SIGNAL.sub('', clause)):
        # Only an explicit, unique same-line antecedent; never infer from gender/cast order.
        prior = {a['instance_id'] for a in actors if any(n in prefix[:-len(clause)] for n in aliases[a['role_id']])}
        if len(prior) == 1:
            return next(a for a in actors if a['instance_id'] in prior)
    return None


def anchored_ranges(key, line, timeline, script, quote):
    ranges = []
    for event in timeline['events']:
        ref = event.get('source', event)
        if tuple(ref[k] for k in SOURCE) != key:
            continue
        for op in event.get('operations', [event]):
            start, end = op['start_frame'], op['end_frame']
            mode = op.get('mode', op.get('render_role', 'speech' if line['kind'] != 'action' else 'action_note'))
            measured = op.get('duration_basis', 'measured_audio' if op.get('source_audio_sha256') else '') == 'measured_audio'
            # Zero-length notes, planned holds and estimates are not performance anchors.
            if not (mode == 'speech' and measured and 0 <= start < end <= script['total_frames']):
                continue
            if quote not in (op.get('cue_text') or op.get('text') or line['text']):
                continue
            subtitle = next((s for s in script['subtitles'] if tuple(s['source'][k] for k in SOURCE) == key
                             and s['start_frame'] == start and s['end_frame'] == end
                             and (line['kind'] != 'dialogue' or s['role_id'] == line.get('speaker_role_id'))), None)
            if subtitle:
                ranges.append({'start_frame': start, 'end_frame': end, 'operation_id': op.get('operation_id', str(event.get('sequence', '')))})
    return sorted(ranges, key=lambda r: (r['start_frame'], r['end_frame'], r['operation_id']))


def build_face_suggestions(preview, delivery, timeline, catalog, request, *, classifier: FaceClassifier | None = None):
    script, face = preview['stage'], preview.get('face')
    if request.source_preview_sha256 != preview['preview_sha256']:
        raise ProductionError('FACE_SUGGESTION_SOURCE_MISMATCH')
    if delivery.get('story_version_id') != script['source_version_id'] or any(
            timeline.get(k) != script[k] for k in ('source_version_id', 'audio_sha256', 'sample_rate', 'total_frames')):
        raise ProductionError('FACE_SUGGESTION_SOURCE_MISMATCH')
    result = {'schema_version': 'legend_face_suggestions/1', 'source_preview_sha256': preview['preview_sha256'],
              'bindings': track_bindings(script), 'profile_sha256': face['profile_sha256'] if face else None,
              'source_script_sha256': preview['script']['artifact']['sha256'],
              'engine_version': ENGINE if classifier is None else 'injected_classifier/1',
              'classifier_version': None if classifier is None else classifier.version,
              'ai_provider_validation': 'not_run', 'suggestions': [], 'diagnostics': []}
    def note(code, message, source=None, quote=''):
        if len(result['diagnostics']) < 256:
            result['diagnostics'].append({'code': code, 'message': message, 'source': source, 'quote': quote[:160]})

    def finish():
        result['request_fingerprint'] = canonical_hash({'preview': preview['preview_sha256'], 'request': request.model_dump(),
                                                       'engine': result['engine_version'], 'classifier': result['classifier_version']})
        if not request.include_diagnostics:
            result['diagnostics'] = []
        return result

    if not face or face.get('schema_version') != 'legend_expression_face/1':
        note('FACE_DISABLED', '请先明确启用并保存已核对的表情 Profile。')
        return finish()
    expected = face_envelope(script, catalog, face['profile_sha256'], track=face.get('track'))
    if any(face.get(k) != expected[k] for k in ('profile_sha256', 'profile_set', 'bindings', 'track')):
        raise ProductionError('FACE_SUGGESTION_PROFILE_MISMATCH')
    if catalog['catalog_sha256'] != script['catalog_sha256']:
        raise ProductionError('FACE_SUGGESTION_SOURCE_MISMATCH')
    profiles = {p['actor_id']: p for p in expected['profile_set']['profiles']}
    ready = {d['instance_id'] for d in expected['diagnostics'] if d['code'] == 'FACE_PROFILE_READY'}
    actors = script['actors']
    aliases = {a['role_id']: {a['name']} for a in actors}
    for c in delivery.get('character_snapshot', []):
        if c['role_id'] in aliases:
            aliases[c['role_id']].update(n for n in [c.get('canon', {}).get('name'), *c.get('canon', {}).get('aliases', [])] if isinstance(n, str) and n)
    lines = [(dict(zip(SOURCE, (ep['episode_id'], sc['scene_id'], line['line_id']))), line)
             for ep in delivery['episodes'] for sc in ep['scenes'] for line in sc['utterances']]
    if (len(actors) > 3 or len(lines) > MAX_LINES or sum(len(line['text']) for _, line in lines) > MAX_CHARS
            or sum(len(e.get('operations', [e])) for e in timeline['events']) > MAX_OPS):
        note('INPUT_LIMIT', '输入超过本轮只读建议预算；未截断后猜测。')
        return finish()
    existing = deepcopy(expected['track'])
    candidates = []
    for source, line in lines:
        if line['kind'] not in ('dialogue', 'narration', 'action'):
            continue
        text = line['text']
        matches = list(SIGNAL.finditer(text))
        if not matches:
            unclear = UNSUPPORTED.search(text) and re.search('她|他|它', text) and not any(n in text for names in aliases.values() for n in names)
            note('AMBIGUOUS_ACTOR' if unclear else 'UNSUPPORTED_EMOTION' if UNSUPPORTED.search(text) else 'NO_EXPLICIT_EXPRESSION',
                 '没有可核对的即时视觉表情证据，保留原生。', source, text)
            continue
        if UNCERTAIN.search(text):
            note('NEGATED_OR_QUOTED', '否定、转述、疑问、假设或将来语境不生成即时表情。', source, text)
            continue
        # Multiple expression descriptions lack word-level anchors; abstain rather than invent timing.
        emotions = {name for name, pattern in PATTERNS.items() if re.search(pattern, text)}
        if len(emotions) != 1:
            note('AMBIGUOUS_EXPRESSION', '同一来源包含不同表情但没有逐词时间锚。', source, text)
            continue
        emotion = next(iter(emotions))
        resolved = [subject(text, m, line, actors, aliases) for m in matches]
        ids = {a['instance_id'] if a else None for a in resolved}
        if len(ids) != 1 or None in ids:
            note('AMBIGUOUS_ACTOR', '表情主体不明确，不根据性别或演员顺序猜测。', source, text)
            continue
        actor = resolved[0]
        quote, confidence = matches[0].group(), .98
        if classifier is not None:
            # One bounded, injected decision, no retry. Guards and provenance remain mandatory.
            payload = {'source': source, 'text': text[:2048], 'role_id': actor['role_id'], 'allowed_emotions': [emotion]}
            try:
                raw = classifier.classify(payload)
                decision = IntentDecision.model_validate(raw)
                if (decision.role_id != actor['role_id'] or decision.emotion != emotion or decision.quote not in text
                        or not re.search(PATTERNS[emotion], decision.quote) or decision.confidence < .9):
                    raise ValueError('uncertain or ungrounded')
                quote, confidence = decision.quote, decision.confidence
            except Exception:
                note('CLASSIFIER_UNCERTAIN', '注入分类器失败或结论无充分来源依据；未重试。', source)
                continue
        profile = profiles.get(actor['actor_id'], {})
        if actor['instance_id'] not in ready or not profile.get('eyes') or emotion not in profile.get('emotions', {}):
            note('PROFILE_UNAVAILABLE', '演员或素材版本无已核对的眼部能力。', source, quote)
            continue
        ranges = anchored_ranges(tuple(source[k] for k in SOURCE), line, timeline, script, quote)
        if not ranges:
            note('NO_ANCHOR', '来源没有对应的实测语音及字幕区间；不创造逐词时间戳。', source, quote)
            continue
        for bounds in ranges:
            cue = {'instance_id': actor['instance_id'], 'type': 'emotion', 'enabled': True, 'emotion': emotion,
                   **{k: bounds[k] for k in ('start_frame', 'end_frame')}}
            evidence = {'source': source, 'quote': quote, 'decision': 'explicit_visual_action' if emotion == 'squint' else 'explicit_emotion',
                        'confidence': confidence, 'rule_version': ENGINE, 'operation_id': bounds['operation_id']}
            cue['id'] = 'auto_face_' + canonical_hash({'preview': preview['preview_sha256'], 'cue': cue, 'evidence': evidence})[:24]
            # Conservative whole-interval exclusion also protects unsupported body modes/hidden actors.
            protected = any(c['enabled'] and c['instance_id'] == actor['instance_id'] and overlap(c, cue)
                            and c['type'] == 'body.play' and c['params']['action'] not in profile['actions'] for c in script['cues'])
            hidden = not actor['visible'] or any(c['enabled'] and c['instance_id'] == actor['instance_id'] and c['start_frame'] < cue['end_frame']
                         and (c['type'] == 'actor.place' and c['params'].get('visible') is False
                              or c['type'] == 'actor.move_to' and c['params'].get('visible_after') is False) for c in script['cues'])
            if protected or hidden:
                note('PROTECTED_ACTION', '区间涉及保护动作、非白名单动作或隐藏状态，保持原生。', source, quote)
                continue
            if any(c['enabled'] and c['type'] == 'emotion' and c['instance_id'] == cue['instance_id'] and overlap(c, cue) for c in existing['cues']):
                note('CONFLICT_WITH_MANUAL', '已保存的表情优先；跳过整个冲突候选。', source, quote)
                continue
            candidates.append({'face_cue': cue, 'evidence': evidence})
    candidates.sort(key=lambda c: (c['face_cue']['start_frame'], c['face_cue']['instance_id'],
                                  0 if c['evidence']['decision'] == 'explicit_visual_action' else 1, c['face_cue']['id']))
    for suggestion in candidates:
        cue, evidence = suggestion['face_cue'], suggestion['evidence']
        if len(result['suggestions']) >= request.max_suggestions:
            note('SUGGESTION_LIMIT', '达到本次候选数量上限。', evidence['source'])
            break
        if any(c['id'] == cue['id'] or c['enabled'] and c['type'] == 'emotion' and c['instance_id'] == cue['instance_id'] and overlap(c, cue) for c in existing['cues']):
            note('CONFLICT_WITH_CANDIDATE', '候选同通道冲突，保留确定性排序中的首项。', evidence['source'])
            continue
        merged = {**existing, 'cues': [*existing['cues'], cue]}
        if len(merged['cues']) > 512:
            note('TRACK_LIMIT', '已有轨道到达容量上限。', evidence['source'])
            break
        validate_face_track(merged, script, expected)
        existing = merged
        result['suggestions'].append(suggestion)
    return finish()
