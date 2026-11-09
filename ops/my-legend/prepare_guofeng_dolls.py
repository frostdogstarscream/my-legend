"""Add approved native dorm skeletons to an immutable mixed actor catalog."""
from pathlib import Path
from copy import deepcopy
from hashlib import sha256
import argparse, csv, json, struct, sys
from prepare_life_preview import atlas_metadata, ACTION_MAP, digest
from prepare_spine_preview import atomic_json
from spine21 import decode, animation_duration

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'source/my-legend-production/src'))
from my_legend_production.production.life_contracts import validate_catalog

def prepare(game, root, selection, activate=False, portraits=ROOT/'output/gfl-guofeng-presentation-20261102'):
    old=json.loads((root/'life-v2-current.json').read_text())['catalog_sha256']
    catalog=deepcopy(json.loads((root/'catalogs'/f'{old}.json').read_text('utf-8')))
    if catalog.get('actor_model')=='mixed_life/1':
        old=catalog['source_mixed_catalog_sha256']
        catalog=json.loads((root/'catalogs'/f'{old}.json').read_text('utf-8'))
    catalog.pop('catalog_sha256');catalog.update(actor_model='mixed_life/1',source_mixed_catalog_sha256=old)
    index=json.loads((game/'少女前线_分类素材库_v1/00_索引与说明/organized_catalog.json').read_text('utf-8-sig'))
    selected=list(csv.DictReader(selection.open(encoding='utf-8-sig')))
    def put(data,mime):
        h=sha256(data).hexdigest();p=root/'content'/h
        if not p.exists():p.write_bytes(data)
        elif sha256(p.read_bytes()).hexdigest()!=h:raise ValueError('Existing content corrupt')
        ref=dict(sha256=h,size_bytes=len(data),media_type=mime);catalog['files'][h]=ref;return ref
    report=[]
    for item in selected:
        sid=int(item['皮肤编号'])
        matches=[x for x in index if x['entity']=='战术人形' and x.get('skin_id')==sid and {'lying','pick','sit'} <= {a['name'] for a in x['animations']}]
        if len(matches)!=1:raise ValueError(f'Ambiguous native dorm skeleton: {sid}')
        row=matches[0];folder=game/'Spine_Export_20261022'/row['directory']
        if row['issues'] or row['parse_status']!='parsed':raise ValueError(f'Source QA failed: {sid}')
        raw=(folder/row['skeleton']).read_bytes();skeleton=decode(raw)
        names=[s.strip() for s in row['atlases'].split(';') if s.strip()]
        if len(names)!=1:raise ValueError('Ambiguous atlas')
        atlas=(folder/names[0]).read_text('utf-8-sig');sizes,regions=atlas_metadata(atlas);pages={}
        for name,size in sizes.items():
            data=(folder/name).read_bytes()
            if data[:8]!=b'\x89PNG\r\n\x1a\n' or struct.unpack('>II',data[16:24])!=size:raise ValueError('Atlas size mismatch')
            pages[name]=put(data,'image/png')
        for skin in skeleton['skins'].values():
            for attachments in skin.values():
                for name,attachment in attachments.items():
                    if attachment['type']!='boundingbox' and attachment.get('path',attachment.get('name',name)) not in regions:raise ValueError('Missing attachment')
        asset_id=f'gfl-dorm-{sid}';actor_id=f'gfl-guofeng-{sid}';oid=actor_id+'-native'
        animations={k:dict(duration=animation_duration(v)) for k,v in skeleton['animations'].items()}
        # Keep every original dorm animation. Known names have stable semantic aliases.
        mapping={k:v for k,v in ACTION_MAP.items() if v in animations}
        for name in animations:
            if name not in mapping.values():mapping['native_'+name]=name
        asset=dict(asset_id=asset_id,source_asset_id=row['id'],source_filename=row['skeleton'],original=put(raw,'application/octet-stream'),
            skeleton=put(json.dumps(skeleton,ensure_ascii=False).encode(),'application/json'),atlas=put(atlas.encode(),'text/plain'),pages=pages,
            format=skeleton['skeleton']['spine'],animations=animations,alpha_mode='premultiplied-alpha',qa='pass')
        # Public portraits come only from the native dorm idle pose, never from
        # the battle-preview cache. Original identity stays in source metadata.
        thumb=portraits/(str(sid)+'.png')
        if not thumb.is_file():raise ValueError(f'Native life portrait missing: {sid}')
        image=put(thumb.read_bytes(),'image/png');name=item['皮肤']
        actor=dict(schema_version='legend_actor/0.3',actor_id=actor_id,display_name=name,asset_mode='single_skeleton',asset_id=asset_id,
            outfit_ids=[oid],default_outfit_id=oid,action_map=mapping,lifecycle='ready',asset_qa='pass',mouth_control=False,
            thumbnail=image,categories=['guofeng','国风'],tags=['国风','原生生活动作'],source_skin_id=sid,
            source_display_name=item['人形']+' · '+item['皮肤'],thumbnail_pose=dict(animation=mapping['idle'],time=0,skeleton_sha256=asset['skeleton']['sha256']))
        cal={'full':dict(x=0,y=0,scale=1,rotation=0,flip=False,layer=0,actions={})}
        outfit=dict(schema_version='legend_outfit/0.3',outfit_id=oid,actor_id=actor_id,display_name=name,parts={'full':asset_id},
            allowed_actions=list(mapping),calibration=cal,calibration_revision=digest(cal),qa='pass',style_category='国风',
            thumbnail=image,categories=['guofeng'],face_presets=['native'],source_asset_id=row['id'],source_skin_id=sid)
        outfit['revision']=digest(outfit);actor['revision']=digest(dict(actor=actor,asset=asset,outfit=outfit))
        catalog['actors'].append(actor);catalog['outfits'].append(outfit);catalog['skeletons'].append(asset)
        report.append(dict(skin_id=sid,name=name,source_skeleton=row['skeleton'],actor_id=actor_id,actions=mapping))
    catalog['catalog_sha256']=digest(catalog);validate_catalog(catalog)
    atomic_json(root/'catalogs'/f"{catalog['catalog_sha256']}.json",catalog)
    pointer=json.loads((root/'commander-scene-current.json').read_text())
    oldscene=pointer.get('by_character_catalog',{}).get(old,pointer['catalog_sha256'])
    rooms=json.loads((root/'scene-catalogs'/f'{oldscene}.json').read_text('utf-8'))
    rooms.pop('catalog_sha256');rooms['commander_catalog_sha256']=catalog['catalog_sha256'];rooms['catalog_sha256']=digest(rooms)
    atomic_json(root/'scene-catalogs'/f"{rooms['catalog_sha256']}.json",rooms)
    if activate:
        mapping=pointer.get('by_character_catalog',{});mapping[old]=oldscene;mapping[catalog['catalog_sha256']]=rooms['catalog_sha256']
        atomic_json(root/'commander-scene-current.json',dict(catalog_sha256=rooms['catalog_sha256'],by_character_catalog=mapping))
        atomic_json(root/'life-v2-current.json',dict(catalog_sha256=catalog['catalog_sha256']))
    return dict(catalog_sha256=catalog['catalog_sha256'],scene_catalog_sha256=rooms['catalog_sha256'],actors=report,activated=activate)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--game',type=Path,default=Path("D:/game/steamapps/common/GIRLS' FRONTLINE"));p.add_argument('--root',type=Path,default=ROOT/'runtime/my-legend-spine');p.add_argument('--selection',type=Path,default=ROOT/'output/gfl-guofeng-audit-20261102/国风已看图筛选.csv');p.add_argument('--activate',action='store_true');p.add_argument('--portraits',type=Path,default=ROOT/'output/gfl-guofeng-presentation-20261102');p.add_argument('--report',type=Path,default=ROOT/'output/gfl-guofeng-presentation-20261102/import-report.json');a=p.parse_args()
    report=prepare(a.game,a.root,a.selection,a.activate,a.portraits);atomic_json(a.report,report);print(json.dumps({k:v for k,v in report.items() if k!='actors'},ensure_ascii=False))
