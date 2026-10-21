from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from my_legend_production.production.audio_readiness import (
    AudioDraftAudit, AudioDraftPolicy, audio_audit_issues, bound_audio_audit_schema, audio_repair_budget)
from my_legend_production.production.repository import ProductionError


IDEA = '两位朋友解开误会，包含少量旁白。'


def sample(narration=False):
    lines = [{'kind': 'action', 'text': '甲坐下，打开木盒。'},
             {'kind': 'dialogue', 'speaker_role_id': 'a', 'text': '我明白你的意思了，我们一起把事情说清楚。' * 15},
             {'kind': 'sfx', 'text': '木盒轻轻合上的声音。'}]
    if narration:
        lines.append({'kind': 'narration', 'text': '两人终于解开了误会。'})
    return {'title': '木盒', 'synopsis': '解开误会', 'episode_summary': '重归于好',
        'scenes': [{'title': '屋内', 'location': '小屋', 'dramatic_goal': '和解', 'lines': lines}]}


def review(draft, idea=IDEA, require_narration=True):
    return {'requirements': [{'request_quote': idea, 'satisfied': True, 'reason': '符合原始要求',
        'event_kind': 'narration' if require_narration else None, 'min_events': 1 if require_narration else 0,
        'max_events': None}], 'approved_outline_preserved': True, 'outline_reason': '保留获批事实',
        'plot_coherent_and_complete': True, 'plot_reason': '误会解除有结局',
        'lines': [{'scene_index': si, 'line_index': li, 'quote': line['text'], 'problems': [], 'reason': '合理',
                   'sound': {'classification': 'audible_event', 'audible_quote': line['text'], 'reason': '可听声源'}
                            if line['kind'] == 'sfx' else None}
            for si, sc in enumerate(draft['scenes'], 1) for li, line in enumerate(sc['lines'], 1)]}


def test_missing_narration_cannot_pass_with_false_satisfied_claim():
    draft = sample()
    audit = AudioDraftAudit.model_validate(review(draft))
    assert any('REQUIRED_EVENT_COUNT:narration:actual=0' in v for v in audio_audit_issues(draft, audit, IDEA))


@pytest.mark.parametrize('kind', ['none_requested', 'explicitly_forbidden'])
def test_does_not_force_narration_or_restrict_normal_actions(kind):
    draft = sample()
    idea = '两位朋友坐下，打开盒子后解开误会。' if kind == 'none_requested' else '不要旁白。'
    raw = review(draft, idea, False)
    if kind == 'explicitly_forbidden':
        raw['requirements'][0].update(event_kind='narration', max_events=0)
    assert audio_audit_issues(draft, AudioDraftAudit.model_validate(raw), idea) == []


@pytest.mark.parametrize('defect', ['missing_line', 'reordered', 'bad_quote', 'invented_request',
                                  'duplicate_requirement', 'bad_kind', 'invalid_bounds'])
def test_malformed_reviews_rejected_before_content_repair(defect):
    draft = sample(True)
    raw = review(draft)
    if defect == 'missing_line': raw['lines'].pop()
    if defect == 'reordered': raw['lines'].reverse()
    if defect == 'bad_quote': raw['lines'][0]['quote'] = '编造的原文'
    if defect == 'invented_request': raw['requirements'][0]['request_quote'] = '必须站立不动'
    if defect == 'duplicate_requirement': raw['requirements'].append(deepcopy(raw['requirements'][0]))
    if defect == 'bad_kind': raw['lines'][0]['problems'] = ['NON_ACOUSTIC_SFX']
    if defect == 'invalid_bounds': raw['requirements'][0]['max_events'] = 0
    expected = 'too_short' if defect == 'missing_line' else 'STORY_AUDIO_REVIEW_INVALID'
    with pytest.raises(ValidationError, match=expected):
        bound_audio_audit_schema(draft, IDEA).model_validate(raw)


