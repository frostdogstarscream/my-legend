import json
import httpx
import pytest
from my_legend_production.production.visual_model import VisualModel
from my_legend_production.production.repository import ProductionError


@pytest.mark.parametrize('remaining,expected_read', [(1200,600),(93,93),(5,5)])
def test_nonstreamed_read_respects_remaining_budget(remaining,expected_read):
    requests=[]
    def transport(request):
        requests.append(request)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}],'model':'test-model'})
    model=VisualModel('https://example.test/v1','test-key','test-model',transport=httpx.MockTransport(transport),
                      max_tokens=24000,read_timeout=600,thinking=True)
    response=model.complete('system',{}, {},remaining)
    assert response['content']=='{}' and response['finish_reason']=='stop'
    assert len(requests)==1
    limits=requests[0].extensions['timeout']
    assert limits['read']==expected_read
    assert limits['connect']==min(15,remaining)
    assert limits['write']==min(30,remaining)
    assert json.loads(requests[0].content)['stream'] is False
    assert json.loads(requests[0].content)['max_tokens']==24000
    assert json.loads(requests[0].content)['thinking']=={'type':'enabled'}
    assert response['request_settings']['thinking']=='enabled'


def test_timeout_is_uncertain_without_retry_or_secret_in_diagnostic():
    requests=[]
    def transport(request):
        requests.append(request)
        raise httpx.ReadTimeout('secret-provider-text',request=request)
    model=VisualModel('https://example.test/v1','test-key','test-model',transport=httpx.MockTransport(transport))
    with pytest.raises(ProductionError) as exc:model.complete('system',{}, {},1200)
    assert len(requests)==1
    assert exc.value.diagnostic=={'exception_type':'ReadTimeout','http_status':None}
    assert 'secret' not in str(exc.value)


def test_generic_visual_profile_keeps_existing_limits():
    seen=[]
    def transport(request):
        seen.append(request)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]})
    model=VisualModel('https://example.test/v1','test-key','test-model',transport=httpx.MockTransport(transport))
    model.complete('system',{}, {},1200)
    assert seen[0].extensions['timeout']['read']==180
    body=json.loads(seen[0].content)
    assert body['max_tokens']==12000 and body['thinking']=={'type':'disabled'}


def test_rate_limit_diagnostic_is_bounded_redacted_and_does_not_retry():
    seen=[]
    def transport(request):
        seen.append(request)
        return httpx.Response(429,json={'error':{'type':'GoUsageLimitError','message':'limited test-key Bearer another-secret https://example.test/private '+('x'*800),'private':'do-not-store'},'metadata':{'limitName':'monthly'}},headers={'Retry-After':'60','X-Request-Id':'request-123'})
    model=VisualModel('https://example.test/v1','test-key','test-model',transport=httpx.MockTransport(transport))
    with pytest.raises(ProductionError) as exc:model.complete('system',{}, {},60)
    d=exc.value.diagnostic
    assert len(seen)==1 and d['http_status']==429 and d['limit_name']=='monthly'
    assert d['retry-after']=='60' and d['x-request-id']=='request-123'
    assert len(d['provider_error']['message'])<=600
    assert not any(v in json.dumps(d) for v in ['test-key','another-secret','https://','do-not-store'])


def test_opencode_session_is_stable_per_job_and_not_sent_to_other_providers():
    seen=[]
    def transport(request):
        seen.append(request)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}]})
    model=VisualModel('https://opencode.ai/inference/go/openai/v1','test-key','deepseek-v4.1-flash',
                      transport=httpx.MockTransport(transport))
    first='00000000-0000-4000-8000-000000000001';second='00000000-0000-4000-8000-000000000002'
    model.set_session(first)
    model.complete('plan',{}, {},60);model.complete('review',{}, {},60)
    model.set_session(second);model.complete('plan',{}, {},60)
    assert [r.headers['x-opencode-session'] for r in seen]==[first,first,second]
    assert all(r.headers['user-agent']=='my-legend-visual-director/1.0' for r in seen)
    assert all(str(r.url)=='https://opencode.ai/inference/go/openai/v1/chat/completions' for r in seen)
    other=VisualModel('https://example.test/v1','test-key','other',transport=httpx.MockTransport(transport))
    other.set_session(first);other.complete('plan',{}, {},60)
    assert 'x-opencode-session' not in seen[-1].headers


@pytest.mark.parametrize('base,model_id,expected',[
    ('https://opencode.ai/inference/go/openai/v1','deepseek-v4.1-flash',65536),
    ('https://moma.cmecloud.cn/v1','qwen/deepseek-v4-pro',24000),
])
def test_directing_registry_isolates_provider_output_allowance(monkeypatch,base,model_id,expected):
    from my_legend_production.production.registry import handlers
    monkeypatch.setenv('MY_LEGEND_PRODUCTION_MODULES','spine_direct')
    monkeypatch.setenv('MY_LEGEND_VISUAL_MODEL_BASE_URL',base)
    monkeypatch.setenv('MY_LEGEND_VISUAL_MODEL_ID',model_id)
    monkeypatch.setenv('MY_LEGEND_VISUAL_MODEL_KEY','test-key')
    model=handlers(None)['spine_direct'].model
    try:
        assert model.max_tokens==expected
        assert model.read_timeout==600 and model.thinking is True
    finally:model.client.close()
