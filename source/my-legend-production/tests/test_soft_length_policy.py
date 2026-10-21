from copy import deepcopy
import json
from pathlib import Path

import pytest

from my_legend_production.production.length_policy import spoken_budget, contract_errors, repair_schema, INSTRUCTION_LIMITS


@pytest.mark.parametrize('count,warning,blocked', [(315, False, False), (383, False, False),
    (400, False, False), (450, True, False), (504, True, False), (505, True, True), (188, True, False), (151, True, False), (94, True, True), (95, True, False)])
def test_story_soft_range_does_not_trigger_hard_repair(count, warning, blocked):
    from drama_engine.workflow.adapters import DraftEpisode
    from test_audio_readiness import sample
    raw = sample()
    raw['scenes'][0]['lines'][1]['text'] = '字' * count
    draft = DraftEpisode.model_validate(raw)
    budget = spoken_budget(draft, 90)
    assert budget['soft_warning'] == warning and budget['hard_violation'] == blocked
    assert bool(contract_errors(draft, 90, {'a'})[1]) == blocked
    if not blocked:
        assert repair_schema(DraftEpisode, 90, {'a'}).model_validate(raw).scenes[0].lines[1].text == '字' * count


def test_soft_length_never_relaxes_unapproved_speaker():
    from drama_engine.workflow.adapters import DraftEpisode, draft_episode_contract_errors
    from test_audio_readiness import sample
    raw = sample()
    raw['scenes'][0]['lines'][1].update(text='字' * 400, speaker_role_id='unapproved')
    draft = DraftEpisode.model_validate(raw)
    assert any('unapproved dialogue roles' in x for x in contract_errors(draft, 90, {'a'})[1])
    assert any('duration estimate' in x for x in draft_episode_contract_errors(draft, 90, {'a'})[1])


def test_soft_warning_does_not_spend_model_repair():
    from test_audio_readiness import sample, review, run_policy
    raw = sample(True)
    raw['scenes'][0]['lines'][1]['text'] = '字' * 440
    execute, records, calls, original = run_policy([review(raw)], draft=raw)
    assert execute() == original
    assert len(calls) == 1 and calls[0][0] == 'production_draft_review'
    assert records[0]['length_assessment']['soft_warning']


def test_native_executor_does_not_reject_soft_warning_after_policy(monkeypatch):
    import importlib.util
    from drama_engine.workflow.adapters import ApprovedPlanLLMExecutor
    tests = Path(__file__).resolve().parents[2] / 'StoryGenerate-0dec8480e3c1b01af66407ff063f825a3fd5dedb/tests/unit'
    monkeypatch.syspath_prepend(str(tests))
    spec = importlib.util.spec_from_file_location('_soft_native_fixture', tests / 'test_story_operations.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app, provider = module.setup()
    plan = app.prepare_story_plan(module.req(creation_preferences={'target_episodes': 1, 'target_duration_sec': 90}))
    job = app.approve_story_plan(plan.plan_id, plan.plan_revision, plan.plan_fingerprint, 'soft-range-hook')
    provider.production_duration_bounds = (.6, 1.6)
    def policy(draft, **kwargs):
        from drama_engine.workflow.adapters import DraftEpisode
        raw = draft.model_dump(mode='json')
        spoken = [line for s in raw['scenes'] for line in s['lines'] if line['kind'] in ('dialogue','narration')]
        spoken[0]['text'] = '字' * (450 - len(spoken) + 1)
        for line in spoken[1:]: line['text'] = '字'
        return DraftEpisode.model_validate(raw)
    provider.production_draft_policy = policy
    class AnnotationReached(Exception): pass
    class Annotator:
        def annotate(self, *args): raise AnnotationReached()
    with pytest.raises(AnnotationReached):
        ApprovedPlanLLMExecutor(provider, Annotator()).execute(job.input_snapshot)


def test_native_tts_limits_propagate_and_long_instruction_is_not_cut(tmp_path, monkeypatch):
    pytest.importorskip('soundfile')
    from my_legend_production.performance_production import prepare_audio, read
    from my_legend_production.core import weighted_length
    workspace = Path(__file__).resolve().parents[3]
    paths = read(workspace / 'runtime/my-legend-production/workflows.json')
    root = workspace / 'source/my-legend-production/examples/source'
    delivery, manifest = read(root / 'story_delivery.json'), read(root / 'render_manifest.json')
    line = next(u for ep in delivery['episodes'] for sc in ep['scenes'] for u in sc['utterances'] if u['kind'] == 'dialogue')
    line['delivery_note'] = '轻声表达。' * 45
    next(t for t in manifest['tasks'] if t['line_id'] == line['line_id'])['performance']['delivery_note'] = line['delivery_note']
    before = deepcopy(delivery)
    voices = {u['speaker_role_id'] if u['kind'] == 'dialogue' else u['narrator_id']: 'longanyang_v3.1'
              for ep in delivery['episodes'] for sc in ep['scenes'] for u in sc['utterances']
              if u['kind'] in ('dialogue', 'narration')}
    _, _, batches = prepare_audio(tmp_path, delivery, manifest, voices, paths,
                                  instruction_limits=INSTRUCTION_LIMITS)
    assert delivery == before
    report = read(tmp_path / 'input/instruction-length-assessment.json')
    assert report['warnings'] and report['warnings'][0]['action'] == 'continue_without_rewrite'
    events = [e for b in batches for e in b['audio_script']['events'] if e['kind'] == 'speech']
    expanded = next(e for e in events if line['delivery_note'] in e['performance']['instructions'])
    assert 360 < weighted_length(expanded['performance']['instructions']) <= 600
    monkeypatch.syspath_prepend(str(Path(paths['audio_root']) / 'src'))
    from lingban_pipeline.instructions import policy, adapter_instruction
    for batch in batches:
        config = read(Path(batch['path']) / 'config.json')
        assert policy(config)['max_weight'] == 600
        assert adapter_instruction(expanded['performance']['instructions'], config['tts'],
            {'instruction_max_weight': policy(config)['max_weight']}) == expanded['performance']['instructions']
