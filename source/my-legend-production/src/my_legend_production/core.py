from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator

PROJECT = Path(__file__).resolve().parents[2]
CONTRACTS = Path(__file__).with_name('contracts')


class AdapterError(ValueError):
    def __init__(self, code, path='', state='blocked', scope='source', remedy='修正该字段或输入后重新准备。', retryable=False):
        self.issue = {'code': code, 'severity': 'blocking', 'scope': scope,
                      'source_path': path, 'message': code, 'remedy': remedy, 'retryable': retryable}
        self.state = state
        super().__init__(code)


def require(condition, code, path='', **kwargs):
    if not condition:
        raise AdapterError(code, path, **kwargs)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def parse_json(data: bytes):
    require(len(data) <= 8 * 1024 * 1024, 'INPUT_TOO_LARGE')
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    def constant(_):
        raise AdapterError('INVALID_JSON_NUMBER')
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, json.JSONDecodeError):
        raise AdapterError('INVALID_JSON') from None


def validate(data, schema_file, definition=None, code='SCHEMA_INVALID'):
    schema = parse_json((CONTRACTS / schema_file).read_bytes())
    if definition:
        schema = {'$defs': schema['$defs'], '$ref': '#/$defs/' + definition}
    error = next(Draft202012Validator(schema).iter_errors(data), None)
    if error:
        raise AdapterError(code, '.'.join(map(str, error.absolute_path)))


def ident(prefix, parts):
    require(all(isinstance(x, str) for x in parts), 'INVALID_ID_COMPONENT')
    return prefix + sha(encoded(parts))[:32]


def locator(ep, sc, line):
    return {'episode_id': ep, 'scene_id': sc, 'line_id': line['line_id']}


def key(loc):
    return tuple(loc[x] for x in ('episode_id', 'scene_id', 'line_id'))


def unique(values, code='DUPLICATE_REFERENCE', path=''):
    values = list(values)
    require(len(values) == len(set(values)), code, path)


def weighted_length(text):
    def han(c):
        return 0x3400 <= c <= 0x4DBF or 0x4E00 <= c <= 0x9FFF or 0xF900 <= c <= 0xFAFF or 0x20000 <= c <= 0x323AF
    return sum(2 if han(ord(c)) else 1 for c in text)


