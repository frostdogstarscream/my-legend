"""Synthetic Chinese source fixtures only; no copyrighted assets or paid providers."""
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from test_life_preview import fixture, compile_value
from my_legend_production.production.auto_face_suggestions import FaceSuggestionRequest, build_face_suggestions
from my_legend_production.production.face_preview import face_envelope
from my_legend_production.production.life_contracts import canonical_hash
from my_legend_production.production.performance_tracks import track_bindings
from my_legend_production.production.spine_preview import save_preview
from my_legend_production.production.repository import ProductionError


CASES = [
    ('甲惊讶地睁大眼睛。', 'surprised'), ('小甲大吃一惊。', 'surprised'),
    ('甲睁大了眼睛。', 'surprised'), ('甲停住脚步，她惊讶地睁大眼睛。', 'surprised'),
    ('甲眯起眼睛，仔细看了看。', 'squint'), ('甲挤眼。', 'squint'),
    ('甲停住脚步，他眯起眼睛。', 'squint'), ('我惊讶地睁大眼睛。', 'surprised'),
    ('乙眯眼。', 'squint'), ('小甲眯起了眼睛。', 'squint'),
    ('她很开心地笑了。', None), ('甲开心地笑了。', None), ('她并不惊讶。', None),
    ('明天一定会让她大吃一惊。', None), ('甲说：“你看起来很惊讶。”', None),
    ('你怎么来了？', None), ('她突然愣住了。', None), ('她惊讶地睁大眼睛。', None),
    ('甲和乙惊讶地睁大眼睛。', None), ('甲看着她惊讶地睁大眼睛。', None),
    ('如果甲惊讶，就停下来。', None), ('甲准备眯起眼睛。', None), ('甲未曾眯眼。', None),
    ('据说乙大吃一惊。', None), ('甲惊讶吗？', None), ('甲眯眼，又惊讶地睁大眼睛。', None),
    ('甲愤怒地皱眉。', None), ('我想要挤眼。', None), ('昨天甲大吃一惊。', None),
    ('甲看起来很惊讶。', None), ('乙没有睁大眼睛。', None), ('甲站着等待。', None),
]


def setup(tmp_path, text='甲惊讶地睁大眼睛。', kind='dialogue'):
    store, job, catalog = fixture(tmp_path, text, kind)
    empty = compile_value(store, job, catalog)['stage']
    profiles = face_envelope(empty, catalog, mode='expressions')['profile_set']['profiles']
    skeletons, actors, files = [], [], {catalog['runtime']['sha256']: catalog['runtime']}
    for p in profiles:
        s = deepcopy(catalog['skeletons'][0]);s['asset_id'] = p['asset_id'];s['source_filename'] = p['source_filename']
        s['original'] = {**s['original'], 'sha256': p['original_sha256']}
        s['skeleton'] = {**s['skeleton'], 'sha256': p['skeleton_sha256']}
        files.update({r['sha256']: r for r in (s['original'], s['skeleton'], s['atlas'], *s['pages'].values())})
        skeletons.append(s)
        actors.append({**catalog['actors'][0], 'actor_id': p['actor_id'], 'revision': p['actor_revision'], 'asset_id': p['asset_id']})
    catalog.update(actors=actors, skeletons=skeletons, outfits=[], files=files)
    catalog['catalog_sha256'] = canonical_hash({k:v for k,v in catalog.items() if k!='catalog_sha256'})
    value = compile_value(store, job, catalog, bindings=dict(zip(('a','b'), (a['actor_id'] for a in actors))), face_enabled=True, face_mode='expressions')
    preview = save_preview(tmp_path, value)
    (tmp_path/'catalogs').mkdir(exist_ok=True)
    (tmp_path/'catalogs'/f"{catalog['catalog_sha256']}.json").write_text(json.dumps(catalog), encoding='utf-8')
    delivery = json.loads((store.root/preview['script']['artifact']['sha256']).read_text(encoding='utf-8'))
    timeline = json.loads((store.root/preview['timeline']['artifact']['sha256']).read_text(encoding='utf-8'))
    return store, job, catalog, preview, delivery, timeline


def generate(data, **kwargs):
    _, _, c, p, d, t = data
    return build_face_suggestions(p, d, t, c, FaceSuggestionRequest(source_preview_sha256=p['preview_sha256']), **kwargs)


