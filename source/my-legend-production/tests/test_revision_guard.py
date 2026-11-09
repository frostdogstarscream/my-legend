import json
from copy import deepcopy
from types import SimpleNamespace
import pytest
from my_legend_production.production.revision_guard import RevisionDraftPolicy, unchanged_revision
from my_legend_production.production.repository import ProductionError


def draft(text):
    value={'scenes':[{'lines':[{'kind':'dialogue','speaker_role_id':'a','text':text}]}]}
    return SimpleNamespace(model_dump=lambda **_:deepcopy(value))


def context(intent='revise'):
    return json.dumps({'operation_request':{'intent':intent},'episode_outline':{'index':1},
        'source':{'source_content_snapshot':{'delivery':{'episodes':[{'index':1,'scenes':[{'utterances':[
            {'kind':'dialogue','speaker_role_id':'a','text':'原句'}]}]}]}}}})


@pytest.mark.parametrize('replacement,fail',[('新句',False),('原句',True)])
def test_noop_gets_one_bounded_repair(replacement,fail):
    calls=[];evidence=[]
    def complete(*args):calls.append(args);return draft(replacement)
    policy=RevisionDraftPolicy(SimpleNamespace(complete_structured=complete),evidence.append)
    if fail:
        with pytest.raises(ProductionError,match='STORY_REVISION_UNCHANGED'):
            policy(draft('原句'),creative_context=context(),schema=object,writer_spec=object)
    else:
        result=policy(draft('原句'),creative_context=context(),schema=object,writer_spec=object)
        assert result.model_dump()['scenes'][0]['lines'][0]['text']=='新句'
    assert len(calls)==1


@pytest.mark.parametrize('intent,text',[('create','原句'),('revise','新句')])
def test_changed_draft_and_new_creation_make_no_extra_call(intent,text):
    def forbidden(*args):raise AssertionError('unexpected paid call')
    policy=RevisionDraftPolicy(SimpleNamespace(complete_structured=forbidden),lambda _:None)
    value=draft(text)
    assert policy(value,creative_context=context(intent),schema=object,writer_spec=object) is value


def test_metadata_changes_do_not_count_as_script_edit():
    original={'episodes':[{'scenes':[{'utterances':[{'kind':'dialogue','text':'原句'}]}]}]}
    changed=deepcopy(original);changed.update(story_version_id='new',title='新标题')
    assert unchanged_revision(original,changed)
    changed['episodes'][0]['scenes'][0]['utterances'][0]['text']='新句'
    assert not unchanged_revision(original,changed)
