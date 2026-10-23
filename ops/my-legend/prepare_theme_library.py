"""Build a portable, immutable static furniture library from verified local sources.

Only reads the game/library directories. Original PNG bytes are content-addressed;
no repainting, alpha merging, guessed theme membership, or room layout generation.
"""
from collections import Counter, defaultdict
from hashlib import sha256
from html import escape
from pathlib import Path
import argparse
import csv
import io
import json
import math
import re
import struct
import tempfile

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(data):
    return sha256(data).hexdigest()


def immutable(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError('Immutable output differs: ' + str(path))
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    temporary.replace(path)


def write_json(path, value):
    immutable(path, canonical(value))


def verified_table(offline, table_id):
    """Independently decode two known tables; compare every named row and offset."""
    schema_path = offline / 'work/stc-named' / f'{table_id}.schema.json'
    named_path = offline / 'work/stc-named' / f'{table_id}.jsonl'
    source_path = offline / 'inputs/pc-20261023/GrilsFrontLine_Data/StreamingAssets/Res/Pc/stc' / f'{table_id}.stc'
    raw = source_path.read_bytes()
    schema = json.loads(schema_path.read_text('utf-8'))
    if schema['status'] != 'native_schema_matched' or schema['source_sha256'] != digest(raw):
        raise ValueError('Unverified or changed table: ' + str(table_id))
    position = 0

    def take(n):
        nonlocal position
        if n < 0 or position + n > len(raw):
            raise ValueError('Truncated STC')
        value = raw[position:position+n]
        position += n
        return value

    def number(fmt):
        return struct.unpack(fmt, take(struct.calcsize(fmt)))[0]

    if number('<H') != table_id or number('<I') != len(raw)-6:
        raise ValueError('STC header mismatch')
    count, width = number('<I'), number('<B')
    types = list(take(width))
    if types != [c['wire_type'] for c in schema['columns']] or count != schema['rows']:
        raise ValueError('STC schema mismatch')
    blocks = [(number('<i'), number('<I')) for _ in range(math.ceil(count/10))]
    rows = []
    for index in range(count):
        offset, values = position, []
        for wire in types:
            if wire == 11:
                marker = number('<B')
                if marker not in (0, 1):
                    raise ValueError('Unknown STC text encoding')
                value = take(number('<H')).decode('ascii' if marker else 'utf-8')
            else:
                value = number({1: '<b', 5: '<i', 8: '<q', 9: '<f'}[wire])
            values.append(value)
        if index % 10 == 0 and blocks[index//10] != (values[0], offset):
            raise ValueError('STC block index mismatch')
        rows.append({'source_offset': offset, 'values': dict(zip((c['name'] for c in schema['columns']), values))})
    named = [json.loads(line) for line in named_path.read_text('utf-8').splitlines() if line.strip()]
    if position != len(raw) or rows != named:
        raise ValueError('STC named rows do not match source bytes')
    return rows, {source_path.name: raw, schema_path.name: schema_path.read_bytes(), named_path.name: named_path.read_bytes()}


def category(record):
    if record['原始名称'].lower().endswith('_alpha') or '原始透明通道' in record['素材路径']:
        return 'alpha_auxiliary'
    if '/poster/' in record['源资源路径'].lower():
        return 'poster'
    name = record['原始名称'].lower()
    for token, kind in [('walldeco', 'wall_decoration'), ('wallpaper', 'wall'), ('wall', 'wall'),
                        ('floor', 'floor'), ('carpet', 'carpet'), ('chair', 'seat'), ('sofa', 'seat'),
                        ('bed', 'bed'), ('table', 'table'), ('desk', 'table'), ('window', 'window'),
                        ('light', 'light'), ('lamp', 'light'), ('cabinet', 'cabinet')]:
        if token in name:
            return kind
    return 'decoration'


def prepare(library, offline, destination):
    library, offline, destination = (Path(p).resolve() for p in (library, offline, destination))
    if destination.is_relative_to(library) or destination.is_relative_to(offline):
        raise ValueError('Output must be separate from source directories')
    index = library / '00_索引与说明'
    names_raw = (index/'source_tables.json').read_bytes()
    source_index = (index/'宿舍静态素材目录.csv').read_bytes()
    names = json.loads(names_raw)
    rows = list(csv.DictReader(io.StringIO(source_index.decode('utf-8-sig'))))
    furniture, evidence_a = verified_table(offline, 5071)
    classes, evidence_b = verified_table(offline, 5172)
    source_evidence = {**evidence_a, **evidence_b, '宿舍静态素材目录.csv': source_index,
                       'localized-names.json': canonical({k: names[k] for k in ('furniture', 'furniture_classes')})}
    assets, files, by_name, groups, source_hashes = {}, {}, defaultdict(list), defaultdict(list), {}
    for i, row in enumerate(rows):
        source = (library/row['素材路径']).resolve()
        if not source.is_relative_to(library):
            raise ValueError('Source escaped library')
        data = source.read_bytes()
        h = digest(data)
        if h != row['sha256'] or len(data) != int(row['bytes']):
            raise ValueError('Indexed PNG changed: ' + str(source))
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if image.format != 'PNG' or image.size != (int(row['宽']), int(row['高'])):
                raise ValueError('PNG dimensions or format mismatch: ' + str(source))
            alpha = list(image.getchannel('A').getextrema()) if 'A' in image.getbands() else [255, 255]
            mode = image.mode
        key = row['素材ID']
        if key in assets:
            raise ValueError('Duplicate source asset ID')
        group = Path(row['素材路径']).parts[3].lower()
        kind = category(row)
        assets[key] = {'asset_id': key, 'name': row['原始名称'], 'category': kind,
            'category_basis': 'source_category_and_filename_hint_not_gameplay_type',
            'sha256': h, 'size_bytes': len(data), 'width': int(row['宽']), 'height': int(row['高']),
            'mode': mode, 'alpha_range': alpha, 'source_alpha_note': row['透明度说明'],
            'source_path': row['素材路径'], 'source_bundle': row['源资源包'], 'source_resource_path': row['源资源路径'],
            'source_object_id': row['对象ID'], 'resource_group': group, 'path': f'content/{h}.png',
            'use': 'auxiliary_channel_not_a_display_sprite' if kind == 'alpha_auxiliary' else 'source_image',
            'visual_review': 'not_individually_reviewed', 'processing': 'original_bytes_unchanged'}
        files[h] = {'sha256': h, 'size_bytes': len(data), 'media_type': 'image/png', 'path': f'content/{h}.png'}
        immutable(destination/f'content/{h}.png', data)
        by_name[row['原始名称'].casefold()].append(key)
        groups[group].append(key)
        source_hashes[row['素材路径']] = h
        if i % 500 == 0:
            print(f'Validated/copied {i}/{len(rows)} indexed PNG records', flush=True)

    class_by_name = {row['values']['name']: row for row in classes}
    if len(class_by_name) != len(classes):
        raise ValueError('Duplicate class localization key')
    furniture_by_class = defaultdict(list)
    for row in furniture:
        furniture_by_class[row['values']['classes']].append(row)
    themes = []
    known_keys = {k: v for k, v in names['furniture_classes']['entries'].items() if k.startswith('furniture_classes-1')}
    for source_key, name in known_keys.items():
        cls = class_by_name.get(source_key)
        if cls is None:
            raise ValueError('Localized class missing from verified STC: ' + source_key)
        source_class = cls['values']
        items, display_ids, aux_ids, missing, dynamic = [], set(), set(), [], []
        for row in furniture_by_class[source_class['id']]:
            f = row['values']
            codes, types = f['code'].split(','), f['texture_type'].split(',')
            if len(codes) != len(types):
                raise ValueError('Component count mismatch: ' + str(f['id']))
            components = []
            for component_index, (code, texture_type) in enumerate(zip(codes, types)):
                component = {'code': code, 'texture_type': texture_type, 'component_index': component_index,
                             'asset_ids': [], 'auxiliary_asset_ids': []}
                if texture_type == '1':
                    hits = [key for key in by_name.get(code.casefold(), []) if assets[key]['category'] != 'alpha_auxiliary']
                    if len({assets[key]['sha256'] for key in hits}) > 1:
                        component['status'] = 'ambiguous_source'
                        missing.append({'furniture_id': f['id'], 'code': code, 'reason': 'multiple_image_hashes'})
                    elif hits:
                        component.update(status='static_verified', asset_ids=hits)
                        display_ids.update(hits)
                        # Same-name companions remain auxiliary; this is not a compositing claim.
                        aux = [key for key in by_name.get((code+'_Alpha').casefold(), []) if assets[key]['category'] == 'alpha_auxiliary']
                        component['auxiliary_asset_ids'] = aux
                        component['auxiliary_relation'] = 'same_name_companion_only_no_automatic_merge'
                        aux_ids.update(aux)
                    else:
                        component['status'] = 'missing_static_source'
                        missing.append({'furniture_id': f['id'], 'code': code, 'reason': 'absent_from_local_static_index'})
                else:
                    component['status'] = 'non_static_dependency'
                    dynamic.append({'furniture_id': f['id'], 'code': code, 'texture_type': texture_type})
                components.append(component)
            items.append({'furniture_id': f['id'], 'name': names['furniture']['entries'].get(f['name'], f['name']),
                          'name_source_key': f['name'], 'source_table': 5071, 'source_offset': row['source_offset'],
                          'source_record': f, 'components': components})
        theme = {'schema_version': 'legend_theme_package/1', 'theme_id': f'theme_{source_class["id"]:06d}',
                 'class_id': source_class['id'], 'name': name, 'source_name_key': source_key,
                 'source_class_offset': cls['source_offset'], 'source_class_record': source_class,
                 'mapping_basis': 'STC5172.name -> localization; STC5071.classes -> class_id; code component -> exact case-insensitive indexed image name',
                 'mapping_status': 'source_config_verified', 'items': items,
                 'asset_ids': sorted(display_ids), 'auxiliary_asset_ids': sorted(aux_ids),
                 'missing_static': missing, 'non_static_dependencies': dynamic,
                 'static_status': 'complete_for_source_config' if not missing else 'partial_missing_source',
                 'layout_status': 'not_authored', 'human_visual_acceptance': False,
                 'resource_groups': sorted({assets[key]['resource_group'] for key in display_ids}),
                 'counts': {'furniture_records': len(items), 'display_images': len(display_ids), 'auxiliary_images': len(aux_ids),
                            'missing_static_components': len(missing), 'non_static_components': len(dynamic)}}
        theme['package_sha256'] = digest(canonical(theme))
        themes.append(theme)
    extra_classes = [row for row in classes if row['values']['name'] not in known_keys]
    summary = {'named_themes': len(themes), 'source_class_rows': len(classes), 'unlocalized_class_rows': len(extra_classes),
        'indexed_images': len(assets), 'unique_image_files': len(files), 'unique_image_bytes': sum(f['size_bytes'] for f in files.values()),
        'complete_static_themes': sum(t['static_status'] == 'complete_for_source_config' for t in themes),
        'partial_static_themes': sum(bool(t['missing_static']) for t in themes),
        'missing_static_components': sum(len(t['missing_static']) for t in themes),
        'non_static_components': sum(len(t['non_static_dependencies']) for t in themes),
        'linked_display_images': len(set().union(*(set(t['asset_ids']) for t in themes))),
        'resource_groups': len(groups)}
    catalog = {'schema_version': 'legend_theme_library/1', 'summary': summary,
        'source_tables_sha256': digest(names_raw), 'source_index_sha256': digest(source_index),
        'generator_sha256': digest(Path(__file__).read_bytes()),
        'evidence_files': {name: digest(data) for name, data in source_evidence.items()},
        'assets': assets, 'files': files, 'groups': dict(sorted(groups.items())),
        'themes': [{k: v for k, v in t.items() if k not in ('items',)} for t in themes],
        'unlocalized_source_classes': extra_classes,
        'notes': ['静态素材包；无新场景布局或互动校准。', '原图与 Alpha 辅助通道分开，未猜测合成关系。',
                  'static complete 仅指当前冻结配置中类型 1 的组件齐全，不含类型 2 动态依赖、布局或人工验收。',
                  '名称索引有 121 项；冻结配置另有未本地化的分类 117，单独保留缺口。',
                  '官方咖啡店为 class 10 / kft2016；现有 coffeeshop 演绎样例来自附属房间资源，不重写历史样例。',
                  'halloween2018 静态资源可由配置关联到 class 60 幽灵古堡；原演绎样例仍保留描述名。']}
    catalog['catalog_sha256'] = digest(canonical(catalog))
    release = destination/'catalogs'/catalog['catalog_sha256']
    for t in themes:
        folder = release/'themes'/t['theme_id']
        write_json(folder/'manifest.json', t)
        immutable(folder/'index.html', render_theme(t, assets).encode('utf-8'))
    for name, data in source_evidence.items():
        immutable(release/'evidence'/name, data)
    write_json(release/'catalog.json', catalog)
    write_json(release/'source-hashes.json', source_hashes)
    immutable(release/'index.html', render_index(catalog).encode('utf-8'))
    # Verify all original source PNGs again before publishing the new pointer.
    changed = [path for path, h in source_hashes.items() if digest((library/path).read_bytes()) != h]
    if changed or digest((index/'source_tables.json').read_bytes()) != digest(names_raw):
        raise ValueError('Sources changed while building: ' + str(changed[:3]))
    write_json(release/'verification.json', {'source_png_records_rehashed': len(source_hashes), 'changed_sources': changed,
        'source_name_table_unchanged': True, 'stc_tables_independently_decoded': [5071, 5172],
        'named_rows_equal_raw': True, 'all_images_fully_decoded': True, 'summary': summary})
    destination.mkdir(parents=True, exist_ok=True)
    (destination/'current.json').write_bytes(canonical({'catalog_sha256': catalog['catalog_sha256']}))
    (destination/'index.html').write_text(f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=catalogs/{catalog["catalog_sha256"]}/index.html"><a href="catalogs/{catalog["catalog_sha256"]}/index.html">打开主题素材库</a>', encoding='utf-8')
    return catalog


STYLE = '''body{margin:0;background:#f1f0eb;color:#24332e;font:15px/1.65 system-ui,"Microsoft YaHei",sans-serif}main{max-width:1440px;margin:auto;padding:32px}h1{font-size:34px;margin:10px 0}h2{margin-top:32px}a{color:#235e4a}header{border-bottom:1px solid #c9d0c7;padding-bottom:22px;margin-bottom:22px}.muted,small{color:#65726a}.stats{display:flex;gap:12px;flex-wrap:wrap}.stats span,.note{padding:12px 18px;background:#fff;border:1px solid #d3dad1;border-radius:10px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px}.card{background:#fff;border:1px solid #d4dcd3;border-radius:12px;padding:14px;overflow:hidden}.card img{display:block;width:100%;height:160px;object-fit:contain;background-color:#dfe5df;background-image:linear-gradient(45deg,#eef1eb 25%,transparent 25%),linear-gradient(-45deg,#eef1eb 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#eef1eb 75%),linear-gradient(-45deg,transparent 75%,#eef1eb 75%);background-size:20px 20px;background-position:0 0,0 10px,10px -10px,-10px 0}.card h3{margin:10px 0 4px}.card p{margin:4px 0}.card code{font-size:11px;overflow-wrap:anywhere}.toolbar{display:flex;gap:12px;flex-wrap:wrap;position:sticky;top:0;background:#f1f0ebed;z-index:1;padding:14px 0}input,select{font:inherit;padding:10px;border:1px solid #b8c7bb;border-radius:8px}input{min-width:280px;flex:1}table{border-collapse:collapse;width:100%;background:#fff}td,th{text-align:left;padding:9px;border-bottom:1px solid #dbe0da}details{margin-top:18px}summary{cursor:pointer}pre{overflow:auto;background:#e5e9e2;padding:16px}code{overflow-wrap:anywhere}.warn{color:#986721}.good{color:#236548}@media(max-width:600px){main{padding:18px}h1{font-size:26px}input{min-width:180px}.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.card img{height:130px}}'''


def document(title, body, script=''):
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+escape(title)+'</title><style>'+STYLE+'</style><main>'+body+'</main><script>'+script+'</script></html>'


def image_card(asset, path_prefix, label=None):
    h = escape
    return f'<article class="card asset" data-category="{h(asset["category"])}" data-search="{h((asset["name"]+" "+(label or "")).lower())}"><a href="{path_prefix}{asset["path"]}" target="_blank"><img loading="lazy" src="{path_prefix}{asset["path"]}" alt="{h(asset["name"])}"></a><h3>{h(label or asset["name"])}</h3><code>{h(asset["name"])}</code><p>{asset["width"]} × {asset["height"]} · {h(asset["category"])}</p><small>Alpha {asset["alpha_range"]} · 原始图片</small><p><a href="{path_prefix}{asset["path"]}" download="{h(asset["name"])}.png">下载 PNG</a></p></article>'


FILTER = '''function filter(){let count=0;const q=document.querySelector('#search').value.toLowerCase(),kind=document.querySelector('#kind')?.value||'';document.querySelectorAll('.filterable').forEach(e=>{e.hidden=!(e.dataset.search.includes(q)&&(!kind||e.dataset.status===kind));if(!e.hidden)count++});document.querySelector('#shown').textContent=count}document.querySelector('#search').addEventListener('input',filter);document.querySelector('#kind')?.addEventListener('change',filter);filter();'''


def render_index(catalog):
    s = catalog['summary']
    body = f'<header><p class="muted">我的传说 · 本地可调用资源 · 版本 {catalog["catalog_sha256"][:12]}</p><h1>121 项主题静态素材库</h1><p>按冻结游戏配置关联主题、家具与原始图片。选择主题查看全部分件、下载 PNG 或读取 manifest.json。布局与家具互动后续制作。</p><div class="stats"><span>{s["named_themes"]} 项名称已关联</span><span>{s["complete_static_themes"]} 项静态组件齐全</span><span>{s["partial_static_themes"]} 项存在源图缺口</span><span>{s["linked_display_images"]} 张已关联展示图片</span></div></header>'
    body += '<p class="note">“静态组件齐全”按当前配置中的类型 1 统计；类型 2 动态部件、布局和人工画面验收单列。Alpha 辅助通道不作为家具图片混用。官方咖啡店对应 kft2016，当前演绎样例使用的 coffeeshop 归于附属房间设施。</p>'
    body += '<div class="toolbar"><input id="search" aria-label="搜索主题" placeholder="搜索名称、主题 ID 或资源组"><select id="kind" aria-label="资源状态"><option value="">全部状态</option><option value="complete_for_source_config">静态组件齐全</option><option value="partial_missing_source">存在源图缺口</option></select><span>显示 <b id="shown"></b> 项</span></div><div class="grid">'
    for t in catalog['themes']:
        # Prefer a furniture cutout over a wallpaper or poster as the theme cover.
        covers = [catalog['assets'][key] for key in t['asset_ids']]
        covers.sort(key=lambda a: (a['category'] not in ('seat', 'table', 'bed'), a['name']))
        cover = f'<img loading="lazy" src="../../{covers[0]["path"]}" alt="{escape(t["name"])}素材示例">' if covers else '<p>暂无静态图片</p>'
        search = escape((t['name']+' '+t['theme_id']+' '+' '.join(t['resource_groups'])).lower())
        status = '静态组件齐全' if not t['missing_static'] else f'缺 {len(t["missing_static"])} 个静态组件'
        body += f'<article class="card filterable" data-search="{search}" data-status="{t["static_status"]}"><a href="themes/{t["theme_id"]}/index.html">{cover}<h3>{escape(t["name"])}</h3></a><code>{t["theme_id"]}</code><p>{t["counts"]["display_images"]} 张展示图片 · {t["counts"]["auxiliary_images"]} 张辅助通道</p><p class="{ "warn" if t["missing_static"] else "good" }">{status}</p><small>另有 {len(t["non_static_dependencies"])} 个动态组件引用</small><p><a href="themes/{t["theme_id"]}/manifest.json">JSON 清单</a></p></article>'
    body += '</div><h2>来源与调用</h2><p><a href="catalog.json">完整版本目录 JSON</a> · <a href="verification.json">验证记录</a> · <a href="source-hashes.json">源图哈希</a></p><p>所有图片路径相对于素材库根目录。目录版本和每个主题包均有 SHA-256，可固定版本重复调用；直接使用展示图片列表 asset_ids，辅助通道列表 auxiliary_asset_ids 保留给后续明确验证的合成流程。</p><p class="muted">另有未本地化的配置分类 117，未冒充第 122 套已命名主题；原始记录保留在 catalog.json。完整资源组与尚未关联图片也保留在 catalog.json 的 groups / assets。</p>'
    return document('121 项主题静态素材库', body, FILTER)


def render_theme(t, assets):
    labels = defaultdict(list)
    for item in t['items']:
        for component in item['components']:
            for key in component['asset_ids']:
                if item['name'] not in labels[key]:
                    labels[key].append(item['name'])
    body = f'<header><a href="../../index.html">← 全部主题</a><h1>{escape(t["name"])}</h1><code>{t["theme_id"]} · class {t["class_id"]}</code><p>配置中 {len(t["items"])} 件家具 · {len(t["asset_ids"])} 张展示图片 · {len(t["auxiliary_asset_ids"])} 张辅助通道</p><p><a href="manifest.json">读取完整 manifest.json</a> · <a href="../../catalog.json">资源与来源索引</a></p></header>'
    body += '<p class="note">这里展示独立原图；不是完整房间布置。拆分部件保留配置顺序与原始 offset / sorting / rotate 字段，不直接当作 960×540 舞台坐标。动态依赖没有用图集冒充静态家具。</p><div class="toolbar"><input id="search" aria-label="搜索素材" placeholder="搜索家具名称或资源代号"><span>显示 <b id="shown"></b> 张</span></div><div class="grid">'
    for key in t['asset_ids']:
        card = image_card(assets[key], '../../../../', ' / '.join(labels[key]))
        body += card.replace('class="card asset"', 'class="card asset filterable"')
    body += '</div><h2>缺口与动态依赖</h2>'
    if t['missing_static']:
        body += '<p class="warn">以下静态组件在当前本地图片索引中缺失，未生成替代图片：</p><ul>' + ''.join(f'<li>{escape(x["code"])} · 家具 {x["furniture_id"]}</li>' for x in t['missing_static'])+'</ul>'
    else:
        body += '<p class="good">本主题当前配置中的静态组件均已定位、解码并验证哈希。</p>'
    body += '<details><summary>动态组件引用（'+str(len(t['non_static_dependencies']))+'）</summary><ul>'+''.join(f'<li>{escape(x["code"])} · 家具 {x["furniture_id"]} · texture_type={x["texture_type"]}</li>' for x in t['non_static_dependencies'])+'</ul></details>'
    body += f'<details><summary>辅助透明通道（{len(t["auxiliary_asset_ids"])} 张；未自动合成）</summary><div class="grid">'
    body += ''.join(image_card(assets[key], '../../../../') for key in t['auxiliary_asset_ids'])+'</div></details>'
    return document(t['name']+' · 静态素材包', body, FILTER)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--offline', type=Path, required=True)
    parser.add_argument('--destination', type=Path, default=ROOT/'runtime/my-legend-spine/theme-library')
    args = parser.parse_args()
    result = prepare(args.library, args.offline, args.destination)
    print(json.dumps({'catalog_sha256': result['catalog_sha256'], **result['summary']}, ensure_ascii=False))
