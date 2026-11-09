"""Immutable local room presets and explicitly authored furniture demonstrations."""
from copy import deepcopy
import json
import math
import re

from pydantic import Field, ValidationError

from .contracts import Contract
from .life_contracts import canonical_hash, fail
from .repository import ProductionError

FURNITURE_STAND_SECONDS = .35


class SceneRef(Contract):
    catalog_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    scene_id: str = Field(pattern=r'^[a-z0-9_]{1,64}$')


class FurnitureCue(Contract):
    id: str = Field(min_length=1, max_length=96)
    instance_id: str = Field(min_length=1, max_length=96)
    seat_id: str = Field(min_length=1, max_length=64)
    start_frame: int = Field(ge=0)
    arrive_frame: int = Field(gt=0)
    leave_frame: int = Field(gt=0)
    end_frame: int = Field(gt=0)


class FurnitureTrack(Contract):
    schema_version: str = Field(pattern=r'^legend_furniture_track/1$')
    cues: list[FurnitureCue] = Field(min_length=1, max_length=8)


def validate_blocking(value, object_ids):
    """Optional authoring hints; never grant an object an animation capability."""
    if (value['schema_version'] != 'legend_scene_blocking/1' or
            value['purpose'] != 'authored_standing_layout' or
            value['interaction_status'] != 'standing_targets_only'):
        raise ValueError('Invalid blocking version or capability')
    def numbers(v, size):
        return isinstance(v, list) and len(v) == size and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in v)
    lane = value['walk_lane']
    if not numbers(lane, 4) or not lane[0] < lane[2] or not lane[1] < lane[3]:
        raise ValueError('Invalid walk lane')
    foot = value['foot_y']
    if type(foot) not in (int, float) or not lane[1] <= foot <= lane[3]:
        raise ValueError('Invalid foot line')
    for key, count in (('dialogue_slots', 2), ('ensemble_slots', 3)):
        slots = value[key]
        if not isinstance(slots, list) or len(slots) != count:
            raise ValueError('Invalid dialogue capacity')
        if any(not numbers(p, 2) or not lane[0] <= p[0] <= lane[2] or p[1] != foot for p in slots):
            raise ValueError('Invalid standing slot')
    for key, limit in (('entrances', 2), ('targets', 16)):
        points = value[key]
        if not isinstance(points, list) or not 1 <= len(points) <= limit:
            raise ValueError('Invalid point count')
        ids = set()
        for p in points:
            if not isinstance(p['id'], str) or not re.fullmatch('[a-z0-9_]{1,96}', p['id']) or p['id'] in ids:
                raise ValueError('Invalid point ID')
            ids.add(p['id'])
            pos = p['position']
            if (not isinstance(p['label'], str) or not 1 <= len(p['label']) <= 64 or
                    not numbers(pos, 2) or not lane[0] <= pos[0] <= lane[2] or pos[1] != foot):
                raise ValueError('Invalid blocking point')
            if key == 'targets' and (p['object_id'] not in object_ids or p['action'] != 'approach'):
                raise ValueError('Unknown object or uncalibrated interaction')


def load_scene_catalog(root, digest=None):
    try:
        digest = digest or json.loads((root / 'scene-current.json').read_text(encoding='utf-8'))['catalog_sha256']
        if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
            raise ValueError('Invalid digest')
        value = json.loads((root / 'scene-catalogs' / (digest + '.json')).read_text(encoding='utf-8'))
        if value.get('schema_version') != 'legend_scene_catalog/1' or value.get('catalog_sha256') != digest:
            raise ValueError('Invalid schema')
        if canonical_hash({k: v for k, v in value.items() if k != 'catalog_sha256'}) != digest:
            raise ValueError('Catalog hash mismatch')
        ids = [s['scene_id'] for s in value['scenes']]
        if len(set(ids)) != len(ids) or not 1 <= len(ids) <= 32:
            raise ValueError('Invalid scenes')
        for scene in value['scenes']:
            if not re.fullmatch('[a-z0-9_]{1,64}', scene['scene_id']):
                raise ValueError('Invalid scene ID')
            if scene['layout_kind'] != 'authored_performance_sample' or scene['width'] != 960 or scene['height'] != 540:
                raise ValueError('Invalid layout')
            if not 0 < scene['actor_scale'] <= 2 or not 1 <= len(scene['objects']) <= 64:
                raise ValueError('Invalid scene size')
            object_ids = set()
            for item in scene['objects']:
                if item['id'] in object_ids or item['layer'] not in ('wall', 'floor', 'back', 'depth', 'front'):
                    raise ValueError('Invalid object')
                object_ids.add(item['id'])
                ref = item['image']
                if ref != value['files'].get(ref['sha256']) or ref['media_type'] != 'image/png' or ref['size_bytes'] <= 0:
                    raise ValueError('Unregistered image')
                nums = [*item['position'], *item['anchor'], item['width'], item['height'], item['depth_y']]
                if len(item['position']) != 2 or len(item['anchor']) != 2 or any(type(n) not in (int, float) or not math.isfinite(n) for n in nums):
                    raise ValueError('Invalid transform')
                if item['width'] <= 0 or item['height'] <= 0 or type(item['repeat']) is not bool:
                    raise ValueError('Invalid size')
            if 'blocking' in scene:
                validate_blocking(scene['blocking'], object_ids)
            seats = set()
            for seat in scene['seats']:
                if seat['seat_id'] in seats or seat['object_id'] not in object_ids or not seat['actor_revision']:
                    raise ValueError('Invalid seat')
                seats.add(seat['seat_id'])
                if seat['facing'] not in (-1, 1) or seat['outfit_id'] is not None:
                    raise ValueError('Unsupported calibration')
                for key in ('approach', 'position', 'exit', 'sit_offset'):
                    if len(seat[key]) != 2 or any(type(n) not in (int, float) or not math.isfinite(n) for n in seat[key]):
                        raise ValueError('Invalid seat transform')
        return value
    except (OSError, ValueError, KeyError, TypeError):
        raise ProductionError('SCENE_CATALOG_INVALID_OR_NOT_CONFIGURED') from None


