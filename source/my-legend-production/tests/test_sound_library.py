from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sqlite3

import pytest

from my_legend_production.production.sound_library import SoundLibrary,SoundLibraryRequest
from my_legend_production.production.sound_library_adapter import sound_library_adapter


def fixture(tmp_path):
    np=pytest.importorskip('numpy');sf=pytest.importorskip('soundfile')
    source=tmp_path/'sound.wav';sf.write(source,.1*np.sin(np.arange(4800)*.1),48000,subtype='PCM_24')
    req=SoundLibraryRequest(scope='owner-one',provider_endpoint='https://audio.example/generate',model='sfx-v1',
                            prompt='屋檐滴水，无人声，无音乐',seed=42,sample_rate=48000,channels=1)
    return SoundLibrary(tmp_path/'library'),req,source


def test_persistent_library_has_exact_matches_review_history_and_immutable_variants(tmp_path):
    library,req,source=fixture(tmp_path)
    first=library.publish(req,source,job_id='job1',operation_id='drips')
    assert first['duration_seconds']==.1 and first['review_state']=='pending'
    assert library.lookup(req) is None
    assert library.lookup(req,allow_unreviewed=True)['asset_sha256']==first['asset_sha256']
    library.review(req.cache_key(),first['asset_sha256'],state='approved',actor='tester',note='听审通过')
    second=library.publish(req,source,job_id='job2',operation_id='other-drips')
    assert second['review_state']=='approved' and second['asset_sha256']==first['asset_sha256']
    restarted=SoundLibrary(tmp_path/'library')
    assert restarted.lookup(req)['human_review']=='approved'
    with restarted.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM assets').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM origins').fetchone()[0]==2
    library.review(req.cache_key(),first['asset_sha256'],state='rejected',actor='tester',note='复听发现杂音')
    assert library.lookup(req,allow_unreviewed=True) is None
    library.publish(req,source,job_id='job3',operation_id='drips')
    assert library.lookup(req,allow_unreviewed=True) is None # Republish cannot erase rejection.


@pytest.mark.parametrize('field,value',[('scope','other-owner'),('model','sfx-v2'),('prompt','窗外大雨'),
    ('seed',43),('candidate_version',2),('sample_rate',24000),('channels',2),('request_revision','new-protocol'),
    ('provider_endpoint','https://other.example/generate')])
def test_no_cross_request_or_cross_owner_reuse(tmp_path,field,value):
    library,req,source=fixture(tmp_path);library.publish(req,source,job_id='job',operation_id='sound')
    changed=SoundLibraryRequest.model_validate(req.model_dump()|{field:value})
    assert library.lookup(changed,allow_unreviewed=True) is None


def test_corrupt_blob_is_not_reused_and_cannot_be_overwritten_by_publish(tmp_path):
    library,req,source=fixture(tmp_path);record=library.publish(req,source,job_id='job',operation_id='sound')
    path=tmp_path/'library/blobs'/(record['asset_sha256']+'.wav');path.write_bytes(b'damaged')
    assert library.lookup(req,allow_unreviewed=True) is None
    with pytest.raises(ValueError,match='SOUND_LIBRARY_BLOB_CORRUPT'):
        library.publish(req,source,job_id='job2',operation_id='sound')
    assert path.read_bytes()==b'damaged'


@pytest.mark.parametrize('fault',['silent','nan','wrong_rate','invalid'])
def test_invalid_audio_is_not_indexed(tmp_path,fault):
    library,req,source=fixture(tmp_path)
    import numpy as np
    import soundfile as sf
    if fault=='invalid':source.write_bytes(b'not audio')
    else:sf.write(source,np.full(100,float('nan') if fault=='nan' else 0.0 if fault=='silent' else .1),
                  24000 if fault=='wrong_rate' else 48000,subtype='FLOAT')
    with pytest.raises((ValueError,RuntimeError)):library.publish(req,source,job_id='job',operation_id='sound')
    with library.connect() as db:assert db.execute('SELECT COUNT(*) FROM entries').fetchone()[0]==0


def test_concurrent_publication_is_idempotent_and_keeps_all_origins(tmp_path):
    library,req,source=fixture(tmp_path)
    def publish(i):return library.publish(req,source,job_id=f'job{i}',operation_id='sound')
    with ThreadPoolExecutor(max_workers=4) as pool:records=list(pool.map(publish,range(8)))
    assert len({r['asset_sha256'] for r in records})==1
    with library.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM entries').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM origins').fetchone()[0]==8


def test_adapter_exact_hit_avoids_supplier_budget_and_force_remake_bypasses_hit(tmp_path):
    library,req,source=fixture(tmp_path);calls=[];budget=[]
    class FakeProvider:
        manages_call_budget=True
        def __init__(self,config,budget=None):self.config=config;self.budget=budget
        def generate(self,payload,destination):
            self.budget();calls.append(payload);destination.write_bytes(source.read_bytes())
            return {'model':'sfx-v1','usage':{'fake_test_units':1}}
    wrapped=sound_library_adapter(FakeProvider,tmp_path/'library',scope='owner-one',job_id='job1',allow_unreviewed=True)
    config=dict(http_url=req.provider_endpoint,model=req.model,sample_rate=48000,channels=1,api_key='fake-secret-not-to-store')
    adapter=wrapped(config,budget=lambda:budget.append(1));payload=dict(prompt=req.prompt,seed=42,media={})
    first=adapter.generate(payload,tmp_path/'a.wav');second=adapter.generate(payload,tmp_path/'b.wav')
    assert first['library_status']=='published' and second['library_status']=='reused'
    assert second['origin']=='sound_library' and 'usage' not in second and len(calls)==len(budget)==1
    assert second['library_reference']['review_state']=='pending'
    third=adapter.generate(dict(payload,media={'candidate_version':2}),tmp_path/'c.wav')
    assert third['library_status']=='published' and len(calls)==len(budget)==2
    with library.connect() as db:
        data=''.join(r[0] for r in db.execute('SELECT request_json FROM entries'))
        assert 'fake-secret' not in data and 'api_key' not in data


def test_fixture_generation_is_never_published(tmp_path):
    library,req,source=fixture(tmp_path)
    class FixtureProvider:
        def __init__(self,config):self.config=config
        def generate(self,payload,destination):destination.write_bytes(source.read_bytes());return {'fixture_only':True}
    wrapped=sound_library_adapter(FixtureProvider,tmp_path/'library',scope='owner-one',job_id='test',allow_unreviewed=True)
    result=wrapped(dict(http_url=req.provider_endpoint,model=req.model)).generate(dict(prompt=req.prompt),tmp_path/'fixture.wav')
    assert result['library_status']=='fixture_not_published'
    assert library.lookup(req,allow_unreviewed=True) is None
