<script setup lang="ts">
import { computed, ref } from 'vue'
import { onLoad, onShow, onHide, onUnload } from '@dcloudio/uni-app'
import FeatureLayout from '../../components/FeatureLayout.vue'
import VoiceRevisionEditor from '../../components/VoiceRevisionEditor.vue'
import { api } from '../../services/api'
import { downloadProductionMedia, readProductionArtifact, releaseProductionMedia, playbackArtifacts } from '../../services/production-media'
import type { IndependentJob, ProductionArtifact, StoryDeliveryView, OutputKind, AudioRemakeSelection } from '../../types/production'
import { playbackSounds, audibleSound, sameSource, selectionIncludes, toggleSoundSelection, soundModeLabel } from '../../utils/audio-timeline'
import type { AudioTimeline, PlaybackSound } from '../../utils/audio-timeline'
import { jobActive, jobStatus, jobTitle, productionError, requestKey, stageNames, stageStatus, progressNote, needsReconciliation, requiresPlanRevision } from '../../utils/production'
import { errorMessage, goPage, requireSession } from '../../utils/navigation'
import { buildOutlineReview } from '../../utils/outline-review'
import type { VoiceRevisionState } from '../../utils/voice-revision'
import type { StoryIntent } from '../../types/production'
import { storyOperationRequest, storyIntentLabels } from '../../utils/story-operation'

const storyIntent=ref<StoryIntent>('revise'),storyInstruction=ref(''),remixRetain=ref(''),remixTransform=ref('')
async function submitStoryOperation(){
  if(!job.value||busy.value)return
  busy.value=true;error.value=''
  const storageKey='my-legend:story-operation:'+job.value.id
  try{
    const signature=JSON.stringify([storyIntent.value,storyInstruction.value,remixRetain.value,remixTransform.value])
    const saved=uni.getStorageSync(storageKey),key=saved?.signature===signature?saved.key:requestKey()
    const request=storyOperationRequest(job.value,key,storyIntent.value,storyInstruction.value,remixRetain.value,remixTransform.value)
    uni.setStorageSync(storageKey,{signature,key})
    const next=await api.startIndependent(request)
    uni.removeStorageSync(storageKey);goPage('work',{id:next.id})
  }catch(e){error.value=errorMessage(e)}finally{busy.value=false}
}