def scene_asset_catalog(root, digest):
    for path in sorted((root / 'scene-catalogs').glob('*.json')):
        try:
            value = load_scene_catalog(root, path.stem)
        except ProductionError:
            continue
        if digest in value['files']:
            return value
    raise ProductionError('SPINE_ASSET_NOT_FOUND')


def scene_envelope(root, ref, track, script, actor_catalog):
    if ref is None:
        if track is not None:
            fail('furniture_track', '家具互动必须选择场景')
        return {}
    try:
        ref = SceneRef.model_validate(ref).model_dump()
    except ValidationError:
        fail('scene', '场景引用格式无效')
    catalog = load_scene_catalog(root, ref['catalog_sha256'])
    scene = next((s for s in catalog['scenes'] if s['scene_id'] == ref['scene_id']), None)
    if scene is None:
        fail('scene.scene_id', '场景不属于指定目录')
    result = {'scene': ref}
    if track is None:
        return result
    try:
        track = FurnitureTrack.model_validate(track).model_dump()
    except ValidationError:
        fail('furniture_track', '家具互动轨道格式无效')
    if script['usage'] != 'technical_coverage':
        fail('furniture_track', '家具互动首版仅允许独立技术样例')
    actors = {a['instance_id']: a for a in script['actors']}
    seen = set()
    previous = []
    for cue in track['cues']:
        actor = actors.get(cue['instance_id'])
        seat = next((s for s in scene['seats'] if s['seat_id'] == cue['seat_id']), None)
        if cue['id'] in seen or actor is None or seat is None:
            fail('furniture_track.cues', '重复 ID、演员或座位不存在')
        seen.add(cue['id'])
        if (actor['actor_id'], actor['actor_revision'], actor['initial_outfit_id']) != (seat['actor_id'], seat['actor_revision'], seat['outfit_id']):
            fail('furniture_track.cues', '该演员版本与座位尚未完成对位校准')
        if not {'idle', 'walk', 'sit'} <= actor['actions'].keys():
            fail('furniture_track.cues', '演员缺少家具互动所需动作')
        if not 0 <= cue['start_frame'] < cue['arrive_frame'] < cue['leave_frame'] < cue['end_frame'] <= script['total_frames']:
            fail('furniture_track.cues', '家具互动时间顺序或范围无效')
        if cue['leave_frame'] - cue['arrive_frame'] < math.ceil(actor['actions']['sit'] * script['sample_rate']):
            fail('furniture_track.cues', '坐姿区间不足以播放完整动作')
        if cue['end_frame'] - cue['leave_frame'] <= math.ceil(FURNITURE_STAND_SECONDS * script['sample_rate']):
            fail('furniture_track.cues', '离开区间需包含站立待机与行走')
        # This v1 track owns placement until the end of the sample. Do not silently
        # override independently authored body, movement, outfit or facing tracks.
        if any(c['enabled'] and c['instance_id'] == cue['instance_id'] and c['type'] != 'camera.focus' for c in script['cues']):
            fail('furniture_track.cues', '家具样例演员不能同时拥有身体、位移或换装编排')
        for other in previous:
            if other['instance_id'] == cue['instance_id'] or (other['seat_id'] == cue['seat_id'] and max(other['start_frame'], cue['start_frame']) < min(other['end_frame'], cue['end_frame'])):
                fail('furniture_track.cues', '演员重复编排或座位占用冲突')
        previous.append(cue)
    result['furniture_track'] = deepcopy(track)
    return result
