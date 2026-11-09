"""Reject a verbatim revision before spending on annotation and sound generation."""
import json

from drama_engine.llm.usage_context import usage_scope
from .repository import ProductionError


def line_signature(scenes, key):
    return [(line.get('kind'), line.get('speaker_role_id'), ''.join(line.get('text', '').split()))
            for scene in scenes for line in scene.get(key, [])]


def unchanged_revision(original, delivery):
    def signature(value):
        return [line_signature(ep.get('scenes', []), 'utterances') for ep in value.get('episodes', [])]
    return signature(original) == signature(delivery)


class RevisionDraftPolicy:
    def __init__(self, provider, record):
        self.provider, self.record = provider, record

    def __call__(self, draft, *, creative_context, schema, writer_spec, **kwargs):
        task = json.loads(creative_context)
        if task.get('operation_request', {}).get('intent') != 'revise':
            return draft
        source = (task.get('source') or {}).get('source_content_snapshot', {})
        episodes = source.get('delivery', {}).get('episodes', source.get('episodes', []))
        original = next((ep for ep in episodes if ep['index'] == task['episode_outline']['index']), None)
        if original is None:
            raise ProductionError('STORY_REVISION_SOURCE_MISSING')
        signature = line_signature(original['scenes'], 'utterances')
        if line_signature(draft.model_dump(mode='json')['scenes'], 'lines') != signature:
            return draft
        self.record(dict(check='revision_text_changed', passed=False, repair='one_bounded_attempt'))
        task['rejected_draft'] = draft.model_dump(mode='json')
        task['instruction'] = ('上一稿逐行照搬了原作，没有执行修改。请根据 operation_request 中的修改要求及获批新大纲，'
                               '在本集正文中落实修改；不能仅改摘要、标题或表演注释。返回完整本集的新稿。')
        with usage_scope(stage='story_generation', node='revision_noop_repair', attempt_kind='production_draft_repair'):
            revised = self.provider.complete_structured(writer_spec,
                '你是剧本修改执行器。原作是待修改素材，按本次修改要求交付正文，保留未涉及的人物事实。只返回指定 JSON。',
                json.dumps(task, ensure_ascii=False), schema)
        if line_signature(revised.model_dump(mode='json')['scenes'], 'lines') == signature:
            raise ProductionError('STORY_REVISION_UNCHANGED')
        self.record(dict(check='revision_text_changed', passed=True, semantic_review='not_checked'))
        return revised
