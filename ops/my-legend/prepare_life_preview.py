"""Import the approved eight Spine files; never modify the source game directory."""
from pathlib import Path
from hashlib import sha256
import argparse
import json
import re
import struct
import sys

from spine21 import decode, animation_duration
from prepare_spine_preview import atomic_json
from provision_spine_runtime import FILES

ROOT = Path(__file__).resolve().parents[2]
PARTS = {
    'ak15mod_dorm': '8387aa75c64f', 'hk416_dorm': '84f5424146b7',
    'uniform_head': '16a77e714e31', 'uniform_body': 'afd5df2b98bc', 'uniform_foot': '2c5c09efd9bc',
    'summer_head': '809892edabef', 'summer_body': '6cc8c910903f', 'summer_foot': 'a1b843f6d758',
}
ACTION_MAP = {'idle': 'wait', 'walk': 'move', 'sit': 'sit', 'pick': 'pick', 'lie': 'lying',
              'victory': 'victory', 'victoryloop': 'victoryloop'}


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def atlas_metadata(text):
    """Atlas page headers and attachment region names, retaining exact names."""
    pages, regions = {}, set()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.strip() or line[0].isspace() or ':' in line:
            continue
        name = line.strip()
        if name.lower().endswith('.png') and i + 1 < len(lines) and lines[i + 1].startswith(('size:', 'format:')):
            if Path(name).name != name or '/' in name or '\\' in name:
                raise ValueError('Atlas page must be a basename')
            size = re.match(r'size:\s*(\d+)\s*,\s*(\d+)', lines[i + 1])
            if not size:
                raise ValueError('Atlas page dimensions required')
            pages[name] = tuple(map(int, size.groups()))
        else:
            regions.add(name)
    if not pages or not regions:
        raise ValueError('Empty atlas')
    return pages, regions


