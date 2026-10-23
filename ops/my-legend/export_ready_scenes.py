"""Verify the fixed scene release and export an offline layered gallery and ZIP."""
from hashlib import sha256
from html import escape
from pathlib import Path
import argparse
import json
import zipfile

from prepare_ready_scenes import verified_json

ROOT = Path(__file__).resolve().parents[2]


def unchanged_copy(source, target):
    data = source.read_bytes()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_bytes() != data:
        raise ValueError('Refusing to overwrite a different export: ' + str(target))
    if not target.exists():
        target.write_bytes(data)


def export(root, evidence, output):
    current = json.loads((root / 'scene-current.json').read_text())['catalog_sha256']
    catalog_path = root / 'scene-catalogs' / (current + '.json')
    catalog = verified_json(catalog_path, current, 'catalog_sha256')
    base = verified_json(root / 'scene-catalogs' / (catalog['base_catalog_sha256'] + '.json'), catalog['base_catalog_sha256'], 'catalog_sha256')
    destination = output / ('my-legend-ready-scenes-v0.8-' + current[:12])
    destination.mkdir(parents=True, exist_ok=True)
    for scene in base['scenes']:
        replacement = next(s for s in catalog['scenes'] if s['scene_id'] == scene['scene_id'])
        assert all(replacement[k] == v for k, v in scene.items() if k != 'display_name'), 'Old scene geometry changed'
    for h, ref in catalog['files'].items():
        source = root / 'content' / h
        assert sha256(source.read_bytes()).hexdigest() == h and source.stat().st_size == ref['size_bytes']
        unchanged_copy(source, destination / 'content' / (h + '.png'))
    unchanged_copy(catalog_path, destination / 'scene-catalog.json')
    cards = []
    for i, scene in enumerate(catalog['scenes']):
        layers = []
        for j, o in enumerate(scene['objects']):
            x, y = o['position'][0]-o['width']*o['anchor'][0], o['position'][1]-o['height']*o['anchor'][1]
            size = f"{o['source_size'][0]*o['tile_scale']/o['width']*100}% {o['source_size'][1]*o['tile_scale']/o['height']*100}%" if o['repeat'] else '100% 100%'
            z = o['depth_y']*1000+j if o['layer']=='depth' else {'wall':-3000000, 'floor':-2000000, 'back':-1000000, 'front':2000000}[o['layer']]+j
            tint = f"background-color:{o['tint']};background-blend-mode:multiply;" if o['tint'] != '#ffffff' else ''
            style = f"left:{x/9.6}%;top:{y/5.4}%;width:{o['width']/9.6}%;height:{o['height']/5.4}%;z-index:{round(z)};background-image:url(content/{o['image']['sha256']}.png);background-size:{size};background-repeat:{'repeat' if o['repeat'] else 'no-repeat'};{tint}"
            layers.append('<div class="sprite" style="'+style+'"></div>')
        for kind in ('scene', 'focus'):
            unchanged_copy(evidence / f"{kind}-{scene['scene_id']}.png", destination / 'previews' / f"{kind}-{scene['scene_id']}.png")
        sample_url = f"http://127.0.0.1:5193/#/pages/performance/index?mode=spine&profile=life_v1&scene={scene['scene_id']}&sceneCatalog={current}"
        cards.append(f'''<article id="{scene['scene_id']}"><div class="visual"><div class="room">{''.join(layers)}</div><img class="actor" loading="lazy" src="previews/scene-{scene['scene_id']}.png" alt="{escape(scene['display_name'])}演员参考"><img class="focus" loading="lazy" src="previews/focus-{scene['scene_id']}.png" alt="{escape(scene['display_name'])}聚焦参考"></div><div class="copy"><span class="number">{i+1:02d}</span><h2>{escape(scene['display_name'])}</h2><p>{escape(scene.get('description',''))}</p><div class="foot"><small>{'含已校准坐姿样例' if scene['seats'] else '固定布景 · 站立与行走'}</small><a href="{sample_url}" target="_blank" rel="noopener">打开 H5 样例 ↗</a></div></div></article>''')
    html = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>我的传说 · 15 套固定场景</title><style>
