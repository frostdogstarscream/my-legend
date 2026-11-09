"""Local integration demonstration; reuses sound, never invokes generation."""
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'output/gfl-guofeng-audit-20261102'
BASE = 'http://127.0.0.1:8895/api/v1'
SOUND = '913f2c82-9e28-4ede-bc8f-59c86dffe591'

def main():
    token = None
    def call(path, body=None):
        req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type':'application/json', **({'Authorization':'Bearer '+token} if token else {})})
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return json.load(response)['data']
        except urllib.error.HTTPError as exc:
            raise RuntimeError(exc.read().decode()) from None
    token = call('/auth/local', {})['accessToken']
    endpoint = '/production-jobs/'+SOUND+'/stage-preview'
    context = call(endpoint, dict(profile='life_v2', operation='prepare'))
    rooms = json.loads((ROOT/'runtime/my-legend-spine/scene-catalogs'/(context['scene_catalog_sha256']+'.json')).read_text(encoding='utf-8'))
    room = next(r for r in rooms['scenes'] if r['scene_id']=='spring_festival')
    source_roles = [r['role_id'] for r in context['roles'][:2]]
    skins = ['405','406']
    scene_bindings = [dict(episode_id=s['episode_id'],scene_id=s['scene_id'],
        scene=dict(catalog_sha256=rooms['catalog_sha256'],scene_id=room['scene_id']),confirmed=True,
        note='国风人形生活动作技术演示，复用已有声音；不代表原故事动作。',
        actors=[dict(instance_id='actor_'+str(i+1),position=[.35+i*.3,room['blocking']['foot_y']],facing=1 if i==0 else -1,visible=True) for i in range(2)]) for s in context['scenes']]
    initial = call(endpoint, dict(profile='life_v2', operation='compile',catalog_sha256=context['catalog_sha256'],
        bindings={r:'gfl-guofeng-'+s for r,s in zip(source_roles,skins)},
        initial_outfits={r:'gfl-guofeng-'+s+'-native' for r,s in zip(source_roles,skins)},scene_bindings=scene_bindings,dialogue_enabled=False))
    script=initial['stage'];script['cues']=[]
    sr=script['sample_rate']
    for i,action in enumerate(['idle','walk','sit','pick','lie']):
        for actor in script['actors']:
            script['cues'].append(dict(id=f'demo-{actor["instance_id"]}-{action}',type='body.play',instance_id=actor['instance_id'],
                start_frame=i*5*sr,end_frame=(i*5+4)*sr,enabled=True,params=dict(action=action,mode='loop'),
                source=None,source_text='',rule_id='manual',reason='生活动作技术演示，不代表原文语义'))
    saved=call(endpoint,dict(profile='life_v2',operation='validate_and_save',script=script,scene_bindings=scene_bindings,
        expression_track=[],mouth_enabled={a['instance_id']:False for a in script['actors']},
        furniture_track=dict(schema_version='commander_furniture/1',cues=[]),dialogue_enabled=False,review_confirmed=False))
    reread=call(endpoint+'/'+saved['preview_sha256'])
    assert reread['stage']==saved['stage']
    assert len(saved['stage']['cues'])==10
    assert saved['audio']['artifact']['sha256']==context['audio']['artifact']['sha256']
    url=f'http://127.0.0.1:5193/#/pages/performance/index?productionId={SOUND}&previewId={saved["preview_sha256"]}&profile=life_v2&view=edit'
    report=dict(preview_sha256=saved['preview_sha256'],url=url,catalog_sha256=saved['catalog_sha256'],
        actions_per_actor=5,actors=2,cues=10,audio_reused=True,saved_roundtrip=True,formal_delivery=False)
    (OUT/'live-demo.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
