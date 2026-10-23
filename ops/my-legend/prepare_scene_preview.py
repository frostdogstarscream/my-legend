"""Copy verified source PNGs into immutable local storage; never edit game assets.

Run with a Python environment containing Pillow. Coordinates are authored room
layouts, not recovered official room arrangements. No generated or repainted art.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import argparse
import csv
import io
import json
import sys

from PIL import Image

from prepare_spine_preview import atomic_json

ROOT = Path(__file__).resolve().parents[2]


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def prepare(library, destination, layout_path=None):
    library, destination = Path(library).resolve(), Path(destination).resolve()
    layout_path = Path(layout_path or Path(__file__).with_name('scene-presets.json'))
    layouts = json.loads(layout_path.read_text(encoding='utf-8'))
    index = library / '00_索引与说明'
    tables_bytes = (index / 'source_tables.json').read_bytes()
    tables = json.loads(tables_bytes)
    with (index / '宿舍静态素材目录.csv').open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    by_name = {}
    for row in rows:
        by_name.setdefault(row['原始名称'].lower(), []).append(row)
    current = json.loads((destination / 'life-current.json').read_text(encoding='utf-8'))['catalog_sha256']
    actors = json.loads((destination / 'catalogs' / (current + '.json')).read_text(encoding='utf-8'))
    hk = next(a for a in actors['actors'] if a['actor_id'] == 'hk416_dorm')
    files, sources, scenes = {}, {}, []
    (destination / 'content').mkdir(parents=True, exist_ok=True)
    (destination / 'scene-catalogs').mkdir(parents=True, exist_ok=True)

    def resource(name):
        matches = by_name.get(name.lower(), [])
        if len(matches) != 1:
            raise ValueError('Need exactly one indexed source: ' + name)
        row = matches[0]
        path = (library / row['素材路径']).resolve()
        if not path.is_relative_to(library):
            raise ValueError('Source escaped library')
        data = path.read_bytes()
        h = sha256(data).hexdigest()
        if h != row['sha256']:
            raise ValueError('Source index hash mismatch: ' + name)
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if image.format != 'PNG' or image.size != (int(row['宽']), int(row['高'])) or image.mode != 'RGBA':
                raise ValueError('Unexpected source image: ' + name)
            width, height = image.size
            alpha = image.getchannel('A').getextrema()
        ref = {'sha256': h, 'size_bytes': len(data), 'media_type': 'image/png'}
        target = destination / 'content' / h
        if target.exists() and target.read_bytes() != data:
            raise ValueError('Existing content differs from its name')
        if not target.exists():
            target.write_bytes(data)
        files[h] = ref
        sources[name] = {'source_path': row['素材路径'], 'source_bundle': row['源资源包'],
                         'object_id': row['对象ID'], 'sha256': h, 'width': width, 'height': height,
                         'alpha_range': alpha, 'alpha_basis': row['透明度说明'],
                         'processing': 'unchanged_indexed_rgba_png'}
        return ref, width, height

    for spec in layouts['scenes']:
        scene = {k: deepcopy(spec[k]) for k in ('scene_id', 'display_name', 'resource_group', 'name_evidence', 'actor_scale')}
        scene.update(width=960, height=540, layout_kind='authored_performance_sample',
                     activity_area=[.06, .68, .94, .88], objects=[], seats=[])
        for item in spec['objects']:
            ref, width, height = resource(item['source'])
            scene['objects'].append({'id': item['id'], 'image': ref, 'source_name': item['source'],
                'position': [item['x'], item['y']], 'width': item['width'],
                'height': item.get('height', round(item['width'] * height / width, 4)),
                'anchor': item.get('anchor', [.5, 1]), 'layer': item['layer'],
                'source_size': [width, height],
                'depth_y': item.get('depth_y', item['y']), 'repeat': item.get('repeat', False),
                'tile_scale': item.get('tile_scale', 1), 'tint': item.get('tint', '#ffffff')})
        scene['seats'].append({**spec['seat'], 'actor_id': hk['actor_id'], 'actor_revision': hk['revision'],
                               'outfit_id': None, 'calibration_kind': 'authored_pose_alignment'})
        scenes.append(scene)
    names = [{'source_key': key, 'name': name, 'name_found': True,
              'resources_located': False, 'pairing_verified': False, 'scene_review': 'not_reviewed',
              'candidate_resource_groups': [], 'note': '名称表记录；尚未逐项关联素材。'}
             for key, name in tables['furniture_classes']['entries'].items() if key.startswith('furniture_classes-1')]
    # Keep resource-name associations as explicit candidates; these are not recovered set IDs.
    for item in names:
        if item['name'] in ('LOFT', '咖啡店'):
            item.update(resources_located=True, candidate_resource_groups=['loft2016' if item['name'] == 'LOFT' else 'coffeeshop'],
                        note='同名资源组已定位；官方套装 ID 到资源的映射尚未确认。')
    value = {'schema_version': 'legend_scene_catalog/1', 'scenes': scenes, 'files': files,
             'sources': sources, 'themes': names, 'source_tables_sha256': sha256(tables_bytes).hexdigest(),
             'layout_source_sha256': sha256(layout_path.read_bytes()).hexdigest(),
             'notes': ['三套均为自行布置的演绎样例，未声称官方推荐布局。',
                       '家具互动为固定演员与固定座位的技术演示，不是剧情语义推断。',
                       '咖啡店墙面底色使用本组地板纹理衬底，前方覆盖原始咖啡店背景构件。']}
    value['catalog_sha256'] = digest(value)
    target = destination / 'scene-catalogs' / (value['catalog_sha256'] + '.json')
    if not target.exists():
        atomic_json(target, value)
    elif json.loads(target.read_text(encoding='utf-8')) != value:
        raise ValueError('Existing scene catalog differs')
    atomic_json(destination / 'scene-current.json', {'catalog_sha256': value['catalog_sha256']})
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--destination', type=Path, default=ROOT / 'runtime/my-legend-spine')
    parser.add_argument('--layouts', type=Path)
    args = parser.parse_args()
    result = prepare(args.library, args.destination, args.layouts)
    print(json.dumps({'catalog_sha256': result['catalog_sha256'], 'scenes': len(result['scenes']),
                      'images': len(result['files']), 'theme_names': len(result['themes'])}, ensure_ascii=False))
