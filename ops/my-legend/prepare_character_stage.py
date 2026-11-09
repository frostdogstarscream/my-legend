"""Import the frozen character library into a separate immutable life_v2 catalog."""
from pathlib import Path
from hashlib import sha256
from copy import deepcopy
import argparse
import json
import sys
from prepare_life_preview import digest
from prepare_spine_preview import atomic_json

ROOT = Path(__file__).resolve().parents[2]

def prepare(library, destination):
    library, destination = Path(library).resolve(), Path(destination).resolve()
    manifest = json.loads((library / 'files.sha256.json').read_text('utf-8'))
    def read(relative):
        path = (library / relative).resolve()
        if not path.is_relative_to(library) or relative not in manifest:
            raise ValueError('Unregistered library resource: ' + relative)
        data = path.read_bytes()
        if sha256(data).hexdigest() != manifest[relative]:
            raise ValueError('Library resource changed: ' + relative)
        return data
    source = json.loads(read('catalog.json'))
    files, skeletons, outfits, actors = {}, {}, [], []
    content = destination / 'content'; content.mkdir(parents=True, exist_ok=True)
    def put(data, mime):
        h = sha256(data).hexdigest(); path = content / h
        if path.exists() and sha256(path.read_bytes()).hexdigest() != h:
            raise ValueError('Existing runtime content corrupted: ' + h)
        if not path.exists(): path.write_bytes(data)
        ref = dict(sha256=h, size_bytes=len(data), media_type=mime); files[h] = ref
        return ref
    runtime = put(read('vendor/spine.js'), 'text/javascript')
    hair = put(read('design/wuxia-hair-v1/front-hair.png'), 'image/png')
    def part(pid, textures):
        p = source['components'][pid]
        pages = {name: put(read(textures.get(name, p['directory']+'/'+name)), 'image/png') for name in p['pages']}
        identity = pid + '-' + digest(pages)[:12]
        if identity not in skeletons:
            skeletons[identity] = dict(asset_id=identity, source_asset_id=p['sourceId'],source_filename=p['sourceFilename'],
                original=put(read(p['directory']+'/'+p['sourceFilename']), 'application/octet-stream'),
                skeleton=put(read(p['directory']+'/'+p['skeleton']), 'application/json'),
                atlas=put(read(p['directory']+'/'+p['atlas']), 'text/plain'),pages=pages,
                format='2.1.27',animations={name:dict(duration=duration) for name,duration in p['animations'].items()},
                alpha_mode=p['alphaMode'],qa='pass')
        return identity
    for c in source['characters']:
        actor_outfits=[]
        for head, pid in c['headPresets'].items():
            hairs=['native','wuxia'] if source['components'][pid]['sourceId']=='337a358d20dc' else ['native']
            for hairstyle in hairs:
                oid=c['id']+'--'+head+'--'+hairstyle
                parts={slot:part(p,c['textures'].get(p,{})) for slot,p in {**c['parts'],'head':pid}.items()}
                calibration={slot:dict(x=0,y=0,scale=1,rotation=0,flip=False,layer=0,actions={}) for slot in parts}
                outfit=dict(schema_version='legend_outfit/0.3',outfit_id=oid,actor_id=c['id'],
                    display_name=('套装原头部' if head=='native' else '既有男女形象')+(' · 侠客束发' if hairstyle=='wuxia' else ' · 原发型'),
                    parts=parts,allowed_actions=list(c['actions']),calibration=calibration,calibration_revision=digest(calibration),
                    head_preset=head,hair=hairstyle,qa='pass')
                if hairstyle=='wuxia':outfit['accessory']=dict(image=hair,bone='head',world_at_wait0=[-61,82,59,182],hidden_slots=['hair','lex1','lex2','lex3'])
                head_part=source['components'][pid]
                head_raw=json.loads(read(head_part['directory']+'/'+head_part['skeleton']))
                attachments=head_raw['skins'].get('default',{})
                choices={'open':{'Leye1':['Leye1','eyeL1'],'Reye1':['Reye1','eyeR1'],'mouth1':['mouth1']},'closed':{'Leye1':['Leye5','eyeL5'],'Reye1':['Reye5','eyeR5'],'mouth1':['mouth1']},'talk':{'Leye1':['Leye1','eyeL1'],'Reye1':['Reye1','eyeR1'],'mouth1':['mouth4']}}
                outfit['face_presets']=['native']+[name for name,slots in choices.items() if all(any(n in attachments.get(slot,{}) for n in names) for slot,names in slots.items())]
                outfit['revision']=digest(outfit);outfits.append(outfit);actor_outfits.append(oid)
        actor=dict(schema_version='legend_actor/0.3',actor_id=c['id'],display_name=c['name'],asset_mode='commander_tripartite',asset_id=None,
            outfit_ids=actor_outfits,default_outfit_id=c['id']+'--'+c['defaultHead']+'--'+c['defaultHair'],
            action_map={name:a['native'] for name,a in c['actions'].items()},lifecycle='ready',asset_qa='pass',mouth_control=False,
            library_revision=c['revision'],gender=c['gender'],categories=c['categories'],tags=c['tags'],
            thumbnail=put(read(c['thumbnail']),'image/png'))
        actor['revision']=digest(dict(actor=actor,outfits=[o for o in outfits if o['actor_id']==c['id']],
            resources=[skeletons[p] for o in outfits if o['actor_id']==c['id'] for p in o['parts'].values()]))
        actors.append(actor)
    catalog=dict(schema_version='spine_life_catalog/2.0',profile='life_v2',runtime=runtime,actors=actors,outfits=outfits,
        skeletons=list(skeletons.values()),files=files,library_version=source['version'],library_sha256=sha256(read('catalog.json')).hexdigest(),
        categories=source['categories'],character_count=len(actors),head_profile_count=sum(len(c['headPresets']) for c in source['characters']))
    catalog['catalog_sha256']=digest(catalog)
    sys.path.insert(0,str(ROOT/'source/my-legend-production/src'))
    from my_legend_production.production.life_contracts import validate_catalog
    validate_catalog(catalog)
    atomic_json(destination/'catalogs'/(catalog['catalog_sha256']+'.json'),catalog)
    atomic_json(destination/'life-v2-current.json',{'catalog_sha256':catalog['catalog_sha256']})
    return {k:catalog[k] for k in ['catalog_sha256','character_count','head_profile_count']} | dict(outfits=len(outfits),files=len(files))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--library',type=Path,default=ROOT/'assets/character-library/v1');p.add_argument('--root',type=Path,default=ROOT/'runtime/my-legend-spine');a=p.parse_args()
    print(json.dumps(prepare(a.library,a.root),ensure_ascii=False,indent=2))
