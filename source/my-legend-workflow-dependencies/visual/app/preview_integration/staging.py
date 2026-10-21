"""视觉侧状态调度：源事件不变，用声音实测时间求值留场、入场与平移。"""
from copy import deepcopy
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .common import IntegrationError, digest

Identifier = Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Cue(StrictModel):
    cue_id: Identifier
    block_id: Identifier
    role_id: Identifier
    action: Literal['enter', 'exit', 'move', 'wait']
    offset_ms: Annotated[int, Field(ge=0, le=90000)] = 0
    duration_ms: Annotated[int, Field(ge=100, le=90000)] = 1000
    target_slot: Annotated[int, Field(ge=1, le=4)] | None = None
    entry_side: Literal['left', 'right'] | None = None

    @model_validator(mode='after')
    def target(self):
        if (self.action == 'move') != (self.target_slot is not None):
            raise ValueError('仅 move 必须提供 target_slot；enter 使用人物预留站位')
        if self.entry_side is not None and self.action != 'enter':
            raise ValueError('仅 enter 可以提供 entry_side')
        return self


class Group(StrictModel):
    group_id: Identifier
    scene_ids: list[Identifier] = Field(min_length=1)
    cast: list[Identifier] = Field(min_length=1, max_length=4)
    initial_cast: list[Identifier]
    cues: list[Cue] = Field(default_factory=list, max_length=100)


class StagingConfig(StrictModel):
    schema_version: Literal['visual_staging/1.0']
    staging_id: Identifier
    groups: list[Group] = Field(min_length=1, max_length=100)


def invalid(message):
    raise IntegrationError('INVALID_VISUAL_STAGING', message)


def unique(values, label):
    if len(values) != len(set(values)):
        invalid(f'{label}不可重复')


def pose(role_id, position, visible):
    return {'role_id': role_id, 'position': list(position), 'visible': visible,
            'direction': 'down', 'walk_frame': 0, 'offset_x': 0.0, 'offset_y': 0.0,
            'reaction': None, 'speaking': False}


def sample(group, time_ms):
    """每次从固定初态计算，绝不依赖上一播放帧；倒拖、重播与离线导出一致。"""
    actors = deepcopy(group['initial'])
    for cue in group['cues']:
        if time_ms < cue['start_ms']:
            break
        actor = deepcopy(cue['before'])
        if time_ms >= cue['end_ms']:
            actor = deepcopy(cue['after'])
        else:
            progress = (time_ms - cue['start_ms']) / (cue['end_ms'] - cue['start_ms'])
            actor['position'] = [a + (b - a) * progress
                                 for a, b in zip(cue['before']['position'], cue['after']['position'])]
            # 退场平移结束才隐藏；入场从锚定事件开始可见，不提前出现在上一句。
            actor['visible'] = True
        actors[cue['role_id']] = actor
    return sorted(actors.values(), key=lambda a: (a['position'][1], a['role_id']))


