"""Local, deterministic keyword choreography over delivered audio and source lines."""
from copy import deepcopy
from hashlib import sha256
import json
import re

from .contracts import ArtifactRef
from .repository import ProductionError
from .life_contracts import RULE_VERSION, LifeValidationError, action_durations, canonical_hash, fail, validate_catalog, validate_script

SOURCE_FIELDS = ('episode_id', 'scene_id', 'line_id')
RULES = [
    ('enter', '走进来|走进|走入|进场|入场|进入房间'),
    ('exit', '走出去|走出|离场|退场|离开房间'),
    ('approach', '走近|靠近|走向|走到'), ('depart', '远离|退离|后退'),
    ('lie', '躺下来|躺下|躺倒|卧倒'), ('sit', '坐下来|坐下|落座'),
    ('idle', '站起来|站起身|起身|站起|站立|停下来|停下|等待|待机'),
    ('pick', '捡起来|捡起|拾起来|拾起|拿起来|拿起|拾取'),
    ('walk', '走动|行走|走过去|向左走|向右走|往左走|往右走'),
]
PATTERN = re.compile('|'.join(f'(?P<{key}>{pattern})' for key, pattern in RULES))
NON_EXECUTION = re.compile(r'不要|不能|不许|别再|别|不想|没有|并未|尚未|未曾|不会|不再|不愿|莫要|准备|打算|想要|将要|将会|稍后|等会|明天|昨天|曾经|如果|假如|要是|是否|能否|可否|据说|听说|说过|说道|告诉|要求|命令|让|请|说[，,:：]|说.*[“「]')


def load_life_catalog(root, digest=None):
    try:
        digest = digest or json.loads((root / 'life-current.json').read_text(encoding='utf-8'))['catalog_sha256']
        if not re.fullmatch('[a-f0-9]{64}', digest):
            raise ValueError('Invalid catalog digest')
        value = json.loads((root / 'catalogs' / (digest + '.json')).read_text(encoding='utf-8'))
        if value['catalog_sha256'] != digest:
            raise ValueError('Catalog name mismatch')
        return validate_catalog(value)
    except (OSError, ValueError, KeyError):
        raise ProductionError('LIFE_CATALOG_INVALID_OR_NOT_CONFIGURED') from None


def life_asset_catalog(root, digest):
    """Resource allowlist includes immutable snapshots, not arbitrary runtime files."""
    for path in sorted((root / 'catalogs').glob('*.json')):
        catalog = load_life_catalog(root, path.stem)
        if digest in catalog['files']:
            return catalog
    raise ProductionError('SPINE_ASSET_NOT_FOUND')


def context(job, body, store, catalog):
    from .spine_preview import StagePreviewRequest, compile_preview
    facade = {'catalog_sha256': catalog['catalog_sha256'], 'actors': [
        {'actor_id': a['actor_id'], 'version': a['revision'], 'idle_animation': 'wait'} for a in catalog['actors']]}
    # Reuse the verified audio/subtitle/source adapter, keeping the new compiler independent.
    base = compile_preview(job, StagePreviewRequest(), store, facade)
    delivery = store.json(ArtifactRef.model_validate(base['script']['artifact']))
    timeline = store.json(ArtifactRef.model_validate(base['timeline']['artifact']))
    lines = {}
    for ep in delivery['episodes']:
        for scene in ep['scenes']:
            for line in scene['utterances']:
                lines[(ep['episode_id'], scene['scene_id'], line['line_id'])] = line
    roles = []
    names = {a['role_id']: a['name'] for a in base['stage']['actors']}
    for c in delivery.get('character_snapshot', []):
        names.setdefault(c['role_id'], c.get('canon', {}).get('name', c['role_id']))
    for key in ('character_portrayals', 'fictional_voice_roles'):
        for c in delivery.get(key, []):
            names.setdefault(c['role_id'], c.get('story_name') or c.get('name') or c['role_id'])
    for line in lines.values():
        rid = line.get('speaker_role_id')
        if line['kind'] == 'dialogue' and rid not in roles:
            roles.append(rid)
    if body.bindings:
        assets = {a['actor_id']: a for a in catalog['actors']}
        if any(role not in roles or aid not in assets for role, aid in body.bindings.items()):
            fail('bindings', '剧情角色或素材演员不存在')
        base['stage']['actors'] = [{'role_id': role, 'name': names.get(role, role), 'actor_id': aid,
                                   'actor_version': assets[aid]['revision'], 'x': (i+1)/(len(body.bindings)+1)}
                                  for i, (role, aid) in enumerate(body.bindings.items())]
    expected = {'job_id': job['id'], 'source_version_id': base['source_version_id'],
                'audio_sha256': base['audio']['artifact']['sha256'], 'timeline_sha256': base['timeline']['artifact']['sha256'],
                'catalog_sha256': catalog['catalog_sha256'], 'sample_rate': base['stage']['sample_rate'],
                'total_frames': base['stage']['total_frames'], 'subtitles': base['stage']['cues'], 'roles': roles}
    return base, delivery, timeline, lines, expected, [{'role_id': r, 'name': names.get(r, r)} for r in roles]


