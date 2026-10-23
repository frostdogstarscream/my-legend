<script setup lang="ts">
import {computed,nextTick,onBeforeUnmount,onMounted,ref} from 'vue'
import {api} from '../services/api'
import PerformanceTrackEditor from './PerformanceTrackEditor.vue'
import FaceSuggestionReview from './FaceSuggestionReview.vue'
import {checkSuggestionContext,mergeFaceSuggestions,type FaceSuggestions} from '../services/face-suggestions'
import {evaluateDialogue,prepareDirection,emptyDirectionTrack,type DirectionTrack,type FaceTrack} from '../../../my-legend-stage/src/performance-tracks'
import HtmlInput from './HtmlInput'
import {stageArtifactJson,stageAsset,stageAudio} from '../services/stage-assets'
import {playbackArtifacts,releaseProductionMedia} from '../services/production-media'
import {BasicFaceRuntime,type BasicFace} from '../../../my-legend-stage/src/basic-face'
import {LifeStage} from '../../../my-legend-stage/src/LifeStage'
import {evaluateStage,technicalCoverage,type CueType,type LifeBinding,type LifeCatalog,type LifeCue,type LifePreview,type LifeScript,type PlayMode} from '../../../my-legend-stage/src/life-types'
import type {StageCue} from '../../../my-legend-stage/src/types'
import type {IndependentJob} from '../types/production'

const props=defineProps<{productionId?:string;previewId?:string}>()
const host=ref<HTMLElement>(),catalog=ref<LifeCatalog>(),draft=ref<LifeScript>(),applied=ref<LifeScript>(),preview=ref<LifePreview>()
const jobs=ref<IndependentJob[]>([]),jobId=ref(props.productionId||''),busy=ref(false),error=ref(''),visualError=ref(''),notice=ref('')
const time=ref(0),duration=ref(12),playing=ref(false),speed=ref(1),ready=ref(false),audioReady=ref(false)
const selected=ref(''),trialAction=ref('idle'),trialMode=ref<PlayMode>('loop'),initialOutfits=ref<Record<string,string|null>>({}),bindings=ref<Record<string,string>>({})
const jsonText=ref(''),jsonOpen=ref(false),savedId=ref(props.previewId||''),savedVersions=ref<{job:string;digest:string;title:string}[]>([])
const dialogueEnabled=ref(true),directionDraft=ref<DirectionTrack>(),appliedDirection=ref<DirectionTrack>(),faceTrackDraft=ref<FaceTrack>(),directionDiagnostics=ref<{code:string;message:string}[]>([])
const faceEnabled=ref(false),faceProfileDigest=ref<string>(),faceDiagnostics=ref<BasicFaceRuntime['diagnostics']>([])
const suggestions=ref<FaceSuggestions>(),suggestionBusy=ref(false),suggestionError=ref(''),addedSuggestionIds=ref<string[]>([])
let suggestionRun=0
const fallbackSubtitles=ref<StageCue[]>([]),loopTrial=ref(true)
const labels:Record<string,string>={idle:'待机',walk:'行走',sit:'坐下',pick:'拾取',lie:'躺卧',victory:'胜利',victoryloop:'胜利待机'}
const cueLabels:Record<CueType,string>={'body.play':'身体动作','actor.move_to':'移动','actor.place':'位置与可见性','actor.change_outfit':'定时换装','camera.focus':'镜头聚焦'}
const modes:Record<PlayMode,string>={once:'播放一次后待机',loop:'循环至结束',hold:'播放后保持末姿态'}
const trialActor=computed(()=>draft.value?.actors.find(a=>a.instance_id===selected.value))
const trialActions=computed(()=>Object.keys(trialActor.value?.actions||{}))
const subtitles=computed(()=> (applied.value?.subtitles||fallbackSubtitles.value).filter(c=>c.start_frame<=time.value*(applied.value?.sample_rate||audioRate)&&time.value*(applied.value?.sample_rate||audioRate)<c.end_frame))
const tracksDirty=computed(()=>dialogueEnabled.value!==!!preview.value?.dialogue||JSON.stringify(directionDraft.value)!==JSON.stringify(preview.value?.dialogue)||JSON.stringify(faceTrackDraft.value)!==JSON.stringify(preview.value?.face?.track))
const dirty=computed(()=>!!draft.value&&(tracksDirty.value||JSON.stringify(draft.value)!==JSON.stringify(applied.value)||faceEnabled.value!==!!preview.value?.face||faceEnabled.value&&faceProfileDigest.value!==preview.value?.face?.profile_sha256))
const shareUrl=computed(()=>preview.value?`#/pages/performance/index?mode=spine&profile=life_v1&productionId=${encodeURIComponent(preview.value.job_id)}&previewId=${preview.value.preview_sha256}`:'')
let stage:LifeStage|undefined,audio:HTMLAudioElement|undefined,mediaUrl='',audioDigest='',audioRate=48000,raf=0,last=0,disposed=false,loadGeneration=0
const copy=<T,>(v:T):T=>JSON.parse(JSON.stringify(v))
const message=(e:unknown)=>e instanceof Error?e.message:String(e)
function seconds(frame:number){return Number((frame/(draft.value?.sample_rate||48000)).toFixed(4))}
function inputNumber(event:Event){return Number((event.target as HTMLInputElement).value)}
function pause(){playing.value=false;audio?.pause();draw()}
function draw(){
  if(!ready.value||!stage||!applied.value)return
  try{stage.render(evaluateDialogue(applied.value,Math.floor(time.value*applied.value.sample_rate),appliedDirection.value));faceDiagnostics.value=[...stage.faceDiagnostics]}
  catch(e){visualError.value=message(e);ready.value=false}
}
function seek(value:number){time.value=Math.max(0,Math.min(duration.value,Number.isFinite(value)?value:0));if(audio&&audioReady.value)audio.currentTime=time.value;draw()}
async function toggle(){
  if(playing.value){pause();return}
  if(time.value>=duration.value)seek(0)
  if(audio){try{await audio.play()}catch(e){error.value='请点击播放以允许浏览器播放声音：'+message(e);return}}
  playing.value=true;last=performance.now()
}
function setSpeed(rate:number){speed.value=rate;if(audio)audio.playbackRate=rate}
function tick(now:number){
  if(disposed)return
  if(playing.value){
    if(audio)time.value=audio.currentTime
    else {time.value+=(now-last)/1000*speed.value;if(time.value>=duration.value){if(loopTrial.value)time.value%=duration.value;else{time.value=duration.value;playing.value=false}}}
    draw()
  }
  last=now;raf=requestAnimationFrame(tick)
}
function replay(){pause();seek(0);void toggle()}
function visibility(){if(document.hidden)pause()}
function releaseAudio(){audio?.pause();audio?.removeAttribute('src');audio?.load();audio=undefined;audioReady.value=false;if(mediaUrl)releaseProductionMedia(mediaUrl);mediaUrl='';audioDigest=''}
function remember(value:LifePreview){
  savedVersions.value=[{job:value.job_id,digest:value.preview_sha256,title:value.title},...savedVersions.value.filter(v=>v.digest!==value.preview_sha256)].slice(0,30)
  try{localStorage.setItem('legend-life-previews',JSON.stringify(savedVersions.value))}catch{/* History is optional; immutable server versions remain available. */}
}

