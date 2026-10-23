"""Publish authored fixed scenes from a pinned, verified local theme library.

Only copies original PNG bytes. Earlier scene catalogs and their seat calibration
are preserved; new layouts deliberately carry no uncalibrated furniture tracks.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import argparse
import json

from prepare_spine_preview import atomic_json

ROOT = Path(__file__).resolve().parents[2]


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def verified_json(path, expected, hash_key):
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get(hash_key) != expected or digest({k: v for k, v in value.items() if k != hash_key}) != expected:
        raise ValueError('Catalog/package hash mismatch: ' + str(path))
    return value


def prepare(destination, layout_path=None):
    destination = Path(destination).resolve()
    layout_path = Path(layout_path or Path(__file__).with_name('ready-scene-presets.json'))
    raw = layout_path.read_bytes()
    layouts = json.loads(raw)
    if layouts['schema_version'] != 'legend_ready_scene_sources/1':
        raise ValueError('Unsupported layouts')
    base_hash, library_hash = layouts['base_scene_catalog'], layouts['theme_library_catalog']
    base = verified_json(destination / 'scene-catalogs' / (base_hash + '.json'), base_hash, 'catalog_sha256')
    library_root = destination / 'theme-library'
    release = library_root / 'catalogs' / library_hash
    library = verified_json(release / 'catalog.json', library_hash, 'catalog_sha256')
    scenes, files, sources = deepcopy(base['scenes']), deepcopy(base['files']), deepcopy(base['sources'])
    themes = {t['theme_id']: t for t in library['themes']}
    for scene in scenes:
        if scene['scene_id'] == 'cafe':
            scene['display_name'] = '咖啡吧'
        if scene['scene_id'] == 'halloween':
            scene['display_name'] = '幽灵古堡'
        scene['description'] = '保留原有布置与已校准的 HK416 坐姿样例。'
    for spec in layouts['scenes']:
        theme = themes[spec['theme_id']]
        package = verified_json(release / 'themes' / spec['theme_id'] / 'manifest.json', theme['package_sha256'], 'package_sha256')
        allowed = [library['assets'][i] for i in package['asset_ids']]
        scene = {k: spec[k] for k in ('scene_id', 'display_name', 'resource_group', 'description')}
        scene.update(width=960, height=540, layout_kind='authored_performance_sample', actor_scale=.68,
                     activity_area=[.27, .76, .73, .89], seats=[], objects=[],
                     name_evidence='来源：' + package['name'] + '主题；固定演绎布局为本项目编排。',
                     theme_ref={'catalog_sha256': library_hash, 'theme_id': package['theme_id'], 'package_sha256': package['package_sha256']})
        for item in spec['objects']:
            key, name, x, y, width, height, layer, *options = item
            matches = [a for a in allowed if a['name'].casefold() == name.casefold()]
            if not matches or len({a['sha256'] for a in matches}) != 1:
                raise ValueError('Not a unique verified theme sprite: ' + name)
            asset = matches[0]
            source = (library_root / asset['path']).resolve()
            if not source.is_relative_to(library_root):
                raise ValueError('Source escaped library')
            data = source.read_bytes()
            if sha256(data).hexdigest() != asset['sha256'] or len(data) != asset['size_bytes']:
                raise ValueError('Original sprite changed: ' + name)
            target = destination / 'content' / asset['sha256']
            if target.exists() and target.read_bytes() != data:
                raise ValueError('Existing content differs: ' + name)
            if not target.exists():
                target.write_bytes(data)
            ref = {'sha256': asset['sha256'], 'size_bytes': len(data), 'media_type': 'image/png'}
            files[asset['sha256']] = ref
            sources[name] = {**deepcopy(asset), 'theme_id': package['theme_id'], 'theme_catalog_sha256': library_hash}
            repeated = bool(options and options[0])
            scene['objects'].append({'id': key, 'source_name': name, 'image': ref, 'position': [x, y],
                                    'width': width, 'height': height if height is not None else round(width * asset['height'] / asset['width'], 4),
                                    'anchor': [0, 0] if repeated else [.5, 1], 'layer': layer, 'depth_y': y,
                                    'repeat': repeated, 'tile_scale': options[1] if len(options) > 1 else 1,
                                    'tint': '#ffffff', 'source_size': [asset['width'], asset['height']]})
        scenes.append(scene)
    ids = [s['scene_id'] for s in scenes]
    if len(ids) != len(set(ids)) or len(ids) > 32:
        raise ValueError('Duplicate or excessive scenes')
    value = {'schema_version': 'legend_scene_catalog/1', 'scenes': scenes, 'files': files, 'sources': sources,
             'base_catalog_sha256': base_hash, 'theme_library_catalog_sha256': library_hash,
             'layout_source_sha256': sha256(raw).hexdigest(), 'source_tables_sha256': library['source_tables_sha256'],
             'themes': [{'source_key': t['source_name_key'], 'name': t['name'], 'name_found': True,
                         'resources_located': bool(t['asset_ids']), 'pairing_verified': not t['missing_static'] and not t['non_static_dependencies'],
                         'static_status': t['static_status'], 'scene_review': 'not_accepted',
                         'candidate_resource_groups': t['resource_groups'],
                         'note': '原配置关联已验证；缺失静态构件 %d，动态依赖 %d。' % (len(t['missing_static']), len(t['non_static_dependencies']))}
                        for t in library['themes']],
             'notes': ['15 套固定演绎场景，由原始 PNG 分层布置；不是官方房间布局。',
                       '原有三套保留已校准家具互动；新增十二套仅站立与行走预览，座位未校准。',
                       '图片原样复制；未重绘、合成透明通道或替换原始素材。']}
    value['catalog_sha256'] = digest(value)
    target = destination / 'scene-catalogs' / (value['catalog_sha256'] + '.json')
    if target.exists():
        if json.loads(target.read_text(encoding='utf-8')) != value:
            raise ValueError('Existing release differs')
    else:
        atomic_json(target, value)
    atomic_json(destination / 'scene-current.json', {'catalog_sha256': value['catalog_sha256']})
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, default=ROOT / 'runtime/my-legend-spine')
    parser.add_argument('--layouts', type=Path)
    args = parser.parse_args()
    result = prepare(args.destination, args.layouts)
    print(json.dumps({'catalog_sha256': result['catalog_sha256'], 'scenes': len(result['scenes']), 'images': len(result['files'])}))
