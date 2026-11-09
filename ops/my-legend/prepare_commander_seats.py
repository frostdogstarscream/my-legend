"""Versioned commander seat calibration on the existing standing layouts."""
from pathlib import Path
from copy import deepcopy
import argparse,json
from prepare_spine_preview import atomic_json
from prepare_life_preview import digest
ROOT=Path(__file__).resolve().parents[2]
# Seat contact is authored as a fraction of the existing furniture rectangle.
SEATS={'loft':('sofa',.0,.30,1),'cafe':('chair',.0,.37,-1),'halloween':('seat',.0,.35,-1),'cafe_classic':('chair',.0,.38,1),'library':('sofa',.0,.32,-1),'classroom':('chair',.0,.35,1),'nanyang':('sofa',.0,.32,-1),'seaside':('chair',.0,.35,-1),'izakaya':('stool',.0,.70,-1),'wedding':('chairs_left',.0,.35,1),'command':('sofa',.0,.31,-1),'snow_cabin':('sofa',.0,.32,-1)}
def prepare(root,measurement,activate=False):
    root=Path(root);m=json.loads(Path(measurement).read_text('utf-8-sig'));a=json.loads((root/'catalogs'/(m['catalog_sha256']+'.json')).read_text('utf-8'))
    if a.get('actor_model')!='fixed_commander/1':raise ValueError('Fixed commander catalog required')
    p=json.loads((root/'scene-current.json').read_text('utf-8'));c=deepcopy(json.loads((root/'scene-catalogs'/(p['catalog_sha256']+'.json')).read_text('utf-8')))
    c['source_scene_catalog_sha256']=c.pop('catalog_sha256');c['commander_catalog_sha256']=m['catalog_sha256'];c['commander_calibrations']=m['groups'];c['commander_seating_version']='commander_seating/1'
    for scene in c['scenes']:
        scene['commander_seats']=[]
        if scene['scene_id'] not in SEATS:continue
        oid,dx,rise,facing=SEATS[scene['scene_id']];o=next(o for o in scene['objects'] if o['id']==oid)
        seat=dict(seat_id='commander_'+oid,object_id=oid,label=next((p['label'].removesuffix('前') for p in scene['blocking']['targets'] if p['object_id']==oid),'座椅'),
            position=[(o['position'][0]+dx*o['width'])/960,(o['position'][1]-rise*o['height'])/540],approach=[o['position'][0]/960,scene['blocking']['foot_y']],exit=[o['position'][0]/960,scene['blocking']['foot_y']],facing=facing,
            calibrated_groups=sorted(m['groups']))
        seat['exit'][0]=max(scene['blocking']['walk_lane'][0],min(scene['blocking']['walk_lane'][2],seat['approach'][0]+(.12 if seat['approach'][0]<.5 else -.12)))
        seat['calibration_revision']=digest(dict(seat=seat,groups=m['groups'],scene_object=o));scene['commander_seats']=[seat]
    c['catalog_sha256']=digest(c);atomic_json(root/'scene-catalogs'/(c['catalog_sha256']+'.json'),c)
    if activate:
        pointer_path=root/'commander-scene-current.json';mapping={}
        if pointer_path.exists():
            previous=json.loads(pointer_path.read_text('utf-8'));mapping.update(previous.get('by_character_catalog',{}))
            previous_catalog=json.loads((root/'scene-catalogs'/(previous['catalog_sha256']+'.json')).read_text('utf-8'))
            mapping[previous_catalog['commander_catalog_sha256']]=previous['catalog_sha256']
        mapping[c['commander_catalog_sha256']]=c['catalog_sha256']
        atomic_json(pointer_path,dict(catalog_sha256=c['catalog_sha256'],by_character_catalog=mapping))
    return dict(catalog_sha256=c['catalog_sha256'],seats=sum(len(s['commander_seats']) for s in c['scenes']),groups=len(m['groups']),activated=activate)
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=ROOT/'runtime/my-legend-spine');p.add_argument('--measurements',type=Path,required=True);p.add_argument('--activate',action='store_true');a=p.parse_args();print(json.dumps(prepare(a.root,a.measurements,a.activate)))