async function swap(script:LifeScript,nextCatalog:LifeCatalog,face?:BasicFace,dialogue?:DirectionTrack){
  const candidate=new LifeStage(),detached=document.createElement('div')
  try{
    await candidate.mount(detached,nextCatalog,script.actors,stageAsset)
    if(disposed){candidate.destroy();return}
    candidate.setFace(await BasicFaceRuntime.prepare(script,nextCatalog,face))
    const relation=prepareDirection(script,dialogue)
    candidate.render(evaluateDialogue(script,Math.floor(time.value*script.sample_rate),relation.track))
    await nextTick()
    if(!host.value)throw Error('舞台容器未就绪')
    const previous=stage
    host.value.replaceChildren(...Array.from(detached.childNodes))
    appliedDirection.value=relation.track;directionDiagnostics.value=relation.diagnostics
    stage=candidate;catalog.value=nextCatalog;applied.value=copy(script);ready.value=true;visualError.value=''
    faceDiagnostics.value=[...candidate.faceDiagnostics]
    previous?.destroy()
  }catch(e){candidate.destroy();throw e}
}

async function accept(value:LifePreview){
  if(value.job_id!==jobId.value||value.audio.artifact.sha256!==audioDigest)throw Error('编排与当前音频版本不一致')
  const nextCatalog=catalog.value?.catalog_sha256===value.catalog_sha256?catalog.value:await api.lifeCatalog(value.catalog_sha256)
  await swap(value.stage,nextCatalog,value.face,value.dialogue)
  if(disposed)return
  cancelSuggestions();addedSuggestionIds.value=[]
  dialogueEnabled.value=!!value.dialogue;directionDraft.value=value.dialogue?copy(value.dialogue):undefined;faceTrackDraft.value=value.face?.track?copy(value.face.track):undefined
  faceEnabled.value=!!value.face;faceProfileDigest.value=value.face?.profile_sha256
  preview.value=value;draft.value=copy(value.stage);savedId.value=value.preview_sha256
  bindings.value=Object.fromEntries(value.stage.actors.map(a=>[a.role_id,a.actor_id]))
  initialOutfits.value=Object.fromEntries(value.stage.actors.map(a=>[a.role_id,a.initial_outfit_id]))
  selected.value=draft.value.actors[0]?.instance_id||''
  remember(value);notice.value='已应用并保存此版本；音频位置保持不变。'
}

