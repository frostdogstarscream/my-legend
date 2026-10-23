"""Life-stage contracts and cross-resource validation. No production mutations."""
from __future__ import annotations

from hashlib import sha256
import json
import math
from typing import Literal

from pydantic import Field, ValidationError, model_validator
from .contracts import Contract

RULE_VERSION = 'zh_life_keywords/1'


class LifeValidationError(ValueError):
    def __init__(self, diagnostics):
        self.diagnostics = diagnostics
        super().__init__('LIFE_VALIDATION_FAILED')


def fail(path, message):
    raise LifeValidationError([{'path': path, 'code': 'INVALID', 'message': message}])


def canonical_hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate_catalog(catalog):
    if catalog.get('schema_version') != 'spine_life_catalog/2.0':
        fail('catalog', '生活素材目录版本错误')
    if canonical_hash({k: v for k, v in catalog.items() if k != 'catalog_sha256'}) != catalog.get('catalog_sha256'):
        fail('catalog_sha256', '素材目录内容哈希不一致')
    assets = {s['asset_id']: s for s in catalog['skeletons']}
    outfits = {o['outfit_id']: o for o in catalog['outfits']}
    actors = {a['actor_id']: a for a in catalog['actors']}
    if len(assets) != len(catalog['skeletons']) or len(outfits) != len(catalog['outfits']) or len(actors) != len(catalog['actors']):
        fail('catalog', '素材 ID 重复')
    for path, ref in [('runtime', catalog['runtime'])] + [(f'skeletons.{s["asset_id"]}.{key}', ref)
            for s in assets.values() for key, ref in [('skeleton', s['skeleton']), ('original', s['original']), ('atlas', s['atlas']), *s['pages'].items()]]:
        if ref != catalog['files'].get(ref.get('sha256')) or ref.get('size_bytes', 0) <= 0:
            fail(path, '资源引用未登记或大小无效')
    for aid, actor in actors.items():
        if actor.get('lifecycle') != 'ready' or actor.get('asset_qa') != 'pass' or not {'idle', 'walk'} <= actor['action_map'].keys():
            fail(aid, '角色未完成 QA 或缺少待机/行走')
        if actor['asset_mode'] == 'single_skeleton':
            if not actor.get('asset_id') or actor['asset_id'] not in assets:
                fail(aid, 'ready 角色必须引用真实骨骼')
            parts = [assets[actor['asset_id']]]
        elif actor['asset_mode'] == 'commander_tripartite':
            if not actor['outfit_ids'] or any(o not in outfits or outfits[o]['actor_id'] != aid for o in actor['outfit_ids']):
                fail(aid, '角色服装引用不完整')
            parts = []
        else:
            fail(aid, '未知演员类型')
        for part in parts:
            if part['qa'] != 'pass' or any(raw not in part['animations'] for raw in actor['action_map'].values()):
                fail(aid, '动作映射不属于实际骨骼')
    for oid, outfit in outfits.items():
        if outfit['actor_id'] not in actors or outfit['qa'] != 'pass' or set(outfit['parts']) != {'head', 'body', 'foot'}:
            fail(oid, '服装部件或所属角色无效')
        actor = actors[outfit['actor_id']]
        for slot, aid in outfit['parts'].items():
            if aid not in assets or assets[aid]['qa'] != 'pass':
                fail(oid, '服装部件不存在或未通过 QA')
            c = outfit['calibration'].get(slot)
            if not c:
                fail(oid, '服装缺少校准')
            for v in [c, *c.get('actions', {}).values()]:
                if any(not isinstance(v.get(k), (int, float)) or not math.isfinite(v[k]) for k in ('x', 'y', 'scale', 'rotation', 'layer')) or v['scale'] <= 0 or not isinstance(v.get('flip'), bool):
                    fail(oid, '服装校准数值无效')
        if canonical_hash(outfit['calibration']) != outfit['calibration_revision']:
            fail(oid, '校准版本不一致')
        for action in outfit['allowed_actions']:
            if action not in actor['action_map'] or any(actor['action_map'][action] not in assets[p]['animations'] for p in outfit['parts'].values()):
                fail(oid, '服装动作不属于三个部件共同能力')
        if not {'idle', 'walk'} <= set(outfit['allowed_actions']):
            fail(oid, '首批服装必须支持待机和行走')
    for asset in assets.values():
        if any(not math.isfinite(a['duration']) or a['duration'] <= 0 for a in asset['animations'].values()):
            fail(asset['asset_id'], '动作时长无效')
    return catalog


