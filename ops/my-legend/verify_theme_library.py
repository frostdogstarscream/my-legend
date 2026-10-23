"""Verify a generated static library independently; optionally create a portable ZIP."""
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit
import argparse
import json
import zipfile


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def check_hash(value, key):
    expected = sha256(canonical({k: v for k, v in value.items() if k != key})).hexdigest()
    if value[key] != expected:
        raise ValueError('Manifest checksum mismatch')


class Links(HTMLParser):
    def __init__(self):
        super().__init__();self.refs = []
    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if (tag, name) in [('a', 'href'), ('img', 'src')]: self.refs.append(value)


def verify(root, archive=None):
    root = Path(root).resolve()
    current = json.loads((root/'current.json').read_text('utf-8'))['catalog_sha256']
    release = root/'catalogs'/current
    c = json.loads((release/'catalog.json').read_text('utf-8'))
    check_hash(c, 'catalog_sha256')
    if current != c['catalog_sha256']: raise ValueError('Pointer differs from catalog')
    for h, ref in c['files'].items():
        path = root/'content'/(h+'.png')
        raw = path.read_bytes()
        if len(raw) != ref['size_bytes'] or sha256(raw).hexdigest() != h: raise ValueError('Image corrupted: '+h)
    refs = 0
    for t in c['themes']:
        folder = release/'themes'/t['theme_id']
        m = json.loads((folder/'manifest.json').read_text('utf-8'))
        check_hash(m, 'package_sha256')
        if m['package_sha256'] != t['package_sha256']: raise ValueError('Theme version mismatch')
        display, aux = set(m['asset_ids']), set(m['auxiliary_asset_ids'])
        if display & aux: raise ValueError('Auxiliary images mixed into display list')
        for key in display | aux:
            asset = c['assets'][key]
            if asset['sha256'] not in c['files']: raise ValueError('Unregistered image')
            if (key in aux) != (asset['category'] == 'alpha_auxiliary'): raise ValueError('Invalid alpha classification')
        missing = 0
        for item in m['items']:
            if item['source_record']['classes'] != m['class_id']: raise ValueError('Wrong theme assignment')
            for component in item['components']:
                if component['status'] == 'static_verified':
                    for key in component['asset_ids']:
                        if c['assets'][key]['name'].casefold() != component['code'].casefold(): raise ValueError('Guessed source name')
                missing += component['status'] in ('missing_static_source', 'ambiguous_source')
        if missing != len(m['missing_static']): raise ValueError('Missing source count mismatch')
    for path in release.rglob('*.html'):
        parser = Links();parser.feed(path.read_text('utf-8'))
        for ref in parser.refs:
            parsed = urlsplit(ref)
            if parsed.scheme or not parsed.path: continue
            target = (path.parent/unquote(parsed.path)).resolve()
            if not target.is_relative_to(root) or not target.is_file(): raise ValueError('Broken or escaped preview link: '+ref)
            refs += 1
    for name, h in c['evidence_files'].items():
        if sha256((release/'evidence'/name).read_bytes()).hexdigest() != h: raise ValueError('Evidence changed: '+name)
    report = {'catalog_sha256': current, 'theme_manifests_verified': len(c['themes']), 'image_files_rehashed': len(c['files']),
              'html_references_verified': refs, 'source_membership_checked': True, 'alpha_separation_checked': True,
              'missing_by_theme': [{'theme_id': t['theme_id'], 'name': t['name'], 'count': len(t['missing_static'])}
                                   for t in c['themes'] if t['missing_static']], 'summary': c['summary']}
    if archive:
        archive = Path(archive).resolve()
        if archive.exists(): raise ValueError('Archive already exists; choose a new versioned name')
        archive.parent.mkdir(parents=True, exist_ok=True)
        paths = [root/'current.json', root/'index.html'] + ([root/'README.md'] if (root/'README.md').is_file() else [])
        paths += sorted(p for p in release.rglob('*') if p.is_file())
        paths += [root/ref['path'] for ref in c['files'].values()]
        with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_STORED, allowZip64=True) as output:
            for path in paths: output.write(path, path.relative_to(root).as_posix())
        with zipfile.ZipFile(archive) as output:
            if output.testzip() is not None: raise ValueError('Archive CRC check failed')
        report['archive'] = {'path': str(archive), 'size_bytes': archive.stat().st_size,
                             'sha256': sha256(archive.read_bytes()).hexdigest(), 'crc_all_entries_passed': True, 'entries': len(paths)}
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2]/'runtime/my-legend-spine/theme-library')
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    result = verify(args.root, args.archive)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
