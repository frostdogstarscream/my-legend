"""Create a new blocking-oriented scene snapshot using existing immutable PNGs."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from prepare_ready_scenes import ROOT, digest, verified_json
from prepare_spine_preview import atomic_json


def build(base, recipe, source_hash):
    value = deepcopy(base)
    value.pop('catalog_sha256', None)
    if recipe['schema_version'] != 'legend_workflow_scene_sources/1':
        raise ValueError('Unsupported workflow layout source')
    specs = {s['scene_id']: s for s in recipe['scenes']}
    if len(specs) != len(recipe['scenes']) or set(specs) != {s['scene_id'] for s in base['scenes']}:
        raise ValueError('Every source scene must have exactly one layout')
    for scene in value['scenes']:
        spec = specs[scene['scene_id']]
        scene['description'] = spec['description']
        scene['actor_scale'] = recipe['actor_scale']
        scene['activity_area'] = [.10, .76, .90, .90]
        # Geometry and scale changed: the previous seating calibration does not apply.
        scene['seats'] = []
        by_id = {o['id']: o for o in scene['objects']}
        omitted = set(spec.get('omit_objects', []))
        if not omitted <= by_id.keys() or omitted & spec['objects'].keys():
            raise ValueError('Invalid omitted object')
        scene['objects'] = [o for o in scene['objects'] if o['id'] not in omitted]
        by_id = {o['id']: o for o in scene['objects']}
        if not set(spec['objects']) <= by_id.keys():
            raise ValueError('Unknown scene object')
        if not set(spec['floor_objects']) <= by_id.keys():
            raise ValueError('Unknown floor object')
        for obj in scene['objects']:
            if obj['repeat']:
                obj['position'] = [0, 0 if obj['layer'] == 'wall' else 320]
                obj['height'] = 320 if obj['layer'] == 'wall' else 220
                if obj['layer'] == 'floor' and 'floor_tile_scale' in spec:
                    obj['tile_scale'] = spec['floor_tile_scale']
            elif obj['id'] in spec['objects']:
                x, y, width = spec['objects'][obj['id']]
                obj['height'] = round(obj['height'] * width / obj['width'], 4)
                obj.update(position=[x, y], width=width,
                           layer='depth' if obj['id'] in spec['floor_objects'] else 'back', depth_y=y)
        scene['blocking'] = {
            'schema_version': 'legend_scene_blocking/1',
            'purpose': 'authored_standing_layout',
            'walk_lane': [.06, .80, .94, .86],
            'foot_y': .825,
            'dialogue_slots': [[1/3, .825], [2/3, .825]],
            'ensemble_slots': [[.25, .825], [.5, .825], [.75, .825]],
            'entrances': [{'id': 'left', 'label': '左侧进出', 'position': [.06, .825]},
                          {'id': 'right', 'label': '右侧进出', 'position': [.94, .825]}],
            'targets': [{'id': oid + '_front', 'object_id': oid, 'label': label,
                         'position': [x, .825], 'action': 'approach'} for oid, label, x in spec['targets']],
            'interaction_status': 'standing_targets_only',
        }
        # All physical props finish behind the foot lane. No front/depth object
        # may silently hide a character moving across the designated lane.
        if any(o['layer'] == 'front' or not o['repeat'] and o['position'][1] > 420 for o in scene['objects']):
            raise ValueError('Foreground object blocks the walking lane')
        if any(t['object_id'] not in by_id or not .10 <= t['position'][0] <= .90 for t in scene['blocking']['targets']):
            raise ValueError('Invalid approach target')
    value.update(parent_catalog_sha256=base['catalog_sha256'], layout_revision='workflow_staging/0.9',
                 layout_source_sha256=source_hash,
                 notes=['15 套面向剧情工作流的站立布局；由既有原始 PNG 重新布置。',
                        '左右进出、对白站位和物件前停靠点为人工设计的编排参考，不等于自动剧情编排。',
                        '家具位置及人物比例变化后不沿用旧坐姿校准；历史目录和旧预览仍可恢复。'])
    value['catalog_sha256'] = digest(value)
    return value


def prepare(destination, layouts=None, activate=False):
    destination = Path(destination).resolve()
    raw = Path(layouts or Path(__file__).with_name('workflow-scene-presets.json')).read_bytes()
    recipe = json.loads(raw)
    base = verified_json(destination / 'scene-catalogs' / (recipe['base_scene_catalog'] + '.json'),
                         recipe['base_scene_catalog'], 'catalog_sha256')
    for h, ref in base['files'].items():
        data = (destination / 'content' / h).read_bytes()
        if sha256(data).hexdigest() != h or len(data) != ref['size_bytes']:
            raise ValueError('Original image changed')
    value = build(base, recipe, sha256(raw).hexdigest())
    target = destination / 'scene-catalogs' / (value['catalog_sha256'] + '.json')
    if target.exists():
        if json.loads(target.read_text(encoding='utf-8')) != value:
            raise ValueError('Existing scene snapshot differs')
    else:
        atomic_json(target, value)
    if activate:
        atomic_json(destination / 'scene-current.json', {'catalog_sha256': value['catalog_sha256']})
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, default=ROOT / 'runtime/my-legend-spine')
    parser.add_argument('--layouts', type=Path)
    parser.add_argument('--activate', action='store_true', help='Select the new catalog after local verification')
    args = parser.parse_args()
    result = prepare(args.destination, args.layouts, args.activate)
    print(json.dumps({'catalog_sha256': result['catalog_sha256'], 'scenes': len(result['scenes']), 'activated': args.activate}))