class Source(Contract):
    episode_id: str
    scene_id: str
    line_id: str


class LifeActor(Contract):
    instance_id: str = Field(min_length=1, max_length=128)
    role_id: str
    name: str
    actor_id: str
    actor_revision: str
    initial_outfit_id: str | None = None
    position: list[float] = Field(min_length=2, max_length=2)
    facing: Literal[-1, 1] = 1
    visible: bool = True
    actions: dict[str, float]


class BodyParams(Contract):
    action: str
    mode: Literal['once', 'loop', 'hold']


class MoveParams(Contract):
    to: list[float] = Field(min_length=2, max_length=2)
    visible_after: bool = True


class PlaceParams(Contract):
    position: list[float] = Field(min_length=2, max_length=2)
    visible: bool = True
    facing: Literal[-1, 1] = 1


class OutfitParams(Contract):
    outfit_id: str


class FocusParams(Contract):
    target_instance_id: str


PARAMS = {'body.play': BodyParams, 'actor.move_to': MoveParams, 'actor.place': PlaceParams,
          'actor.change_outfit': OutfitParams, 'camera.focus': FocusParams}


class LifeCue(Contract):
    id: str = Field(min_length=1, max_length=128)
    type: Literal['body.play', 'actor.move_to', 'actor.place', 'actor.change_outfit', 'camera.focus']
    instance_id: str
    start_frame: int = Field(ge=0)
    end_frame: int = Field(ge=0)
    enabled: bool = True
    params: dict
    source: Source | None = None
    source_text: str = ''
    rule_id: str = 'manual'
    reason: str = '人工指令'

    @model_validator(mode='after')
    def valid_params(self):
        PARAMS[self.type].model_validate(self.params)
        instant = self.type in ('actor.place', 'actor.change_outfit')
        if (instant and self.start_frame != self.end_frame) or (not instant and self.start_frame >= self.end_frame):
            raise ValueError('瞬时指令起止必须相同，区间指令必须具有正时长')
        point = self.params.get('to', self.params.get('position'))
        if point is not None and any(not math.isfinite(x) or not 0 <= x <= 1 for x in point):
            raise ValueError('坐标必须为 0 到 1 的有限数值')
        return self


class Subtitle(Contract):
    source: Source
    role_id: str | None
    speaker: str
    text: str
    start_frame: int = Field(ge=0)
    end_frame: int = Field(ge=1)


class LifeScript(Contract):
    schema_version: Literal['legend_stage_script/0.3'] = 'legend_stage_script/0.3'
    job_id: str
    source_version_id: str
    audio_sha256: str
    timeline_sha256: str
    catalog_sha256: str
    rule_version: Literal['zh_life_keywords/1'] = RULE_VERSION
    sample_rate: int = Field(ge=8000, le=192000)
    total_frames: int = Field(ge=1)
    actors: list[LifeActor] = Field(min_length=1, max_length=3)
    subtitles: list[Subtitle]
    cues: list[LifeCue] = Field(max_length=2000)
    usage: Literal['story_preview', 'technical_coverage'] = 'story_preview'


def action_durations(actor, catalog, outfit_id=None):
    assets = {s['asset_id']: s for s in catalog['skeletons']}
    outfit = next((o for o in catalog['outfits'] if o['outfit_id'] == outfit_id), None)
    parts = [assets[p] for p in outfit['parts'].values()] if outfit else [assets[actor['asset_id']]]
    return {name: max(p['animations'][raw]['duration'] for p in parts) for name, raw in actor['action_map'].items()
            if not outfit or name in outfit['allowed_actions']}


