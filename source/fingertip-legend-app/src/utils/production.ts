import type { IndependentJob, JobStatus } from '../types/production'

export const jobStatus:Record<JobStatus,string>={queued:'排队中',running:'制作中',needs_input:'需要处理',succeeded:'制作完成',failed:'部分制作未完成',cancelling:'正在取消',cancelled:'已取消'}
export const stageNames:Record<string,string>={story:'生成剧本',sound_plan:'声音制作准备',audio:'制作广播剧',visual_plan:'规划视觉表演',visual_render:'执行视觉表演',integrate:'合成视频'}
export function jobTitle(job:IndependentJob){const source=job.request.source;return source.kind==='creative'?source.title:source.kind==='operation'?({revise:'修改剧本',continue:'续写下一章',remix:'改编故事'}[source.intent]):'从已有剧本继续制作'}
export function jobActive(job:IndependentJob){return ['queued','running','cancelling'].includes(job.status)}
export function stageStatus(job:IndependentJob,status:JobStatus){
  if(status==='queued'&&job.status==='failed')return '等待上游修复'
  if(status==='queued'&&job.status==='needs_input')return '等待处理'
  if(status==='queued'&&['cancelled','cancelling'].includes(job.status))return '未继续制作'
  return jobStatus[status]
}
export function progressNote(job:IndependentJob){
  if(['queued','running'].includes(job.status))return '离开页面后后台会继续制作。已完成的内容可先阅读、收听或观看。'
  if(job.status==='cancelling')return '正在停止后续制作，已完成的结果会保留。'
  if(job.status==='succeeded')return '制作已结束，可以阅读、收听或观看已完成的内容。'
  return '本次制作已停止或等待处理，已完成的内容仍可使用。'
}
export function needsReconciliation(job:IndependentJob){return job.unknown_calls===undefined?job.error_code==='EXTERNAL_RESULT_UNKNOWN':job.unknown_calls>0}
export function requiresPlanRevision(code:string|null){
  return !!code&&(code.startsWith('ARRANGEMENT_')||['AUDIO_CHOICES_REPAIR_FAILED','AUDIO_ARRANGEMENT_SCHEMA_INVALID',
    'SOUND_BRIDGE_CLASSIFICATION_FAILED','SOUND_BRIDGE_PACKAGE_INVALID',
    'STORY_DRAFT_REPAIR_CONTRACT_INVALID','STORY_AUDIO_READINESS_FAILED','AUDIO_NEW_REVISION_REQUIRED'].includes(code))
}
export function productionError(code:string|null){
  if(!code)return ''
  const messages:Record<string,string>={
    OUTLINE_REVIEW_REQUIRED:'大纲已准备好，请阅读并确认。', EXACT_OUTLINE_DECISION_REQUIRED:'大纲版本已变化，请刷新后确认。',
    EXTERNAL_RESULT_UNKNOWN:'供应商的生成结果尚未确认，需要完成对账后才能继续，避免重复扣费。',
    VISUAL_ACTION_UNSUPPORTED:'剧本中的部分动作超出当前视觉能力。已完成的剧本和声音仍可使用。',
    VISUAL_SEMANTIC_REVIEW_FAILED:'视觉方案未通过剧情一致性检查，已有结果保留。',
    VISUAL_EXECUTION_PREFLIGHT_FAILED:'视觉动作未通过执行前检查，已有剧本和声音仍可使用。',
    VISUAL_ACTION_NO_DISPLACEMENT:'部分移动动作未产生实际位移，本轮已停止，已有结果保留。',
    VISUAL_EXECUTION_VERSION_CHANGED:'视觉执行规则已更新，请从当前剧本建立新的制作任务。',
    VISUAL_ACTION_COVERAGE_MISMATCH:'剧本动作与视觉方案未能对应，需要修复交付内容后继续。已有剧本保留。',
    AUDIO_GENERATION_OR_CHECK_FAILED:'声音制作或校验未通过，已有剧本保留。请查看制作记录后处理。',
    SOUND_BRIDGE_CLASSIFICATION_FAILED:'声音需求未能正确分类，尚未开始配音，已有剧本和问题记录保留。',
    SOUND_BRIDGE_PACKAGE_REQUIRED:'声音制作清单尚未准备完整，尚未开始配音。',
    SOUND_BRIDGE_PACKAGE_INVALID:'声音制作清单与剧本或审核版本不一致，已停止后续制作。',
    AUDIO_NEW_REVISION_REQUIRED:'本次已完成生成并保留素材，不能直接重发同一任务。需要修订制作方案后继续。',
    AUDIO_CHOICES_REPAIR_FAILED:'声音方案在自动修复后仍不符合制作规则，尚未开始配音。已有剧本和问题记录保留。',
    AUDIO_ARRANGEMENT_SCHEMA_INVALID:'声音方案的操作或时间引用不符合规则，已有剧本保留，需要修订方案。',
    ARRANGEMENT_EMPTY_AUDIO_INTERVAL:'声音素材已保留，但部分声音结束时间早于开始时间。自动时序修复未完成，尚无可播放成品。',
    ARRANGEMENT_FADE_EXCEEDS_BACKGROUND:'声音素材已保留，但背景淡出超过实际持续范围，需要修订时序。',
    ARRANGEMENT_SPEECH_OVERLAP_UNSUPPORTED:'声音素材已保留，但对白出现不允许的重叠，需要修订时序。',
    STORY_DRAFT_REPAIR_CONTRACT_INVALID:'剧本修订未通过结构或内容边界检查，尚未开始配音。具体问题已保存到制作记录。',
    STORY_AUDIO_REVIEW_INVALID:'剧本检查结果未能完整对应原文，尚未开始配音，制作记录已保留。',
    STORY_AUDIO_READINESS_FAILED:'剧本自动修订后仍有未解决的内容问题，尚未开始配音。',
    STORY_REQUIRED_CAPABILITY_UNAVAILABLE:'故事中的必要动作超出当前素材或表演能力，需要补齐能力后制作。',
    STORY_PRODUCTION_READINESS_FAILED:'剧本尚未通过动作与连续性检查，本轮已停止，尚未开始配音。',
    STORY_READINESS_REVIEW_INVALID:'剧本审查结果未能正确对应原文，本轮已停止，制作记录已保留。',
    STORY_PLAN_READINESS_FAILED:'大纲仍包含无法制作的动作或场景，本轮已停止，尚未生成正文和配音。',
    STORY_PLAN_REVIEW_INVALID:'大纲审查结果未能正确对应原文，本轮已停止，制作记录已保留。',
    CALL_BUDGET_EXCEEDED:'本次制作已达到调用预算上限，已有结果保留。',
    REPAIR_BUDGET_EXCEEDED:'已达到本次自动修复次数上限，已有结果保留。',
    DEADLINE_EXCEEDED:'本次制作超过时限，已完成的结果保留。',
    MODULE_NOT_CONFIGURED:'所需制作服务暂不可用，请稍后再试。',
  }
  return messages[code]||'此阶段尚未完成，已保存制作记录和可用结果。'
}
export function requestKey(){return 'app-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,12)}