def make_actors(base, catalog, initial_outfits):
    actors = []
    available = {a['actor_id']: a for a in catalog['actors']}
    for index, a in enumerate(base['stage']['actors']):
        asset = available[a['actor_id']]
        outfit = initial_outfits.get(a['role_id'], asset['outfit_ids'][0] if asset['outfit_ids'] else None)
        if outfit and outfit not in asset['outfit_ids']:
            fail('initial_outfits.' + a['role_id'], '服装不属于演员')
        if asset['asset_mode'] == 'commander_tripartite' and not outfit:
            fail('initial_outfits.' + a['role_id'], '请选择初始服装')
        actors.append({'instance_id': f'actor_{index+1}', 'role_id': a['role_id'], 'name': a['name'],
                       'actor_id': asset['actor_id'], 'actor_revision': asset['revision'], 'initial_outfit_id': outfit,
                       'position': [a['x'], .825], 'facing': 1, 'visible': True,
                       'actions': action_durations(asset, catalog, outfit)})
    return actors


def compile_keywords(script, delivery, timeline, lines):
    actors = script['actors']
    by_role = {a['role_id']: a for a in actors}
    aliases = {a['instance_id']: {a['name']} for a in actors}
    for c in delivery.get('character_snapshot', []):
        if c['role_id'] not in by_role:
            continue
        iid = by_role[c['role_id']]['instance_id']
        canon = c.get('canon', {})
        if isinstance(canon.get('name'), str):
            aliases[iid].add(canon['name'])
        for alias in canon.get('aliases', []):
            if isinstance(alias, str):
                aliases[iid].add(alias)
    ambiguous = {name for names in aliases.values() for name in names if sum(name in others for others in aliases.values()) > 1}
    aliases = {iid: {n for n in names if n and n not in ambiguous} for iid, names in aliases.items()}
    diagnostics, candidates, matched_sources = [], [], set()
    rate, total = script['sample_rate'], script['total_frames']

    def note(code, message, source, text, cue_id=None):
        diagnostics.append({'code': code, 'message': message, 'source': source, 'text': text, 'cue_id': cue_id})

    for sequence, event in enumerate(timeline['events']):
        ref = event.get('source', event)
        key = tuple(ref[k] for k in SOURCE_FIELDS)
        line = lines.get(key)
        if not line or line['kind'] not in ('action', 'narration', 'dialogue'):
            continue
        source = dict(zip(SOURCE_FIELDS, key))
        operations = event.get('operations', [event])
        for operation_index, op in enumerate(operations):
            if op.get('mode', op.get('render_role', 'speech')) in ('stop_sound', 'change_background', 'continue_background') or event.get('marker_type') == 'stop_sound':
                continue
            text = op.get('cue_text') or line['text']
            matches = list(PATTERN.finditer(text))
            if not matches:
                continue
            matched_sources.add(key)
            cursor = op['start_frame']
            # A non-audio marker has no audio extent; use original clip durations.
            limit = op['end_frame'] if op['end_frame'] > cursor else total
            for match_index, match in enumerate(matches):
                action, word = match.lastgroup, match.group()
                before = text[:match.start()]
                clause = re.split(r'[，,。；;！!？?\n]', before)[-1]
                sentence = re.split(r'[。；;！!？?\n]', before)[-1]
                suffix = text[match.end():]
                ending = re.split(r'[。；;！!\n]', suffix)[0]
                if NON_EXECUTION.search(sentence) or re.search(r'(?:不|未|要|想|会)(?:再|去|去把)?\s*$', clause) or re.search(r'[？?]|(?:吗|么|吧)[，,。；;！!？?\s]*$', ending):
                    note('NON_EXECUTION', '排除否定、意图、条件、疑问或转述语境', source, text)
                    continue
                # Carry an explicit subject across clauses in the same sentence, never across a sentence boundary.
                subject_text = clause if any(n in clause for names in aliases.values() for n in names) or re.search(r'你|他|她|它', clause) else sentence
                subject_hits = [(subject_text.rfind(n), iid) for iid, names in aliases.items() for n in names if n in subject_text]
                nearest = max(subject_hits, default=(-1, ''))
                ids = {iid for _, iid in subject_hits}
                if len(ids) > 1 and re.search(r'和|与|及|、', subject_text):
                    note('AMBIGUOUS_ACTOR', '多个动作主体，请在简表中指定演员', source, text)
                    continue
                iid = nearest[1]
                if line['kind'] == 'dialogue' and re.search(r'我(?:正|正在|已经|便|就)?\s*$', clause):
                    iid = by_role.get(line.get('speaker_role_id'), {}).get('instance_id', '')
                elif not iid and line['kind'] == 'dialogue' and not re.search(r'你|他|她|它', clause):
                    iid = by_role.get(line.get('speaker_role_id'), {}).get('instance_id', '')
                if not iid:
                    note('AMBIGUOUS_ACTOR', '无法唯一确定动作演员', source, text)
                    continue
                actor = next(a for a in actors if a['instance_id'] == iid)
                body_action = 'walk' if action in ('enter', 'exit', 'approach', 'depart') else action
                if body_action not in actor['actions']:
                    note('UNSUPPORTED_ACTION', actor['name'] + ' 当前素材不支持 ' + body_action, source, text)
                    continue
                target = None
                if action in ('approach', 'depart'):
                    target_text = re.split(r'[，,。；;！!？?\n]', suffix)[0]
                    targets = {other for other, names in aliases.items() if other != iid and any(n in target_text for n in names)}
                    if len(targets) != 1:
                        note('AMBIGUOUS_TARGET', '靠近／远离需要一个明确的舞台目标', source, text)
                        continue
                    target = targets.pop()
                frames = max(1, round(actor['actions'][body_action] * rate))
                end = min(total, limit, cursor + frames)
                if end <= cursor:
                    note('NO_TIME', '来源区间不足，跳过后续动作', source, text)
                    continue
                cue_id = f'auto_{sequence}_{operation_index}_{match_index}'
                cue = {'id': cue_id, 'type': 'actor.move_to' if body_action == 'walk' else 'body.play',
                       'instance_id': iid, 'start_frame': cursor, 'end_frame': end, 'enabled': True,
                       'params': {'to': actor['position'][:], 'visible_after': action != 'exit'} if body_action == 'walk' else
                                 {'action': body_action, 'mode': 'hold' if body_action in ('sit', 'lie') else 'loop' if body_action == 'idle' else 'once'},
                       'source': source, 'source_text': text, 'rule_id': RULE_VERSION + '/' + action,
                       'reason': '匹配“' + word + '”；按来源区间定位，不含逐词对齐'}
                if end - cursor < frames:
                    note('TRUNCATED', '动作被来源区间或音频结尾裁剪', source, text, cue_id)
                candidates.append(({'action': 0, 'narration': 1, 'dialogue': 2}[line['kind']], cue, action, target, word))
                cursor = end
    for key, line in lines.items():
        if line['kind'] in ('action', 'narration', 'dialogue') and PATTERN.search(line['text']) and key not in matched_sources:
            if not any(tuple(e.get('source', e)[k] for k in SOURCE_FIELDS) == key for e in timeline['events']):
                note('NO_TIMELINE', '来源句没有实测时间锚点', dict(zip(SOURCE_FIELDS, key)), line['text'])
    accepted = []
    for priority, c, action, target, word in sorted(candidates, key=lambda item: (item[0], item[1]['start_frame'], item[1]['id'])):
        if any(o[0]['instance_id'] == c['instance_id'] and max(o[0]['start_frame'], c['start_frame']) < min(o[0]['end_frame'], c['end_frame']) for o in accepted):
            note('CONFLICT_SKIPPED', '与更高优先级或较早动作重叠，跳过本条', c['source'], c['source_text'], c['id'])
        else:
            accepted.append((c, action, target, word))
    result = []
    def position_at(iid, frame):
        pos = next(a['position'][:] for a in actors if a['instance_id'] == iid)
        for c in result:
            if c['instance_id'] != iid or c['start_frame'] > frame:
                continue
            if c['type'] == 'actor.move_to':
                p = min(1, (frame - c['start_frame']) / (c['end_frame'] - c['start_frame']))
                pos = [x + (y - x) * p for x, y in zip(pos, c['params']['to'])]
        return pos
    for c, action, target, word in sorted(accepted, key=lambda item: (item[0]['start_frame'], item[0]['id'])):
        if c['type'] == 'actor.move_to':
            pos = position_at(c['instance_id'], c['start_frame'])
            if action == 'enter':
                # Entering starts at the visible edge; place is deterministic on reverse seek.
                place = {**deepcopy(c), 'id': c['id'] + '_place', 'type': 'actor.place', 'end_frame': c['start_frame'],
                         'params': {'position': [.06, pos[1]], 'visible': True, 'facing': 1}}
                result.append(place)
                destination = next(a['position'][0] for a in actors if a['instance_id'] == c['instance_id'])
                pos = [.06, pos[1]]
            elif action == 'exit':
                destination = .96
            elif target:
                x = position_at(target, c['start_frame'])[0]
                direction = 1 if x > pos[0] else -1
                destination = x - direction * .18 if action == 'approach' else pos[0] - direction * .18
            else:
                destination = pos[0] + (-.15 if '左' in word else .15)
            destination = min(.94, max(.06, destination))
            occupied = [position_at(a['instance_id'], c['start_frame'])[0] for a in actors if a['instance_id'] != c['instance_id']]
            if abs(destination - pos[0]) < .02 or any(abs(destination - x) < .16 or min(pos[0], destination) < x < max(pos[0], destination) for x in occupied):
                note('MOVEMENT_BLOCKED', '目标或路径被其他站位占用，跳过移动', c['source'], c['source_text'], c['id'])
                result = [p for p in result if p['id'] != c['id'] + '_place']
                continue
            c['params']['to'] = [destination, pos[1]]
        result.append(c)
    script['cues'] = result
    return diagnostics