*{box-sizing:border-box}body{margin:0;background:#f4f0e9;color:#2d3c36;font:15px/1.7 system-ui,'Microsoft YaHei',sans-serif}header,main,footer{max-width:1500px;margin:auto;padding:32px}header{padding-top:46px;padding-bottom:18px}.eyebrow{letter-spacing:.18em;font-size:12px;color:#7b6651}h1{font-size:36px;letter-spacing:.03em;margin:8px 0}header p{color:#6b726c;max-width:900px}.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin:24px 0 4px}button{background:transparent;color:#385047;border:1px solid #c5c9bb;padding:9px 22px;border-radius:24px;font:inherit;cursor:pointer}button[aria-pressed=true]{background:#274d40;color:#fff;border-color:#274d40}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:22px}article{border:1px solid #ddd9cd;background:#fffdfa;border-radius:14px;overflow:hidden;box-shadow:0 6px 20px #29362a09}.visual{position:relative;aspect-ratio:16/9;background:#243736}.room{position:absolute;inset:0;isolation:isolate;overflow:hidden}.sprite{position:absolute}.visual img{display:none;width:100%;height:100%;object-fit:cover}.mode-actor .room,.mode-focus .room{display:none}.mode-actor .actor,.mode-focus .focus{display:block}.copy{padding:17px 19px}.number{float:right;color:#9c9f8f;font-size:22px;font-weight:300}h2{font-size:19px;margin:0 0 6px}.copy p{font-size:13px;min-height:45px;color:#6e776f;margin:0 0 12px}.foot{border-top:1px solid #e6e5da;padding-top:11px;display:flex;justify-content:space-between;gap:10px;align-items:center}small{font-size:11px;color:#7d806b}a{color:#416c57;text-decoration:none;font-size:12px}a:hover{text-decoration:underline}footer{font-size:12px;color:#788074;padding-top:8px}code{overflow-wrap:anywhere}@media(max-width:1050px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:650px){header,main,footer{padding:20px}.grid{grid-template-columns:1fr}h1{font-size:28px}}
</style><header><div class="eyebrow">MY LEGEND / SCENE COLLECTION 01</div><h1>15 套固定场景，打开即可使用</h1><p>从居室、咖啡店到博物馆与节日街景。每套均以原始素材分层布置，中央留出表演空间。选择纯布景查看环境，也可切换演员与镜头参考。</p><div class="toolbar" aria-label="预览模式"><button data-mode="room" aria-pressed="true">纯布景</button><button data-mode="actor" aria-pressed="false">演员参考</button><button data-mode="focus" aria-pressed="false">聚焦参考</button></div></header><main class="grid">'''+''.join(cards)+'''</main><footer>纯布景可离线打开；H5 演员样例需本机预览服务运行。布局为本项目编排，原始素材未重绘。新增场景的家具坐姿尚未校准。<br>版本：<code>'''+current+'''</code> · <a href="scene-catalog.json">布局与资源清单</a></footer><script>document.querySelectorAll('[data-mode]').forEach(b=>b.addEventListener('click',()=>{document.body.className='mode-'+b.dataset.mode;document.querySelectorAll('[data-mode]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)))}))</script></html>'''
    (destination / 'index.html').write_text(html, encoding='utf-8')
    report = {'catalog_sha256': current, 'scene_count': len(catalog['scenes']), 'objects': sum(len(s['objects']) for s in catalog['scenes']),
              'original_pngs_sha256_verified': len(catalog['files']), 'legacy_scene_geometry_preserved': 3,
              'uncalibrated_scenes_without_seats': sum(not s['seats'] for s in catalog['scenes']),
              'human_visual_acceptance': False, 'gallery': str(destination / 'index.html')}
    baseline = ROOT / 'output/playwright/scenes-v06/baseline-hashes.json'
    if baseline.exists():
        old = json.loads(baseline.read_text())
        changed = [p for p, h in old.items() if sha256((ROOT / p).read_bytes()).hexdigest() != h]
        assert not changed, changed
        report['protected_existing_files'] = len(old)
    archive = output / (destination.name + '.zip')
    if not archive.exists():
        with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for path in sorted(destination.rglob('*')):
                if path.is_file():
                    z.write(path, path.relative_to(destination))
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        for path in destination.rglob('*'):
            if path.is_file():
                assert z.read(path.relative_to(destination).as_posix()) == path.read_bytes()
        report['zip_entries'] = len(z.infolist())
    report.update(zip=str(archive), zip_bytes=archive.stat().st_size, zip_sha256=sha256(archive.read_bytes()).hexdigest())
    (evidence / 'delivery-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=ROOT / 'runtime/my-legend-spine')
    p.add_argument('--evidence', type=Path, default=ROOT / 'output/playwright/ready-scenes-v08')
    p.add_argument('--output', type=Path, default=ROOT / 'output')
    a = p.parse_args()
    print(json.dumps(export(a.root, a.evidence, a.output), ensure_ascii=False, indent=2))
