"""Parent production policy enforced before each native StoryGenerate model call."""
from .repository import ProductionError


def production_guidance(request):
    """Trusted, versioned production contract; never splice creative text into system policy."""
    outputs = set(request.get('outputs', []))
    parts = ['【我的传说独立生产交付补充规则 v1.5】']
    if outputs and 'visual' not in outputs:
        parts.append('逐项保留原始创作要求与禁止项，尤其是旁白、角色、关键事件与结局。'
                     '明确要求旁白时必须实际交付narration，明确禁止时不得新增；不得仅在摘要中声称包含。'
                     '音效sfx只描述可听的声源、强弱、距离和时序；气味、内心与文学叙述放在合适的叙述或非朗读说明中。'
                     '仅停止动作、停止已有声音或静默的指令放入非朗读action，不当作独立sfx生成；'
                     '确有可听过程的声音衰减和环境声仍可为sfx，不按“停下”等词机械分类。'
                     '不得把表演说明当作对白朗读。人物可以自然行动，本次不施加视觉动作或素材限制。')
        parts.append('朗读字数以目标时长每秒3.5字估算；建议目标的80%至130%，超过建议只提醒，不为轻微偏差反复改稿。'
                     '60%至160%才是本项目的异常保护范围。正文完整、人物一致和结局清楚优先；字符估算不是实测音频时长。')
    if outputs & {'audio','visual'}:
        length_rule = ('建议总权重不超过360，超过360只记录提醒；本地异常保护上限为600。'
                       if 'visual' not in outputs else '总权重不得超过240。')
        parts.append('声音表演注释会合并为单条指令，汉字计2、其他字符计1，'+length_rule+
                     'tone_instruction与delivery_note分别保持简短，避免重复；连同情绪、重音、停顿、语速、音高、音量一并预算。'
                     '重音优先1至2个短语，复杂情绪可以适当增加必要说明，不要求凑满预算。'
                     '不得删改朗读台词来满足指令预算。')
    if 'visual' in outputs:
        actions = ('enter、exit、move、wait' if request.get('visual_profile') == 'standing_performance/1'
                   else 'enter、exit、move、face、wait、nod、shake_head、react')
        parts.append('本次剧本同时交付声音与视觉表演。所有要实际演出的可见角色动作必须以kind=action独立列出，'
                     'text写清唯一施动者、单一动作、对象或目的地和先后关系。人物入场、离场、移动、转向不得只藏在sfx、对白或摘要里。'
                     'kind=sfx仅描述需要听见的声音；如同一事实包含动作与声音，分别交付action与sfx，不能用声音推测代替明确动作。'
                     '旁白与对白保持可听，不能朗读制作说明。当前视觉支持'+actions+'。'
                     '大纲阶段在用户要求范围内选择可执行动作，不额外加入站起、坐下、拥抱、递物、开关门或多人同步肢体动作。'
                     '已批准事实不能为了通过门禁被删除或改成wait；明确要求超出能力时如实保留以便返回能力缺口。'
                     '一条action只包含一个演员和一个动作，先转身再离开应分成两条，等待不能冒充其他动作。'
                     'enter已包含走进并站定，不另拆stop；走向某处但尚未离场是move，不是exit。'
                     'exit后演员不再可见，不能继续执行场内动作；用户未要求画外事件时，将告别对白安排在实际离场前。'
                     '笑声、叹息、脚步等sfx仅表示声音，不自动要求同步嘴形或肢体动作；不能仅因声音在相邻action之前就认定动作遗漏。')
    return '\n'.join(parts) if len(parts)>1 else ''


