"""Business invariants around the real StoryGenerate HTTP contract; no paid model calls."""
from copy import deepcopy
from pathlib import Path

import pytest
pytest.importorskip('fastapi')
pytest.importorskip('drama_engine')
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from drama_engine.service.schemas import PlanApprovalRequest, PlanRevisionRequest
from drama_engine.workflow.schemas import StoryOperationRequest
from my_legend_production.story_api import create_app, failure_message


def test_duration_error_is_actionable_without_exposing_provider_body():
    assert '104 秒' in failure_message({'error_message':'EPISODE_CONTRACT_FAILED: episode 1: duration estimate 104s outside 85%-115% of 90s'})
    assert 'private backend text' not in failure_message({'error_message':'private backend text'})


class Engine:
    def __init__(self):
        self.calls = []
        self.revision = 1
        self.failed = False
        self.deliver = False
        self.plan_state = 'SUCCEEDED'

    def call(self, method, path, body=None):
        self.calls.append((method, path, deepcopy(body)))
        if path == '/health':
            return {'status': 'ready'}
        if path == '/v1/plans' and method == 'POST':
            TypeAdapter(StoryOperationRequest).validate_python(body)
            assert set(body['characters']['selected_character_ids']) == {r['character_id'] for r in body['characters']['role_bindings']}
            assert all(r['role_id'] and r['performer_id'] for r in body['characters']['role_bindings'])
            return {'task_id': 'planning-1'}
        if path.startswith('/v1/tasks/'):
            if path.endswith('/retry'):
                self.failed = False
                return {'status': 'QUEUED'}
            if path.endswith('/cancel'):
                self.plan_state = 'CANCELLED'
                return {'status': 'CANCELLED'}
            return {'status': 'FAILED' if self.failed else self.plan_state, 'result': {'plan_id': 'plan-1'}, 'error_code': 'MODEL_OUTPUT_INVALID'}
        if path == '/v1/plans/plan-1':
            return {'plan_id': 'plan-1', 'plan_revision': self.revision, 'plan_fingerprint': str(self.revision) * 64,
                    'plan': {'title': '新大纲', 'premise': '仅测试结构', 'episode_outlines': []},
                    'resolved_preferences': {'target_duration_sec': 90, 'target_episodes': 1}}
        if path == '/v1/plans/plan-1/revisions':
            PlanRevisionRequest.model_validate(body)
            self.revision += 1
            return {'task_id': 'revision-1'}
        if path == '/v1/plans/plan-1/approve':
            PlanApprovalRequest.model_validate(body)
            assert body['expected_revision'] == self.revision
            assert body['fingerprint'] == str(self.revision) * 64
            return {'job_id': 'writing-1'}
        if path == '/v1/jobs/writing-1':
            if not self.deliver:
                return {'status': 'RUNNING', 'progress_pct': 15}
            return {'status': 'SUCCEEDED', 'result_story_version_id': 'version-1'}
        if path == '/v1/story-versions/version-1':
            return {'status': 'READY', 'story_version_id': 'version-1', 'character_portrayals': [{'role_id': 'r1', 'story_name': '墨羽'}],
                    'episodes': [{'episode_id': 'ep1', 'title': '真实交付测试', 'scenes': [{'title': '房间', 'utterances': [
                        {'kind': 'dialogue', 'speaker_role_id': 'r1', 'text': '保留原台词。'}, {'kind': 'sfx', 'text': '一声敲门。'}]}]}]}
        if path.endswith('/render-manifest'):
            return {'story_version_id': 'version-1', 'tasks': []}
        raise AssertionError((method, path))


@pytest.fixture
def setup(tmp_path):
    engine = Engine()
    app = create_app(tmp_path, engine)
    client = TestClient(app)
    token = client.post('/api/v1/auth/local', json={}).json()['data']['accessToken']
    client.headers['Authorization'] = 'Bearer ' + token
    return client, engine, app


def draft(client):
    response = client.post('/api/v1/stories', json={'title': '测试', 'genre': '生活治愈', 'idea': '保留这份用户创意', 'roleIds': ['legend-moyu']})
    assert response.status_code == 200
    return response.json()['data']


def start(client):
    story = draft(client)
    task = client.post('/api/v1/stories/' + story['id'] + '/generation-tasks').json()['data']
    return story, task


def ready_plan(client, task):
    response = client.get('/api/v1/generation-tasks/' + task['id'])
    assert response.status_code == 200
    value = response.json()['data']
    assert value['status'] == 'awaiting_approval'
    return value


def test_local_login_rejects_foreign_browser_origin(setup):
    client, _, _ = setup
    assert client.post('/api/v1/auth/local', headers={'Origin': 'https://untrusted.example'}, json={}).status_code == 403


def test_auth_and_logout_revoke_session(setup):
    client, _, _ = setup
    assert client.get('/api/v1/stories', headers={'Authorization': 'Bearer wrong'}).status_code == 401
    assert client.post('/api/v1/auth/logout', json={}).status_code == 200
    assert client.get('/api/v1/stories').status_code == 401


