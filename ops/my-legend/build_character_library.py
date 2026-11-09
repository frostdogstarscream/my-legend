"""Build a portable character wardrobe catalog from verified local sources."""
from pathlib import Path
from hashlib import sha256
import json
import shutil
import sys

from spine21 import decode, animation_duration

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'assets/character-library/v1'
HISTORY = ROOT / 'outputs/01a15d0e-b113-7f02-8171-e76fc720aab4'
WARDROBE = HISTORY / 'commander-gufeng-wardrobe-v2-20261027'
INVENTORY = HISTORY / 'commander-costume-inventory-20261027/inventory.json'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def build():
    if (OUT / 'catalog.json').exists():
        raise ValueError('Library already exists; preserve v1 and use a new version directory for a rebuild.')
    OUT.mkdir(parents=True, exist_ok=True)
    inventory, prior = read(INVENTORY), read(WARDROBE / 'manifest.json')
    source = Path(inventory['summary']['source'])
    rows = {r['目录']: r for r in inventory['skeletonParts']}
    components, files, characters = {}, {}, []
    old_snapshot = [{'path': str(p.relative_to(WARDROBE)).replace('\\', '/'),
                     'sha256': sha256(p.read_bytes()).hexdigest()}
                    for p in WARDROBE.rglob('*') if p.is_file() and p.suffix != '.log']

    def copy(path, relative):
        target = OUT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        data = path.read_bytes()
        digest = sha256(data).hexdigest()
        if target.exists() and target.read_bytes() != data:
            raise ValueError('Refusing to replace an existing library asset: ' + relative)
        if not target.exists():
            target.write_bytes(data)
        files[relative] = {'sha256': digest, 'bytes': len(data), 'source': str(path)}

    # Preserve the approved wardrobe and its exact renderer dependencies.
    for folder in ['assets', 'materials', 'design', 'vendor']:
        for p in (WARDROBE / folder).rglob('*'):
            if p.is_file():
                copy(p, p.relative_to(WARDROBE).as_posix())
    for name in ['core.mjs', 'hair.mjs', 'pose.mjs']:
        copy(WARDROBE / name, name)

    def register(part):
        cid = 'part-' + part['sourceId']
        if cid not in components:
            components[cid] = dict(part, id=cid, alphaMode='premultiplied-alpha')
        return cid

    established = {a['gender']: register(a['parts'][0]) for a in prior['actors'] if a['family'] == '58'}
    theme_names = {'baseline': '侠客原案', 'bamboo': '青竹江湖', 'night': '玄衣夜行',
                   'moon': '月白清霜', 'snow': '朔雪冬行', **prior['wardrobe']['newThemeNames']}
    theme_tags = {'baseline': ['侠客', '云纹'], 'bamboo': ['竹青', '竹叶'], 'night': ['夜行', '墨蓝'],
                  'moon': ['月白', '云纹'], 'snow': ['冬装', '梅枝'], '19-plain': ['青碧', '素锦'],
                  '19-brocade': ['绛红', '云纹'], '55-silver': ['雾蓝', '银饰'], '55-gold': ['碧玉', '金饰']}
    for a in prior['actors']:
        family, gender = a['family'], a['gender']
        variants = ['baseline', 'bamboo', 'night', 'moon', 'snow'] if family == '58' else \
            (['19-plain', '19-brocade'] if family == '19' else ['55-silver', '55-gold'])
        for variant in variants:
            parts = {p['kind']: register(p) for p in a['parts']}
            body = a['parts'][1]
            material = ('materials/' + gender + '/' + ('pattern' if variant == 'baseline' else variant)) \
                if family == '58' else f'materials/class{family}/{gender}/{variant}'
            cid = f'gufeng-{family}-{variant}-{gender}'
            categories = ['guofeng'] + (['formal'] if family == '55' else [])
            characters.append({'id': cid, 'name': theme_names[variant] + ' · ' + ('男款' if gender == 'male' else '女款'),
                'gender': gender, 'family': family, 'categories': categories,
                'tags': ['国风', '古风', *theme_tags[variant]],
                'description': '已确认的国风衣柜配色；保持既有头部、服装结构与原生动作。',
                'parts': parts, 'headPresets': {'established': parts['head']}, 'defaultHead': 'established',
                'defaultHair': 'wuxia' if gender == 'male' else 'native',
                'textures': {parts['body']: {page: material + '/' + page for page in body['pages']}},
                'sourceType': 'approved-recolor', 'sourceName': '古风衣柜第二辑',
                'acceptance': {'userAesthetic': True, 'evidence': 'User acknowledged completion on 2026-10-27'},
                'thumbnail': 'thumbnails/' + cid + '.png'})

    # Names and style tags describe the actual silhouette, not merely the gender pair.
    styles = {
        ('40', 'male'): ('modern-suit', '现代商务西装', ['business'], ['现代西装', '商务正装', '职场', '西裤'], '原生西装与长裤，保留衬衫和领口。'),
        ('40', 'female'): ('evening-dress', '华丽礼裙', ['formal'], ['华丽礼服', '舞台装', '晚宴', '连衣裙'], '原生礼裙；不归类为女式西装。'),
        ('45', 'female'): ('office-skirt', '都市职业裙装', ['business'], ['商务正装', '都市职业装', '职场', '衬衫', '裙装'], '原生衬衫与职业裙装；不宣称为裤装西服。'),
        ('34', 'male'): ('detective-coat', '都市侦探风衣', ['detective'], ['风衣', '侦探', '都市复古', '英伦'], '原生风衣、衬衫与配套裤装。'),
        ('34', 'female'): ('detective-coat', '都市侦探风衣', ['detective'], ['风衣', '侦探', '都市复古', '英伦'], '原生女款侦探衣装，保留对应裙裤与鞋袜。'),
        ('16', 'male'): ('campus-jacket', '校园街头夹克', ['casual'], ['校园', '棒球夹克', '街头休闲', '牛仔裤'], '原生夹克、印花与裤装；未重新绘制服装。'),
        ('16', 'female'): ('campus-jacket', '校园街头制服', ['casual'], ['校园', '街头休闲', '制服', '短裙'], '原生校园风上衣、短裙与手套；与男款棒球夹克分别分类。'),
        ('55', 'male'): ('ceremonial-uniform', '黑金仪仗礼服', ['formal'], ['华丽礼服', '仪仗制服', '舞台装', '黑金'], '原生双排扣、装饰肩袖与长裤，可用于仪仗或舞台角色。'),
        ('55', 'female'): ('qipao-gala', '华灯旗袍礼服', ['formal', 'guofeng'], ['华丽礼服', '舞台装', '旗袍', '白色披肩'], '原生长旗袍礼服与披肩，区别于男款仪仗制服。'),
        ('57', 'male'): ('culinary-apron', '餐饮围裙职业装', ['culinary'], ['餐饮职业装', '咖啡师', '烘焙师', '厨师', '围裙'], '餐饮职业装原生底版；咖啡师、烘焙师是用途标签，共用此套服装。'),
        ('57', 'female'): ('culinary-apron', '女仆咖啡馆职业装', ['culinary'], ['餐饮职业装', '咖啡师', '女仆咖啡馆', '花边围裙'], '原生女仆咖啡馆风格，含花边围裙和头饰；可作主题餐饮角色，非通用厨师或烘焙制服。'),
        ('49', 'male'): ('sports-training', '运动训练休闲装', ['sports'], ['运动', '健身', '篮球休闲', '训练服', '无袖'], '原生无袖运动上衣、短裤与运动鞋。'),
        ('49', 'female'): ('sports-training', '运动训练休闲装', ['sports'], ['运动', '健身', '篮球休闲', '训练服'], '原生女款运动服与运动鞋。'),
    }
    for combo in inventory['combinations']:
        family = combo['套装编号']
        gender = {'男款': 'male', '女款': 'female'}.get(combo['款式'])
        if (family, gender) not in styles:
            continue
        if combo['部件齐全'] != 'True':
            raise ValueError('Incomplete costume ' + family)
        parts = {}
        for key, kind in [('头部', 'head'), ('上身', 'body'), ('下身', 'foot')]:
            row = rows[combo[key]]
            folder = source / row['目录']
            skel = folder / row['骨骼文件']
            raw = decode(skel.read_bytes())
            atlases = list(folder.glob('*.atlas'))
            if len(atlases) != 1:
                raise ValueError('Ambiguous atlas ' + str(folder))
            pages = [line.strip() for line in atlases[0].read_text(encoding='utf-8-sig').splitlines()
                     if line.strip().lower().endswith('.png') and not line.startswith(' ')]
            directory = f'assets/native/class{family}/{gender}/{kind}'
            for path in [skel, atlases[0], *(folder / page for page in pages)]:
                copy(path, directory + '/' + path.name)
            save(OUT / directory / 'skeleton.json', raw)
            part = {'kind': kind, 'sourceId': row['素材ID'], 'sourceFilename': skel.name,
                    'directory': directory, 'atlas': atlases[0].name, 'skeleton': 'skeleton.json', 'pages': pages,
                    'animations': {name: animation_duration(anim) for name, anim in raw['animations'].items()},
                    'boneCount': len(raw['bones']), 'slotCount': len(raw['slots']),
                    'sourcePrefab': row['来源prefab'], 'sourcePartName': row['部件名称']}
            parts[kind] = register(part)
        slug, name, categories, tags, description = styles[family, gender]
        cid = f'{slug}-{family}-{gender}'
        characters.append({'id': cid, 'name': name + ' · ' + ('男款' if gender == 'male' else '女款'),
            'gender': gender, 'family': family, 'categories': categories, 'tags': tags, 'description': description,
            'parts': parts, 'headPresets': {'native': parts['head'], 'established': established[gender]},
            'defaultHead': 'native', 'defaultHair': 'native', 'textures': {},
            'sourceType': 'native-costume', 'sourceName': combo['套装名称'],
            'acceptance': {'userAesthetic': False}, 'thumbnail': 'thumbnails/' + cid + '.png'})

    actions = {'idle': 'wait', 'walk': 'move', 'sit': 'sit', 'victory': 'victory', 'victoryloop': 'victoryloop'}
    for c in characters:
        shared = set.intersection(*(set(components[p]['animations']) for p in c['parts'].values()))
        c['actions'] = {name: {'native': native, 'duration': components[c['parts']['body']]['animations'][native]}
                        for name, native in actions.items() if native in shared}
        c['validation'] = {'assets': 'verified', 'motion': 'pending', 'browser': 'pending'}
        c['revision'] = sha256(json.dumps(c, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    categories = [{'id': key, 'name': name} for key, name in [
        ('guofeng', '国风与侠客'), ('business', '西装与商务'), ('detective', '风衣与都市复古'),
        ('casual', '校园与街头'), ('formal', '礼服与舞台'), ('culinary', '餐饮职业'), ('sports', '运动与健身')]]
    catalog = {'schema': 'character-library/1', 'version': '1.0.0', 'created': '2026-10-27',
        'renderer': {'format': 'Spine 2.1.27', 'module': 'renderer.mjs', 'sdk': 'sdk.mjs'},
        'categories': categories, 'characters': characters, 'components': components,
        'rightsBasis': 'User-reported authorization for local asset reuse',
        'scope': 'Portable local character library. Production timeline integration is not implied.'}
    save(OUT / 'catalog.json', catalog)
    save(OUT / 'data/source-files.json', files)
    save(OUT / 'data/wardrobe-preservation.json', {'source': str(WARDROBE), 'files': old_snapshot})
    save(OUT / 'data/source-inventory.json', {'source': str(INVENTORY), 'sha256': sha256(INVENTORY.read_bytes()).hexdigest(),
        'selectedClasses': sorted({c['family'] for c in characters}, key=int)})
    print(json.dumps({'characters': len(characters), 'components': len(components), 'copiedFiles': len(files),
                      'categories': {cat['id']: sum(cat['id'] in c['categories'] for c in characters) for cat in categories}}, ensure_ascii=False))


if __name__ == '__main__':
    build()