async function compile(){
  if(!jobId.value||busy.value)return
  pause();busy.value=true;error.value=''
  try{
    const chosen=Object.fromEntries(Object.entries(bindings.value).filter(([,v])=>v))
    if(!Object.keys(chosen).length)throw Error('请至少绑定一个舞台演员')
    if(Object.keys(chosen).length>3)throw Error('最多绑定三个舞台演员')
    const value=await api.lifeCompile(jobId.value,chosen,initialOutfits.value,faceEnabled.value,faceProfileDigest.value,{dialogue_enabled:dialogueEnabled.value})
    await accept(value)
  }catch(e){error.value=message(e)}finally{busy.value=false}
}
function trackOptions(){return {dialogue_enabled:dialogueEnabled.value,dialogue_track:dialogueEnabled.value?directionDraft.value:undefined,face_track:faceEnabled.value?faceTrackDraft.value:undefined}}
function cancelSuggestions(){suggestionRun++;suggestionBusy.value=false;suggestions.value=undefined;suggestionError.value=''}
async function suggestFace(){
  if(busy.value||suggestionBusy.value||dirty.value||!preview.value?.face?.track)return
  const run=++suggestionRun,source=preview.value
  suggestionBusy.value=true;suggestionError.value='';suggestions.value=undefined
  try{
    const value=await api.lifeSuggestFace(source.job_id,source.preview_sha256)
    if(run!==suggestionRun||disposed)return
    if(dirty.value||preview.value?.preview_sha256!==source.preview_sha256)throw Error('请求期间版本或草稿发生变化，请重新生成建议。')
    checkSuggestionContext(value,preview.value);suggestions.value=value
  }catch(e){if(run===suggestionRun)suggestionError.value='建议未生成：'+message(e)}
  finally{if(run===suggestionRun)suggestionBusy.value=false}
}
function acceptSuggestions(ids:string[]){
  try{
    if(dirty.value||!suggestions.value||!preview.value||!faceTrackDraft.value)throw Error('草稿已变化，请重新生成建议。')
    faceTrackDraft.value=mergeFaceSuggestions(suggestions.value,preview.value,faceTrackDraft.value,ids)
    addedSuggestionIds.value=[...ids];cancelSuggestions();notice.value='建议已加入待审草稿，当前舞台未改变。调整后点击“应用并预览”，或撤销加入的建议。'
  }catch(e){suggestionError.value=message(e)}
}
function undoSuggestions(){if(faceTrackDraft.value)faceTrackDraft.value={...faceTrackDraft.value,cues:faceTrackDraft.value.cues.filter(c=>!addedSuggestionIds.value.includes(c.id))};addedSuggestionIds.value=[];notice.value='已撤销加入的建议，当前已应用舞台保持不变。'}
function enableDirection(){if(dialogueEnabled.value&&draft.value&&!directionDraft.value)notice.value='应用后从原剧本生成朝向覆盖；也可添加人工修正。'}
function addDirectionDraft(){if(draft.value&&!directionDraft.value)directionDraft.value=emptyDirectionTrack(draft.value)}
async function enableExpressions(){
  if(!draft.value||busy.value)return
  pause();busy.value=true;error.value=''
  try{await accept(await api.lifeSave(jobId.value,copy(draft.value),true,undefined,{...trackOptions(),face_track:undefined,face_mode:'expressions'}))}catch(e){error.value=message(e)}finally{busy.value=false}
}
async function apply(){
  if(!draft.value||busy.value)return
  pause();busy.value=true;error.value=''
  try{await accept(await api.lifeSave(jobId.value,copy(draft.value),faceEnabled.value,faceProfileDigest.value,trackOptions()))}catch(e){error.value=message(e)}finally{busy.value=false}
}
async function reopen(){
  if(!jobId.value||!savedId.value||busy.value)return
  pause();busy.value=true;error.value=''
  try{await accept(await api.lifeSaved(jobId.value,savedId.value.trim()))}catch(e){error.value=message(e)}finally{busy.value=false}
}

async function loadJob(digest=''){
  if(busy.value)return
  cancelSuggestions();addedSuggestionIds.value=[]
  pause();busy.value=true;error.value='';visualError.value='';notice.value='';const generation=++loadGeneration
  releaseAudio();stage?.destroy();stage=undefined;ready.value=false;applied.value=undefined;draft.value=undefined;preview.value=undefined;fallbackSubtitles.value=[]
  dialogueEnabled.value=true;directionDraft.value=undefined;appliedDirection.value=undefined;faceTrackDraft.value=undefined;directionDiagnostics.value=[]
  time.value=0;bindings.value={};initialOutfits.value={};faceEnabled.value=false;faceProfileDigest.value=undefined;faceDiagnostics.value=[]
  try{
    if(!jobId.value){await materialTrial();return}
    const id=jobId.value,playback=await api.independentPlayback(id),refs=playbackArtifacts(playback,id)
    const ref=refs.find(a=>a.kind==='AudioMix'),timelineRef=refs.find(a=>a.kind==='MeasuredTimeline'),scriptRef=refs.find(a=>a.kind==='StoryDelivery')
    if(!ref||!timelineRef||!scriptRef)throw Error('此任务尚未交付完整音频与时间轴')
    const url=await stageAudio(id,ref)
    if(disposed||generation!==loadGeneration){releaseProductionMedia(url);return}
    mediaUrl=url;audioDigest=ref.sha256;duration.value=playback.manifest!.duration_seconds!;audioRate=playback.manifest!.sample_rate!
    audio=new Audio(url);audio.preload='auto';audio.playbackRate=speed.value
    audio.onended=()=>{time.value=audio!.currentTime;pause()};audio.onpause=()=>{playing.value=false};audio.onplay=()=>{playing.value=true}
    audio.onseeked=()=>{time.value=audio!.currentTime;draw()};audio.onerror=()=>{pause();error.value='声音文件播放失败'}
    audioReady.value=true
    // Read original subtitles independently so a catalog/GPU/compiler failure does not remove them.
    const [timeline,story]=await Promise.all([stageArtifactJson<any>(id,timelineRef),stageArtifactJson<any>(id,scriptRef)])
    if(timeline.audio_sha256!==audioDigest||story.story_version_id!==playback.manifest!.source_version_id)throw Error('原始音频、字幕或剧本版本不一致')
    const lines=new Map<string,any>(),names:Record<string,string>={}
    for(const c of story.character_snapshot||[])names[c.role_id]=c.canon?.name||c.role_id
    for(const k of ['character_portrayals','fictional_voice_roles'])for(const c of story[k]||[])names[c.role_id]=c.story_name||c.name||c.role_id
    for(const ep of story.episodes)for(const sc of ep.scenes)for(const line of sc.utterances)lines.set([ep.episode_id,sc.scene_id,line.line_id].join('|'),line)
    fallbackSubtitles.value=timeline.events.flatMap((event:any)=>{
      const r=event.source||event,source={episode_id:r.episode_id,scene_id:r.scene_id,line_id:r.line_id},line=lines.get(Object.values(source).join('|'))
      if(!line||!['dialogue','narration'].includes(line.kind))return []
      const role=line.kind==='dialogue'?line.speaker_role_id:null
      return (event.operations||[event]).filter((op:any)=>(op.mode||op.render_role||'speech')==='speech'&&op.end_frame>op.start_frame).map((op:any)=>({source,role_id:role,speaker:names[role]||'旁白',text:op.cue_text||line.text,start_frame:op.start_frame,end_frame:op.end_frame}))
    })
    try{await accept(digest?await api.lifeSaved(id,digest):await api.lifeCompile(id))}
    catch(e){visualError.value=message(e)}
  }catch(e){error.value=message(e)}finally{busy.value=false}
}