@pytest.mark.parametrize('text,want', CASES)
def test_chinese_precision_cases(tmp_path, text, want):
    data = setup(tmp_path, text)
    before = deepcopy(data[1:])
    result = generate(data)
    assert [s['face_cue']['emotion'] for s in result['suggestions']] == ([want] if want else [])
    for s in result['suggestions']:
        assert (s['face_cue']['start_frame'], s['face_cue']['end_frame']) == (32000, 48000)
        assert s['evidence']['quote'] in text and s['evidence']['source']['line_id'] == 'action'
    assert data[1:] == before
    assert generate(data) == result


def test_manual_conflicts_blink_coexistence_and_body_protection(tmp_path):
    data = setup(tmp_path);p = data[3];cue = generate(data)['suggestions'][0]['face_cue']
    p['face']['track']['cues'] = [{**cue, 'id':'manual'}]
    r = generate(data);assert not r['suggestions'] and any(d['code']=='CONFLICT_WITH_MANUAL' for d in r['diagnostics'])
    p['face']['track']['cues'] = [{**cue, 'id':'manual-blink', 'type':'blink', 'emotion':None}]
    assert generate(data)['suggestions']
    p['face']['track']['cues'] = []
    for action in ('pick','lie'):
        p['stage']['cues'] = [{'id':'protected','instance_id':cue['instance_id'],'type':'body.play','start_frame':32000,'end_frame':40000,'enabled':True,'params':{'action':action,'mode':'hold'}}]
        r = generate(data);assert not r['suggestions'] and any(d['code']=='PROTECTED_ACTION' for d in r['diagnostics'])


def test_actions_without_measured_anchor_and_unique_antecedent(tmp_path):
    data = setup(tmp_path, '甲惊讶地睁大眼睛。', 'action')
    r = generate(data);assert not r['suggestions'] and any(d['code']=='NO_ANCHOR' for d in r['diagnostics'])
    data = setup(tmp_path/'narration', '甲停住脚步，她惊讶地睁大眼睛。', 'narration')
    assert generate(data)['suggestions']
    data[3]['stage']['actors'].append({**data[3]['stage']['actors'][0], 'instance_id':'third','role_id':'c','name':'丙'})
    data[3]['face']['track']['bindings']=track_bindings(data[3]['stage'])
    line = data[4]['episodes'][0]['scenes'][0]['utterances'][-1];line['text']='她惊讶地睁大眼睛。'
    assert any(d['code']=='AMBIGUOUS_ACTOR' for d in generate(data)['diagnostics'])


def test_compound_timeline_and_cue_text_not_in_source(tmp_path):
    data = setup(tmp_path)
    t = data[5];t['schema_version']='measured_timeline/2.0'
    t['events'] = [{'source':{k:e[k] for k in ('episode_id','scene_id','line_id')}, 'kind':e['kind'], 'text':e['text'],
        'operations':[{'operation_id':str(e['sequence']),'mode':'speech','cue_text':e['text'],'start_frame':e['start_frame'],
                       'end_frame':e['end_frame'],'source_audio_sha256':e['source_audio_sha256']}]} for e in t['events']]
    assert generate(data)['suggestions'][0]['face_cue']['start_frame']==32000
    t['events'][-1]['operations'][0]['cue_text']='另外一句没有表情的台词。'
    assert any(d['code']=='NO_ANCHOR' for d in generate(data)['diagnostics'])


@pytest.mark.parametrize('field', ['audio_sha256','source_version_id','sample_rate','total_frames'])
def test_timing_and_source_mismatch_rejected(tmp_path, field):
    data = setup(tmp_path);data[5][field]='wrong'
    with pytest.raises(ProductionError, match='SOURCE_MISMATCH'):generate(data)


def test_disabled_stale_profiles_and_input_limits(tmp_path):
    data=setup(tmp_path);p=data[3];face=p.pop('face')
    assert generate(data)['diagnostics'][0]['code']=='FACE_DISABLED'
    p['face']=face;p['face']['profile_set']['profiles'][0]['skeleton_sha256']='0'*64
    with pytest.raises(ProductionError, match='PROFILE_MISMATCH'):generate(data)
    data=setup(tmp_path/'large');data[4]['episodes'][0]['scenes'][0]['utterances'][-1]['text']='甲'*32769
    assert generate(data)['diagnostics'][0]['code']=='INPUT_LIMIT'


