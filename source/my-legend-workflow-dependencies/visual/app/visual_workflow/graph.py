"""可嵌入主图的子图：结构化数据优先，暂停绝不冒充交付成功。"""
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from .contracts import Decision
from .planner import VisualError, digest
from .source_adapter import apply_context_answers
from .input_pipeline import prepare_request, compile_prepared

class State(TypedDict, total=False):
    request: dict
    status: str
    normalized_script: dict | None
    source_snapshot: dict
    missing_requirements: list
    source_warnings: list
    degradations: list
    generation_issues: list
    plan: dict
    preview_ref: str
    review_id: str
    result: dict
    error: dict

def build_graph(repository, checkpointer=None):
    """不传 checkpointer 时由父图继承；独立运行必须传持久化保存器。"""
    def normalize(state):
        try:
            prepared = prepare_request(state['request'])
            # 人工补充外观后保留最初收到的源快照，补充结果另存入 request。
            prepared['source_snapshot'] = state.get('source_snapshot') or prepared['source_snapshot']
            return {**prepared, 'status':'normalized'}
        except VisualError as e:
            return {'status':'failed', 'error':e.issue}

    def context(state):
        if state['request']['context_confirmed'] and not state.get('missing_requirements'):
            return {'status': 'context_confirmed'}
        review_id = digest(state['request'])
        answer = interrupt({'status': 'awaiting_context', 'review_id': review_id,
            'expected_plan_hash': review_id,
            'missing_requirements':state.get('missing_requirements', []),
            'question': '请确认输入的通用人物外观、背景、初始在场状态及节选衔接符合原剧本；基础动作之外按输入约束显示原行动说明旁白。'})
        decision = Decision.model_validate(answer)
        if decision.review_id != review_id or decision.expected_plan_hash != review_id:
            raise VisualError('STALE_REVIEW', '确认版本不匹配')
        if decision.decision == 'reject':
            return {'status': 'rejected'}
        request = state['request']
        if state.get('missing_requirements'):
            request = apply_context_answers(request, decision.answers, state['missing_requirements'])
        request = dict(request, context_confirmed=True, context_notes=decision.notes)
        return {'request': request, 'status': 'context_confirmed'}

    def compile_node(state):
        try:
            prepared = {k:state.get(k, [] if k in ('source_warnings','missing_requirements','degradations','generation_issues') else None)
                for k in ('request','normalized_script','source_snapshot','source_warnings','missing_requirements','degradations','generation_issues')}
            if prepared['source_snapshot'] is None:
                prepared['source_snapshot'] = state['normalized_script']
            plan = compile_prepared(prepared)
            return {'plan':plan, 'normalized_script':plan['document'], 'status':'compiled'}
        except VisualError as e:
            return {'status':'failed', 'error':e.issue}

    def preview(state):
        try:
            ref = repository.preview(state['plan'])
            return {'preview_ref': ref, 'review_id': state['plan']['plan_hash'], 'status': 'awaiting_review'}
        except OSError as e:
            return {'status': 'failed', 'error': {'code': 'PREVIEW_FAILED', 'message': str(e)}}

    def review(state):
        # 中断节点恢复时从头执行；此前只计算提示，不写文件、不调用模型。
        answer = interrupt({'status': 'awaiting_review', 'review_id': state['review_id'],
            'expected_plan_hash': state['plan']['plan_hash'], 'preview_ref': state['preview_ref'],
            'degradations':state['plan'].get('degradations', []),
            'warnings': state['plan']['validation_report']['warnings']})
        decision = Decision.model_validate(answer)
        if decision.review_id != state['review_id'] or decision.expected_plan_hash != state['plan']['plan_hash']:
            raise VisualError('STALE_REVIEW', '预览审核版本不匹配')
        return {'status': 'approved' if decision.decision == 'approve' else 'rejected'}

    def deliver(state):
        try:
            result = repository.freeze(state['request'], state['plan'])
            return {'status': 'succeeded', 'result': {**result, 'preview_ref': state['preview_ref'],
                'target_profile': state['request']['target_profile'], 'timing_mode': 'visual_estimated', 'generation_issues':state['plan'].get('generation_issues', []),
                'review_status':'not_required' if state['request'].get('generation_mode')=='automatic' else 'approved'}}
        except (VisualError, OSError) as e:
            return {'status': 'failed', 'error': e.issue if isinstance(e, VisualError) else
                    {'code': 'DELIVERY_FAILED', 'message': str(e)}}

    graph = StateGraph(State)
    for name, fn in [('normalize', normalize), ('context_review', context), ('compile_plan', compile_node),
                     ('build_preview', preview), ('preview_review', review), ('deliver', deliver)]:
        graph.add_node(name, fn)
    graph.add_edge(START, 'normalize')
    def next_if_ok(next_node):
        return lambda s: END if s['status'] in ('failed', 'rejected') else next_node
    graph.add_conditional_edges('normalize', lambda s: END if s['status']=='failed' else
        ('context_review' if s.get('missing_requirements') else 'compile_plan'))
    graph.add_conditional_edges('compile_plan', lambda s: END if s['status']=='failed' else ('build_preview' if s['request'].get('generation_mode')=='automatic' else 'context_review'))
    graph.add_conditional_edges('context_review', lambda s: END if s['status'] in ('failed','rejected') else
        ('normalize' if s.get('missing_requirements') else 'build_preview'))
    graph.add_conditional_edges('build_preview', lambda s: END if s['status']=='failed' else ('deliver' if s['request'].get('generation_mode')=='automatic' else 'preview_review'))
    graph.add_conditional_edges('preview_review', next_if_ok('deliver'))
    graph.add_edge('deliver', END)
    return graph.compile(checkpointer=checkpointer)