async function materialTrial(){
  const c=await api.lifeCatalog(),actors:LifeBinding[]=c.actors.map((a,i)=>{
    const outfit=c.outfits.find(o=>o.outfit_id===a.outfit_ids[0]),parts=(outfit?Object.values(outfit.parts):[a.asset_id!]).map(id=>c.skeletons.find(s=>s.asset_id===id)!)
    return {instance_id:'trial_'+i,role_id:a.actor_id,name:a.display_name,actor_id:a.actor_id,actor_revision:a.revision,
      initial_outfit_id:outfit?.outfit_id||null,position:[(i+1)/(c.actors.length+1),.825],facing:1,visible:true,
      actions:Object.fromEntries(Object.entries(a.action_map).map(([name,raw])=>[name,Math.max(...parts.map(p=>p.animations[raw].duration))]))}
  })
  const script:LifeScript={schema_version:'legend_stage_script/0.3',job_id:'material_trial',source_version_id:'material_trial',audio_sha256:'',timeline_sha256:'',catalog_sha256:c.catalog_sha256,
    rule_version:'zh_life_keywords/1',sample_rate:48000,total_frames:12*48000,actors,subtitles:[],cues:[],usage:'technical_coverage'}
  duration.value=12;await swap(script,c);draft.value=copy(script);selected.value=actors[0].instance_id
  trialAction.value='idle';notice.value='素材试演使用独立预览时间；选择成品后切换为音频主时钟。'
}
function trial(){
  if(!draft.value||!trialActor.value||jobId.value)return
  const s=copy(draft.value),a=s.actors.find(a=>a.instance_id===selected.value)!
  if(!a.actions[trialAction.value])trialAction.value='idle'
  s.cues=[{id:'trial',type:'body.play',instance_id:a.instance_id,start_frame:0,end_frame:s.total_frames,enabled:true,
    params:{action:trialAction.value,mode:trialMode.value},source:null,source_text:'素材试演',rule_id:'manual',reason:'素材试演'}]
  applied.value=s;seek(0);draw()
}
function selectTrial(){trialAction.value='idle';trial()}
function coverage(){if(!draft.value||!catalog.value)return;draft.value=technicalCoverage(draft.value,catalog.value);notice.value='已生成技术覆盖草稿，请应用；这些动作不代表剧情自动识别结果。'}
function addCue(type:CueType='body.play'){
  if(!draft.value)return
  const start=Math.min(draft.value.total_frames-1,Math.round(time.value*draft.value.sample_rate))
  const actor=(type==='actor.change_outfit'?draft.value.actors.find(a=>a.initial_outfit_id):draft.value.actors.find(a=>a.instance_id===selected.value))||draft.value.actors[0]
  const c:LifeCue={id:'manual_'+crypto.randomUUID(),type,instance_id:actor.instance_id,start_frame:start,end_frame:Math.min(draft.value.total_frames,start+draft.value.sample_rate*2),enabled:true,
    params:{},source:null,source_text:'人工添加',rule_id:'manual',reason:'人工指令'}
  resetType(c);draft.value.cues.push(c)
}
function cueActor(c:LifeCue){return draft.value?.actors.find(a=>a.instance_id===c.instance_id)}
function outfitsFor(c:LifeCue){return catalog.value?.outfits.filter(o=>o.actor_id===cueActor(c)?.actor_id)||[]}
function resetType(c:LifeCue){
  const a=cueActor(c)||draft.value!.actors[0]
  if(c.type==='body.play')c.params={action:'idle',mode:'loop'}
  else if(c.type==='actor.move_to')c.params={to:[...a.position],visible_after:true}
  else if(c.type==='actor.place')c.params={position:[...a.position],visible:true,facing:1}
  else if(c.type==='actor.change_outfit')c.params={outfit_id:outfitsFor(c)[0]?.outfit_id||''}
  else c.params={target_instance_id:a.instance_id}
  if(['actor.place','actor.change_outfit'].includes(c.type))c.end_frame=c.start_frame
  else if(c.end_frame<=c.start_frame)c.end_frame=Math.min(draft.value!.total_frames,c.start_frame+draft.value!.sample_rate*2)
  c.rule_id='manual';c.reason='人工修正'
}
function markEdited(c:LifeCue){if(c.rule_id!=='manual'){c.reason='人工修正；原规则 '+c.rule_id;c.rule_id='manual'}}
function changeTime(c:LifeCue,key:'start_frame'|'end_frame',event:Event){c[key]=Math.round(inputNumber(event)*draft.value!.sample_rate);if(['actor.place','actor.change_outfit'].includes(c.type))c.end_frame=c.start_frame}
function exportJson(){
  if(!draft.value)return
  jsonText.value=JSON.stringify(faceEnabled.value||dialogueEnabled.value?{schema_version:'legend_performance_draft/1',stage:draft.value,face_enabled:faceEnabled.value,face_profile_sha256:faceProfileDigest.value,...trackOptions()}:draft.value,null,2);jsonOpen.value=true
  const url=URL.createObjectURL(new Blob([jsonText.value],{type:'application/json'})),a=document.createElement('a')
  a.href=url;a.download=`life-stage-${draft.value.job_id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
}
async function importJson(){
  if(busy.value)return
  busy.value=true
  try{const v=JSON.parse(jsonText.value);const wrapped=['legend_face_draft/1','legend_performance_draft/1'].includes(v.schema_version);const checked=await api.lifeSave(jobId.value,wrapped?v.stage:v,wrapped&&v.face_enabled===true,wrapped?v.face_profile_sha256:undefined,wrapped?{dialogue_enabled:v.dialogue_enabled===true,dialogue_track:v.dialogue_track,face_track:v.face_track}:{});draft.value=checked.stage;faceEnabled.value=!!checked.face;faceProfileDigest.value=checked.face?.profile_sha256;dialogueEnabled.value=!!checked.dialogue;directionDraft.value=checked.dialogue;faceTrackDraft.value=checked.face?.track;remember(checked);error.value='';notice.value='JSON 已通过校验并载入草稿，点击应用后更换当前舞台。'}catch(e){error.value=message(e)}finally{busy.value=false}
}
async function importFile(event:Event){const f=(event.target as HTMLInputElement).files?.[0];if(!f)return;jsonText.value=await f.text();jsonOpen.value=true;importJson()}
function historySelected(event:Event){savedId.value=(event.target as HTMLSelectElement).value}
function actorChanged(role:string){initialOutfits.value[role]=catalog.value?.actors.find(a=>a.actor_id===bindings.value[role])?.outfit_ids[0]||null}
onMounted(async()=>{
  try{savedVersions.value=JSON.parse(localStorage.getItem('legend-life-previews')||'[]')}catch{savedVersions.value=[]}
  document.addEventListener('visibilitychange',visibility);raf=requestAnimationFrame(tick)
  try{const value=await api.independentJobs();jobs.value=value.items.filter(j=>j.artifacts.some(a=>a.kind==='AudioMix'))}catch(e){notice.value='成品列表暂不可用，可从作品页打开：'+message(e)}
  if(disposed)return
  await loadJob(props.previewId||'')
})
onBeforeUnmount(()=>{disposed=true;loadGeneration++;cancelSuggestions();cancelAnimationFrame(raf);document.removeEventListener('visibilitychange',visibility);releaseAudio();stage?.destroy()})
defineExpose({pause,snapshot:()=>stage?.snapshot(),playbackState:()=>({time:time.value,audioTime:audio?.currentTime,
  playing:playing.value,ready:ready.value,previewId:preview.value?.preview_sha256,audioDigest})})
</script>

<template>
  <div class="life-page">
    <div class="life-heading"><div><p class="eyebrow">我的传说 · H5 技术验证</p><h1>生活动作与动态演绎</h1><p>原始生活动作 · 三部件换装 · 音频同步</p></div><span class="badge">{{jobId?'音频演绎':'素材试演'}}</span></div>
    <section class="panel selection">
      <label>选择成品<select v-model="jobId" :disabled="busy" @change="loadJob()"><option value="">素材试演（无音频）</option><option v-if="productionId&&!jobs.some(j=>j.id===productionId)" :value="productionId">当前作品 {{productionId}}</option><option v-for="job in jobs" :key="job.id" :value="job.id">{{job.id}} · {{job.status}}</option></select></label>
      <span v-if="busy" role="status">正在准备与校验…</span>
      <p v-if="notice" class="notice">{{notice}}</p>
    </section>
    <div v-if="error" class="error" role="alert">{{error}}</div>
    <div class="theatre"><div class="floor"/><div ref="host" class="canvas-host"/><div class="stage-caption">{{preview?.title||'首批素材试演'}} <span v-if="applied?.usage==='technical_coverage'">· 技术覆盖</span></div><div v-if="visualError" class="visual-error">{{visualError}}<p v-if="audioReady">声音与字幕仍可播放；当前画面可能保留上次有效状态。</p></div><div v-if="busy&&!ready" class="visual-error">正在加载舞台…</div></div>
    <section class="panel transport">
      <div class="subtitle"><span v-for="(cue,i) in subtitles" :key="i">{{cue.speaker}}：{{cue.text}}</span><span v-if="!subtitles.length">{{jobId?'等待下一句对白…':'选择角色与动作检查原始素材'}}</span></div>
      <label class="seek"><span>进度</span><HtmlInput aria-label="演绎进度" type="range" min="0" :max="duration" step=".01" :value="time" @input="seek(inputNumber($event))"/><HtmlInput aria-label="定位秒数" class="time-input" type="number" min="0" :max="duration" step=".01" :value="Number(time.toFixed(2))" @change="seek(inputNumber($event))"/><span>/ {{duration.toFixed(2)}} 秒</span></label>
      <div class="buttons"><button :disabled="!ready&&!audioReady" @click="seek(0)">回到开头</button><button class="primary" :disabled="busy||!ready&&!audioReady" @click="toggle">{{playing?'暂停':'播放'}}</button><button @click="replay" :disabled="busy||!ready&&!audioReady">重播</button><label v-if="!jobId"><HtmlInput v-model="loopTrial" type="checkbox"/>循环试演</label><button v-for="rate in [.5,1,1.5,2]" :key="rate" :class="{active:speed===rate}" @click="setSpeed(rate)">{{rate}}×</button></div>
      <p class="fine">{{jobId?'以原音频绝对播放位置驱动；编排不会重新生成声音。':'素材试演使用独立时钟，音频演绎使用原音频时钟。'}} 基础面部需在下方明确启用并应用保存。</p>
    </section>
    <section v-if="draft&&!jobId" class="panel">
      <h2>素材试演</h2><div class="fields"><label>演员<select v-model="selected" @change="selectTrial"><option v-for="actor in draft.actors" :key="actor.instance_id" :value="actor.instance_id">{{actor.name}}</option></select></label><label>动作<select v-model="trialAction" @change="trial"><option v-for="a in trialActions" :key="a" :value="a">{{labels[a]||a}}</option></select></label><label>播放方式<select v-model="trialMode" @change="trial"><option v-for="(label,key) in modes" :key="key" :value="key">{{label}}</option></select></label><label v-if="trialActor?.initial_outfit_id">服装<select v-model="trialActor.initial_outfit_id" @change="trial"><option v-for="o in catalog?.outfits.filter(o=>o.actor_id===trialActor?.actor_id)" :key="o.outfit_id" :value="o.outfit_id">{{o.display_name}}</option></select></label></div>
    </section>
    <template v-if="jobId&&draft&&preview">
      <section class="panel"><h2>演员绑定与初始服装</h2><div class="cast" v-for="role in preview.roles" :key="role.role_id"><span>{{role.name}}</span><select v-model="bindings[role.role_id]" :aria-label="role.name+'素材演员'" @change="actorChanged(role.role_id)"><option value="">仅声音与字幕</option><option v-for="a in catalog?.actors" :key="a.actor_id" :value="a.actor_id">{{a.display_name}}</option></select><select v-if="catalog?.actors.find(a=>a.actor_id===bindings[role.role_id])?.asset_mode==='commander_tripartite'" v-model="initialOutfits[role.role_id]" aria-label="初始服装"><option v-for="o in catalog?.outfits.filter(o=>o.actor_id===bindings[role.role_id])" :key="o.outfit_id" :value="o.outfit_id">{{o.display_name}}</option></select></div><button :disabled="busy" @click="compile">重新自动编排（生成新草稿）</button><p class="fine">重新编排会生成新版本并重建朝向、面部轨道；已经保存的修改可从历史版本恢复。</p></section>
      <section class="panel"><div class="section-heading"><h2>编排简表</h2><span class="badge">{{dirty?'草稿有修改':'已应用'}} · {{draft.usage==='technical_coverage'?'技术覆盖':'剧情预览'}}</span></div><div class="buttons"><button @click="addCue()">添加动作</button><button @click="addCue('actor.change_outfit')">添加定时换装</button><button @click="coverage">生成技术覆盖草稿</button><button class="primary" :disabled="busy" @click="apply">应用并预览</button><button @click="exportJson">导出 JSON</button><button @click="jsonOpen=!jsonOpen">导入 JSON</button></div>
        <div v-if="jsonOpen" class="json"><HtmlInput type="file" accept=".json,application/json" aria-label="导入脚本文件" @change="importFile"/><HtmlInput tag="textarea" v-model="jsonText" aria-label="编排 JSON" spellcheck="false"/><button @click="importJson">载入草稿</button></div>
        <div class="table-scroll"><table><thead><tr><th>启用 / 来源</th><th>演员 / 指令</th><th>起止时间（秒）</th><th>参数</th></tr></thead><tbody><tr v-for="c in draft.cues" :key="c.id" :class="{disabled:!c.enabled}" @change="markEdited(c)"><td><label><HtmlInput v-model="c.enabled" type="checkbox" :aria-label="'启用 '+c.id"/>{{c.id}}</label><p>{{c.source_text}}</p><small>{{c.source?`${c.source.episode_id}/${c.source.scene_id}/${c.source.line_id}`:'人工 / 技术覆盖'}}</small><small>{{c.reason}}</small></td><td><select v-model="c.instance_id" :aria-label="'演员 '+c.id" @change="resetType(c)"><option v-for="a in draft.actors" :key="a.instance_id" :value="a.instance_id">{{a.name}}</option></select><select v-model="c.type" :aria-label="'指令 '+c.id" @change="resetType(c)"><option v-for="(label,key) in cueLabels" :key="key" :value="key">{{label}}</option></select></td><td><HtmlInput type="number" min="0" step=".01" :aria-label="'开始 '+c.id" :value="seconds(c.start_frame)" @change="changeTime(c,'start_frame',$event)"/><HtmlInput type="number" min="0" step=".01" :aria-label="'结束 '+c.id" :disabled="c.type==='actor.change_outfit'||c.type==='actor.place'" :value="seconds(c.end_frame)" @change="changeTime(c,'end_frame',$event)"/></td><td>
          <template v-if="c.type==='body.play'"><select v-model="c.params.action" :aria-label="'动作 '+c.id"><option v-for="a in Object.keys(cueActor(c)?.actions||{})" :key="a" :value="a">{{labels[a]||a}}</option></select><select v-model="c.params.mode" :aria-label="'播放方式 '+c.id"><option v-for="(label,key) in modes" :key="key" :value="key">{{label}}</option></select></template>
          <template v-if="c.type==='actor.move_to'&&c.params.to"><label>X<HtmlInput v-model.number="c.params.to[0]" type="number" step=".01" min="0" max="1"/></label><label>Y<HtmlInput v-model.number="c.params.to[1]" type="number" step=".01" min="0" max="1"/></label><label><HtmlInput v-model="c.params.visible_after" type="checkbox"/>结束后显示</label></template>
          <template v-if="c.type==='actor.place'&&c.params.position"><label>X<HtmlInput v-model.number="c.params.position[0]" type="number" step=".01" min="0" max="1"/></label><label>Y<HtmlInput v-model.number="c.params.position[1]" type="number" step=".01" min="0" max="1"/></label><label><HtmlInput v-model="c.params.visible" type="checkbox"/>显示</label><select v-model.number="c.params.facing"><option :value="1">朝右</option><option :value="-1">朝左</option></select></template>
          <select v-if="c.type==='actor.change_outfit'" v-model="c.params.outfit_id" :aria-label="'服装 '+c.id"><option value="">请选择指挥官演员及服装</option><option v-for="o in outfitsFor(c)" :key="o.outfit_id" :value="o.outfit_id">{{o.display_name}}</option></select>
          <select v-if="c.type==='camera.focus'" v-model="c.params.target_instance_id" :aria-label="'镜头目标 '+c.id"><option v-for="a in draft.actors" :key="a.instance_id" :value="a.instance_id">{{a.name}}</option></select>
        </td></tr><tr v-if="!draft.cues.length"><td colspan="4">未生成可执行动作，演员保持待机与说话高亮。可查看诊断、添加指令或生成技术覆盖草稿。</td></tr></tbody></table></div>
      </section>
      <section class="panel"><h2>对白关系</h2><label><HtmlInput v-model="dialogueEnabled" type="checkbox" aria-label="启用双人对白关系" @change="enableDirection"/>默认互相面向（新编排启用，历史版本保留原规则）</label><p class="fine">应用后生效。背对、转身与人工修正覆盖默认规则；离场按移动方向行走。当前素材以左右镜像表示转向。</p><button v-if="dialogueEnabled&&!directionDraft" @click="addDirectionDraft">编辑朝向轨道</button><p v-for="d in directionDiagnostics" :key="d.code">{{d.code}} · {{d.message}}</p></section>
      <section class="panel"><h2>自动表情建议 · 离线规则</h2>
        <p class="fine">仅根据明确原文与实测语音区间提出“惊讶”或“挤眼强调”。不会调用付费模型；已有表情优先。生成建议不会改变当前演出。</p>
        <button :disabled="busy||suggestionBusy||dirty||!faceEnabled||!preview.face?.track" @click="suggestFace">生成表情建议</button>
        <button v-if="suggestionBusy" @click="cancelSuggestions">取消建议请求</button>
        <button v-if="addedSuggestionIds.length" @click="undoSuggestions">撤销加入的建议</button>
        <p class="fine" v-if="suggestionBusy">正在检查来源与时间区间…</p>
        <p class="fine" v-if="!preview.face?.track">请先明确启用并保存“表情与手工轨道”。</p>
        <p class="fine" v-else-if="dirty">请先应用或撤销当前草稿修改，再生成建议。</p>
        <p v-if="suggestionError" class="error" role="alert">{{suggestionError}}</p>
        <FaceSuggestionReview v-if="suggestions" :key="suggestions.request_fingerprint" :value="suggestions" :script="draft" :stale="dirty" @accept="acceptSuggestions" @cancel="cancelSuggestions" @seek="seek"/>
      </section>
      <PerformanceTrackEditor v-if="dialogueEnabled" :script="draft" v-model:direction="directionDraft" :time="time"/>
      <PerformanceTrackEditor v-if="faceEnabled" :script="draft" v-model:face-track="faceTrackDraft" :profiles="preview.face?.profile_set.profiles" :time="time"/>
      <section class="panel"><h2>基础面部试播</h2><label><HtmlInput v-model="faceEnabled" type="checkbox" aria-label="启用基础面部"/>启用 HK416 两态口型 / AK15 眨眼（应用后生效）</label><p class="fine">默认关闭。对白区间内使用节律口型，未做音素对齐；保留原生眨眼，躺卧与拾取使用原动作。表情需使用下方已核对的版本；头部摆动未启用。</p><button v-if="!preview.face?.track" :disabled="busy" @click="enableExpressions">启用表情与手工轨道</button><p class="fine" v-if="preview.face">已保存 Profile：{{preview.face.profile_sha256}}</p><ul><li v-for="(d,i) in faceDiagnostics" :key="i"><strong>{{d.code}}</strong> {{d.instance_id}} · {{d.message}}</li></ul><p v-if="!preview.face">当前保存版本使用原生面部。</p></section>
      <section class="panel"><h2>编排诊断</h2><p v-if="!preview.diagnostics.length">本版本没有自动编排诊断。</p><ul><li v-for="(d,i) in preview.diagnostics" :key="i"><strong>{{d.code}}</strong> {{d.message}}<p v-if="d.text">{{d.text}}</p></li></ul></section>
      <section class="panel"><h2>保存与恢复</h2><p class="fine">版本 {{preview.preview_sha256}}</p><a :href="shareUrl">打开当前保存版本</a><div class="fields"><select aria-label="历史版本" @change="historySelected"><option value="">选择本机历史版本</option><option v-for="v in savedVersions.filter(v=>v.job===jobId)" :key="v.digest" :value="v.digest">{{v.title}} · {{v.digest.slice(0,12)}}</option></select><HtmlInput v-model="savedId" aria-label="保存版本哈希" placeholder="完整版本哈希"/><button :disabled="busy" @click="reopen">恢复保存版本</button></div></section>
    </template>
  </div>
</template>

<style scoped>
.life-page{padding:26px 22px 56px;background:#f5eddf;color:#493a30;min-height:100vh}.life-heading,.panel,.theatre,.error{max-width:1100px;margin:0 auto 20px}.life-heading{display:flex;align-items:center;justify-content:space-between;gap:20px}.life-heading h1{font-size:28px;margin:6px 0 10px}.life-heading p{margin:4px 0;color:#8b6c58}.eyebrow{font-size:12px;letter-spacing:2px}.badge{padding:7px 12px;border:1px solid #ccb69b;border-radius:20px;background:#fff5df;font-size:12px;white-space:nowrap}.panel{box-sizing:border-box;background:#fffaf1;border:1px solid #e5d8c7;border-radius:16px;padding:20px}.panel h2{font-size:17px;margin:0 0 16px}.selection label{display:flex;gap:14px;align-items:center}.selection select{min-width:260px;max-width:85%}button,select,input,textarea{font:inherit}button{cursor:pointer;border:1px solid #d9c5ad;border-radius:8px;background:#f8eddb;color:#674932;padding:9px 14px;margin:0;font-size:13px;line-height:1.5}button:disabled{opacity:.5;cursor:default}button.primary{background:#8b422d;color:#fff7e7;border-color:#8b422d}button.active{border-color:#b8432e;background:#f1d8c2}select,input[type=number],input:not([type]),textarea{border:1px solid #d9c5ad;border-radius:7px;padding:7px;background:#fffdf7;color:#493a30;box-sizing:border-box;font-size:13px}select{max-width:100%}input[type=checkbox]{accent-color:#8b422d}label{font-size:13px}.buttons,.fields{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.fields label{display:flex;flex-direction:column;gap:8px}.theatre{position:relative;width:100%;aspect-ratio:16/9;overflow:hidden;border-radius:18px;background:radial-gradient(ellipse at 50% 25%,#58685a,#243736 70%);border:1px solid #8f9c8950;box-sizing:border-box}.floor{position:absolute;top:82.5%;left:0;right:0;bottom:0;background:#142c2b;border-top:1px solid #a8bc8f60}.canvas-host{position:absolute;inset:0}.stage-caption{position:absolute;left:18px;top:15px;color:#ece2c6;font-size:13px;pointer-events:none}.visual-error{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:center;text-align:center;padding:30px;background:#182728b0;color:#fff1cf;font-size:14px}.subtitle{min-height:52px;display:flex;flex-direction:column;justify-content:center;background:#f1e7d5;border-radius:10px;padding:12px 16px;font-size:16px;line-height:1.7}.seek{display:flex;align-items:center;gap:10px;margin:18px 0}.seek input[type=range]{flex:1;accent-color:#a74830}.time-input{width:85px}.seek span{font-size:12px;white-space:nowrap}.transport .buttons{justify-content:center}.fine{font-size:12px;color:#9b7d65;line-height:1.7;overflow-wrap:anywhere}.notice{font-size:13px;color:#71684d;margin:12px 0 0}.error{padding:15px;border:1px solid #e1b7a9;background:#fae3dc;border-radius:10px;color:#9f3529;box-sizing:border-box;overflow-wrap:anywhere}.cast{display:flex;align-items:center;gap:14px;margin:10px 0}.cast>span{min-width:100px}.cast:last-of-type{margin-bottom:20px}.section-heading{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}.section-heading h2{margin:0}.table-scroll{overflow:auto;margin-top:20px}table{width:100%;border-collapse:collapse;min-width:780px;font-size:13px}th{text-align:left;background:#f1e3cd;color:#795238;padding:10px}td{padding:12px 10px;border-bottom:1px solid #e8dccb;vertical-align:top}td:first-child{width:35%;max-width:340px}td p{line-height:1.7;margin:7px 0}td small{display:block;font-size:11px;line-height:1.5;color:#987963;overflow-wrap:anywhere}td select,td input[type=number]{display:block;width:100%;margin-bottom:7px;min-width:80px}td label{display:block}.disabled{opacity:.5}.json{margin-top:16px}.json textarea{display:block;width:100%;min-height:220px;font-family:monospace;margin:10px 0}ul{padding-left:20px;font-size:13px;line-height:1.8}li{margin:8px 0}li strong{color:#935c3c;font-size:11px}li p{margin:3px 0;color:#8b6c58}a{font-size:13px;color:#9c4c32}.fields{margin-top:12px}.fields input{flex:1;min-width:220px}@media(max-width:650px){.life-page{padding:18px 12px 36px}.life-heading h1{font-size:23px}.life-heading>.badge{display:none}.panel{padding:15px}.selection label{flex-direction:column;align-items:flex-start}.selection select{max-width:100%;width:100%;min-width:0}.stage-caption{font-size:10px;left:10px;top:10px}.seek{gap:5px}.time-input{width:68px}.seek>span:first-child{display:none}.cast{flex-wrap:wrap}.cast>span{min-width:75px}.subtitle{font-size:14px}.section-heading{align-items:flex-start;gap:8px}.section-heading .badge{font-size:10px}.fields{align-items:stretch}.fields>*{max-width:100%}}
</style>
