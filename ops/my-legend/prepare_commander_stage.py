"""Build immutable fixed-commander wardrobe and face resources without changing v1."""
from pathlib import Path
from hashlib import sha256
from copy import deepcopy
import argparse,json,re,sys
from prepare_spine_preview import atomic_json
from prepare_life_preview import digest
ROOT=Path(__file__).resolve().parents[2]

def prepare(root,activate=False):
    root=Path(root);current=json.loads((root/'life-v2-current.json').read_text('utf-8'))['catalog_sha256']
    source=json.loads((root/'catalogs'/(current+'.json')).read_text('utf-8'))
    if source.get('actor_model')=='fixed_commander/1':source=json.loads((root/'catalogs'/(source['source_character_catalog_sha256']+'.json')).read_text('utf-8'))
    library=json.loads((ROOT/'assets/character-library/v1/catalog.json').read_text('utf-8'))
    files=deepcopy(source['files']);assets={a['asset_id']:deepcopy(a) for a in source['skeletons']}
    def put(data,mime):
        h=sha256(data).hexdigest();p=root/'content'/h
        if p.exists() and p.read_bytes()!=data:raise ValueError('Corrupt immutable resource '+h)
        if not p.exists():p.write_bytes(data)
        ref=dict(sha256=h,size_bytes=len(data),media_type=mime);files[h]=ref;return ref
    def raw(ref):
        b=(root/'content'/ref['sha256']).read_bytes()
        if sha256(b).hexdigest()!=ref['sha256']:raise ValueError('Corrupt source '+ref['sha256'])
        return b
    male=next(o for o in source['outfits'] if o['outfit_id']=='gufeng-58-baseline-male--established--wuxia')
    female=next(o for o in source['outfits'] if o['outfit_id']=='gufeng-58-baseline-female--established--native')
    mh=assets[male['parts']['head']];fh=deepcopy(assets[female['parts']['head']]);female_raw=json.loads(raw(fh['skeleton']));male_raw=json.loads(raw(mh['skeleton']))
    # Reuse the original commander mouth artwork, with a separate female attachment.
    original=female_raw['skins']['default']['mouth1']['mouth1']
    atlas=raw(mh['atlas']).decode('utf-8').lstrip();lines=atlas.splitlines();regions=[]
    for state,source_name in [('open','mouth4'),('closed','mouth1')]:
        name='commander_mouth_'+state
        attachment=deepcopy(male_raw['skins']['default']['mouth1'][source_name])
        attachment.update(name=name,path=name,x=original['x'],y=original['y'],rotation=original['rotation'])
        female_raw['skins']['default']['mouth1'][name]=attachment
        start=lines.index(source_name);end=start+1
        while end<len(lines) and (lines[end].startswith(' ') or not lines[end].strip()):end+=1
        regions.append(name+'\n'+'\n'.join(lines[start+1:end]).rstrip())
    page_name=next(iter(mh['pages']));header=atlas[:atlas.index('\n',atlas.index('repeat:'))]
    if header.splitlines()[0]!=page_name:raise ValueError('Unexpected mouth atlas header')
    new_atlas=raw(fh['atlas']).decode('utf-8').rstrip()+'\n\n'+header+'\n'+'\n'.join(regions)+'\n'
    fh.update(asset_id=fh['asset_id']+'-mouth-v1',skeleton=put(json.dumps(female_raw,ensure_ascii=False).encode(),'application/json'),atlas=put(new_atlas.encode(),'text/plain'),pages={**fh['pages'],page_name:mh['pages'][page_name]},derivation='commander female open-mouth attachment from original male mouth4; original bones and closed mouth preserved')
    assets[fh['asset_id']]=fh
    # Reuse the visually checked hollow-eye regions from the earlier commander
    # adaptation. Keep the original head and every historical catalog immutable.
    candidates_root=ROOT/'outputs/01a15d0e-b113-7f02-8171-e76fc720aab4/xianxia-appearance-pilot-20261026'
    candidates=json.loads((candidates_root/'candidates.json').read_text('utf-8'))
    heads=[]
    for gender,original_head in [('male',mh),('female',fh)]:
        head=deepcopy(original_head);skeleton=json.loads(raw(head['skeleton']));extra=[]
        for slot,key,native in [('Leye1','c03','Leye1' if gender=='male' else 'eyeL1'),('Reye1','c08','Reye1' if gender=='male' else 'eyeR1')]:
            row=next(r for r in candidates if r['key']==key);png=(candidates_root/row['image']).read_bytes()
            if sha256(png).hexdigest()!=row['sha256']:raise ValueError('Hollow eye source hash mismatch')
            width,height=row['size'];name='commander_surprised_'+slot;page=name+'.png'
            anchor=deepcopy(skeleton['skins']['default'][slot][native])
            anchor.update(name=name,path=name,width=width/height*anchor['height'])
            skeleton['skins']['default'][slot][name]=anchor
            head['pages'][page]=put(png,'image/png')
            extra.append(f'{page}\nsize: {width},{height}\nformat: RGBA8888\nfilter: Linear,Linear\nrepeat: none\n{name}\n  rotate: false\n  xy: 0, 0\n  size: {width}, {height}\n  orig: {width}, {height}\n  offset: 0, 0\n  index: -1\n')
        head.update(asset_id=head['asset_id']+'-surprised-v1',skeleton=put(json.dumps(skeleton,ensure_ascii=False).encode(),'application/json'),atlas=put((raw(head['atlas']).decode().rstrip()+'\n\n'+'\n'.join(extra)).encode(),'text/plain'),derivation='Fixed commander head with independently bound hollow eyes from verified c03/c08 adaptation; original attachments preserved')
        assets[head['asset_id']]=head;heads.append(head)
    mh,fh=heads
    actors=[];outfits=[]
    for gender,base in [('male',male),('female',female)]:
        actor_id='commander_'+gender;head=mh if gender=='male' else fh
        face=dict(schema_version='commander_face/1',head_asset_id=head['asset_id'],head_sha256=head['skeleton']['sha256'],
            eyes={'open':{'Leye1':'Leye1' if gender=='male' else 'eyeL1','Reye1':'Reye1' if gender=='male' else 'eyeR1'},'closed':{'Leye1':'Leye5','Reye1':'Reye5'},'smile':{'Leye1':'Leye2','Reye1':'Reye2'},'squint':{'Leye1':'Leye4','Reye1':'Reye4'}},
            mouth=dict(slot='mouth1',closed='mouth1' if gender=='male' else 'commander_mouth_closed',open='mouth4' if gender=='male' else 'commander_mouth_open',period_ms=280,open_start_ms=75,open_end_ms=215,lead_ms=70,tail_ms=75,actions=['idle','walk','sit']))
        face['eyes']['surprised']={'Leye1':'commander_surprised_Leye1','Reye1':'commander_surprised_Reye1'}
        face['profile_sha256']=digest(face)
        for c in library['characters']:
            if c['gender']!=gender:continue
            old=next(o for o in source['outfits'] if o['outfit_id']==c['id']+'--established--'+('wuxia' if gender=='male' else 'native'))
            outfit=deepcopy(old);outfit.update(outfit_id=c['id'],actor_id=actor_id,display_name=c['name'],parts={**old['parts'],'head':head['asset_id']},head_preset='fixed',hair='wuxia' if gender=='male' else 'native',face_presets=['native','open','closed','smile','squint','talk'],source_character_id=c['id'],source_outfit_id=old['outfit_id'],calibration_group=gender+':'+c['family'],categories=c['categories'],tags=c['tags'],thumbnail=next(a['thumbnail'] for a in source['actors'] if a['actor_id']==c['id']))
            outfit['face_presets'].append('surprised')
            outfit.pop('revision',None);outfit['revision']=digest(outfit);outfits.append(outfit)
        actor=dict(schema_version='legend_actor/0.3',actor_id=actor_id,display_name='男指挥官' if gender=='male' else '女指挥官',asset_mode='commander_tripartite',asset_id=None,
            outfit_ids=[o['outfit_id'] for o in outfits if o['actor_id']==actor_id],default_outfit_id='gufeng-58-bamboo-'+gender,normalization_outfit_id='gufeng-58-baseline-'+gender,
            action_map={'idle':'wait','walk':'move','sit':'sit','victory':'victory','victoryloop':'victoryloop'},lifecycle='ready',asset_qa='pass',mouth_control=True,gender=gender,
            face_profile=face,thumbnail=next(a['thumbnail'] for a in source['actors'] if a['actor_id']=='gufeng-58-bamboo-'+gender))
        actor['revision']=digest(actor);actors.append(actor)
    used={p for o in outfits for p in o['parts'].values()}
    catalog=dict(schema_version='spine_life_catalog/2.0',profile='life_v2',actor_model='fixed_commander/1',runtime=source['runtime'],actors=actors,outfits=outfits,skeletons=[assets[k] for k in sorted(used)],files=files,source_character_catalog_sha256=source['catalog_sha256'],library_sha256=source['library_sha256'])
    catalog['catalog_sha256']=digest(catalog)
    sys.path.insert(0,str(ROOT/'source/my-legend-production/src'))
    from my_legend_production.production.life_contracts import validate_catalog
    validate_catalog(catalog);atomic_json(root/'catalogs'/(catalog['catalog_sha256']+'.json'),catalog)
    if activate:atomic_json(root/'life-v2-current.json',dict(catalog_sha256=catalog['catalog_sha256']))
    return dict(catalog_sha256=catalog['catalog_sha256'],actors=len(actors),outfits=len(outfits),activated=activate)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=ROOT/'runtime/my-legend-spine');p.add_argument('--activate',action='store_true');a=p.parse_args();print(json.dumps(prepare(a.root,a.activate),ensure_ascii=False))