def build_schedule(plan, raw):
    try:
        config = StagingConfig.model_validate(raw)
    except ValidationError as exc:
        invalid(f'视觉调度字段无效：{exc.errors()[0]["msg"]}')
    scenes = plan['manifest']['scenes']
    source_scenes = {s['scene_id']: s for s in plan['document']['scenes']}
    roles = {r['role_id'] for r in plan['document']['characters']}
    unique([g.group_id for g in config.groups], '视觉连续组 ID')
    unique([c.cue_id for g in config.groups for c in g.cues], '视觉动作 ID')
    # 完整、顺序且连续的分组禁止 A→B→A 暗中恢复旧状态；背景相同不等于剧情连续。
    if [s for g in config.groups for s in g.scene_ids] != [s['scene_id'] for s in scenes]:
        invalid('视觉连续组必须按原场景顺序完整覆盖，不能跳场或重复')
    scene_lookup = {s['scene_id']: s for s in scenes}
    events = {b['block']['block_id']: (s['scene_id'], b)
              for s in scenes for b in s['blocks']}
    # 明确采用侧车调度时，不能同时执行原人物动作，否则会出现两套互相覆盖的状态。
    if any(b['block']['type'] == 'action' for s in scenes for b in s['blocks']):
        invalid('原剧本含行动事件，请统一由视觉调度提供，不能同时使用两套人物动作')
    schedules = []
    for group in config.groups:
        unique(group.cast, '在场人物'); unique(group.initial_cast, '开场人物')
        if not set(group.cast) <= roles or not set(group.initial_cast) <= set(group.cast):
            invalid('连续组引用未知人物或开场人物不属于在场名单')
        if any(not set(source_scenes[s]['cast']) <= set(group.cast) for s in group.scene_ids):
            invalid('连续组不能遗漏源场景中的人物')
        if len({source_scenes[s]['background_type'] for s in group.scene_ids}) != 1:
            invalid('当前视觉连续组只支持相同背景类型')
        # 按整组人物预留位置，后来入场的人不会挤走已经在场的角色。
        xs = {1: [160], 2: [104, 216], 3: [80, 160, 240], 4: [56, 124, 196, 264]}[len(group.cast)]
        slots = [[x, 108] for x in xs]
        initial = {role: pose(role, slots[i], role in group.initial_cast)
                   for i, role in enumerate(group.cast)}
        current = deepcopy(initial)
        scheduled = []
        first = scene_lookup[group.scene_ids[0]]; last = scene_lookup[group.scene_ids[-1]]
        previous_end = first['start_ms']
        for cue in group.cues:
            if cue.block_id not in events or events[cue.block_id][0] not in group.scene_ids:
                invalid(f'{cue.cue_id} 引用了不属于本组的事件')
            if cue.role_id not in current:
                invalid(f'{cue.cue_id} 引用了不属于本组的人物')
            event = events[cue.block_id][1]
            start = event['start_ms'] + cue.offset_ms
            # 视觉平移只能占用已有事件时长，不能为了动画延长同事的音轨。
            end = min(start + cue.duration_ms, event['end_ms'])
            if end <= start or start < previous_end:
                invalid(f'{cue.cue_id} 超出锚定事件或与前一个视觉动作重叠/乱序')
            actor = current[cue.role_id]
            if (cue.action == 'enter') == actor['visible']:
                invalid(f'{cue.cue_id} 人物显隐状态不允许此动作')
            before = deepcopy(actor); after = deepcopy(actor)
            if cue.action == 'enter':
                before['position'] = [308 if cue.entry_side == 'right' else 12, 108]
                before['visible'] = True
                after['position'] = slots[group.cast.index(cue.role_id)]
                after['visible'] = True
            elif cue.action == 'exit':
                after['position'] = [308, actor['position'][1]]
                after['visible'] = False
            elif cue.action == 'move':
                if cue.target_slot > len(slots):
                    invalid(f'{cue.cue_id} 的目标站位不存在')
                after['position'] = slots[cue.target_slot - 1]
            if after['visible'] and any(a['visible'] and r != cue.role_id and
                                       a['position'] == after['position'] for r, a in current.items()):
                invalid(f'{cue.cue_id} 的目标站位已被占用')
            scheduled.append(cue.model_dump() | {'start_ms': start, 'end_ms': end,
                             'effective_duration_ms': end - start, 'before': before, 'after': after})
            current[cue.role_id] = deepcopy(after)
            previous_end = end
        schedules.append({'group_id': group.group_id, 'scene_ids': group.scene_ids,
                          'cast': group.cast, 'initial': initial, 'cues': scheduled,
                          'start_ms': first['start_ms'], 'end_ms': last['end_ms'],
                          'first_event_ms': first['blocks'][0]['start_ms'],
                          'last_event_ms': last['blocks'][-1]['end_ms']})
    return config.model_dump(), schedules


def apply_staging(plan, raw=None):
    if raw is None:
        return deepcopy(plan)
    config, schedules = build_schedule(plan, raw)
    out = deepcopy(plan)
    by_scene = {scene: group for group in schedules for scene in group['scene_ids']}
    for frame in out['frames']:
        group = by_scene[frame['scene_id']]
        frame['visual_group_id'] = group['group_id']
        frame['actors'] = sample(group, frame['time_ms'])
        for actor in frame['actors']:
            actor['speaking'] = actor['visible'] and actor['role_id'] == frame['speaker_id']
        if frame['speaker_id'] and not any(a['speaking'] for a in frame['actors']):
            invalid(f'{frame["block_id"]} 的说话人物不在场')
        # 源片段边界仍保留用于声音溯源，同一视觉组内部不淡出或重置人物。
        if group['first_event_ms'] <= frame['time_ms'] < group['last_event_ms']:
            frame['fade'] = 0.0
    for scene in out['manifest']['scenes']:
        scene['source_initial'] = scene['initial']
        scene['initial'] = {a['role_id']: a for a in sample(by_scene[scene['scene_id']], scene['start_ms'])}
    out['aligned_plan_hash'] = plan['plan_hash']
    out['visual_staging'] = {'schema_version': config['schema_version'], 'staging_id': config['staging_id'],
                             'config_sha256': digest(config), 'groups': schedules,
                             'policy': 'explicit_groups_persistent_actor_state',
                             'audio_events_unchanged': True}
    out['plan_hash'] = digest({k: v for k, v in out.items() if k != 'plan_hash'})
    return out