def build_life_preview(job, body, store, catalog):
    base, delivery, timeline, lines, expected, roles = context(job, body, store, catalog)
    diagnostics = []
    if body.operation == 'compile':
        script = {k: deepcopy(v) for k, v in expected.items() if k != 'roles'}
        script.update(schema_version='legend_stage_script/0.3', rule_version=RULE_VERSION,
                      actors=make_actors(base, catalog, body.initial_outfits), cues=[], usage='story_preview')
        diagnostics = compile_keywords(script, delivery, timeline, lines)
        if len(roles) > len(script['actors']):
            diagnostics.append({'code': 'UNBOUND_ROLES', 'message': '超过三个的声音角色保留声音和字幕，未绑定舞台演员'})
    else:
        if body.script is None:
            fail('script', '请提供需要校验保存的脚本')
        script = body.script
    script = validate_script(script, catalog, expected, set(lines))
    if body.operation == 'validate_and_save':
        diagnostics = compile_keywords(deepcopy(script), delivery, timeline, lines)
    return {'schema_version': 'spine_stage_preview/2.0', 'profile': 'life_v1', 'job_id': job['id'],
            'title': base['title'], 'source_version_id': base['source_version_id'], 'catalog_sha256': catalog['catalog_sha256'],
            'audio': base['audio'], 'timeline': base['timeline'], 'script': base['script'],
            'stage': script, 'roles': roles, 'diagnostics': diagnostics, 'mouth_control': False, 'usage': 'local_preview_only'}


def read_life_preview(root, job, digest, store):
    if not re.fullmatch('[a-f0-9]{64}', digest) or not re.fullmatch('[A-Za-z0-9_-]{1,128}', job['id']):
        raise ProductionError('SPINE_PREVIEW_NOT_FOUND')
    try:
        data = (root / 'previews' / job['id'] / (digest + '.json')).read_bytes()
        if sha256(data).hexdigest() != digest:
            raise ValueError('Preview hash mismatch')
        value = json.loads(data)
        if value['job_id'] != job['id'] or value['schema_version'] != 'spine_stage_preview/2.0':
            raise ValueError('Preview binding mismatch')
    except (OSError, ValueError, KeyError):
        raise ProductionError('SPINE_PREVIEW_NOT_FOUND') from None
    from .spine_preview import StagePreviewRequest
    body = StagePreviewRequest(profile='life_v1', operation='validate_and_save', script=value['stage'])
    catalog = load_life_catalog(root, value['catalog_sha256'])
    # Recheck current audio/source provenance without recompiling or changing the saved draft.
    build_life_preview(job, body, store, catalog)
    return {**value, 'preview_sha256': digest}
