"""Actual settled usage frees unused capacity without releasing unknown intent."""
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_production_queue import env
from test_production_contracts import production_request
from my_legend_production.production.repository import ProductionError

def stage_with_budget(env):
    repo,_=env
    request=production_request(outputs=['script'])
    request.budget.max_provider_calls=10
    jid=repo.create('alice',request)
    return repo,jid,repo.claim('test-budget')

def test_settled_actual_usage_frees_capacity_without_rewriting_audit(env):
    repo,jid,stage=stage_with_budget(env)
    repo.reserve_call(stage,'first','a',8)
    repo.complete_call(stage,'first',{'calls':2},2)
    repo.reserve_call(stage,'second','b',8)
    with pytest.raises(ProductionError,match='CALL_BUDGET_EXCEEDED'):repo.reserve_call(stage,'extra','c',1)
    job=repo.get(jid,'alice')
    assert job['actual_calls']==2 and job['reserved_calls']==16
    assert job['request']['budget']['max_provider_calls']==10
    assert repo.reserve_call(stage,'first','a',8)['state']=='completed'

@pytest.mark.parametrize('unknown',[False,True])
def test_pending_and_unknown_usage_keep_full_reservation(env,unknown):
    repo,jid,stage=stage_with_budget(env)
    repo.reserve_call(stage,'first','a',8)
    repo.observe_call(stage,'first',1,unknown=unknown)
    with pytest.raises(ProductionError,match='CALL_BUDGET_EXCEEDED'):repo.reserve_call(stage,'second','b',3)
    assert repo.get(jid,'alice')['actual_calls']==1

def test_concurrent_reservations_cannot_double_spend_released_capacity(env):
    repo,jid,stage=stage_with_budget(env)
    repo.reserve_call(stage,'first','a',8);repo.complete_call(stage,'first',{},2)
    def reserve(key):
        try:repo.reserve_call(stage,key,key,6);return True
        except ProductionError as exc:
            assert exc.code=='CALL_BUDGET_EXCEEDED';return False
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(reserve,['second','third']))
    assert sum(results)==1

def test_zero_submission_rearm_must_fit_remaining_budget(env):
    from my_legend_production.production.contracts import StageResult
    repo,_=env;request=production_request(outputs=['audio']);request.budget.max_provider_calls=10
    jid=repo.create('alice',request)
    # Queue-only fixture: mark its dependency done, without invoking any provider.
    with repo.transaction() as db:db.execute("UPDATE stages SET status='succeeded' WHERE job_id=%s AND name='story'",(jid,))
    stage=repo.claim('audio-budget',stage_names=['audio'])
    repo.reserve_call(stage,'audio:generation-budget','audio',8)
    repo.complete_call(stage,'audio:generation-budget',{'provider_calls':0,'preflight_failed':True},0)
    repo.reserve_call(stage,'other','other',8);repo.complete_call(stage,'other',{},8)
    repo.finish(stage,StageResult(status='failed',error_code='AUDIO_GENERATION_OR_CHECK_FAILED'))
    with pytest.raises(ProductionError,match='CALL_BUDGET_EXCEEDED'):repo.resume(jid,'alice',1,{})
    assert repo.get(jid,'alice')['unsettled_calls']==0