def test_literary_sfx_is_reported_but_same_text_in_narration_is_legal():
    draft = sample(True)
    draft['scenes'][0]['lines'][2]['text'] = '空气里有泥土气息，心情渐渐舒展。'
    raw = review(draft)
    raw['lines'][2].update(problems=['NON_ACOUSTIC_SFX'], reason='气味和内心不是可听声源')
    assert any('NON_ACOUSTIC_SFX' in x for x in audio_audit_issues(draft, AudioDraftAudit.model_validate(raw), IDEA))
    draft['scenes'][0]['lines'][2]['kind'] = 'narration'
    assert audio_audit_issues(draft, AudioDraftAudit.model_validate(review(draft)), IDEA) == []


def run_policy(responses, draft=None):
    from drama_engine.workflow.adapters import DraftEpisode
    from drama_engine.llm.usage_context import current_usage_context
    records, calls = [], []

    class Provider:
        def complete_structured(self, spec, system, user, schema):
            calls.append((current_usage_context()['attempt_kind'], json.loads(user), system))
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return schema.model_validate(result)

    original = DraftEpisode.model_validate(draft or sample())
    policy = AudioDraftPolicy(Provider(), {'source': {'idea': IDEA}}, records.append)
    def execute():
        return policy(original, creative_context='{}', schema=DraftEpisode, writer_spec=None,
            review_spec=None, approved_roles={'a'}, target_seconds=90)
    return execute, records, calls, original


def test_bounded_repair_is_reaudited_and_original_is_preserved():
    bad, good = sample(), sample(True)
    execute, records, calls, original = run_policy([review(bad), {'replacements': [], 'additions': [{'scene_index': 1, 'after_line_index': 3, 'line': good['scenes'][0]['lines'][3]}]}, review(good)])
    before = original.model_dump(mode='json')
    assert any(line.kind == 'narration' for line in execute().scenes[0].lines)
    assert original.model_dump(mode='json') == before
    assert [c[0] for c in calls] == ['production_draft_review', 'production_draft_repair', 'production_draft_review']
    assert calls[-1][1]['original_draft'] == before
    assert all('capabilities' not in c[1] for c in calls)
    assert records[0]['issue_count'] == 1 and records[1]['issue_count'] == 0
    budget = calls[1][1]['spoken_character_budget']
    assert budget['added_narration_uses_same_total_budget'] is True
    assert sum(x['dialogue_chars'] + x['narration_chars'] for x in budget['per_scene_current']) == budget['current']


def test_narration_addition_has_no_separate_budget_and_existing_dialogue_can_be_rebalanced():
    from drama_engine.workflow.adapters import DraftEpisode
    from my_legend_production.production.length_policy import repair_schema
    draft = sample()
    draft['scenes'][0]['lines'][1]['text'] = '字' * 359
    budget = audio_repair_budget(DraftEpisode.model_validate(draft), 90)
    assert budget['remaining_to_max'] == 145 and budget['reduce_to_ideal'] == 44
    assert budget['current'] == 359 and budget['ideal'] == 315
    draft['scenes'][0]['lines'].append({'kind': 'narration', 'text': '字' * 24})
    schema = repair_schema(DraftEpisode, 90, {'a'})
    assert audio_repair_budget(schema.model_validate(draft), 90)['current'] == 383
    draft['scenes'][0]['lines'][1]['text'] = '字' * 500
    with pytest.raises(ValidationError, match='STORY_DRAFT_REPAIR_CONTRACT_INVALID'):
        schema.model_validate(draft)
    draft['scenes'][0]['lines'][1]['text'] = '字' * 291
    balanced = schema.model_validate(draft)
    assert audio_repair_budget(balanced, 90)['current'] == 315


def test_failed_repair_stops_before_annotation_or_audio():
    draft = sample()
    execute, records, calls, _ = run_policy([review(draft), {'replacements': [{'scene_index': 1, 'line_index': 2, 'original_text': draft['scenes'][0]['lines'][1]['text'], 'line': draft['scenes'][0]['lines'][1]}], 'additions': []}, review(draft)])
    with pytest.raises(ProductionError, match='STORY_AUDIO_READINESS_FAILED'):
        execute()
    assert len(calls) == 3 and len(records) == 2