def test_injected_classifier_is_bounded_grounded_and_never_retried(tmp_path):
    data=setup(tmp_path)
    class Fake:
        version='test-fixture/1'
        calls=0
        value={'role_id':'a','emotion':'surprised','quote':'惊讶','confidence':.98}
        def classify(self,payload):
            self.calls+=1
            assert 'start_frame' not in payload and len(payload['text'])<=2048
            if isinstance(self.value,Exception):raise self.value
            return self.value
    good=Fake();assert generate(data,classifier=good)['suggestions'] and good.calls==1
    for bad in [TimeoutError(), {**good.value,'emotion':'happy'}, {**good.value,'role_id':'b'},
                {**good.value,'confidence':.5}, {**good.value,'quote':'甲'}, {**good.value,'quote':'没有出现的证据'}, {**good.value,'start_frame':42}]:
        f=Fake();f.value=bad;r=generate(data,classifier=f)
        assert f.calls==1 and not r['suggestions'] and any(d['code']=='CLASSIFIER_UNCERTAIN' for d in r['diagnostics'])


def test_unavailable_skeleton_profile_and_deterministic_limits(tmp_path):
    data=setup(tmp_path);p=data[3];c=data[2]
    c['skeletons'][0]['skeleton']['sha256']='0'*64
    r=generate(data);assert not r['suggestions'] and any(d['code']=='PROFILE_UNAVAILABLE' for d in r['diagnostics'])
    data=setup(tmp_path/'stable');original=generate(data)
    assert all(generate(data)==original for _ in range(20))
    request=FaceSuggestionRequest(source_preview_sha256=data[3]['preview_sha256'],include_diagnostics=False,max_suggestions=1)
    result=build_face_suggestions(data[3],data[4],data[5],data[2],request)
    assert len(result['suggestions'])==1 and result['diagnostics']==[]
    with pytest.raises(ProductionError,match='SOURCE_MISMATCH'):
        build_face_suggestions(data[3],data[4],data[5],data[2],FaceSuggestionRequest(source_preview_sha256='0'*64))


@pytest.mark.parametrize('change', [{'max_suggestions':True},{'max_suggestions':65},{'max_suggestions':0},
    {'max_suggestions':'32'},{'include_diagnostics':'false'},{'mode':'paid_ai'},{'extra':'field'},{'source_preview_sha256':'../file'}])
def test_request_is_strict_and_bounded(change):
    with pytest.raises(ValidationError):FaceSuggestionRequest.model_validate({'source_preview_sha256':'a'*64,**change})


def test_endpoint_owner_read_only_and_immutable_acceptance(tmp_path, monkeypatch):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from my_legend_production.production.api import register_production_routes
    data=setup(tmp_path);store,job,c,p,_,_=data
    class Repository:
        def get(self,jid,uid):
            if jid!=job['id'] or uid!='alice':raise ProductionError('JOB_NOT_FOUND')
            return deepcopy(job)
    def owner(request):
        if not request.headers.get('authorization'):raise HTTPException(401,'UNAUTHORIZED')
        return request.headers['authorization']
    monkeypatch.setenv('MY_LEGEND_SPINE_ROOT',str(tmp_path))
    app=FastAPI();register_production_routes(app,Repository(),store,owner,lambda:{})
    client=TestClient(app);path=f"/api/v1/production-jobs/{job['id']}/stage-preview";body={'source_preview_sha256':p['preview_sha256']}
    before={str(f):f.read_bytes() for f in tmp_path.rglob('*') if f.is_file()}
    assert client.post(path+'/suggest-face',json=body).status_code==401
    assert client.post(path+'/suggest-face',json=body,headers={'authorization':'bob'}).status_code==404
    h={'authorization':'alice'};response=client.post(path+'/suggest-face',json=body,headers=h)
    assert response.status_code==200,response.text
    result=response.json()['data'];assert len(result['suggestions'])==1
    assert client.post(path+'/suggest-face',json=body,headers=h).json()['data']==result
    assert {str(f):f.read_bytes() for f in tmp_path.rglob('*') if f.is_file()}==before
    track=deepcopy(p['face']['track']);track['cues']+=[result['suggestions'][0]['face_cue']]
    save={'profile':'life_v1','operation':'validate_and_save','script':p['stage'],'face_enabled':True,'face_profile_sha256':p['face']['profile_sha256'],'face_track':track}
    accepted=client.post(path,json=save,headers=h);assert accepted.status_code==200,accepted.text
    assert accepted.json()['data']['preview_sha256']!=p['preview_sha256']
    assert client.get(path+'/'+p['preview_sha256'],headers=h).json()['data']==p
    assert all(__import__('pathlib').Path(f).read_bytes()==b for f,b in before.items())
    bad=deepcopy(save);bad['face_track']['bindings']['audio_sha256']='0'*64
    assert client.post(path,json=bad,headers=h).status_code==422
    missing=client.post(path+'/suggest-face',json={'source_preview_sha256':'0'*64},headers=h)
    assert missing.status_code==409 and missing.json()['detail']=='SPINE_PREVIEW_NOT_FOUND'