def prepare(game, destination):
    game, destination = Path(game).resolve(), Path(destination).resolve()
    content = destination / 'content'
    snapshots = destination / 'catalogs'
    content.mkdir(parents=True, exist_ok=True)
    snapshots.mkdir(parents=True, exist_ok=True)
    files = {}

    def put(data, mime):
        h = sha256(data).hexdigest()
        path = content / h
        if path.exists() and sha256(path.read_bytes()).hexdigest() != h:
            raise ValueError('Existing content hash mismatch: ' + h)
        if not path.exists():
            path.write_bytes(data)
        ref = {'sha256': h, 'size_bytes': len(data), 'media_type': mime}
        files[h] = ref
        return ref

    runtime = (destination / 'vendor/spine.js').read_bytes()
    if sha256(runtime).hexdigest() != FILES['spine.js'][1]:
        raise ValueError('Pinned runtime hash mismatch')
    runtime_ref = put(runtime + b'\nexport default spine;\n', 'text/javascript')
    rows = json.loads((game / '少女前线_分类素材库_v1/00_索引与说明/organized_catalog.json').read_text(encoding='utf-8'))
    by_id = {r['id']: r for r in rows}
    skeletons = []
    export_root = (game / 'Spine_Export_20261022').resolve()
    for key, aid in PARTS.items():
        row = by_id[aid]
        folder = (export_root / row['directory']).resolve()
        if not folder.is_relative_to(export_root) or row['issues']:
            raise ValueError('Source path or QA invalid: ' + aid)
        def source_file(name):
            path = (folder / name).resolve()
            if path.parent != folder:
                raise ValueError('Source filename must be a basename')
            return path.read_bytes()
        raw = source_file(row['skeleton'])
        skeleton = decode(raw)
        atlas_names = [x.strip() for x in row['atlases'].split(';') if x.strip()]
        if len(atlas_names) != 1:
            raise ValueError('First batch requires exactly one atlas')
        atlas = source_file(atlas_names[0]).decode('utf-8-sig')
        dimensions, regions = atlas_metadata(atlas)
        texture_names = {x.strip() for x in row['textures'].split(';') if x.strip()}
        pages = {}
        for name, size in dimensions.items():
            if name not in texture_names:
                raise ValueError('Unpaired atlas page: ' + name)
            png = source_file(name)
            if png[:8] != b'\x89PNG\r\n\x1a\n' or struct.unpack('>II', png[16:24]) != size:
                raise ValueError('Atlas/PNG dimension mismatch: ' + name)
            pages[name] = put(png, 'image/png')
        for skin in skeleton['skins'].values():
            for attachments in skin.values():
                for name, attachment in attachments.items():
                    if attachment['type'] != 'boundingbox' and attachment.get('path', attachment.get('name', name)) not in regions:
                        raise ValueError('Missing atlas attachment: ' + name)
        animations = {k: {'duration': animation_duration(v)} for k, v in skeleton['animations'].items()}
        if not {'wait', 'move'} <= animations.keys():
            raise ValueError('Required idle/walk missing: ' + aid)
        skeletons.append({'asset_id': key, 'source_asset_id': aid, 'source_filename': row['skeleton'],
                          'original': put(raw, 'application/octet-stream'),
                          'skeleton': put(json.dumps(skeleton, ensure_ascii=False, allow_nan=False).encode(), 'application/json'),
                          'atlas': put(atlas.encode(), 'text/plain'), 'pages': pages,
                          'format': skeleton['skeleton']['spine'], 'animations': animations,
                          'alpha_mode': 'premultiplied-alpha', 'qa': 'pass'})
    lookup = {s['asset_id']: s for s in skeletons}
    outfits = []
    for name, display in [('uniform', '初始服装 · 女款'), ('summer', '夏装 · 女款')]:
        parts = {slot: name + '_' + slot for slot in ('head', 'body', 'foot')}
        shared = set.intersection(*(set(lookup[p]['animations']) for p in parts.values()))
        calibration = {slot: {'x': 0, 'y': 0, 'scale': 1, 'rotation': 0, 'flip': False, 'layer': 0, 'actions': {}} for slot in parts}
        outfit = {'schema_version': 'legend_outfit/0.3', 'outfit_id': 'commander_' + name,
                  'actor_id': 'commander_female', 'display_name': display, 'parts': parts,
                  'allowed_actions': [a for a, raw in ACTION_MAP.items() if raw in shared],
                  'calibration': calibration, 'calibration_revision': digest(calibration), 'qa': 'pass'}
        outfit['revision'] = digest(outfit)
        outfits.append(outfit)
    actors = []
    for key, name in [('ak15mod_dorm', 'AK15Mod'), ('hk416_dorm', 'HK416'), ('commander_female', '女指挥官')]:
        single = key != 'commander_female'
        actions = set(lookup[key]['animations']) if single else set.intersection(*(set(lookup[p]['animations']) for o in outfits for p in o['parts'].values()))
        actor = {'schema_version': 'legend_actor/0.3', 'actor_id': key, 'display_name': name,
                 'asset_mode': 'single_skeleton' if single else 'commander_tripartite',
                 'asset_id': key if single else None, 'outfit_ids': [] if single else [o['outfit_id'] for o in outfits],
                 'action_map': {k: v for k, v in ACTION_MAP.items() if v in actions},
                 'lifecycle': 'ready', 'asset_qa': 'pass', 'mouth_control': False}
        actor['revision'] = digest({'actor': actor, 'assets': [lookup[key]] if single else [s for s in skeletons if s['asset_id'].startswith(('uniform_', 'summer_'))], 'outfits': [] if single else outfits})
        actors.append(actor)
    value = {'schema_version': 'spine_life_catalog/2.0', 'profile': 'life_v1', 'runtime': runtime_ref,
             'runtime_provenance': json.loads((destination / 'vendor/provenance.json').read_text(encoding='utf-8-sig')),
             'actors': actors, 'outfits': outfits, 'skeletons': skeletons, 'files': files,
             'rights_basis': 'official_permission_reported_by_user', 'usage': 'local_preview_only'}
    value['catalog_sha256'] = digest(value)
    sys.path.insert(0, str(ROOT / 'source/my-legend-production/src'))
    from my_legend_production.production.life_contracts import validate_catalog
    validate_catalog(value)
    target = snapshots / (value['catalog_sha256'] + '.json')
    if not target.exists():
        atomic_json(target, value)
    atomic_json(destination / 'life-current.json', {'catalog_sha256': value['catalog_sha256']})
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game', type=Path, default=Path("D:/game/steamapps/common/GIRLS' FRONTLINE"))
    parser.add_argument('--destination', type=Path, default=ROOT / 'runtime/my-legend-spine')
    args = parser.parse_args()
    result = prepare(args.game, args.destination)
    print(json.dumps({'catalog_sha256': result['catalog_sha256'], 'skeletons': len(result['skeletons']), 'actors': len(result['actors']), 'outfits': len(result['outfits'])}))
