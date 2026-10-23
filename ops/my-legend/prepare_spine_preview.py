"""Import three local Spine 2.1 samples into a private content-addressed catalog.

No archive-wide extraction, model calls, or uploads. Original files are unchanged.
The official runtime must be provisioned locally; its license header is retained.
"""
import argparse
from hashlib import sha256
from pathlib import Path, PurePosixPath
import json
import os
import re
import tempfile
from zipfile import ZipFile

from spine21 import decode, animation_duration
from provision_spine_runtime import FILES

ROOT = Path(__file__).resolve().parents[2]


def atomic_json(path, value):
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.import-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def prepare(archive, destination, actor_ids):
    destination = Path(destination)
    content = destination / 'content'
    content.mkdir(parents=True, exist_ok=True)
    files = {}

    def put(data, media_type):
        digest = sha256(data).hexdigest()
        target = content / digest
        if not target.exists():
            target.write_bytes(data)
        files[digest] = {'sha256': digest, 'size_bytes': len(data), 'media_type': media_type}
        return files[digest]

    runtime = destination / 'vendor' / 'spine.js'
    if sha256(runtime.read_bytes()).hexdigest() != FILES['spine.js'][1]:
        raise ValueError('Runtime is not the pinned official Spine 2.1 reference')
    provenance = json.loads((destination / 'vendor' / 'provenance.json').read_text(encoding='utf-8-sig'))
    runtime_ref = put(runtime.read_bytes() + b'\nexport default spine;\n', 'text/javascript')
    actors = []
    with ZipFile(archive) as z:
        names = {n for n in z.namelist() if not n.startswith('__MACOSX/')}
        for actor_id in actor_ids:
            if not re.fullmatch('[a-zA-Z0-9_-]+', actor_id):
                raise ValueError('Invalid actor id')
            candidates = sorted(n for n in names if n.endswith('/' + actor_id + '.skel'))
            def readable(name):
                try:
                    return name.encode('cp437').decode('utf-8')
                except (UnicodeError, LookupError):
                    return name
            preferred = [n for n in candidates if '/6星人形/' in readable(n)]
            candidates = preferred or candidates
            if len(candidates) != 1:
                raise ValueError(f'Ambiguous or missing skeleton: {actor_id}')
            source = candidates[0]
            folder = str(PurePosixPath(source).parent)
            atlas_name = folder + '/' + actor_id + '.atlas'
            raw, atlas = z.read(source), z.read(atlas_name).decode('utf-8-sig')
            data = decode(raw)
            pages = {}
            # In this export format image page names are unindented lines followed by size/format.
            lines = atlas.splitlines()
            for i, line in enumerate(lines):
                if line.strip().lower().endswith('.png') and i+1 < len(lines) and lines[i+1].startswith(('size:', 'format:')):
                    name = line.strip()
                    if PurePosixPath(name).name != name or '\\' in name:
                        raise ValueError('Atlas page must be a local basename')
                    pages[name] = put(z.read(folder + '/' + name), 'image/png')
            if not pages:
                raise ValueError(f'No atlas pages: {actor_id}')
            skeleton_ref = put(json.dumps(data, ensure_ascii=False, allow_nan=False).encode(), 'application/json')
            animations = {name: {'duration': animation_duration(value)} for name, value in data['animations'].items()}
            if 'wait' not in animations:
                raise ValueError(f'No wait animation: {actor_id}')
            actors.append({'actor_id': actor_id, 'display_name': actor_id.upper(), 'version': sha256(raw).hexdigest(),
                           'format': data['skeleton']['spine'], 'skeleton': skeleton_ref,
                           'atlas': put(atlas.encode(), 'text/plain'), 'pages': pages,
                           'animations': animations, 'idle_animation': 'wait',
                           'mouth_control': False, 'alpha_mode': 'premultiplied-alpha',
                           'bone_count': len(data['bones']), 'slot_count': len(data['slots']),
                           'source_entry': readable(source), 'usage': 'local_preview_only'})
    manifest = {'schema_version': 'spine_preview_catalog/1.0', 'usage': 'local_preview_only',
                'mouth_control': False, 'runtime': runtime_ref, 'runtime_provenance': provenance,
                'actors': actors, 'files': files}
    manifest['catalog_sha256'] = sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    atomic_json(destination / 'catalog.json', manifest)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--destination', type=Path, default=ROOT / 'runtime/my-legend-spine')
    parser.add_argument('--actors', nargs='+', default=['hk416mod', 'mg4', 'g11mod'])
    args = parser.parse_args()
    catalog = prepare(args.archive, args.destination, args.actors)
    print(json.dumps({'actors': [a['actor_id'] for a in catalog['actors']], 'catalog_sha256': catalog['catalog_sha256'],
                      'local_only': True, 'mouth_control': False}, ensure_ascii=False))
