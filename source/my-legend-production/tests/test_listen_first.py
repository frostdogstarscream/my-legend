"""Listening delivery skips model approval, but never source/media integrity."""
import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError

from my_legend_production.production.contracts import ArtifactRef, fingerprint
from my_legend_production.production.sound_bridge import validate_package
from test_production_contracts import production_request
from test_sound_bridge import setup_context, finish_bridge, BridgeModel


def listen_context(tmp_path):
    ctx = setup_context(tmp_path)
    ctx.request = production_request(outputs=['audio'], sound_workflow='sound_bridge/1',
        audio_delivery_policy='audio_first', review_policy='listen_first')
    return ctx


def test_plan_delivers_without_classification_or_semantic_review_calls(tmp_path):
    ctx = listen_context(tmp_path)
    # This model rejects semantic reviews if called.
    model = BridgeModel(failure='review')
    bridge, result = finish_bridge(ctx, model)
    assert result.status == 'succeeded'
    assert len(model.calls) == 1 and 'required_events' in model.calls[0]
    refs = {a['kind']: ArtifactRef.model_validate(a) for a in ctx.stage['artifacts']}
    refs.update({a.kind: a for a in result.artifacts})
    package = validate_package(ctx, refs)
    assert package.speech_tasks and package.sound_tasks and package.human_listening == 'pending'
    assert ctx.store.json(refs['AudioArrangementEvidence'])['semantic_review'] == 'not_checked'
    assert bridge(ctx) == result and len(model.calls) == 1
    ctx.request.review_policy = 'standard'
    with pytest.raises(Exception, match='SOUND_BRIDGE_PACKAGE_INVALID'):
        validate_package(ctx, refs)


def test_bad_sound_plan_falls_back_to_complete_speech(tmp_path):
    ctx = listen_context(tmp_path)
    class InvalidPlan(BridgeModel):
        def complete(self, *args, **kwargs):
            self.calls.append({})
            return {'content': '{}'}
    _, result = finish_bridge(ctx, InvalidPlan())
    assert result.status == 'succeeded'
    refs = {a['kind']: ArtifactRef.model_validate(a) for a in ctx.stage['artifacts']}
    refs.update({a.kind: a for a in result.artifacts})
    package = validate_package(ctx, refs)
    delivery = ctx.store.json(refs['StoryDelivery'])
    expected = [u['text'] for ep in delivery['episodes'] for sc in ep['scenes']
                for u in sc['utterances'] if u['kind'] in ('dialogue', 'narration')]
    assert [t.text for t in package.speech_tasks] == expected
    assert not package.sound_tasks


def test_policy_cannot_pause_for_outline_or_enable_visual_and_old_fingerprints_hold():
    for values in [dict(mode='reviewed'), dict(outputs=['visual'])]:
        with pytest.raises(ValidationError, match='LISTEN_FIRST'):
            production_request(review_policy='listen_first', **values)
    old = production_request()
    assert fingerprint(old) == fingerprint(old.model_dump(exclude={'review_policy', 'audio_revision'}))
    assert fingerprint(old) != fingerprint(old.model_copy(update={'review_policy': 'listen_first'}))


def native_fixture(monkeypatch):
    tests = Path(__file__).resolve().parents[2] / 'StoryGenerate-0dec8480e3c1b01af66407ff063f825a3fd5dedb/tests/unit'
    monkeypatch.syspath_prepend(str(tests))
    spec = importlib.util.spec_from_file_location('_listen_native_fixture', tests / 'test_story_operations.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_native_story_skips_quality_judge_but_reports_unreviewed(monkeypatch):
    module = native_fixture(monkeypatch)
    app, provider = module.setup()
    provider.production_review_policy = 'listen_first'
    delivery, _ = module.generate(app, module.req(
        creation_preferences={'target_episodes': 1, 'target_duration_sec': 90}))
    assert delivery.ready_for_playback
    assert not delivery.quality_report.continuity_passed
    assert any('SEMANTIC_REVIEW_SKIPPED' in w for w in delivery.quality_report.warnings)
    assert not any(issubclass(schema, module.QualityDecision) for schema, _ in provider.calls)
    from drama_engine.service.downstream import rendering_manifest
    from my_legend_production.production.story import validate_handoff
    manifest = rendering_manifest(delivery)
    validate_handoff(delivery.model_dump(mode='json'), manifest.model_dump(mode='json'))


def test_listen_policy_does_not_bypass_invalid_spoken_performance(monkeypatch):
    module = native_fixture(monkeypatch)
    app, provider = module.setup()
    provider.production_review_policy = 'listen_first'
    original = app.story_executor.annotator.annotate
    def damaged(*args, **kwargs):
        rows = original(*args, **kwargs)
        rows[0] = rows[0].model_copy(update={'text': ''})
        return rows
    app.story_executor.annotator.annotate = damaged
    with pytest.raises(ValueError):
        module.generate(app, module.req(
            creation_preferences={'target_episodes': 1, 'target_duration_sec': 90}))


def test_durable_worker_wrapper_preserves_policy_through_publication(monkeypatch):
    """Exercise the real wrapper that differs from direct executor unit tests."""
    from types import SimpleNamespace
    from drama_engine.service import worker as native
    module = native_fixture(monkeypatch)
    app, provider = module.setup()
    provider.production_review_policy = 'listen_first'
    executor = app.story_executor
    plan = app.prepare_story_plan(module.req(
        creation_preferences={'target_episodes': 1, 'target_duration_sec': 90}))
    job = app.approve_story_plan(plan.plan_id, plan.plan_revision, plan.plan_fingerprint, 'listen-wrapper-test')

    class Session:
        def commit(self): pass
        def rollback(self): pass
        def query(self, *args): return self
        def filter_by(self, **kwargs): return self
        def populate_existing(self): return self
        def with_for_update(self): return self
        def first(self): return SimpleNamespace(lease_owner='other')

    def service(repo, planner, wrapper):
        app.story_executor = wrapper
        return app
    monkeypatch.setattr(native, 'StoryWorkflowService', service)
    monkeypatch.setattr(native, 'WorkerRepository', lambda *args: None)
    monkeypatch.setattr(native, 'SqlAlchemyWorkflowRepository', lambda session: app.repository)
    worker = SimpleNamespace(provider=lambda *args: provider, executor_factory=lambda p: executor,
        pulse=lambda *args: None, evidence_dir=None, owner='test', settings=SimpleNamespace(heartbeat_seconds=1))
    native.DurableWorker.run_job(worker, Session(), job.job_id)
    saved = app.repository.get_job(job.job_id)
    assert saved.status == 'SUCCEEDED', saved.error_message
    delivery = app.repository.get_story_version(saved.result_story_version_id)
    assert delivery.ready_for_playback and not delivery.quality_report.continuity_passed
