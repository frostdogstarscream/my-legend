"""Fetch hash-pinned official Spine 2.1 references into the ignored local runtime."""
from hashlib import sha256
from pathlib import Path
import json
from urllib.request import urlopen

COMMIT = '142e770e5b4b42395a3a0a68ff3c8a21ba2774eb'
FILES = {
    'spine.js': ('spine-js/spine.js', '89a8964502a9a0e23e97d129f5fe5565c1bc8ec5fafc5c8ed575172eccc3434c'),
    'SkeletonBinary.java': ('spine-libgdx/spine-libgdx/src/com/esotericsoftware/spine/SkeletonBinary.java',
                            'c8478dff8156b6ccd32819215713e27a65b51d1eb94dacef2618d497237b1427'),
}


def main():
    target = Path(__file__).resolve().parents[2] / 'runtime/my-legend-spine/vendor'
    target.mkdir(parents=True, exist_ok=True)
    for name, (source, expected) in FILES.items():
        path = target / name
        if path.exists():
            payload = path.read_bytes()
        else:
            with urlopen(f'https://raw.githubusercontent.com/EsotericSoftware/spine-runtimes/{COMMIT}/{source}', timeout=40) as response:
                payload = response.read()
        if sha256(payload).hexdigest() != expected:
            raise ValueError(f'Official reference hash mismatch: {name}')
        if not path.exists():
            path.write_bytes(payload)
    provenance = {'repository': 'https://github.com/EsotericSoftware/spine-runtimes', 'tag': '2.1.25',
                  'commit': COMMIT, 'files': list(FILES)}
    path = target / 'provenance.json'
    if not path.exists():
        path.write_text(json.dumps(provenance, indent=2), encoding='utf-8')
    print('Pinned runtime verified locally; original license headers retained.')


if __name__ == '__main__':
    main()
