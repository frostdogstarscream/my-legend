import type { IndependentJob, IndependentRequest, StoryIntent, StoryOperationSource } from '../types/production'

export const storyIntentLabels:Record<StoryIntent,string>={revise:'修改剧本',continue:'续写下一章',remix:'改编故事'}
export function storyOperationRequest(job:IndependentJob, key:string, intent:StoryIntent, instruction:string,
  retain:string, transform:string):IndependentRequest {
  const delivery=job.artifacts.find(a=>a.kind==='StoryDelivery'),manifest=job.artifacts.find(a=>a.kind==='RenderManifest')
  if(!delivery||!manifest||delivery.source_version_id!==manifest.source_version_id)throw new Error('当前剧本版本尚未准备好')
  if(!instruction.trim())throw new Error('请填写创作要求')
  const source:StoryOperationSource={kind:'operation',intent,source_version_id:delivery.source_version_id,delivery,manifest,
    instruction:instruction.trim(),target_duration_seconds:90}
  if(intent==='revise')source.edit={scope:'whole_story',target:{},downstream_policy:'replan_affected'}
  if(intent==='remix'){
    if(!retain.trim()||!transform.trim())throw new Error('请填写保留元素和改编方向')
    source.remix={retain_elements:[retain.trim()],transform_elements:[transform.trim()],relation_to_source:'adaptation'}
  }
  return {idempotency_key:key,...(intent!=='remix'?{work_id:job.work_id}:{}),source,outputs:['script','audio'],
    mode:'automatic',review_policy:'listen_first',sound_workflow:'sound_bridge/1',audio_delivery_policy:'audio_first',
    visual_profile:'pixel_performance/1'}
}
