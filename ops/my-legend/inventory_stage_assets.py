"""Read-only source audit and versioned local capability report. Never edits source assets."""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import html
import json
from pathlib import Path
from my_legend_production.production.asset_capabilities import build_capabilities, save_capabilities
from my_legend_production.production.h5_preview import load_h5_catalog
from my_legend_production.production.scene_preview import load_scene_catalog
from my_legend_production.production.commander_preview import commander_scene_digest
from my_legend_production.production.life_contracts import canonical_hash


def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source',type=Path,required=True);ap.add_argument('--runtime',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();root=args.source.resolve();out=args.output
    if out.exists():raise ValueError('Use a new output directory; historical reports are immutable.')
    out.mkdir(parents=True)
    indexes=root/'00_索引与说明'
    raw=read(indexes/'organized_catalog.json');pictures=read(indexes/'scene_catalog.json');copies=read(indexes/'copy_manifest.json')
    files={}
    for x in copies:files[x['destination']]=dict(expected=x['sha256'],source=x['source'])
    for x in pictures:files[x['素材路径']]=dict(expected=x['sha256'],source=x['源资源路径'])
    # Include commander extension files and any unindexed asset files, without
    # treating index JSON or archives as executable visual assets.
    for path in root.rglob('*'):
        if path.is_file() and path.suffix.lower() in ('.skel','.atlas','.png','.jpg','.jpeg','.webp'):
            relative=str(path.relative_to(root));files.setdefault(relative,dict(expected=None,source='unindexed_local_file'))
    def check(item):
        relative,meta=item;path=(root/relative).resolve()
        if not path.is_relative_to(root):return dict(path=relative,status='outside_source_root',**meta)
        try:
            with path.open('rb') as stream:digest=sha256(stream.read()).hexdigest()
            return dict(path=relative,sha256=digest,size=path.stat().st_size,status='verified' if not meta['expected'] or digest==meta['expected'] else 'hash_mismatch',**meta)
        except OSError:return dict(path=relative,status='missing',**meta)
    with ThreadPoolExecutor(max_workers=6) as pool:verified=list(pool.map(check,sorted(files.items())))
    bypath={v['path']:v for v in verified};groups=defaultdict(list)
    for f in verified:
        if f.get('sha256'):groups[f['sha256']].append(f['path'])
    catalog=load_h5_catalog(args.runtime);rooms=load_scene_catalog(args.runtime,commander_scene_digest(args.runtime,catalog))
    caps=build_capabilities(catalog,rooms);save_capabilities(args.runtime,caps)
    known_raw={s.get('source_asset_id') for s in catalog['skeletons']}
    used_images={o['image']['sha256'] for r in rooms['scenes'] for o in r['objects']}
    runtime_hashes=set(catalog.get('files',{}))|used_images
    for f in verified:f['capability_status']='unavailable' if f['status']!='verified' else 'ready_component' if f.get('sha256') in runtime_hashes else 'needs_adaptation'
    copy_by_id=defaultdict(list)
    for x in copies:copy_by_id[x['asset_id']].append(x['destination'])
    entries=[]
    for x in raw:
        paths=copy_by_id[x['id']];bad=any(bypath[p]['status']!='verified' for p in paths)
        status='unavailable' if bad or x.get('issues') or x.get('parse_status')!='parsed' else 'needs_adaptation'
        entries.append(dict(id=x['id'],name=x['display_name'],category=x['entity'],status=status,
            reason=x.get('issues') or ('部分组件已用于固定指挥官；原始组合不能直接作为已接入演员' if x['id'] in known_raw else '原始骨骼已有解析记录，尚未绑定当前角色与播放器能力'),
            source_paths=paths,animations=x['animations'],spine_version=x['spine_version'],semantic_basis='source_index_not_visual_acceptance'))
    for x in pictures:
        f=bypath[x['素材路径']];status='unavailable' if f['status']!='verified' else 'ready' if x['sha256'] in used_images else 'needs_adaptation'
        entries.append(dict(id=x['素材ID'],name=x['中文说明']+' / '+x['原始名称'],category=x['类别'],status=status,
            reason='已绑定场景中的静态图像，不具备物件操作能力' if status=='ready' else x.get('使用限制','尚未接入当前场景'),
            source_paths=[x['素材路径']],sha256=x['sha256'],semantic_basis=x['命名依据']))
    audit=dict(schema_version='stage_asset_inventory/1',source_root=str(root),
        index_hashes={n:sha256((indexes/n).read_bytes()).hexdigest() for n in ('organized_catalog.json','scene_catalog.json','action_index.json','copy_manifest.json')},
        raw_entries=entries,files=verified,deduplicated_files=[dict(sha256=k,paths=v) for k,v in sorted(groups.items())],
        summary=dict(raw_skeletons=len(raw),indexed_animations=sum(len(x['animations']) for x in raw),raw_images=len(pictures),
            audited_files=len(verified),unique_file_hashes=len(groups),file_states=dict(Counter(f['status'] for f in verified)),
            entry_states=dict(Counter(x['status'] for x in entries)),categories=dict(Counter(x['category'] for x in entries)),
            ready_actors=len(caps['actors']),ready_outfits=sum(len(a['outfits']) for a in caps['actors']),ready_scenes=len(caps['scenes']),
            calibrated_seats=sum(len(s['seats']) for s in caps['scenes'])),
        capability_sha256=caps['capability_sha256'],visual_acceptance='See separate runtime preview evidence; raw source entries are not accepted for playback.')
    audit['inventory_sha256']=canonical_hash(audit)
    (out/'inventory.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'capabilities.json').write_text(json.dumps(caps,ensure_ascii=False,indent=2),encoding='utf-8')
    names={'ready':'当前可用','needs_adaptation':'需要适配','unavailable':'暂不可用'}
    rows=''.join('<tr><td>'+html.escape(s['name'])+'</td><td>'+html.escape(s['description'])+'</td><td>'+str(len(s['objects']))+'</td><td>'+str(len(s['seats']))+'</td></tr>' for s in caps['scenes'])
    actor_rows=''.join('<tr><td>'+html.escape(a['name'])+'</td><td>'+str(len(a['outfits']))+'</td><td>'+html.escape('、'.join(next(iter(a['outfits']))['actions']))+'</td><td>'+html.escape('、'.join(a['eyes'].values()))+'</td></tr>' for a in caps['actors'])
    listing=[dict(id=e['id'],name=e['name'],category=e['category'],status=names[e['status']],reason=e['reason']) for e in entries]
    data=json.dumps(listing,ensure_ascii=False).replace('<','\\u003c')
    document='''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>素材能力清单</title><style>body{font:16px/1.6 system-ui;margin:28px auto;padding:0 20px;max-width:1160px;color:#26332d;background:#f6f5ef}h1,h2{color:#254c3c}table{border-collapse:collapse;width:100%;background:white;margin:16px 0}td,th{padding:10px;border:1px solid #d7dfd6;text-align:left;overflow-wrap:anywhere}input,select{font:inherit;padding:8px;max-width:100%}.note{background:#e2ebdf;padding:16px;border-radius:8px}a{color:#256746}@media(max-width:500px){body{font-size:13px;padding:0 8px}td,th{padding:5px}}</style><h1>现有素材能力清单</h1><p class="note">本清单区分原始资源盘点和当前可执行能力。原始动画名称及描述来自解析索引，不等于已完成视觉验收；导演只使用下面已接入的固定角色和场景。静态家具不等于可操作道具。</p>'''
    document+='<p>原始骨骼 '+str(len(raw))+' 组 · 索引动画 '+str(audit['summary']['indexed_animations'])+' 条 · 原始图像 '+str(len(pictures))+' 条 · 当前角色 '+str(len(caps['actors']))+' 名、服装 '+str(audit['summary']['ready_outfits'])+' 套、场景 '+str(len(caps['scenes']))+' 套。</p>'
    document+='<p><a href="inventory.json">完整盘点与逐文件哈希</a> · <a href="capabilities.json">冻结能力目录</a></p><h2>当前角色能力</h2><table><tr><th>角色</th><th>服装</th><th>动作</th><th>表情</th></tr>'+actor_rows+'</table><p>坐姿仅限兼容服装与已校准座位；对白口型自动驱动。动作时长、循环方式、素材版本、空间与兼容信息见能力目录。</p><h2>当前场景</h2><table><tr><th>场景</th><th>布局说明</th><th>物件</th><th>座位</th></tr>'+rows+'</table>'
    document+='''<h2>全量原始素材检索</h2><input id="q" aria-label="搜索素材" placeholder="名称、类型或说明"><select id="status" aria-label="素材状态"><option value="">全部状态</option><option>当前可用</option><option>需要适配</option><option>暂不可用</option></select><p id="count"></p><table><thead><tr><th>名称</th><th>类型</th><th>状态</th><th>依据与限制</th></tr></thead><tbody id="rows"></tbody></table><script>const data='''+data+''';const q=document.querySelector('#q'),status=document.querySelector('#status');function render(){const found=data.filter(x=>(!status.value||x.status===status.value)&&(!q.value||JSON.stringify(x).toLowerCase().includes(q.value.toLowerCase())));document.querySelector('#count').textContent=`匹配 ${found.length} 项，显示前 100 项；完整记录见 JSON。`;const target=document.querySelector('#rows');target.replaceChildren();for(const x of found.slice(0,100)){const tr=document.createElement('tr');for(const key of ['name','category','status','reason']){const td=document.createElement('td');td.textContent=x[key];tr.appendChild(td)}target.appendChild(tr)}}q.oninput=render;status.onchange=render;render();</script></html>'''
    (out/'素材能力清单.html').write_text(document,encoding='utf-8')
    print(json.dumps(audit['summary'],ensure_ascii=False))


if __name__=='__main__':main()
