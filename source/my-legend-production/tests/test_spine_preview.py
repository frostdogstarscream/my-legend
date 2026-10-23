from copy import deepcopy
from hashlib import sha256
import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError
from my_legend_production.production.artifacts import ArtifactStore
from my_legend_production.production.repository import ProductionError
from my_legend_production.production.spine_preview import StagePreviewRequest, compile_preview, resolve_asset, save_preview


def fixture(tmp_path):
    store = ArtifactStore(tmp_path / 'artifacts')
    delivery = {'story_version_id': 'v1', 'title': 'fixture', 'character_snapshot': [
        {'role_id': 'b', 'canon': {'name': 'B'}}, {'role_id': 'a', 'canon': {'name': 'A'}}],
        'episodes': [{'episode_id': 'ep', 'scenes': [{'scene_id': 'sc', 'utterances': [
            {'line_id': 'l1', 'kind': 'dialogue', 'speaker_role_id': 'a', 'text': 'hello'},
            {'line_id': 'l2', 'kind': 'dialogue', 'speaker_role_id': 'b', 'text': 'world'}]}]}]}
    script = store.put_json('StoryDelivery', 'v1', delivery)
    audio = store.put('AudioMix', 'v1', b'fixture-audio-not-playable', 'audio/wav')
    timeline = store.put_json('MeasuredTimeline', 'v1', {'source_version_id': 'v1', 'sample_rate': 8000,
        'channels': 1, 'total_frames': 16000, 'audio_sha256': audio.sha256, 'events': [
        {'sequence': i+1, 'episode_id': 'ep', 'scene_id': 'sc', 'line_id': f'l{i+1}', 'kind': 'dialogue',
         'text': text, 'start_frame': i*8000, 'end_frame': (i+1)*8000, 'duration_basis': 'measured_audio',
         'source_audio_sha256': audio.sha256} for i, text in enumerate(['hello', 'world'])]})
    job = {'id': 'job-1', 'work_id': 'work-1', 'source_version_id': 'v1', 'status': 'succeeded',
           'artifacts': [a.model_dump() for a in (script, audio, timeline)]}
    catalog = {'catalog_sha256': 'a'*64, 'actors': [{'actor_id': a, 'version': a*64, 'idle_animation': 'wait'} for a in ['a','b','c']]}
    return store, job, catalog


def test_real_playback_contract_and_role_ids_preserve_job_and_audio(tmp_path):
    store, job, catalog = fixture(tmp_path)
    before = deepcopy(job)
    value = compile_preview(job, StagePreviewRequest(), store, catalog)
    assert [(a['role_id'], a['name']) for a in value['stage']['actors']] == [('a','A'),('b','B')]
    assert [c['text'] for c in value['stage']['cues']] == ['hello','world']
    assert value['audio']['artifact'] == job['artifacts'][1]
    assert job == before and value['mouth_control'] is False
    assert save_preview(tmp_path, value) == save_preview(tmp_path, value)
    assert len(list((tmp_path/'previews'/'job-1').glob('*.json'))) == 1


@pytest.mark.parametrize('bindings', [{'unknown':'a'},{'a':'missing'},{'a':'b','b':'b'}])
def test_invalid_bindings_cannot_silently_recast(tmp_path, bindings):
    store, job, catalog = fixture(tmp_path)
    with pytest.raises(ProductionError):
        compile_preview(job, StagePreviewRequest(bindings=bindings), store, catalog)


def test_asset_is_allowlisted_hashed_and_not_arbitrary_path(tmp_path):
    payload=b'fixture';digest=sha256(payload).hexdigest();folder=tmp_path/'content';folder.mkdir();(folder/digest).write_bytes(payload)
    catalog={'files':{digest:{'size_bytes':len(payload),'media_type':'image/png'}}}
    assert resolve_asset(tmp_path,catalog,digest)[0] == folder/digest
    for wrong in ['../catalog.json','f'*64]:
        with pytest.raises(ProductionError): resolve_asset(tmp_path,catalog,wrong)
    (folder/digest).write_bytes(b'changed')
    with pytest.raises(ProductionError,match='HASH_MISMATCH'): resolve_asset(tmp_path,catalog,digest)


def test_binary_reader_rejects_truncation_and_unsupported_versions():
    path=Path(__file__).resolve().parents[3]/'ops/my-legend/spine21.py'
    spec=importlib.util.spec_from_file_location('spine21_test',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    for data in [b'', b'\xff'*6, b'\x00\x064.2.0']:
        with pytest.raises((ValueError, UnicodeError)): module.decode(data)


def test_binding_limit():
    with pytest.raises(ValidationError): StagePreviewRequest(bindings={str(i):'a' for i in range(4)})