def test_invalid_role_is_rejected_before_model_work(setup):
    client, engine, _ = setup
    response = client.post('/api/v1/stories', json={'title': 'x', 'genre': 'x', 'idea': 'x', 'roleIds': ['unknown']})
    assert response.status_code == 422
    assert not engine.calls


def test_start_is_idempotent_and_never_auto_approves(setup):
    client, engine, _ = setup
    story, task = start(client)
    second = client.post('/api/v1/stories/' + story['id'] + '/generation-tasks').json()['data']
    assert second['id'] == task['id']
    assert second['status'] == 'awaiting_approval'
    assert len([c for c in engine.calls if c[:2] == ('POST', '/v1/plans')]) == 1
    assert not any(c[1].endswith('/approve') for c in engine.calls)


def test_stale_outline_cannot_start_paid_body(setup):
    client, engine, _ = setup
    _, task = start(client)
    ready_plan(client, task)
    response = client.post('/api/v1/generation-tasks/' + task['id'] + '/approve', json={'revision': 2, 'fingerprint': '2' * 64})
    assert response.status_code == 409
    assert not any(c[1].endswith('/approve') for c in engine.calls)


def test_approval_is_exact_and_duplicate_does_not_create_second_job(setup):
    client, engine, _ = setup
    _, task = start(client)
    ready_plan(client, task)
    body = {'revision': 1, 'fingerprint': '1' * 64}
    for _ in range(2):
        response = client.post('/api/v1/generation-tasks/' + task['id'] + '/approve', json=body)
        assert response.status_code == 200
        assert response.json()['data']['phase'] == 'writing'
    assert len([c for c in engine.calls if c[1].endswith('/approve')]) == 1


def test_revision_requires_confirmation_again(setup):
    client, engine, _ = setup
    _, task = start(client)
    ready_plan(client, task)
    response = client.post('/api/v1/generation-tasks/' + task['id'] + '/revise', json={'revision': 1, 'fingerprint': '1' * 64, 'feedback': '结局更温暖'})
    assert response.status_code == 200
    updated = ready_plan(client, task)
    assert updated['plan']['revision'] == 2
    assert not any(c[1].endswith('/approve') for c in engine.calls)


def test_failure_preserves_idea_and_never_inserts_demo_chapters(setup):
    client, engine, _ = setup
    story, task = start(client)
    engine.failed = True
    result = client.get('/api/v1/generation-tasks/' + task['id']).json()['data']
    assert result['status'] == 'failed'
    saved = client.get('/api/v1/stories/' + story['id']).json()['data']
    assert saved['idea'] == story['idea']
    assert not saved.get('chapters')
    retried = client.post('/api/v1/generation-tasks/' + task['id'] + '/retry', json={}).json()['data']
    assert retried['id'] == task['id']


def test_chapters_and_handoff_share_source_version(setup):
    client, engine, _ = setup
    story, task = start(client)
    ready_plan(client, task)
    client.post('/api/v1/generation-tasks/' + task['id'] + '/approve', json={'revision': 1, 'fingerprint': '1' * 64})
    engine.deliver = True
    assert client.get('/api/v1/generation-tasks/' + task['id']).json()['data']['status'] == 'succeeded'
    saved = client.get('/api/v1/stories/' + story['id']).json()['data']
    assert saved['chapters'][0]['source'] == 'generated'
    assert saved['chapters'][0]['paragraphs'][1:] == ['墨羽：保留原台词。', '音效：一声敲门。']
    assert {a['storyVersionId'] for a in saved['handoffArtifacts']} == {saved['storyVersionId']}
    assert saved['production']['visual'] == 'not_started'
    assert not saved.get('media')


def test_restart_recovers_pending_outline_without_another_create(setup, tmp_path):
    client, engine, _ = setup
    _, task = start(client)
    token = client.headers['Authorization']
    restarted = TestClient(create_app(tmp_path, engine))
    restarted.headers['Authorization'] = token
    result = restarted.get('/api/v1/generation-tasks/' + task['id']).json()['data']
    assert result['status'] == 'awaiting_approval'
    assert len([c for c in engine.calls if c[:2] == ('POST', '/v1/plans')]) == 1


def test_timeout_resubmits_the_same_idempotent_request(setup):
    client, engine, _ = setup
    original = engine.call
    first = True
    def interrupted(method, path, body=None):
        nonlocal first
        if path == '/v1/plans' and first:
            first = False
            engine.calls.append((method, path, deepcopy(body)))
            raise HTTPException(503, 'connection interrupted')
        return original(method, path, body)
    engine.call = interrupted
    story = draft(client)
    assert client.post('/api/v1/stories/' + story['id'] + '/generation-tasks').status_code == 503
    assert client.get('/api/v1/stories/' + story['id']).status_code == 200
    body = {k: story[k] for k in ('title','genre','idea','roleIds')}
    assert client.post('/api/v1/stories/' + story['id'], json=body).status_code == 200
    assert client.post('/api/v1/stories/' + story['id'], json={**body, 'idea':'不能改变已有请求'}).status_code == 409
    result = client.post('/api/v1/stories/' + story['id'] + '/generation-tasks')
    assert result.status_code == 200
    submitted = [c[2] for c in engine.calls if c[:2] == ('POST', '/v1/plans')]
    assert submitted[0] == submitted[1]
