from copy import deepcopy
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

import pytest
pytest.importorskip('fastapi')
from fastapi import FastAPI
from fastapi.testclient import TestClient
from my_legend_production.story_api import create_app
from my_legend_production.performance_api import register_routes
from my_legend_production.performance_production import VOICES, validate_voices
from my_legend_production.storyboard import partition_coverage,project_segment
from my_legend_production.worker import save
from my_legend_production.core import AdapterError
from my_legend_production.performance_review import resume_visual_review,apply as apply_workflow_review
from types import SimpleNamespace


@pytest.fixture
def setup(tmp_path):
    class Engine:
        def call(self,*_args):return {'status':'ready'}
    app=create_app(tmp_path,Engine());repo=app.state.repository
    # Use an isolated router with a process stub; tests never launch paid models.
    isolated=FastAPI();launched=[]
    register_routes(isolated,repo,lambda data:{'code':0,'data':data},lambda job:launched.append(job),lambda root,record:None)
    c=TestClient(isolated)
    d=json.loads((Path(__file__).resolve().parents[1]/'examples/source/story_delivery.json').read_text(encoding='utf-8'))
    m=json.loads((Path(__file__).resolve().parents[1]/'examples/source/render_manifest.json').read_text(encoding='utf-8'))
    artifacts=[repo.artifact('StoryDelivery',d['story_version_id'],d),repo.artifact('RenderManifest',d['story_version_id'],m)]
    repo.put('story',{'id':'s1','status':'ready','storyVersionId':d['story_version_id'],'handoffArtifacts':artifacts})
    ids={u.get('speaker_role_id') or u.get('narrator_id') for ep in d['episodes'] for sc in ep['scenes'] for u in sc['utterances'] if u['kind'] in ('dialogue','narration')}
    voices={identity:VOICES[n]['id'] for n,identity in enumerate(sorted(ids))}
    return c,repo,launched,d,voices


def start(setup):
    c,repo,launched,d,voices=setup
    response=c.post('/api/v1/stories/s1/production-tasks',json={'voices':voices,'acknowledgeStoryboard':True})
    assert response.status_code==200
    return response.json()['data']


def ready(setup):
    c,repo,launched,d,voices=setup;t=start(setup);job=repo.get('production_job',t['id']);root=Path(job['root'])
    (root/'preview').mkdir();raw=b'0123456789-test-only-media';(root/'preview/scene.mp4').write_bytes(raw)
    save(root/'status.json',{'id':t['id'],'storyVersionId':d['story_version_id'],'status':'awaiting_review','message':'test preview','audioReview':'pending','visualReview':'pending',
        'media':{'sourceVersionId':d['story_version_id'],'videoSha256':hashlib.sha256(raw).hexdigest(),'videoUrl':'scene.mp4','posterUrl':'poster.png','audioUrl':'mix.wav'}})
    return t,root,raw


def test_start_is_idempotent_and_checks_all_voice_bindings(setup):
    t=start(setup);assert start(setup)['id']==t['id'];assert len(setup[2])==1
    c=setup[0]
    assert c.post('/api/v1/stories/s1/production-tasks',json={'voices':{},'acknowledgeStoryboard':True}).status_code==422
    assert c.post('/api/v1/stories/s1/production-tasks',json={'voices':setup[4],'acknowledgeStoryboard':False}).status_code==422


def test_media_range_signature_and_scope(setup):
    t,root,raw=ready(setup);c=setup[0]
    data=c.get('/api/v1/production-tasks/'+t['id']).json()['data'];url=urlparse(data['media']['videoUrl']);path=url.path+'?'+url.query
    assert c.get(path,headers={'Range':'bytes=0-3'}).status_code==206
    assert c.get(path,headers={'Range':'bytes=0-3'}).content==raw[:4]
    assert c.get(path.replace('scene.mp4','mix.wav')).status_code==403
    assert c.get(path.replace('signature=','signature=bad')).status_code==403


def test_review_is_bound_to_version_hash_and_does_not_publish(setup):
    t,root,raw=ready(setup);c=setup[0];sha=hashlib.sha256(raw).hexdigest()
    body={'kind':'audio','decision':'approve','storyVersionId':setup[3]['story_version_id'],'videoSha256':sha,'notes':'unit test only'}
    bad=body|{'storyVersionId':'stale'};assert c.post('/api/v1/production-tasks/'+t['id']+'/reviews',json=bad).status_code==409
    result=c.post('/api/v1/production-tasks/'+t['id']+'/reviews',json=body).json()['data']
    assert result['audioReview']=='approved' and result['visualReview']=='pending'
    assert result['externalPublication']=='not_requested' and result['status']=='awaiting_review'
    (root/'preview/scene.mp4').write_bytes(b'changed');assert c.post('/api/v1/production-tasks/'+t['id']+'/reviews',json=body).status_code==409


def test_changed_voice_requires_explicit_new_version_and_keeps_old_job(setup):
    t,root,raw=ready(setup);c,repo,launched,d,voices=setup
    changed={k:VOICES[-1]['id'] for k in voices}
    assert c.post('/api/v1/stories/s1/production-tasks',json={'voices':changed,'acknowledgeStoryboard':True}).status_code==409
    result=c.post('/api/v1/stories/s1/production-tasks',json={'voices':changed,'acknowledgeStoryboard':True,'newRevision':True}).json()['data']
    assert result['id']!=t['id'] and root.is_dir() and len(launched)==2