const id=ref(''), job=ref<IndependentJob|null>(null), error=ref(''), busy=ref(false), refreshing=ref(false)
const tab=ref<'script'|'audio'|'video'>('script'), delivery=ref<StoryDeliveryView|null>(null)
const videoUrl=ref(''), audioUrl=ref(''), mediaLoading=ref(false), playing=ref(false), currentTime=ref(0), duration=ref(0)
const timeline=ref<AudioTimeline|null>(null)
const revisions=ref<IndependentJob[]>([])
const revisionsNext=ref<number|null>(null),revisionsBusy=ref(false)
const playable=ref<ProductionArtifact[]>([])
const deliveryWarnings=ref<string[]>([])
const remakeEvents=ref<AudioRemakeSelection[]>([])
const voiceEditOpen=ref(false),voiceEdit=ref<VoiceRevisionState>({ready:false,bindings:{},changedCount:0,affectedSegments:0})
let timer:ReturnType<typeof setTimeout>|undefined, visible=true, epoch=0
let fetchSequence=0
let audio:ReturnType<typeof uni.createInnerAudioContext>|undefined
const artifact=(kind:string)=>kind==='RenderManifest'?job.value?.artifacts.find(a=>a.kind===kind):playable.value.find(a=>a.kind===kind)
const hasScript=computed(()=>!!artifact('StoryDelivery')),hasAudio=computed(()=>!!artifact('AudioMix')),hasVideo=computed(()=>!!artifact('Video'))
const canRemake=computed(()=>!!job.value&&hasAudio.value&&!deliveryWarnings.value.length&&!job.value.artifacts.some(a=>a.kind==='AudioExecutionArrangement')&&['succeeded','failed','cancelled'].includes(job.value.status)&&job.value.unsettled_calls===0&&['AudioCache','VoiceBindings'].every(kind=>job.value!.artifacts.some(a=>a.kind===kind)))
const outline=computed(()=>job.value?.status==='needs_input'?job.value.stages.find(s=>s.name==='story'&&s.status==='needs_input')?.input_request:undefined)
// The detail response is newer than the paginated history while a job advances.
const displayedRevisions=computed(()=>revisions.value.map(item=>item.id===job.value?.id?job.value:item))
const title=computed(()=>delivery.value?.title||(job.value?jobTitle(job.value):'作品制作'))
const subtitle=computed(()=>job.value?jobStatus[job.value.status]:'正在读取后台任务')
const orderedStages=computed(()=>[...(job.value?.stages||[])].sort((a,b)=>['story','sound_plan','audio','visual_plan','visual_render','integrate'].indexOf(a.name)-['story','sound_plan','audio','visual_plan','visual_render','integrate'].indexOf(b.name)))
const outlineReview=computed(()=>buildOutlineReview(outline.value?.outline))
const speakerNames=computed(()=>Object.fromEntries([
  ...(delivery.value?.character_snapshot||[]).map(c=>[c.role_id,c.canon?.name||c.role_id]),
  ...(delivery.value?.character_portrayals||[]).filter(c=>c.story_name).map(c=>[c.role_id,c.story_name!]),
  ...(delivery.value?.fictional_voice_roles||[]).filter(c=>c.story_name||c.name).map(c=>[c.role_id,c.story_name||c.name!])
]))
const sounds=computed(()=>playbackSounds(timeline.value))
const activeCaption=computed(()=>{
  const value=timeline.value;if(!value)return ''
  const frame=currentTime.value*value.sample_rate
  return sounds.value.find(e=>e.start_frame<=frame&&frame<e.end_frame&&e.mode==='speech')?.cue_text||''
})
function stopPolling(){clearTimeout(timer);timer=undefined;epoch++}
function release(){audio?.destroy();audio=undefined;playing.value=false;releaseProductionMedia(videoUrl.value);releaseProductionMedia(audioUrl.value);videoUrl.value='';audioUrl.value='';delivery.value=null;timeline.value=null}
async function readScript(){
  const ref=artifact('StoryDelivery');if(!job.value||!ref||delivery.value)return
  const captured=epoch;const value=await readProductionArtifact<StoryDeliveryView>(job.value.id,ref)
  if(captured===epoch){if(value.story_version_id!==ref.source_version_id)throw new Error('剧本版本不一致，请刷新');delivery.value=value}
}
async function readTiming(){
  const ref=artifact('MeasuredTimeline');if(!job.value||!ref||timeline.value)return
  const captured=epoch;const value=await readProductionArtifact<AudioTimeline>(job.value.id,ref)
  if(captured===epoch){if(value.source_version_id!==ref.source_version_id)throw new Error('声音版本不一致，请刷新');timeline.value=value}
}
async function refresh(){
  if(!id.value||!visible)return
  clearTimeout(timer);const sequence=++fetchSequence
  const captured=epoch;refreshing.value=true
  try{
    const [value,view]=await Promise.all([api.independentJob(id.value),api.independentPlayback(id.value)])
    if(captured!==epoch||sequence!==fetchSequence)return
    const results=playbackArtifacts(view,id.value)
    if(view.manifest&&(view.manifest.work_id!==value.work_id||(value.source_version_id&&view.manifest.source_version_id!==value.source_version_id)))throw new Error('作品版本不一致，请刷新')
    if(view.manifest?.duration_seconds)duration.value=view.manifest.duration_seconds
    deliveryWarnings.value=view.manifest?.delivery_warnings||[]
    job.value=value;playable.value=results;error.value='';if(hasScript.value&&!delivery.value)await readScript();if(hasAudio.value)await readTiming()
    if(captured===epoch&&sequence===fetchSequence&&jobActive(value))timer=setTimeout(refresh,2500)
  }catch(e){if(captured===epoch&&sequence===fetchSequence){error.value=errorMessage(e);timer=setTimeout(refresh,5000)}}finally{if(sequence===fetchSequence)refreshing.value=false}
}
async function loadVersions(more=false){
  if(!job.value||revisionsBusy.value)return
  revisionsBusy.value=true;const captured=epoch
  try{const result=await api.independentJobs(more?(revisionsNext.value||0):0,job.value.work_id)
    if(captured!==epoch)return
    revisions.value=more?[...revisions.value,...result.items.filter(item=>!revisions.value.some(old=>old.id===item.id))]:result.items
    revisionsNext.value=result.next_offset
  }catch{/* Current work remains usable if version listing fails. */}finally{revisionsBusy.value=false}
}
async function act(kind:'cancel'|'resume'|'approve'|'reconcile'){
  if(!job.value||busy.value)return
  if(kind==='approve'&&!outlineReview.value.complete){error.value='大纲内容不完整，请刷新后再确认。';return}
  busy.value=true;error.value='';stopPolling()
  try{
    if(kind==='cancel')job.value=await api.cancelIndependent(job.value.id)
    else if(kind==='reconcile'){
      const result=await api.reconcileIndependent(job.value.id,job.value.revision);job.value=result.job
      if(result.unresolved.length)uni.showToast({title:'仍有结果待确认，制作记录已保留',icon:'none'})
      else uni.showToast({title:job.value.status==='cancelled'?'结果已核实，任务保持取消':'结果已核实，可以恢复未完成阶段',icon:'none'})
    }
    else {const input=outline.value;const resolution=kind==='approve'&&input?{decision:'approve',plan_id:input.plan_id,revision:input.revision,fingerprint:input.fingerprint}:{}
      job.value=await api.resumeIndependent(job.value.id,job.value.revision,resolution)}
    await refresh()
  }catch(e){error.value=errorMessage(e)}finally{busy.value=false}
}
async function selectResult(value:'script'|'audio'|'video'){
  tab.value=value;if(value!=='audio'){audio?.pause();playing.value=false}
  if(value==='script'){try{await readScript()}catch(e){error.value=errorMessage(e)}return}
  const kind=value==='audio'?'AudioMix':'Video',ref=artifact(kind)
  if(!job.value||!ref||mediaLoading.value||(value==='audio'?audioUrl.value:videoUrl.value))return
  mediaLoading.value=true;error.value='';const captured=epoch
  try{
    const path=await downloadProductionMedia(job.value.id,ref)
    if(captured!==epoch){releaseProductionMedia(path);return}
    if(value==='video')videoUrl.value=path
    else{audioUrl.value=path;audio=uni.createInnerAudioContext();audio.src=path;audio.autoplay=false
      audio.onPlay(()=>{playing.value=true});audio.onPause(()=>{playing.value=false});audio.onEnded(()=>{playing.value=false})
      audio.onTimeUpdate(()=>{if(audio){currentTime.value=audio.currentTime;duration.value=audio.duration}})
      audio.onError(()=>{playing.value=false;error.value='声音播放失败，请重新加载作品'})
      await readTiming()}
  }catch(e){if(captured===epoch)error.value=errorMessage(e)}finally{mediaLoading.value=false}
}
function toggleAudio(){if(!audio)return;if(playing.value)audio.pause();else audio.play()}
function seek(event:{detail:{value:number}}){audio?.seek(event.detail.value);currentTime.value=event.detail.value}
async function continueFromScript(output:OutputKind){
  const source=artifact('StoryDelivery'),manifest=artifact('RenderManifest');if(!job.value||!source||!manifest||busy.value)return
  busy.value=true;error.value=''
  const storageKey='my-legend:continue:'+job.value.id+':'+output+(output!=='visual'?':listen-first':'')
  try{
    const key=uni.getStorageSync(storageKey)||requestKey();uni.setStorageSync(storageKey,key)
    const next=await api.startIndependent({idempotency_key:key,work_id:job.value.work_id,
      source:{kind:'delivery',source_version_id:source.source_version_id,delivery:source,manifest},outputs:[output],mode:'automatic',visual_profile:'pixel_performance/1',
      ...(output!=='visual'?{review_policy:'listen_first' as const}:{}),
      ...(output==='audio'?{sound_workflow:'sound_bridge/1' as const,audio_delivery_policy:'audio_first' as const}:{})})
    uni.removeStorageSync(storageKey);goPage('work',{id:next.id})
  }catch(e){error.value=errorMessage(e)}finally{busy.value=false}
}
function label(kind:string,role?:string|null){return kind==='dialogue'?(speakerNames.value[role||'']||'角色'):({narration:'旁白',action:'动作',sfx:'音效',music:'音乐'}[kind]||kind)}
function timeLabel(value:number){return `${Math.floor(value/60)}:${Math.floor(value%60).toString().padStart(2,'0')}`}
function selectedEvent(episode:string,scene:string,line:string){return selectionIncludes(remakeEvents.value,{episode_id:episode,scene_id:scene,line_id:line})}
function toggleRemake(episode:string,scene:string,line:string){
  if(busy.value)return
  remakeEvents.value=toggleSoundSelection(remakeEvents.value,{episode_id:episode,scene_id:scene,line_id:line})
}
function canRemakeLine(episode:string,scene:string,line:string){return sounds.value.some(o=>sameSource(o.source,{episode_id:episode,scene_id:scene,line_id:line})&&audibleSound(o))}
function toggleOperation(sound:PlaybackSound){if(!busy.value&&audibleSound(sound))remakeEvents.value=toggleSoundSelection(remakeEvents.value,sound.source)}
function seekSound(sound:PlaybackSound){if(audio&&timeline.value){const seconds=sound.start_frame/timeline.value.sample_rate;audio.seek(seconds);currentTime.value=seconds}}
async function submitRemake(){
  if(!job.value||!canRemake.value||(!remakeEvents.value.length&&!voiceEdit.value.changedCount)||busy.value)return
  if(voiceEditOpen.value&&!voiceEdit.value.ready){error.value='请先完整加载音色并为角色选择可用音色。';return}
  busy.value=true;error.value=''
  const storageKey='my-legend:remake:'+job.value.id
  const regenerate=[...remakeEvents.value].sort((a,b)=>JSON.stringify(a).localeCompare(JSON.stringify(b)))
  const outputs:('audio'|'visual')[]=['audio']
  const voices=voiceEditOpen.value&&voiceEdit.value.changedCount?voiceEdit.value.bindings:undefined
  const signature=JSON.stringify({revision:job.value.revision,regenerate,outputs,voices})
  try{
    const saved=uni.getStorageSync(storageKey)
    const key=saved?.signature===signature?saved.key:requestKey()
    uni.setStorageSync(storageKey,{signature,key})
    const next=await api.remakeAudio(job.value.id,{expected_revision:job.value.revision,idempotency_key:key,regenerate,outputs,...(voices?{voices}:{})})
    uni.removeStorageSync(storageKey);goPage('work',{id:next.id})
  }catch(e){error.value=errorMessage(e)}finally{busy.value=false}
}
function closeVoiceEditor(){voiceEditOpen.value=false;voiceEdit.value={ready:false,bindings:{},changedCount:0,affectedSegments:0}}
function outputLabel(item:IndependentJob){return item.request.outputs.includes('visual')?'视觉演绎':item.request.outputs.includes('audio')?'广播剧':'剧本'}
onLoad(q=>{id.value=String(q?.id||'')})
onShow(async()=>{visible=true;if(!requireSession())return;stopPolling();await refresh();await loadVersions()})
onHide(()=>{visible=false;stopPolling();audio?.pause();uni.createVideoContext('work-video').pause()})
onUnload(()=>{visible=false;stopPolling();release()})
</script>

