import csv
from hashlib import sha256
import importlib.util
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest

from PIL import Image

spec = importlib.util.spec_from_file_location('theme_builder', Path(__file__).parents[1]/'prepare_theme_library.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def table(root, table_id, rows):
    columns = list(rows[0])
    types = [5 if isinstance(rows[0][k], int) else 11 for k in columns]
    start = 11+len(columns)+8
    body = struct.pack('<IB', len(rows), len(columns))+bytes(types)+struct.pack('<iI', rows[0]['id'], start)
    offsets = []
    for row in rows:
        offsets.append(6+len(body))
        for key, wire in zip(columns, types):
            v = row[key]
            if wire == 5: body += struct.pack('<i', v)
            else:
                b = v.encode('utf-8');body += b'\x00'+struct.pack('<H', len(b))+b
    raw = struct.pack('<HI', table_id, len(body))+body
    source = root/'inputs/pc-20261023/GrilsFrontLine_Data/StreamingAssets/Res/Pc/stc'
    named = root/'work/stc-named'
    source.mkdir(parents=True, exist_ok=True);named.mkdir(parents=True, exist_ok=True)
    (source/f'{table_id}.stc').write_bytes(raw)
    (named/f'{table_id}.schema.json').write_text(json.dumps({'status': 'native_schema_matched', 'rows': len(rows),
        'source_sha256': sha256(raw).hexdigest(), 'columns': [{'name': k, 'wire_type': t} for k, t in zip(columns, types)]}))
    (named/f'{table_id}.jsonl').write_text('\n'.join(json.dumps({'source_offset': o, 'values': r}) for o, r in zip(offsets, rows)))


class ThemeBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lib, self.offline, self.dest = [self.root/p for p in ('library', 'offline', 'output')]
        index = self.lib/'00_索引与说明';index.mkdir(parents=True)
        (index/'source_tables.json').write_text(json.dumps({'furniture': {'entries': {'furniture-10000601': 'LOFT-chair'}},
            'furniture_classes': {'entries': {'furniture_classes-10000006': 'LOFT', 'furniture_classes-10000010': 'Cafe'}}}), encoding='utf-8')
        records = []
        for i, name in enumerate(['chair', 'chair_Alpha']):
            out = io.BytesIO();Image.new('RGBA', (2, 3), (i*100, 80, 90, 100)).save(out, format='PNG')
            data = out.getvalue();relative = f'02_room/furniture/static/group_abc/{name}.png'
            path = self.lib/relative;path.parent.mkdir(parents=True, exist_ok=True);path.write_bytes(data)
            records.append({'素材ID': str(i), '原始名称': name, '素材路径': relative, '源资源路径': f'assets/furniture/{name}.png',
                '源资源包': 'fixture.ab', '对象ID': i, '宽': 2, '高': 3, 'bytes': len(data), 'sha256': sha256(data).hexdigest(), '透明度说明': 'fixture'})
        with (index/'宿舍静态素材目录.csv').open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
        table(self.offline, 5172, [{'id': 6, 'name': 'furniture_classes-10000006'}, {'id': 10, 'name': 'furniture_classes-10000010'}, {'id': 117, 'name': 'not_localized'}])
        table(self.offline, 5071, [{'id': 601, 'name': 'furniture-10000601', 'classes': 6, 'code': 'CHAIR,animated', 'texture_type': '1,2'},
            {'id': 1001, 'name': 'unknown_name', 'classes': 10, 'code': 'missing', 'texture_type': '1'}])

    def test_exact_config_membership_alpha_separation_and_explicit_gaps(self):
        result = builder.prepare(self.lib, self.offline, self.dest)
        self.assertEqual(result['summary']['complete_static_themes'], 1)
        self.assertEqual(result['summary']['partial_static_themes'], 1)
        self.assertEqual(result['summary']['non_static_components'], 1)
        self.assertEqual(result['summary']['unlocalized_class_rows'], 1)
        theme = result['themes'][0]
        self.assertEqual(theme['asset_ids'], ['0']);self.assertEqual(theme['auxiliary_asset_ids'], ['1'])
        self.assertFalse(theme['human_visual_acceptance'])
        self.assertEqual(result['themes'][1]['missing_static'][0]['code'], 'missing')

    def test_rerun_is_idempotent_and_sources_are_unchanged(self):
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        first = builder.prepare(self.lib, self.offline, self.dest)
        second = builder.prepare(self.lib, self.offline, self.dest)
        self.assertEqual(first['catalog_sha256'], second['catalog_sha256'])
        self.assertTrue(all(p.read_bytes() == b for p, b in before.items()))

    def test_named_table_tamper_cannot_override_source_mapping(self):
        path = self.offline/'work/stc-named/5071.jsonl'
        path.write_text(path.read_text().replace('CHAIR', 'other'))
        with self.assertRaisesRegex(ValueError, 'do not match'): builder.prepare(self.lib, self.offline, self.dest)

    def test_source_image_change_is_rejected(self):
        (self.lib/'02_room/furniture/static/group_abc/chair.png').write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError, 'Indexed PNG changed'): builder.prepare(self.lib, self.offline, self.dest)

    def test_existing_content_corruption_is_not_overwritten(self):
        result = builder.prepare(self.lib, self.offline, self.dest)
        path = self.dest/next(iter(result['files'].values()))['path'];path.write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError, 'Immutable output differs'): builder.prepare(self.lib, self.offline, self.dest)
        self.assertEqual(path.read_bytes(), b'bad')

    def test_source_output_overlap_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'separate'): builder.prepare(self.lib, self.offline, self.lib/'output')


if __name__ == '__main__':
    unittest.main()