def test_corrupt_media_version_is_not_attached_to_current_story(setup):
    t,root,_=ready(setup);data=json.loads((root/'status.json').read_text(encoding='utf-8'));data['media']['sourceVersionId']='other-version';save(root/'status.json',data)
    assert setup[0].get('/api/v1/production-tasks/'+t['id']).status_code==503


def test_media_hash_change_blocks_serving_even_with_valid_scoped_url(setup):
    t,root,raw=ready(setup);c=setup[0]
    result=c.get('/api/v1/production-tasks/'+t['id']).json()['data'];url=urlparse(result['media']['videoUrl'])
    (root/'preview/scene.mp4').write_bytes(b'tampered')
    assert c.get(url.path+'?'+url.query).status_code==409


def test_business_routes_require_session_while_media_checks_its_own_scope(tmp_path):
    class Engine:
        def call(self,*_args):return {'status':'ready'}
    c=TestClient(create_app(tmp_path,Engine()))
    assert c.get('/api/v1/production-options').status_code==401
    assert c.get('/media/x/scene.mp4?expires=1&signature=x').status_code==403


def test_batching_retains_source_order_including_audio_only_tail():
    coverage=[{'source':{'episode_id':'e','scene_id':'s','line_id':str(i)},'target_block_ids':['a'+str(i)]} for i in range(11)]
    visual=deepcopy(coverage)
    for c in visual[8:]:c['target_block_ids']=[]
    groups=partition_coverage(coverage,visual,4)
    assert [c['source']['line_id'] for group in groups for c in group]==[str(i) for i in range(11)]
    assert all(any(v['target_block_ids'] for v in visual if v['source'] in [c['source'] for c in g]) for g in groups)


def test_segment_offsets_are_measured_frames_and_do_not_rewrite_text():
    cov=[{'source':{'episode_id':'e','scene_id':'s','line_id':'l'},'target_block_ids':['b']}]
    am={'coverage':cov};vm={'coverage':cov};script={'events':[{'block_id':'b','text':'unchanged'}]}
    target={'script':{'scenes':[{'blocks':[{'block_id':'b','text':'unchanged'}]}]}}
    timeline={'placements':[{'block_id':'b','start_frame':100,'end_frame':250}],'total_frames':500,'sample_rate':1000}
    t,a,v,s,tl,start,end=project_segment(target,am,vm,script,timeline,cov)
    assert (start,end,tl['total_frames'])==(100,250,150)
    assert tl['placements'][0]['start_frame']==0 and tl['placements'][0]['end_frame']==150
    assert s['events'][0]['text']==t['script']['scenes'][0]['blocks'][0]['text']=='unchanged'
    assert timeline['placements'][0]['start_frame']==100


def test_visual_review_resumes_only_the_matching_original_gate():
    class Graph:
        def __init__(self):self.answers=[]
        def get_state(self,_config):
            if self.answers:return SimpleNamespace(values={'plan':{'plan_hash':'p1'},'status':'succeeded','result':{'review_status':'approved'}},tasks=[])
            return SimpleNamespace(values={'plan':{'plan_hash':'p1'},'status':'awaiting_review'},tasks=[SimpleNamespace(interrupts=[SimpleNamespace(value={'review_id':'p1','expected_plan_hash':'p1'})])])
        def invoke(self,command,_config):self.answers.append(command.resume)
    graph=Graph();assert resume_visual_review(graph,{},'p1','unit test')['review_status']=='approved'
    assert graph.answers==[{'review_id':'p1','expected_plan_hash':'p1','decision':'approve','notes':'unit test'}]
    with pytest.raises(AdapterError):resume_visual_review(Graph(),{},'stale','unit test')


def test_missing_visual_context_is_not_answered_on_behalf_of_the_reviewer():
    class Graph:
        def get_state(self,_config):return SimpleNamespace(values={'plan':{'plan_hash':'p1'},'status':'awaiting_context'},tasks=[SimpleNamespace(interrupts=[SimpleNamespace(value={'review_id':'p1','expected_plan_hash':'p1','missing_requirements':['cast']})])])
        def invoke(self,*_args):raise AssertionError('must not invent context answers')
    with pytest.raises(AdapterError,match='VISUAL_CONTEXT_ANSWERS_REQUIRED'):resume_visual_review(Graph(),{},'p1','unit test')


def test_workflow_review_rechecks_actual_media_before_any_approval(tmp_path):
    root=tmp_path/'job';(root/'preview').mkdir(parents=True);(root/'preview/scene.mp4').write_bytes(b'changed')
    expected=hashlib.sha256(b'original').hexdigest()
    save(root/'status.json',{'id':'j1','storyVersionId':'v1','media':{'videoSha256':expected}})
    save(root/'review.json',{'decision':'approve','jobId':'j1','storyVersionId':'v1','videoSha256':expected,'kind':'audio','reviewer':'test','notes':''})
    save(tmp_path/'paths.json',{})
    with pytest.raises(AdapterError,match='MEDIA_HASH_CHANGED'):apply_workflow_review(root,root/'review.json',tmp_path/'paths.json')