def authorize_call(repository, binding, phase, attempt_kind):
    repair = attempt_kind in {"json_schema_repair", "targeted_duration_repair", "production_draft_repair",
                              "production_plan_repair", "performance_annotation_repair"}
    with repository.transaction() as db:
        row = db.execute("SELECT *,deadline>now() AS within_deadline FROM jobs WHERE id=%s FOR UPDATE",
                         (binding["job_id"],)).fetchone()
        if not row or row["request_hash"] != binding["request_hash"]:
            raise ProductionError("PRODUCTION_BINDING_INVALID")
        if row["cancel_requested"] or row["status"] not in ("queued", "running"):
            raise ProductionError("PRODUCTION_NOT_ACTIVE")
        if not row["within_deadline"]:
            raise ProductionError("DEADLINE_EXCEEDED")
        budget = db.execute("SELECT * FROM calls WHERE job_id=%s AND call_key=%s",
                            (binding["job_id"], "story:" + phase + "-budget")).fetchone()
        if not budget or budget["state"] != "pending":
            raise ProductionError("STORY_CALL_NOT_RESERVED")
        counts = db.execute("""SELECT count(*) FILTER (WHERE details->>'phase'=%s) AS calls,
            count(*) FILTER (WHERE details->>'repair'='true') AS repairs
            FROM events WHERE job_id=%s AND event_type='story_model_intent'""", (phase, binding["job_id"])).fetchone()
        if counts["calls"] >= budget["reserved_units"]:
            raise ProductionError("CALL_BUDGET_EXCEEDED")
        if repair and counts["repairs"] >= row["request"]["budget"]["max_repairs_per_stage"]:
            raise ProductionError("REPAIR_BUDGET_EXCEEDED")
        repository.event(db, binding["job_id"], "story_model_intent",
                         {"phase": phase, "repair": repair, "attempt_kind": attempt_kind})


def production_worker_class(base):
    class ProductionAwareWorker(base):
        def provider(self, kind, resource_id, token):
            from sqlalchemy.orm import Session
            from drama_engine.persistence.models import ServicePlanTaskModel, StoryJobModel, StoryPlanModel
            from drama_engine.llm.usage_context import current_usage_context
            from .cli import services
            with Session(self.engine) as session:
                if kind == "task":
                    row = session.query(ServicePlanTaskModel).filter_by(task_id=resource_id).one()
                    request = row.payload_json.get("request", {})
                else:
                    row = session.query(StoryJobModel).filter_by(job_id=resource_id).one()
                    plan = session.query(StoryPlanModel).filter_by(plan_id=row.approved_plan_id).one()
                    request = plan.request_snapshot
            binding = request.get("client_context", {}).get("production")
            provider = super().provider(kind, resource_id, token)
            if not binding:
                return provider
            repository, _ = services()
            with repository.transaction() as db:
                parent = db.execute('SELECT request,request_hash FROM jobs WHERE id=%s', (binding['job_id'],)).fetchone()
            if not parent or parent['request_hash'] != binding['request_hash']:
                raise ProductionError('PRODUCTION_BINDING_INVALID')
            guidance = production_guidance(parent['request'])
            complete = provider.complete

            def guarded(spec, system, user, deadline=None):
                authorize_call(repository, binding, "planning" if kind == "task" else "writing",
                               current_usage_context().get("attempt_kind", "initial"))
                return complete(spec, system + ('\n\n'+guidance if guidance else ''), user, deadline)

            provider.complete = guarded
            def record(evidence):
                with repository.transaction() as db:
                    repository.event(db, binding['job_id'],
                        'story_plan_readiness_checked' if kind == 'task' else 'story_readiness_checked', evidence)

            if 'visual' in parent['request'].get('outputs', []):
                import os
                from .story_readiness import ProductionDraftPolicy
                from .plan_readiness import ProductionPlanPolicy
                from .visual_catalog import catalog_from_paths
                catalog = catalog_from_paths(os.environ['MY_LEGEND_WORKFLOW_PATHS'], parent['request']['visual_profile'])

                if kind == 'task':
                    provider.production_plan_policy = ProductionPlanPolicy(provider, catalog, parent['request'], record)
                else:
                    provider.production_draft_policy = ProductionDraftPolicy(provider, catalog, parent['request'], record)
            elif kind != 'task':
                from .audio_readiness import AudioDraftPolicy
                from .length_policy import DURATION_HARD_BOUNDS
                provider.production_duration_bounds = DURATION_HARD_BOUNDS
                if parent['request'].get('review_policy') == 'listen_first':
                    # This flag comes from the bound parent job, never creative text.
                    provider.production_review_policy = 'listen_first'
                    record(dict(review_policy='listen_first', semantic_review='not_checked',
                                human_approval=False, skipped=['audio_readiness', 'story_quality_judge']))
                else:
                    provider.production_draft_policy = AudioDraftPolicy(provider, parent['request'], record)
            return provider
    return ProductionAwareWorker