def test_provider_failure_is_not_retried_as_a_content_defect():
    failure = ProductionError('EXTERNAL_RESULT_UNKNOWN')
    execute, records, calls, _ = run_policy([failure])
    with pytest.raises(ProductionError, match='EXTERNAL_RESULT_UNKNOWN'):
        execute()
    assert len(calls) == 1 and not records


def test_actual_first_pilot_delivery_missing_narration_regression():
    from pathlib import Path
    root = Path(__file__).resolve().parents[3] / 'runtime/my-legend-production/acceptance/story-audio-20261019-01'
    if not root.exists():
        pytest.skip('local real pilot evidence is not part of the source distribution')
    result = json.loads((root / 'result.json').read_text(encoding='utf-8'))
    ref = next(r for r in result['verified_artifacts'] if r['kind'] == 'StoryDelivery')
    delivery = json.loads((root / 'artifacts' / ref['sha256']).read_bytes())
    draft = {'scenes': [{'lines': sc['utterances']} for ep in delivery['episodes'] for sc in ep['scenes']]}
    idea = json.loads((root / 'request.json').read_text(encoding='utf-8'))['source']['idea']
    audit = review(draft, idea)
    assert len(audit['lines']) == 29
    assert any('REQUIRED_EVENT_COUNT:narration:actual=0' in x
               for x in audio_audit_issues(draft, AudioDraftAudit.model_validate(audit), idea))


@pytest.mark.parametrize('outputs', [['script'], ['script', 'audio']])
@pytest.mark.parametrize('review_policy', ['standard', 'listen_first'])
def test_real_worker_selects_nonvisual_policy_without_loading_visual_catalog(monkeypatch, outputs, review_policy):
    import sqlalchemy.orm
    from my_legend_production.production import cli, story_guard, visual_catalog
    request = {'source': {'idea': IDEA}, 'outputs': outputs, 'review_policy': review_policy}
    binding = {'job_id': 'test-job', 'request_hash': 'bound-hash'}
    native_request = {'client_context': {'production': binding}}

    class Session:
        def __init__(self, *args): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def query(self, *args): return self
        def filter_by(self, **kwargs): return self
        def one(self): return SimpleNamespace(approved_plan_id='plan', request_snapshot=native_request)

    class Database(Session):
        def execute(self, *args): return self
        def fetchone(self): return {'request': request, 'request_hash': 'bound-hash'}
    class Repository:
        transaction = Database
        def event(self, *args): pass

    class Provider:
        def complete(self, *args): return 'ok'

    class Base:
        engine = None
        def provider(self, *args): return Provider()

    def forbidden(*args): raise AssertionError('visual catalog loaded for audio-only job')
    monkeypatch.setattr(sqlalchemy.orm, 'Session', Session)
    monkeypatch.setattr(cli, 'services', lambda: (Repository(), None))
    monkeypatch.setattr(visual_catalog, 'catalog_from_paths', forbidden)
    provider = story_guard.production_worker_class(Base)().provider('job', 'native-job', 'token')
    if review_policy == 'listen_first':
        assert provider.production_review_policy == 'listen_first'
        assert not hasattr(provider, 'production_draft_policy')
    else:
        assert isinstance(provider.production_draft_policy, AudioDraftPolicy)
    assert provider.production_duration_bounds == (.3, 1.6)


@pytest.mark.parametrize('code', ['STORY_AUDIO_REVIEW_INVALID', 'STORY_AUDIO_READINESS_FAILED'])
def test_public_failure_preserves_safe_policy_code(code):
    from my_legend_production.production.story import StoryStage
    assert StoryStage.failed({}, {'error_code': 'WORKFLOW_GENERATION_FAILED', 'error_message': code}).error_code == code
