"""恢复入口先验证评审版本，再提交 Command，防止错误答案进入检查点。"""
from langgraph.types import Command
from .contracts import Decision
from .planner import VisualError
from .source_adapter import apply_context_answers

def resume_graph(graph, config, answer):
    decision = Decision.model_validate(answer).model_dump()
    snapshot = graph.get_state(config)
    pending = [i.value for t in snapshot.tasks for i in t.interrupts]
    if len(pending) != 1:
        raise VisualError('NOT_PAUSED', '任务不存在或没有唯一待审核项')
    if any(decision[k] != pending[0][k] for k in ('review_id', 'expected_plan_hash')):
        raise VisualError('STALE_REVIEW', '审核版本不匹配，未写入恢复答案')
    if decision['decision'] == 'approve' and pending[0].get('missing_requirements'):
        apply_context_answers(snapshot.values['request'], decision['answers'], pending[0]['missing_requirements'])
    elif decision['answers'] is not None:
        raise VisualError('INVALID_REVIEW', '当前审核不接受内容修改，请新建修订')
    return graph.invoke(Command(resume=decision), config)