def source_check(request, delivery, manifest):
    validate(delivery, 'source/StoryDelivery.schema.json', code='SOURCE_SCHEMA_INVALID')
    source = request['source']
    require(delivery['story_id'] == source['story_id'] and delivery['story_version_id'] == source['story_version_id'], 'SOURCE_VERSION_MISMATCH')
    require(delivery.get('status') == 'READY' and delivery.get('delivery_format_version') == '1.0', 'SOURCE_NOT_READY')
    episodes, scenes, lines, result = [], [], [], []
    known_roles = {}
    for char in delivery.get('character_snapshot', []):
        rid = char.get('role_id') or char['character_id']
        require(rid not in known_roles, 'DUPLICATE_ROLE_ID')
        known_roles[rid] = char
    for char in delivery.get('character_portrayals', []) + delivery.get('fictional_voice_roles', []):
        rid = char.get('role_id')
        if rid:
            known_roles.setdefault(rid, char)
    for ei, episode in enumerate(delivery['episodes'], 1):
        episodes.append(episode['episode_id'])
        require(episode['index'] == ei, 'INVALID_SOURCE_ORDER', 'episodes')
        for si, scene in enumerate(episode['scenes'], 1):
            scenes.append(scene['scene_id'])
            require(scene['scene_index'] == si, 'INVALID_SOURCE_ORDER', 'scenes')
            for line in scene['utterances']:
                lines.append(line['line_id'])
                if line['kind'] in ('dialogue', 'narration'):
                    field = 'speaker_role_id' if line['kind'] == 'dialogue' else 'narrator_id'
                    require(bool(line.get(field)) and bool(line.get('performer_id')) and bool(line['text'].strip()), 'SOURCE_PERFORMANCE_INVALID', line['line_id'])
                    if line['kind'] == 'dialogue':
                        require(line[field] in known_roles, 'SOURCE_ROLE_UNKNOWN', line['line_id'])
                    # Resolve/check occurrence using upstream's non-overlapping Unicode code-point convention.
                    for span in line.get('emphasis', []):
                        cursor = 0
                        for _ in range(int(span.get('occurrence',1))):
                            start = line['text'].find(span['span_text'], cursor)
                            require(start >= 0, 'SOURCE_EMPHASIS_INVALID', line['line_id'])
                            cursor = start + len(span['span_text'])
                        require(span.get('start_char') in (None, start) and span.get('end_char') in (None, cursor), 'SOURCE_EMPHASIS_INVALID', line['line_id'])
                result.append((episode, scene, line))
    unique(episodes, 'DUPLICATE_EPISODE_ID'); unique(scenes, 'DUPLICATE_SCENE_ID'); unique(lines, 'DUPLICATE_LINE_ID')
    require(bool(result), 'SOURCE_EMPTY')
    if manifest is not None:
        validate(manifest, 'source/RenderManifest.schema.json', code='SOURCE_SCHEMA_INVALID')
        require(manifest.get('delivery_format_version') == delivery['delivery_format_version'], 'SOURCE_VERSION_MISMATCH')
        require(manifest['story_version_id'] == source['story_version_id'], 'SOURCE_VERSION_MISMATCH')
        require(manifest.get('story_state') == 'SUCCEEDED', 'SOURCE_NOT_READY')
        tasks = manifest['tasks']
        unique((t['task_id'] for t in tasks), 'SOURCE_MANIFEST_MISMATCH')
        require(len(tasks) == len(result), 'SOURCE_MANIFEST_MISMATCH')
        fields = ('text', 'speaker_role_id', 'narrator_id', 'performer_id')
        defaults = {'emotion': None, 'tone_instruction': None, 'emphasis': [], 'speech_rate': 1.0,
                    'pitch': None, 'volume': None, 'pauses': [], 'delivery_note': None, 'audio_cue_ref': None}
        for index, ((ep, sc, line), task) in enumerate(zip(result, tasks), 1):
            require(task['sequence'] == index and task['episode_index'] == ep['index'], 'INVALID_SOURCE_ORDER')
            require(key(task) == (ep['episode_id'], sc['scene_id'], line['line_id']), 'SOURCE_MANIFEST_MISMATCH')
            require(task['source_kind'] == line['kind'] and all(task.get(f) == line.get(f) for f in fields), 'SOURCE_MANIFEST_MISMATCH')
            require(task['kind'] == ('speech' if line['kind'] in ('dialogue', 'narration') else 'cue'), 'SOURCE_MANIFEST_MISMATCH')
            require(task['task_id'] == sha(f"{source['story_version_id']}:{ep['episode_id']}:{sc['scene_id']}:{line['line_id']}".encode('utf-8')), 'SOURCE_MANIFEST_MISMATCH')
            require(all(task['performance'].get(f, default) == line.get(f, default) for f, default in defaults.items()), 'SOURCE_MANIFEST_MISMATCH')
            if line['kind'] in ('dialogue', 'narration'):
                rid = line.get('speaker_role_id') if line['kind'] == 'dialogue' else line.get('narrator_id')
                require(manifest['role_map'].get(rid, {}).get('performer_id') == line['performer_id'], 'SOURCE_MANIFEST_MISMATCH')
    selected = [(s['episode_id'], s['scene_id']) for s in request['scope']['scenes']]
    full = [(ep['episode_id'], sc['scene_id']) for ep in delivery['episodes'] for sc in ep['scenes']]
    require(all(x in full for x in selected), 'SCOPE_NOT_FOUND')
    require(selected == sorted(set(selected), key=full.index), 'INVALID_SOURCE_ORDER')
    return [row for row in result if (row[0]['episode_id'], row[1]['scene_id']) in selected]


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

