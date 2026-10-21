"""Use the original durable Worker with an explicit MOMA parameter/budget profile."""
import json
from pydantic import create_model, model_validator

from drama_engine.llm.openai_compat import build_provider_from_env
from drama_engine.service.config import ServiceSettings
from drama_engine.service.worker import DurableWorker
from drama_engine.workflow.adapters import ApprovedPlanLLMExecutor, draft_episode_contract_errors, normalize_draft_stage_directions


def evidence_quality_schema(schema):
    def require_pass_evidence(self):
        for check in self.operation_checks:
            if check.passed and not check.evidence_line_ids:
                raise ValueError(f'PASSED_CHECK_REQUIRES_SOURCE_LINE_IDS:{check.requirement_id}')
        return self
    return create_model('MomaEvidenceQuality', __base__=schema,
                        __validators__={'require_pass_evidence': model_validator(mode='after')(require_pass_evidence)})


def profile_request(spec, system, user):
    """Keep the frozen story facts, but remove conflicting repair execution budgets."""
    extra = dict(spec.extra)
    extra['json_mode'] = True
    extra['provider_params'] = {**extra.get('provider_params', {}), 'thinking': {'type': 'disabled'}}
    if spec.role == 'storycraft_operation_judge':
        system += ('\n每个passed=true的operation_check（包括timeline）必须给出非空evidence_line_ids，'
                   '只能引用输入episodes里的实际line_id；不要用无证据的“通过”。'
                   '无精确钟点时也应引用表现事件先后及同一现场连续性的实际行；无法证明则passed=false并说明原因。'
                   '不得捏造时间主张、引用不存在的行或为了凑证据改变结论。')
    if spec.role == 'storycraft_episode_writer':
        system += '\n所有 lines[].kind 只用 dialogue、narration、sfx、music、action 五种规范值。dialogue/narration 的text只放实际朗读文字。'
        try:
            task = json.loads(user)
            if task.get('repair_type') == 'duration_only_single_scene':
                target = task['replacement_scene_spoken_character_target']
                original = task['original_scene']['lines']
                count = sum(len(line['text']) for line in original if line['kind'] in ('dialogue', 'narration'))
                # The original card carries an obsolete pre-repair allocation. The
                # replacement target is computed from actual text by the engine.
                if task.get('scene_card'):
                    card = dict(task['scene_card'])
                    ratio = card.get('dialogue_character_budget', 0) / max(1, card.get('spoken_character_budget', 0))
                    card.update(spoken_character_budget=target, dialogue_character_budget=round(target * ratio), narration_character_budget=target-round(target * ratio))
                    task['scene_card'] = card
                direction = '扩写' if target > count else '压缩'
                rule = (f'本次必须{direction}本场朗读文本：原场对白+旁白实际共{count}字符，替换后应接近{target}字符（含标点），'
                        f'即净变化{target-count:+d}字符。只计算 dialogue/narration 的text；action/sfx/music不计。'
                        f'可以把朗读内容分配为约{max(2,round(target/22))}句，每句平均22字符，再按人物语气自然调整；不要把动作说明误算为朗读文本。'
                        '在既定行动中让人物通过具体提问、验证与回应推进，保留获批事件，不增加支线、角色、反转，不重复凑字数。'
                        '生成前分配各句字数，输出前自行核对合计，不能只同义改写原场。')
                task['instruction'] += '\n' + rule
                system += '\n' + rule
                user = json.dumps(task, ensure_ascii=False)
                # Arithmetic-heavy repairs use the vendor's reasoning mode. The
                # larger output cap includes its reasoning tokens, not more calls.
                extra['provider_params']['thinking'] = {'type': 'enabled'}
                spec = spec.model_copy(update={'max_tokens': max(spec.max_tokens, 8192)})
        except (ValueError, KeyError, TypeError):
            pass
    return spec.model_copy(update={'extra': extra}), system, user


class MomaStoryExecutor(ApprovedPlanLLMExecutor):
    """At most three local scene revisions; the original final quality gates still apply."""
    def _repair_duration_scene(self, **kwargs):
        for attempt in range(3):
            draft = super()._repair_duration_scene(**kwargs)
            draft = normalize_draft_stage_directions(draft)
            estimated, errors = draft_episode_contract_errors(draft, kwargs['target_duration_sec'], kwargs['approved_role_ids'])
            if not errors or any(not e.startswith('duration estimate') for e in errors):
                return draft
            kwargs = {**kwargs, 'draft': draft, 'observed_sec': estimated}
        return draft


def provider():
    model = build_provider_from_env()
    model.max_retries = 0
    complete = model.complete
    structured = model.complete_structured

    def checked_structure(spec, system, user, schema):
        if spec.role == 'storycraft_operation_judge':
            schema = evidence_quality_schema(schema)
        return structured(spec, system, user, schema)

    def bounded(spec, system, user, deadline=None):
        # One short-scene attempt stays bounded, including the engine's repair calls.
        if len(model.call_history) >= 12:
            raise RuntimeError('LOCAL_STORY_MODEL_CALL_BUDGET_EXCEEDED')
        spec, system, user = profile_request(spec, system, user)
        return complete(spec, system, user, deadline)

    model.complete = bounded
    model.complete_structured = checked_structure
    return model


if __name__ == '__main__':
    settings = ServiceSettings.from_env()
    settings.validate()
    engine = settings.engine()
    from my_legend_production.production.story_guard import production_worker_class
    production_worker_class(DurableWorker)(engine, settings, provider_factory=provider, executor_factory=MomaStoryExecutor).run()