<template><FeatureLayout :title="title" :subtitle="subtitle" active="stories"><view class="page-content">
  <view v-if="!job&&!error" class="state-panel">正在读取作品…</view>
  <view v-if="error" class="paper-panel"><text class="error-copy" role="alert">{{error}}</text><button class="outline-button full-button" @tap="refresh">刷新作品</button></view>
  <template v-if="job">
    <view class="paper-panel"><view class="row-between"><text class="section-title serif">制作进度</text><text class="pill">{{jobStatus[job.status]}}</text></view>
      <view v-for="stage in orderedStages" :key="stage.name" class="stage-row"><text>{{stage.name==='story'&&job.request.source.kind==='delivery'?'读取已有剧本':stageNames[stage.name]||stage.name}}</text><text :class="{problem:stage.status==='failed'}">{{stageStatus(job,stage.status)}}</text></view>
      <text class="small-note spaced">{{progressNote(job)}}</text>
      <text v-if="job.request.review_policy==='listen_first'" class="small-note spaced">先试听模式：不等待内容审批，生成后直接试听；剧情和声音效果尚待你确认。</text>
      <text v-for="warning in deliveryWarnings" :key="warning" class="small-note spaced">{{warning}}</text>
      <text v-if="job.error_code" class="error-copy">{{productionError(job.error_code)}}</text>
      <button v-if="['queued','running'].includes(job.status)" class="outline-button full-button" :disabled="busy" @tap="act('cancel')">取消后续制作</button>
      <text v-if="needsReconciliation(job)" class="hint">部分外部生成结果尚未确认，完成对账后才能继续，避免重复扣费。</text>
      <button v-if="needsReconciliation(job)&&['failed','needs_input','cancelled'].includes(job.status)" class="outline-button full-button" :disabled="busy" @tap="act('reconcile')">核实外部生成结果</button>
      <button v-if="(job.status==='failed'||(job.status==='needs_input'&&job.error_code==='EXTERNAL_RESULT_UNKNOWN'))&&!needsReconciliation(job)&&!requiresPlanRevision(job.error_code)&&!['CALL_BUDGET_EXCEEDED','VISUAL_ACTION_UNSUPPORTED','REPAIR_BUDGET_EXCEEDED'].includes(job.error_code||'')" class="outline-button full-button" :disabled="busy" @tap="act('resume')">恢复未完成阶段</button>
    </view>
    <view v-if="outline" class="paper-panel section-gap"><text class="section-title serif">确认本次大纲</text>
      <text class="small-note spaced">请核对章节事件、角色表现和声音安排。确认后将按这个版本继续制作。</text>
      <view v-for="section in outlineReview.sections" :key="section.id" class="outline-section"><text class="outline-heading serif">{{section.title}}</text>
        <view v-for="(field,index) in section.fields" :key="index" class="outline-part"><text v-if="field.label" class="field-label">{{field.label}}</text><text class="copy">{{field.text}}</text></view>
      </view>
      <text v-for="warning in outlineReview.warnings" :key="warning" class="error-copy">{{warning}}</text>
      <button class="warm-button full-button" :disabled="busy||!outlineReview.complete" :loading="busy" @tap="act('approve')">确认大纲，继续制作</button>
      <button class="outline-button full-button" :disabled="busy" @tap="act('cancel')">取消这次制作</button>
    </view>
    <view class="paper-panel section-gap"><text class="section-title serif">作品内容</text><view class="chip-list spaced">
      <button class="chip" :class="{selected:tab==='script'}" :disabled="!hasScript" @tap="selectResult('script')">剧本{{hasScript?'':' · 未就绪'}}</button>
      <button class="chip" :class="{selected:tab==='audio'}" :disabled="!hasAudio" @tap="selectResult('audio')">广播剧{{hasAudio?'':' · 未就绪'}}</button>
      <button class="chip" :class="{selected:tab==='video'}" :disabled="!hasVideo" @tap="selectResult('video')">演绎{{hasVideo?'':' · 未就绪'}}</button>
    </view><text v-if="!hasScript&&!hasAudio&&!hasVideo" class="small-note spaced">制作完成的内容会出现在这里。</text>
      <view v-if="tab==='script'&&delivery" class="reader"><view v-for="episode in delivery.episodes" :key="episode.episode_id"><text class="chapter-title serif">{{episode.title}}</text>
        <view v-for="scene in episode.scenes" :key="scene.scene_id"><text class="scene-title">{{scene.title}}</text><view v-for="line in scene.utterances" :key="line.line_id" class="script-line"><text class="speaker">{{label(line.kind,line.speaker_role_id)}}</text><text>{{line.text}}</text><button v-if="canRemake&&canRemakeLine(episode.episode_id,scene.scene_id,line.line_id)" class="chip remake-line" :class="{selected:selectedEvent(episode.episode_id,scene.scene_id,line.line_id)}" :aria-pressed="selectedEvent(episode.episode_id,scene.scene_id,line.line_id)" :disabled="busy" @tap="toggleRemake(episode.episode_id,scene.scene_id,line.line_id)">{{selectedEvent(episode.episode_id,scene.scene_id,line.line_id)?'已选重制':'重做这段声音'}}</button></view></view>
      </view></view>
      <view v-if="tab==='audio'&&hasAudio" class="audio-player"><text v-if="mediaLoading" class="copy">正在加载广播剧…</text><template v-else-if="audioUrl"><text class="copy">{{activeCaption||'点击播放，听见你的故事'}}</text><slider :value="currentTime" :max="Math.max(1,duration)" :step="0.1" activeColor="#cf502a" @change="seek"/><text class="small-note">{{timeLabel(currentTime)}} / {{timeLabel(duration)}}</text><button class="warm-button full-button" @tap="toggleAudio">{{playing?'暂停':'播放广播剧'}}</button></template><button v-else class="outline-button full-button" @tap="selectResult('audio')">加载广播剧</button></view>
      <view v-if="tab==='audio'&&sounds.length" class="sound-list"><text class="section-title serif">声音片段</text><text class="small-note spaced">点击定位可试听对应位置。可单独选择需要重制的配音或音效。</text>
        <view v-for="sound in sounds" :key="sound.operation_id" class="sound-row"><text class="speaker">{{soundModeLabel(sound.mode)}} · {{timeLabel(sound.start_frame/(timeline?.sample_rate||48000))}}</text><text class="copy">{{sound.cue_text}}</text><view class="chip-list spaced"><button class="chip" :disabled="!audioUrl||mediaLoading" @tap="seekSound(sound)">定位</button><button v-if="canRemake&&audibleSound(sound)" class="chip" :class="{selected:selectionIncludes(remakeEvents,sound.source)}" :aria-pressed="selectionIncludes(remakeEvents,sound.source)" :disabled="busy" @tap="toggleOperation(sound)">{{selectionIncludes(remakeEvents,sound.source)?'已选重制':'重做这段声音'}}</button><text v-if="!audibleSound(sound)" class="small-note">随编排执行</text></view></view>
      </view>
      <view v-if="tab==='video'&&hasVideo"><text v-if="mediaLoading" class="copy">正在加载演绎视频…</text><video v-else-if="videoUrl" id="work-video" class="work-video" :src="videoUrl" controls object-fit="contain"/><button v-else class="outline-button full-button" @tap="selectResult('video')">加载视频</button><text class="small-note spaced">基础像素演绎 · 制作完成后可自行评价效果。</text></view>
    </view>
    <button v-if="canRemake&&delivery&&!voiceEditOpen" class="outline-button full-button" :disabled="busy" @tap="voiceEditOpen=true">更换角色或旁白音色</button>
    <VoiceRevisionEditor v-if="canRemake&&delivery&&voiceEditOpen" :job="job" :delivery="delivery" :disabled="busy" @revision-change="voiceEdit=$event" @close="closeVoiceEditor"/>
    <view v-if="canRemake&&(remakeEvents.length||voiceEdit.changedCount)" class="paper-panel section-gap"><text class="section-title serif">更新作品声音</text><text v-if="remakeEvents.length" class="small-note spaced">另选 {{remakeEvents.length}} 段声音重新生成。</text><text v-if="voiceEdit.changedCount" class="small-note spaced">更换 {{voiceEdit.changedCount}} 个声音角色的音色，涉及 {{voiceEdit.affectedSegments}} 段配音。</text><text class="small-note spaced">创建新的制作记录，保留当前作品。未改动的声音优先复用，新的时长会重新计算。重制会产生模型调用。</text><button class="warm-button full-button" :disabled="busy||(voiceEditOpen&&!voiceEdit.ready)" :loading="busy" @tap="submitRemake">按当前选择重制声音</button></view>
    <view v-if="hasScript&&!jobActive(job)" class="paper-panel section-gap"><text class="section-title serif">基于当前版本创作</text>
      <view class="chip-list spaced"><button v-for="(label,intent) in storyIntentLabels" :key="intent" class="chip" :class="{selected:storyIntent===intent}" :disabled="busy" @tap="storyIntent=intent">{{label}}</button></view>
      <text class="small-note spaced">源版本：{{job.source_version_id}}</text>
      <textarea v-model="storyInstruction" class="operation-input" maxlength="8000" placeholder="填写希望修改、续写或改编的内容" :disabled="busy"/>
      <template v-if="storyIntent==='remix'"><textarea v-model="remixRetain" class="operation-input" maxlength="2000" placeholder="希望保留什么？例如人物关系、核心误会" :disabled="busy"/><textarea v-model="remixTransform" class="operation-input" maxlength="2000" placeholder="希望改变什么？例如时代背景、地点、故事风格" :disabled="busy"/></template>
      <text class="small-note spaced">{{storyIntent==='continue'?'续写一章，目标约90秒。当前制作会包含原章节和新章节的完整广播剧。':storyIntent==='remix'?'改编会创建独立作品，保留与源版本的关联。':'按照要求生成新的剧本版本。'}}</text>
      <text class="small-note spaced">自动生成剧本和广播剧，完成后直接试听。原版本保留，演绎暂缓。</text>
      <button class="warm-button full-button" :loading="busy" :disabled="busy||!storyInstruction.trim()" @tap="submitStoryOperation">{{storyIntentLabels[storyIntent]}}并制作广播剧</button>
      <button v-if="!hasAudio" class="outline-button full-button" :disabled="busy" @tap="continueFromScript('audio')">仅从原剧本制作广播剧</button>
    </view>
    <view v-if="revisions.length>1||revisionsNext!==null" class="paper-panel section-gap"><text class="section-title serif">本作品的制作记录</text><button v-for="item in displayedRevisions" :key="item.id" class="version-link" :disabled="item.id===job.id" @tap="goPage('work',{id:item.id})">{{new Date(item.created_at).toLocaleString()}} · {{outputLabel(item)}} · {{jobStatus[item.status]}}{{item.id===job.id?' · 当前':''}}</button><button v-if="revisionsNext!==null" class="outline-button full-button" :disabled="revisionsBusy" @tap="loadVersions(true)">加载更早的制作记录</button></view>
  </template>