def validate_script(value, catalog, expected, source_keys):
    try:
        script = LifeScript.model_validate(value).model_dump()
    except ValidationError as exc:
        raise LifeValidationError([{'path': '.'.join(map(str, e['loc'])), 'code': 'SCHEMA', 'message': e['msg']} for e in exc.errors()]) from None
    for key in ('job_id', 'source_version_id', 'audio_sha256', 'timeline_sha256', 'catalog_sha256', 'sample_rate', 'total_frames'):
        if script[key] != expected[key]:
            fail(key, '脚本与实际成品或素材版本不一致')
    if script['subtitles'] != expected['subtitles']:
        fail('subtitles', '字幕必须保留真实成品时间轴')
    instances, role_ids = {}, set()
    actor_map = {a['actor_id']: a for a in catalog['actors']}
    outfits = {o['outfit_id']: o for o in catalog['outfits']}
    for i, binding in enumerate(script['actors']):
        path = f'actors.{i}'
        aid, iid = binding['actor_id'], binding['instance_id']
        if iid in instances or binding['role_id'] in role_ids or binding['role_id'] not in expected['roles']:
            fail(path, '舞台实例重复或剧情角色不存在')
        if aid not in actor_map or binding['actor_revision'] != actor_map[aid]['revision']:
            fail(path, '演员资源版本不一致')
        actor = actor_map[aid]
        oid = binding['initial_outfit_id']
        if actor['asset_mode'] == 'commander_tripartite':
            if oid not in outfits or outfits[oid]['actor_id'] != aid:
                fail(path, '初始服装不属于当前演员')
        elif oid is not None:
            fail(path, '单骨骼演员不能设置服装')
        if binding['actions'] != action_durations(actor, catalog, oid):
            fail(path, '动作时长或能力被更改')
        if any(not math.isfinite(x) or not 0 <= x <= 1 for x in binding['position']):
            fail(path, '演员坐标无效')
        instances[iid] = binding
        role_ids.add(binding['role_id'])
    seen, channels, instants = set(), {}, set()
    cues = [c for c in script['cues'] if c['enabled']]
    for i, c in enumerate(script['cues']):
        path = f'cues.{i}:{c["id"]}'
        if c['id'] in seen:
            fail(path, '指令 ID 重复')
        seen.add(c['id'])
        if c['instance_id'] not in instances or c['end_frame'] > script['total_frames']:
            fail(path, '角色不存在或时间越界')
        if c['source'] and tuple(c['source'][k] for k in ('episode_id', 'scene_id', 'line_id')) not in source_keys:
            fail(path, '来源句不存在')
        if not c['enabled']:
            continue
        iid = c['instance_id']
        if c['type'] in ('actor.place', 'actor.change_outfit'):
            key = (iid, c['type'], c['start_frame'])
            if key in instants:
                fail(path, '同一时刻存在冲突的瞬时指令')
            instants.add(key)
        if c['type'] == 'actor.change_outfit':
            oid = c['params']['outfit_id']
            if oid not in outfits or outfits[oid]['actor_id'] != instances[iid]['actor_id']:
                fail(path, '换装目标不属于当前演员')
        if c['type'] == 'camera.focus' and c['params']['target_instance_id'] not in instances:
            fail(path, '镜头目标不存在')
        if c['type'] in ('body.play', 'actor.move_to', 'camera.focus'):
            channel = ('camera', '') if c['type'] == 'camera.focus' else ('body', iid)
            for old in channels.get(channel, []):
                if max(old['start_frame'], c['start_frame']) < min(old['end_frame'], c['end_frame']):
                    fail(path, f'与 {old["id"]} 的动作区间重叠')
            channels.setdefault(channel, []).append(c)
        if c['type'] in ('body.play', 'actor.move_to'):
            action = c['params']['action'] if c['type'] == 'body.play' else 'walk'
            actor = actor_map[instances[iid]['actor_id']]
            if action not in actor['action_map']:
                fail(path, '演员不支持该动作')
            relevant = sorted([o for o in cues if o['type'] == 'actor.change_outfit' and o['instance_id'] == iid and o['start_frame'] < c['end_frame']], key=lambda x: (x['start_frame'], x['id']))
            oid = instances[iid]['initial_outfit_id']
            for o in relevant:
                if o['start_frame'] <= c['start_frame']:
                    oid = o['params']['outfit_id']
            check_outfits = [oid] + [o['params']['outfit_id'] for o in relevant if o['start_frame'] > c['start_frame']]
            if any(o and action not in outfits[o]['allowed_actions'] for o in check_outfits):
                fail(path, '该区间内的服装不支持动作：' + action)
            for place in cues:
                if place['type'] == 'actor.place' and place['instance_id'] == iid and c['type'] == 'actor.move_to' and c['start_frame'] < place['start_frame'] < c['end_frame']:
                    fail(path, '移动区间内部不能重设位置')
    return script
