"""Build an offline review gallery from verified H5 screenshots, without publishing assets."""
import argparse
from html import escape
import json
from pathlib import Path

from prepare_ready_scenes import ROOT, verified_json


def export(root, evidence):
    result = json.loads((evidence / 'review.json').read_text(encoding='utf-8'))
    digest = result['hash']
    catalog = verified_json(root / 'scene-catalogs' / (digest + '.json'), digest, 'catalog_sha256')
    by_id = {s['id']: s for s in result['scenes']}
    if result['errors'] or set(by_id) != {s['scene_id'] for s in catalog['scenes']}:
        raise ValueError('Incomplete browser review')
    cards = []
    for index, scene in enumerate(catalog['scenes']):
        sid = scene['scene_id']
        for kind in ('scene', 'guide'):
            if not (evidence / f'{kind}-{sid}.png').read_bytes().startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('Missing browser screenshot')
        url = f"http://127.0.0.1:5193/#/pages/performance/index?mode=spine&profile=life_v1&productionId={result['job']}&previewId={by_id[sid]['preview']}&sceneCatalog={digest}"
        targets = ' · '.join(t['label'] for t in scene['blocking']['targets'])
        cards.append(f'''<article><div class="visual"><img class="scene" src="scene-{sid}.png" alt="{escape(scene['display_name'])}实际演绎"><img class="guide" src="guide-{sid}.png" alt="{escape(scene['display_name'])}走位参考"></div><div class="copy"><span class="number">{index+1:02}</span><h2>{escape(scene['display_name'])}</h2><p>{escape(scene['description'])}</p><p class="targets">{escape(targets)}</p><a href="{escape(url,quote=True)}" target="_blank" rel="noopener">打开真实作品预览 ↗</a></div></article>''')
    html = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>剧情场景布局 · v0.9</title><style>
*{box-sizing:border-box}body{margin:0;background:#f3efe7;color:#35453e;font:15px/1.7 system-ui,'Microsoft YaHei',sans-serif}header,main,footer{max-width:1500px;margin:auto;padding:28px}header{padding-top:44px}.kicker{font-size:12px;letter-spacing:.16em;color:#7c7767}h1{font-size:34px;margin:8px 0}header p{max-width:900px;color:#6d786e}.controls{display:flex;gap:10px;margin-top:22px}button{padding:9px 22px;border:1px solid #9cae9e;border-radius:30px;background:transparent;color:#354e42;font:inherit;cursor:pointer}button[aria-pressed=true]{background:#354e42;color:white}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:22px}article{background:#fffdf7;border:1px solid #d8dfd1;border-radius:14px;overflow:hidden}.visual{aspect-ratio:16/9;background:#293d35}.visual img{width:100%;height:100%;object-fit:contain}.guide{display:none}.guides .scene{display:none}.guides .guide{display:block}.copy{padding:18px}.number{float:right;color:#a3ad99;font-size:22px}h2{margin:0 0 7px;font-size:20px}.copy p{font-size:13px;color:#697264;min-height:65px}.copy .targets{min-height:0;border-top:1px solid #e3e7dc;padding-top:12px;font-size:12px;color:#8b7549}a{color:#357259;font-size:13px}footer{font-size:12px;color:#727b6e}code{overflow-wrap:anywhere}@media(max-width:1050px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:650px){header,main,footer{padding:20px}.grid{grid-template-columns:1fr}h1{font-size:27px}}
</style><header><div class="kicker">MY LEGEND / SCENE BLOCKING v0.9</div><h1>布置合理，也让故事有地方发生</h1><p>15 套原素材场景，按家具组合、尺度、视觉主次与人物通道重新布置。以下为同一真实作品的 H5 截图；切换走位参考可查看对白站位、进出通道和物件前停靠点。</p><div class="controls"><button data-guide="false" aria-pressed="true">演绎画面</button><button data-guide="true" aria-pressed="false">走位参考</button></div></header><main class="grid">'''+''.join(cards)+'''</main><footer>场景选用为人工布局示例，并非这些地点均来自原剧本。停靠点用于站立与靠近；自动分场选景、自动物件交互和家具坐姿尚未接通。原图片、旧场景及历史作品保留。<br>本机 H5 链接需要预览服务及登录会话；本页截图可离线浏览。版本：<code>'''+digest+'''</code></footer><script>document.querySelectorAll('[data-guide]').forEach(b=>b.addEventListener('click',()=>{document.body.classList.toggle('guides',b.dataset.guide==='true');document.querySelectorAll('[data-guide]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)))}))</script></html>'''
    (evidence / 'index.html').write_text(html, encoding='utf-8')
    return evidence / 'index.html'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT / 'runtime/my-legend-spine')
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    print(export(args.root, args.evidence))