</view></FeatureLayout></template>
<style scoped>
.operation-input{box-sizing:border-box;width:100%;height:180rpx;margin-top:24rpx;padding:20rpx;border:1px solid #dfc7ad;border-radius:16rpx;background:#fffaf1;font-size:28rpx}
.sound-row{padding:22rpx 0;border-bottom:1px solid #eadcc9}.sound-row .speaker{display:block;margin-bottom:10rpx}
.remake-line{align-self:flex-start;margin-top:8rpx}
.outline-section{margin-top:28rpx;padding-top:24rpx;border-top:1px solid #eadcc9}.outline-heading{display:block;font-size:30rpx;font-weight:700;color:#795238}
.stage-row{display:flex;justify-content:space-between;gap:20rpx;padding:23rpx 0;border-bottom:1px solid #eadcc9;font-size:27rpx}.stage-row>text:last-child{color:#82735d;font-size:24rpx}.stage-row>text.problem{color:#b7432d}.outline-part{padding-top:20rpx}.outline-part .copy{white-space:pre-wrap;overflow-wrap:anywhere}.reader{padding-top:25rpx}.chapter-title{display:block;font-size:36rpx;margin:20rpx 0}.scene-title{display:block;color:#856143;font-size:28rpx;margin:24rpx 0}.script-line{display:flex;flex-direction:column;gap:8rpx;font-size:29rpx;line-height:1.8;margin:25rpx 0}.speaker{color:#a8452d;font-size:24rpx;font-weight:700}.audio-player{padding:30rpx 0}.work-video{width:100%;height:390rpx;background:#211a16;border-radius:18rpx;margin-top:24rpx}.version-link{text-align:left;padding:20rpx 0;font-size:24rpx;color:#795f45;line-height:1.7}
</style>
