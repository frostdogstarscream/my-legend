<script setup lang="ts">
import {computed,nextTick,onMounted,onBeforeUnmount,ref} from 'vue'
import {api} from '../services/api'
import {goPage} from '../utils/navigation'
import {requestKey} from '../utils/production'
import HtmlInput from './HtmlInput.vue'
import HtmlButton from './HtmlButton.vue'
import HtmlLabel from './HtmlLabel.vue'
import StoryPlaybackShell from './StoryPlaybackShell.vue'
import {adjacentCue,sentenceEnd} from '../utils/stage-playback'
import {stageAsset,stageAudio} from '../services/stage-assets'
import {LifeStage} from '../../../my-legend-stage/src/LifeStage'
import type {AssetRef} from '../../../my-legend-stage/src/types'
import type {LifeCatalog,LifeScript,LifeCue} from '../../../my-legend-stage/src/life-types'
import type {SceneCatalog} from '../../../my-legend-stage/src/scene-types'
import {defaultSceneActors,sceneKey,type H5Context,type H5Preview,type SceneBinding,type CoverageDecision,type CoverageItem,type ExpressionCue} from '../../../my-legend-stage/src/h5-types'
import {evaluatePerformance,type CommanderPreview,type CommanderExpression,type CommanderFurnitureCue} from '../../../my-legend-stage/src/commander-types'
type Performance=H5Preview|CommanderPreview
const props=defineProps<{productionId?:string;previewId?:string;deliveryId?:string}>()
const flowQuery=new URLSearchParams(window.location.hash.split('?')[1]||'')
const flowId=flowQuery.get('flowId')||''
const displayTitle=ref('')
const playOnly=ref(flowQuery.get('view')==='play'||!!props.deliveryId&&flowQuery.get('view')!=='edit')
function returnToFlow(){pause();if(flowId)goPage('flow',{id:flowId});else goPage('work',{id:props.deliveryId||sourceId.value})}
function changeView(){pause();goPage('performance',{profile:'life_v2',...(props.deliveryId?{deliveryId:props.deliveryId}:{productionId:sourceId.value,previewId:preview.value?.preview_sha256||''}),...(flowId?{flowId}:{}),view:playOnly.value?'edit':'play'})}
const sourceId=ref(props.productionId||''),parentDelivery=ref(props.deliveryId||''),deliveryEnabled=ref(false)
const directingMode=ref(new URLSearchParams(window.location.hash.split('?')[1]||'').get('directing')==='1'),directingEnabled=ref(false),castingConfirmed=ref(false)
const context=ref<H5Context>(),catalog=ref<LifeCatalog>(),rooms=ref<SceneCatalog>(),preview=ref<Performance>(),draft=ref<LifeScript>()
const selected=ref<Record<string,string>>({}),outfits=ref<Record<string,string>>({}),sceneBindings=ref<SceneBinding[]>([]),decisions=ref<CoverageDecision[]>([]),expressions=ref<(ExpressionCue|CommanderExpression)[]>([])
const host=ref<HTMLElement>(),error=ref(''),notice=ref(''),loading=ref(true),busy=ref(false),ready=ref(false),playing=ref(false),time=ref(0),speed=ref(1),dirty=ref(false),dialogue=ref(true)
const selectedRole=ref(''),category=ref(''),search=ref(''),selectedScene=ref(0),savedVersions=ref<{id:string;title:string}[]>([]),thumbs=ref<Record<string,string>>({})
let stage:LifeStage|undefined,audio:HTMLAudioElement|undefined,mediaUrl='',animation=0,fallbackTimer=0,lastDraw=0,disposed=false,loadGeneration=0
const sceneCache=new Map<string,SceneCatalog>(),assetCache=new Map<string,Promise<ArrayBuffer>>()
const copy=<T,>(v:T):T=>JSON.parse(JSON.stringify(v))
const getAsset=(ref:AssetRef)=>{let p=assetCache.get(ref.sha256);if(!p){p=stageAsset(ref);assetCache.set(ref.sha256,p);p.catch(()=>assetCache.delete(ref.sha256))}return p}
const duration=computed(()=>context.value?context.value.total_frames/context.value.sample_rate:0)
const roles=computed(()=>context.value?.roles||[])
const chosenActors=computed(()=>roles.value.filter(r=>selected.value[r.role_id]).map((r,i)=>{
 const a=catalog.value!.actors.find(a=>a.actor_id===selected.value[r.role_id])!
 return {instance_id:'actor_'+(i+1),role_id:r.role_id,name:r.name,actor_id:a.actor_id,actor_revision:a.revision,initial_outfit_id:outfits.value[r.role_id]||a.default_outfit_id!,position:[.5,.825],facing:1 as const,visible:true,actions:{}}
}))
const choices=computed(()=>catalog.value?.actors.filter(a=>(!category.value||(a.categories?.includes(category.value)||category.value==='guofeng'&&a.categories?.includes('国风')))&&(!search.value||[a.display_name,...a.tags||[]].join(' ').includes(search.value)))||[])
const commander=computed(()=>['fixed_commander/1','mixed_life/1'].includes(catalog.value?.actor_model||''))
const mouthEnabled=ref<Record<string,boolean>>({}),furniture=ref<CommanderFurnitureCue[]>([]),upgradedFrom=ref<string|null>(null)
const wardrobe=computed(()=>catalog.value?.outfits.filter(o=>o.actor_id===selected.value[selectedRole.value]&&(!category.value||(o.categories?.includes(category.value)||category.value==='guofeng'&&o.style_category==='国风'))&&(!search.value||[o.display_name,...o.tags||[]].join(' ').includes(search.value)))||[])
const currentScene=computed(()=>sceneBindings.value[selectedScene.value]),currentRoom=computed(()=>roomFor(currentScene.value))
const statusLabels={needs_attention:'存在待处理动作',preview_ready:'可预览 · 待人工确认',confirmed:'已人工确认',confirmed_with_omissions:'已确认 · 含明确省略'}
const presentationLabels={direct:'直接表现',adapted:'舞台化表达',audio_led:'声音主导'}
const coverageLabels={compiled:'已编排',manual:'人工校正',needs_correction:'待校正',unsupported:'能力不支持',omitted:'本版不演出',not_executed:'未执行语义'}
const categoryLabels:Record<string,string>={guofeng:'国风侠客',business:'商务正装',detective:'风衣侦探',casual:'校园街头',formal:'礼服舞台',culinary:'餐饮职业',sports:'运动休闲'}
const actionLabels:Record<string,string>={idle:'待机',walk:'行走',sit:'坐姿（非家具互动）',pick:'被提起',lie:'躺卧',victory:'胜利',victoryloop:'胜利循环'}
const faceLabels:Record<string,string>={native:'原生表情',open:'睁眼',closed:'闭眼',talk:'张嘴静态表情',smile:'微笑眯眼',squint:'挤眼',surprised:'惊讶（空心圆眼）'}
const activeLine=computed(()=>preview.value?.stage.subtitles.filter(c=>c.start_frame<=time.value*preview.value!.stage.sample_rate&&time.value*preview.value!.stage.sample_rate<c.end_frame)||[])
const muted=ref(false),showSubtitles=ref(true),autoNext=ref(true)
let stopAt:number|undefined
const playbackRoles=computed(()=>roles.value.map(r=>{const actor=preview.value?.stage.actors.find(a=>a.role_id===r.role_id);return {id:r.role_id,name:r.name,offstage:!actor,portrait:actor?thumbs.value[actor.initial_outfit_id||'']||thumbs.value[actor.actor_id]:undefined}}))
function selectRole(id:string){const cue=preview.value?.stage.subtitles.find(c=>c.role_id===id);if(cue&&preview.value)seek(cue.start_frame/preview.value.stage.sample_rate)}
function stepCue(direction:number){if(!preview.value)return;const cue=adjacentCue(preview.value.stage.subtitles,time.value*preview.value.stage.sample_rate,direction);if(cue)seek(cue.start_frame/preview.value.stage.sample_rate)}
function armSentence(){stopAt=!autoNext.value&&preview.value?sentenceEnd(preview.value.stage.subtitles,time.value*preview.value.stage.sample_rate):undefined}
function toggleAutoNext(){autoNext.value=!autoNext.value;armSentence()}
function toggleMute(){muted.value=!muted.value;if(audio)audio.muted=muted.value}
function syncAudioTime(){if(!audio)return;const next=audio.currentTime;if(stopAt!==undefined&&preview.value&&next>=stopAt/preview.value.stage.sample_rate){const end=stopAt/preview.value.stage.sample_rate;pause();seek(end);return}time.value=next;draw()}
const activeRoom=computed(()=>{if(!preview.value)return '';const {segment}=evaluatePerformance(preview.value,time.value*preview.value.stage.sample_rate);return roomFor(segment)?.display_name||segment.title})
function fmt(s:number){return Math.floor(s/60)+':'+(s%60).toFixed(2).padStart(5,'0')}
function mark(){dirty.value=true;castingConfirmed.value=false;notice.value=directingMode.value?'人物和服装已调整，请确认后提交 AI 编排。':'调整好了以后，记得保存。'}
function roomFor(binding?:SceneBinding){if(!binding)return;return sceneCache.get(binding.scene.catalog_sha256)?.scenes.find(s=>s.scene_id===binding.scene.scene_id)}
function seatLabel(c:CommanderFurnitureCue){const b=sceneBindings.value.find(b=>b.episode_id===c.episode_id&&b.scene_id===c.scene_id);return roomFor(b)?.commander_seats?.find(s=>s.seat_id===c.seat_id)?.label||'已保存座位'}
function expressionLabel(c:ExpressionCue|CommanderExpression){return 'channel' in c&&c.channel==='mouth'?({native:'原生嘴部',open:'开嘴',closed:'闭嘴'} as Record<string,string>)[c.preset]:faceLabels[c.preset]}
function rememberPosition(){if(!props.deliveryId||!preview.value)return;try{localStorage.setItem('h5-position:'+props.deliveryId,JSON.stringify({preview:preview.value.preview_sha256,time:time.value,speed:speed.value}))}catch{}}
function pause(){playing.value=false;audio?.pause();rememberPosition()}
function draw(){if(!stage||!preview.value||!ready.value)return;try{const sample=evaluatePerformance(preview.value,time.value*preview.value.stage.sample_rate);stage.setScene(sceneKey(sample.segment.scene));stage.render(sample.state);lastDraw=performance.now()}catch(e){pause();ready.value=false;error.value=String(e)}}
function seek(value:number){time.value=Math.max(0,Math.min(duration.value,value));if(audio)audio.currentTime=time.value;armSentence();draw();rememberPosition()}
async function toggle(){if(playing.value){pause();return}if(!ready.value||!audio)return;if(time.value>=duration.value)seek(0);armSentence();try{await audio.play()}catch(e){error.value='播放未开始，请再次点击播放：'+String(e)}}
function tick(){if(disposed)return;if(playing.value&&audio)syncAudioTime();animation=requestAnimationFrame(tick)}
function setSpeed(){if(audio)audio.playbackRate=speed.value;rememberPosition()}
function roleName(iid:string){return (draft.value?.actors||chosenActors.value).find(a=>a.instance_id===iid)?.name||iid}
function chooseActor(id:string){if(!selectedRole.value||selected.value[selectedRole.value]===id)return;selected.value[selectedRole.value]=id;outfits.value[selectedRole.value]=catalog.value!.actors.find(a=>a.actor_id===id)!.default_outfit_id!;resetSceneActors();mark()}
function actionDurations(actorId:string,oid:string){const c=catalog.value!,a=c.actors.find(a=>a.actor_id===actorId)!,o=c.outfits.find(o=>o.outfit_id===oid)!;return Object.fromEntries(Object.entries(a.action_map).filter(([name])=>o.allowed_actions.includes(name)).map(([name,raw])=>[name,Math.max(...Object.values(o.parts).map(id=>c.skeletons.find(s=>s.asset_id===id)!.animations[raw].duration))]))}
function chooseOutfit(role:string,oid:string){outfits.value[role]=oid;const a=draft.value?.actors.find(a=>a.role_id===role);if(commander.value&&a&&a.actor_id===selected.value[role]){a.initial_outfit_id=oid;a.actions=actionDurations(a.actor_id,oid)}mark()}
async function loadThumbs(){const c=catalog.value;if(!c)return;for(const a of [...c.actors,...(commander.value?c.outfits:[])]){const id='actor_id' in a&&'outfit_id' in a?a.outfit_id:a.actor_id;if(!a.thumbnail||thumbs.value[id])continue;const bytes=await getAsset(a.thumbnail);if(!disposed)thumbs.value[id]=URL.createObjectURL(new Blob([bytes],{type:'image/png'}))}}
function removeActor(role:string){delete selected.value[role];delete outfits.value[role];resetSceneActors();mark()}
function resetSceneActors(){for(const binding of sceneBindings.value){const room=roomFor(binding);if(room)binding.actors=defaultSceneActors(chosenActors.value,room);binding.confirmed=false}}
function chooseScene(index:number,id:string){const binding=sceneBindings.value[index],room=rooms.value!.scenes.find(s=>s.scene_id===id);if(!room)return;binding.scene={catalog_sha256:rooms.value!.catalog_sha256,scene_id:id};binding.actors=defaultSceneActors(chosenActors.value,room);binding.confirmed=false;mark()}
function copyPrevious(index:number){if(index<1)return;const old=sceneBindings.value[index-1];sceneBindings.value[index]={...copy(old),episode_id:sceneBindings.value[index].episode_id,scene_id:sceneBindings.value[index].scene_id,confirmed:false};mark()}
function saveHistory(value:Performance){savedVersions.value=[{id:value.preview_sha256,title:value.title},...savedVersions.value.filter(x=>x.id!==value.preview_sha256)].slice(0,20);try{localStorage.setItem('h5-story-'+sourceId.value,JSON.stringify(savedVersions.value))}catch{}}
async function apply(value:Performance){
 if(!context.value||value.job_id!==sourceId.value||value.audio.artifact.sha256!==context.value.audio.artifact.sha256||value.timeline.artifact.sha256!==context.value.timeline.artifact.sha256||value.source_version_id!==context.value.source_version_id)throw Error('演绎与当前作品声音或来源版本不一致')
 const generation=++loadGeneration,candidate=new LifeStage(),detached=document.createElement('div')
 try{
  const nextCatalog=catalog.value?.catalog_sha256===value.catalog_sha256?catalog.value:await api.h5Catalog(value.catalog_sha256)
  for(const s of value.scene_track.segments)if(!sceneCache.has(s.scene.catalog_sha256)){const c=await api.sceneCatalog(s.scene.catalog_sha256);sceneCache.set(c.catalog_sha256,c)}
  const entries=[...new Map(value.scene_track.segments.map(s=>[sceneKey(s.scene),{key:sceneKey(s.scene),scene:roomFor(s)!}])).values()]
  if(entries.some(e=>!e.scene))throw Error('保存版本引用的场景缺失')
  await candidate.mount(detached,nextCatalog,value.stage.actors,getAsset,entries[0].scene);await candidate.prepareScenes(entries,getAsset)
  const state=evaluatePerformance(value,time.value*value.stage.sample_rate);candidate.setScene(sceneKey(state.segment.scene));candidate.render(state.state)
  await nextTick();if(disposed||generation!==loadGeneration){candidate.destroy();return}
  if(!host.value)throw Error('舞台容器未就绪');const previous=stage;host.value.replaceChildren(...Array.from(detached.childNodes));stage=candidate
  catalog.value=nextCatalog;preview.value=value;draft.value=copy(value.stage);sceneBindings.value=copy(value.scene_bindings);decisions.value=copy(value.coverage_decisions);expressions.value=copy(value.expression_track);dialogue.value=value.dialogue_enabled
  mouthEnabled.value=value.schema_version!=='spine_stage_preview/3.0'?copy(value.mouth_enabled):{};furniture.value=value.schema_version!=='spine_stage_preview/3.0'?copy(value.furniture_track.cues):[];upgradedFrom.value=value.schema_version!=='spine_stage_preview/3.0'?value.upgraded_from||null:null
  selected.value=Object.fromEntries(value.stage.actors.map(a=>[a.role_id,a.actor_id]));outfits.value=Object.fromEntries(value.stage.actors.map(a=>[a.role_id,a.initial_outfit_id!]))
  ready.value=true;dirty.value=false;saveHistory(value);void loadThumbs();notice.value='舞台已准备好。'
  // Cleanup of an already-detached renderer must never tear down its replacement.
  try{previous?.destroy()}catch(e){console.warn('旧舞台资源清理未完成',e)}
 }catch(e){candidate.destroy();throw e}
}
async function generate(){
 if(!sourceId.value||!catalog.value)return;pause();busy.value=true;error.value=''
 try{
  if(!chosenActors.value.length||chosenActors.value.length>3)throw Error('请选择 1 到 3 个视觉演员')
  if(sceneBindings.value.some(s=>!s.scene.scene_id||!s.confirmed))throw Error('请逐场确认场景、演员在场状态及站位')
  await apply(await api.h5Compile(sourceId.value,{profile:'life_v2',operation:'compile',catalog_sha256:catalog.value.catalog_sha256,bindings:selected.value,initial_outfits:outfits.value,scene_bindings:sceneBindings.value,dialogue_enabled:dialogue.value}))
 }catch(e){error.value=String(e)}finally{busy.value=false}
}
async function save(confirm=false){
 if(!draft.value||!sourceId.value)return;pause();busy.value=true;error.value=''
 try{
  if(draft.value.actors.some(a=>selected.value[a.role_id]!==a.actor_id||outfits.value[a.role_id]!==a.initial_outfit_id)||chosenActors.value.length!==draft.value.actors.length)throw Error('人物或外观已变化，请重新应用场景设置')
  await apply(await api.h5Compile(sourceId.value,{profile:'life_v2',operation:'validate_and_save',script:draft.value,scene_bindings:sceneBindings.value,coverage_decisions:decisions.value,expression_track:expressions.value,dialogue_enabled:dialogue.value,review_confirmed:confirm,...(preview.value?.schema_version==='spine_stage_preview/3.2'?{directed_from:preview.value.preview_sha256}:{}),...(commander.value?{mouth_enabled:mouthEnabled.value,furniture_track:{schema_version:'commander_furniture/1' as const,cues:furniture.value},upgraded_from:upgradedFrom.value}:{})}))
 }catch(e){error.value=String(e)}finally{busy.value=false}
}
async function restore(id:string){pause();busy.value=true;error.value='';try{await apply(await api.h5Saved(sourceId.value!,id))}catch(e){error.value=String(e)}finally{busy.value=false}}
async function direct(){
 if(!castingConfirmed.value||!catalog.value||busy.value)return
 pause();busy.value=true;error.value=''
 try{
  if(!chosenActors.value.length||chosenActors.value.length>3)throw Error('请选择 1 到 3 位视觉演员')
  const bindings=Object.fromEntries(chosenActors.value.map(a=>[a.role_id,a.actor_id])),initial_outfits=Object.fromEntries(chosenActors.value.map(a=>[a.role_id,a.initial_outfit_id!]))
  const body={catalog_sha256:catalog.value.catalog_sha256,bindings,initial_outfits,casting_confirmed:true as const,...(parentDelivery.value?{parent_job_id:parentDelivery.value}:{})}
  const signature='h5-directing:'+sourceId.value+':'+JSON.stringify(body)
  let key=localStorage.getItem(signature);if(!key){key=requestKey();localStorage.setItem(signature,key)}
  if(flowId){const flow=await api.flow(flowId);const result=await api.reviseFlow(flowId,{kind:'stage',expected_revision:flow.revision,idempotency_key:key,casting:{catalog_sha256:body.catalog_sha256,bindings,initial_outfits,casting_confirmed:true}});goPage('flow',{id:result.id});return}
  const result=await api.createH5Directing(sourceId.value,{...body,idempotency_key:key})
  goPage('work',{id:result.id})
 }catch(e){error.value=String(e)}finally{busy.value=false}
}
async function deliver(){
 if(!preview.value||busy.value)return
 if(dirty.value){error.value='请先点击“应用并保存”，再交付当前修改。';return}
 pause();busy.value=true;error.value=''
 const signature=[sourceId.value,preview.value.preview_sha256,parentDelivery.value].join(':')
 const storageKey='h5-delivery:'+signature
 try{
  let key=localStorage.getItem(storageKey);if(!key){key=requestKey();localStorage.setItem(storageKey,key)}
  if(flowId){const flow=await api.flow(flowId);const result=await api.reviseFlow(flowId,{kind:'stage_saved',expected_revision:flow.revision,idempotency_key:key,preview_sha256:preview.value.preview_sha256});goPage('flow',{id:result.id});return}
  const result=await api.createH5Delivery(sourceId.value,{preview_sha256:preview.value.preview_sha256,idempotency_key:key,...(parentDelivery.value?{parent_job_id:parentDelivery.value}:{})})
  goPage('work',{id:result.id})
 }catch(e){error.value=String(e)}finally{busy.value=false}
}
async function share(){if(!preview.value)return;const url=location.origin+location.pathname+'#/pages/performance/index?profile=life_v2&productionId='+encodeURIComponent(sourceId.value!)+'&previewId='+preview.value.preview_sha256;try{await navigator.clipboard.writeText(url);notice.value='已复制作品链接'}catch{notice.value=url}}
function download(){if(!preview.value)return;const url=URL.createObjectURL(new Blob([JSON.stringify(preview.value,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='h5-story-'+preview.value.preview_sha256.slice(0,12)+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
const form=ref({outfitId:'',id:'',coverageId:'',instanceId:'',kind:'move',action:'idle',mode:'loop',start:0,end:1.4,x:.5,facing:1,visible:true,target:'',source:null as LifeCue['source'],sourceText:''})
const editing=ref(false),omitText=ref<Record<string,string>>({})
const editingSegment=computed(()=>preview.value?.scene_track.segments.find(s=>s.start_frame<=form.value.start*(draft.value?.sample_rate||1)&&form.value.start*(draft.value?.sample_rate||1)<s.end_frame))
const editingRoom=computed(()=>roomFor(editingSegment.value)),formActor=computed(()=>draft.value?.actors.find(a=>a.instance_id===form.value.instanceId))
function editCue(cue?:LifeCue,coverage?:CoverageItem){
 if(!draft.value)return;const sr=draft.value.sample_rate
 form.value={outfitId:cue?.params.outfit_id||draft.value.actors[0].initial_outfit_id||'',id:cue?.id||'',coverageId:coverage?.id||'',instanceId:cue?.instance_id||coverage?.instance_id||draft.value.actors[0].instance_id,kind:cue?.type==='actor.change_outfit'?'outfit':cue?.type==='body.play'?'body':cue?.type==='actor.place'?'place':'move',action:cue?.params.action||'idle',mode:cue?.params.mode||'loop',
  start:(cue?.start_frame??coverage?.start_frame??Math.round(time.value*sr))/sr,end:(cue?.end_frame??Math.min(draft.value.total_frames,(coverage?.start_frame??Math.round(time.value*sr))+Math.round(1.4*sr)))/sr,
  x:cue?.params.to?.[0]??cue?.params.position?.[0]??.5,facing:cue?.params.facing||1,visible:cue?.params.visible??cue?.params.visible_after??true,target:cue?.params.target?cue.params.target.kind+':'+cue.params.target.id:'',source:copy(cue?.source||coverage?.source||null),sourceText:cue?.source_text||coverage?.text||''};editing.value=true
}
function correct(item:CoverageItem){if(commander.value&&item.instance_id&&hasFace(item.instance_id)&&['sit','stand'].includes(item.action)){editSeat(furniture.value.find(c=>item.cue_ids.includes(c.id)),item);return}const cue=draft.value?.cues.find(c=>item.cue_ids.includes(c.id));editCue(cue,item);if(!cue&&['sit','stand'].includes(item.action)){form.value.kind='body';form.value.action=item.action==='sit'?'sit':'idle'}}
function addCue(){
 if(!draft.value)return;error.value=''
 try{
  const f=form.value,sr=draft.value.sample_rate,segment=editingSegment.value,room=editingRoom.value;if(!segment||!room)throw Error('请选择分场内的有效开始时间')
  const begin=Math.round(f.start*sr),end=['place','outfit'].includes(f.kind)?begin:Math.round(f.end*sr)
  if(begin<segment.start_frame||end>segment.end_frame||!['place','outfit'].includes(f.kind)&&end<=begin)throw Error('动作时间须位于同一分场内，结束晚于开始')
  let x=f.x
  if(f.target.startsWith('object:')){const point=room.blocking?.targets.find(p=>p.id===f.target.slice(7));if(!point)throw Error('物件目标已变化');x=point.position[0]}
  if(f.target.startsWith('actor:')){const state=evaluatePerformance({...preview.value!,stage:draft.value},begin).state;const other=state.actors.find(a=>a.instance_id===f.target.slice(6)),self=state.actors.find(a=>a.instance_id===f.instanceId);if(!other?.visible||!self)throw Error('目标人物不在场');x=other.position[0]+(self.position[0]<=other.position[0]?-.18:.18)}
  const params:LifeCue['params']=f.kind==='outfit'?{outfit_id:f.outfitId}:f.kind==='body'?{action:f.action,mode:f.mode as 'loop'|'once'|'hold'}:f.kind==='place'?{position:[x,room.blocking!.foot_y],facing:f.facing as 1|-1,visible:f.visible}:{to:[x,room.blocking!.foot_y],visible_after:f.visible}
  if(f.kind==='move'&&f.target){const [kind,id]=f.target.split(':');params.target={kind:kind as 'actor'|'object',id}}
  const cue:LifeCue={id:f.id||'manual_'+crypto.randomUUID(),type:f.kind==='outfit'?'actor.change_outfit':f.kind==='body'?'body.play':f.kind==='place'?'actor.place':'actor.move_to',instance_id:f.instanceId,start_frame:begin,end_frame:end,enabled:true,params,source:f.source,source_text:f.sourceText,rule_id:'manual',reason:f.target?'人工选择目标：'+f.target:'人工校正'}
  const index=draft.value.cues.findIndex(c=>c.id===cue.id);if(index>=0)draft.value.cues[index]=cue;else draft.value.cues.push(cue)
  if(f.coverageId){decisions.value=decisions.value.filter(d=>d.coverage_id!==f.coverageId);decisions.value.push({coverage_id:f.coverageId,resolution:'manual',cue_ids:[cue.id],note:'已人工校正动作、目标和时间。'})}
  editing.value=false;mark()
 }catch(e){error.value=String(e)}
}
function omit(item:CoverageItem){const note=omitText.value[item.id]?.trim();if(!note){error.value='请先填写本版不演出的说明';return}decisions.value=decisions.value.filter(d=>d.coverage_id!==item.id);decisions.value.push({coverage_id:item.id,resolution:'omitted',cue_ids:[],note});mark()}
function deleteCue(id:string){if(draft.value){draft.value.cues=draft.value.cues.filter(c=>c.id!==id);mark()}}
const faceForm=ref({channel:'eyes' as 'eyes'|'mouth',instanceId:'actor_1',preset:'native',start:0,end:1})
function hasFace(iid:string){return !!catalog.value?.actors.find(a=>a.actor_id===draft.value?.actors.find(b=>b.instance_id===iid)?.actor_id)?.face_profile}
const faceOptions=computed(()=>{if(commander.value&&!hasFace(faceForm.value.instanceId))return ['native'];if(commander.value)return faceForm.value.channel==='mouth'?['native','open','closed']:['native',...Object.keys(catalog.value?.actors.find(a=>a.actor_id===draft.value?.actors.find(a=>a.instance_id===faceForm.value.instanceId)?.actor_id)?.face_profile?.eyes||{})];const actor=draft.value?.actors.find(a=>a.instance_id===faceForm.value.instanceId);return catalog.value?.outfits.find(o=>o.outfit_id===actor?.initial_outfit_id)?.face_presets||['native']})
function addFace(){if(!draft.value)return;expressions.value.push({id:'face_'+crypto.randomUUID(),instance_id:faceForm.value.instanceId,...(commander.value?{channel:faceForm.value.channel}:{}),preset:faceForm.value.preset as ExpressionCue['preset'],start_frame:Math.round(faceForm.value.start*draft.value.sample_rate),end_frame:Math.round(faceForm.value.end*draft.value.sample_rate),enabled:true});mark()}
const seatEditing=ref(false),seatCoverage=ref(''),seatForm=ref({id:'',instanceId:'actor_1',seatId:'',start:0,arrive:1.4,leave:4,end:6,hold:true,source:null as LifeCue['source'],sourceText:''})
const seatSegment=computed(()=>preview.value?.scene_track.segments.find(s=>s.start_frame<=seatForm.value.start*(draft.value?.sample_rate||1)&&seatForm.value.start*(draft.value?.sample_rate||1)<s.end_frame)),seatRoom=computed(()=>roomFor(seatSegment.value))
function editSeat(cue?:CommanderFurnitureCue,item?:CoverageItem){const sr=draft.value!.sample_rate;seatCoverage.value=item?.id||'';seatForm.value={id:cue?.id||'',instanceId:cue?.instance_id||item?.instance_id||draft.value!.actors.find(a=>hasFace(a.instance_id))!.instance_id,seatId:cue?.seat_id||'',start:(cue?.start_frame??item?.start_frame??Math.round(time.value*sr))/sr,arrive:cue?cue.arrive_frame/sr:(item?.start_frame||Math.round(time.value*sr))/sr+1.4,leave:cue?.leave_frame?cue.leave_frame/sr:time.value+4,end:cue?cue.end_frame/sr:time.value+6,hold:cue?.leave_frame==null,source:cue?.source||item?.source||null,sourceText:cue?.source_text||item?.text||''};seatEditing.value=true}
function addSeat(){try{const f=seatForm.value,segment=seatSegment.value,seat=seatRoom.value?.commander_seats?.find(s=>s.seat_id===f.seatId),sr=draft.value!.sample_rate;if(!seat||!segment)throw Error('请选择当前分场的实际座位');const start=Math.round(f.start*sr),state=evaluatePerformance({...preview.value!,stage:draft.value!},start).state.actors.find(a=>a.instance_id===f.instanceId)!;const c:CommanderFurnitureCue={id:f.id||'seat_'+crypto.randomUUID(),instance_id:f.instanceId,episode_id:segment.episode_id,scene_id:segment.scene_id,seat_id:seat.seat_id,calibration_revision:seat.calibration_revision,outfit_id:state.outfit_id!,start_frame:start,arrive_frame:Math.round(f.arrive*sr),leave_frame:f.hold?null:Math.round(f.leave*sr),end_frame:f.hold?segment.end_frame:Math.round(f.end*sr),enabled:true,source:f.source,source_text:f.sourceText,reason:f.source?'人工校正原文家具动作':'人工验证编排，不属于原文内容'};const index=furniture.value.findIndex(x=>x.id===c.id);if(index>=0)furniture.value[index]=c;else furniture.value.push(c);if(seatCoverage.value){decisions.value=decisions.value.filter(d=>d.coverage_id!==seatCoverage.value);decisions.value.push({coverage_id:seatCoverage.value,resolution:'manual',cue_ids:[c.id],note:'已人工选择实际座位和时间。'})}seatEditing.value=false;mark()}catch(e){error.value=String(e)}}
async function upgrade(){if(!preview.value||preview.value.schema_version!=='spine_stage_preview/3.0')return;pause();busy.value=true;error.value='';try{const old=preview.value,c=await api.h5Catalog(),ctx=await api.h5Prepare(sourceId.value!,c.catalog_sha256);if(!['fixed_commander/1','mixed_life/1'].includes(c.actor_model||''))throw Error('固定指挥官目录尚未启用');const rc=await api.sceneCatalog(ctx.scene_catalog_sha256),script=copy(old.stage);script.catalog_sha256=c.catalog_sha256;const mapOutfit=(oid:string)=>{const found=c.outfits.find(o=>o.source_outfit_id===oid||o.source_character_id===oid.split('--')[0]);if(!found)throw Error('旧服装没有固定指挥官映射：'+oid);return found};for(const a of script.actors){const o=mapOutfit(a.initial_outfit_id!),asset=c.actors.find(x=>x.actor_id===o.actor_id)!;a.actor_id=asset.actor_id;a.actor_revision=asset.revision;a.initial_outfit_id=o.outfit_id;a.actions=Object.fromEntries(Object.entries(asset.action_map).map(([name,raw])=>[name,Math.max(...Object.values(o.parts).map(id=>c.skeletons.find(s=>s.asset_id===id)!.animations[raw].duration))]))}for(const cue of script.cues)if(cue.type==='actor.change_outfit')cue.params.outfit_id=mapOutfit(cue.params.outfit_id!).outfit_id;const bindings=copy(old.scene_bindings);for(const b of bindings){if(!rc.scenes.some(s=>s.scene_id===b.scene.scene_id))throw Error('旧场景没有对应版本');b.scene.catalog_sha256=rc.catalog_sha256}const result=await api.h5Compile(sourceId.value!,{profile:'life_v2',operation:'validate_and_save',script,scene_bindings:bindings,coverage_decisions:old.coverage_decisions,expression_track:old.expression_track.map(e=>({...e,channel:e.preset==='talk'?'mouth':'eyes',preset:e.preset==='talk'?'open':e.preset})),dialogue_enabled:old.dialogue_enabled,review_confirmed:false,upgraded_from:old.preview_sha256});await apply(result);rooms.value=rc;sceneCache.set(rc.catalog_sha256,rc);notice.value='已另存固定指挥官版本，保留旧版本链接；头部已统一。'}catch(e){error.value=String(e)}finally{busy.value=false}}
async function upgradeFaces(){
 if(!preview.value||preview.value.schema_version!=='spine_stage_preview/3.1'||dirty.value)return
 pause();busy.value=true;error.value=''
 try{
  const old=preview.value,c=await api.h5Catalog(),ctx=await api.h5Prepare(sourceId.value!,c.catalog_sha256)
  if(!c.actors.filter(a=>a.face_profile).every(a=>a.face_profile?.eyes.surprised))throw Error('圆眼资源尚未准备完成')
  const rc=await api.sceneCatalog(ctx.scene_catalog_sha256),script=copy(old.stage),bindings=copy(old.scene_bindings),seats=copy(old.furniture_track.cues)
  script.catalog_sha256=c.catalog_sha256
  for(const a of script.actors){const current=c.actors.find(x=>x.actor_id===a.actor_id);if(!current)throw Error('当前演员没有对应的新资源');a.actor_revision=current.revision}
  for(const b of bindings){if(!rc.scenes.some(s=>s.scene_id===b.scene.scene_id))throw Error('当前场景没有对应的新资源');b.scene.catalog_sha256=rc.catalog_sha256}
  for(const seat of seats){const b=bindings.find(b=>b.episode_id===seat.episode_id&&b.scene_id===seat.scene_id),s=rc.scenes.find(s=>s.scene_id===b?.scene.scene_id)?.commander_seats?.find(s=>s.seat_id===seat.seat_id);if(!s)throw Error('当前座位没有对应的新校准');seat.calibration_revision=s.calibration_revision}
  const result=await api.h5Compile(sourceId.value!,{profile:'life_v2',operation:'validate_and_save',script,scene_bindings:bindings,coverage_decisions:old.coverage_decisions,expression_track:old.expression_track,mouth_enabled:old.mouth_enabled,furniture_track:{schema_version:'commander_furniture/1',cues:seats},dialogue_enabled:old.dialogue_enabled,review_confirmed:false,upgraded_from:old.upgraded_from||undefined})
  await apply(result);rooms.value=rc;sceneCache.set(rc.catalog_sha256,rc);notice.value='已另存含圆眼表情的版本，原有动作、换装、坐具和口型设置已保留。'
 }catch(e){error.value=String(e)}finally{busy.value=false}
}
function visibility(){if(document.hidden)pause()}
onMounted(async()=>{
 document.addEventListener('visibilitychange',visibility);window.addEventListener('pagehide',rememberPosition);animation=requestAnimationFrame(tick)
 // Occluded desktop windows can throttle rAF to 1 Hz while audio keeps playing.
 // Sample the same audio clock if rAF stalls; hidden pages still pause playback.
 fallbackTimer=window.setInterval(()=>{if(!disposed&&playing.value&&audio&&!document.hidden&&performance.now()-lastDraw>25)syncAudioTime()},16)
 try{
  const delivered=props.deliveryId?await api.h5Performance(props.deliveryId):undefined
  if(delivered)sourceId.value=delivered.manifest.source_job_id
  const capabilities=await api.independentCapabilities();deliveryEnabled.value=!!capabilities.h5_delivery?.configured;directingEnabled.value=!!capabilities.h5_directing?.configured
  if(!sourceId.value)throw Error('请从已完成配音的作品进入 H5 剧情演绎。')
  if(flowId)displayTitle.value=(await api.flow(flowId)).title
  else {try{const job=await api.independentJob(sourceId.value);displayTitle.value=(await api.work(job.work_id)).title}catch{/* Older direct links retain their artifact title. */}}
  const saved=delivered?.preview||(props.previewId?await api.h5Saved(sourceId.value,props.previewId):undefined)
  const [actors,ctx]=await Promise.all([api.h5Catalog(saved?.catalog_sha256),api.h5Prepare(sourceId.value,saved?.catalog_sha256)])
  const roomCatalog=await api.sceneCatalog(ctx.scene_catalog_sha256)
  if(disposed)return;catalog.value=actors;rooms.value=roomCatalog;context.value=ctx;sceneCache.set(roomCatalog.catalog_sha256,roomCatalog)
  for(const [i,r] of ctx.roles.slice(0,3).entries()){const a=actors.actors.find(a=>a.gender===(i%2?'female':'male'))||actors.actors[i];selected.value[r.role_id]=a.actor_id;outfits.value[r.role_id]=a.default_outfit_id!}
  selectedRole.value=ctx.roles[0]?.role_id||''
  sceneBindings.value=ctx.scenes.map(s=>({episode_id:s.episode_id,scene_id:s.scene_id,scene:{catalog_sha256:roomCatalog.catalog_sha256,scene_id:''},actors:[],confirmed:false,note:''}))
  try{savedVersions.value=JSON.parse(localStorage.getItem('h5-story-'+sourceId.value)||'[]')}catch{}
  mediaUrl=await stageAudio(sourceId.value,ctx.audio.artifact);if(disposed){URL.revokeObjectURL(mediaUrl);return}
  audio=new Audio(mediaUrl);audio.preload='auto';audio.onplay=()=>playing.value=true;audio.onpause=()=>playing.value=false;audio.onended=()=>{time.value=duration.value;pause();draw()};audio.onerror=()=>{pause();error.value='已有配音文件播放失败'}
  await nextTick();if(saved)await apply(saved)
  if(saved&&props.deliveryId){try{const position=JSON.parse(localStorage.getItem('h5-position:'+props.deliveryId)||'null');if(position?.preview===saved.preview_sha256&&Number.isFinite(position.time)){speed.value=[.5,1,1.5].includes(position.speed)?position.speed:1;setSpeed();seek(position.time)}}catch{}}
  await loadThumbs()
 }catch(e){error.value=String(e)}finally{loading.value=false}
 ;(window as any).__h5Story={get ready(){return ready.value},get preview(){return preview.value},get context(){return context.value},get draft(){return draft.value},get state(){return stage?.snapshot()},get time(){return time.value},get playing(){return playing.value},get audioTime(){return audio?.currentTime},get audioRate(){return audio?.playbackRate},get audioState(){return audio?{readyState:audio.readyState,networkState:audio.networkState,paused:audio.paused,muted:audio.muted,duration:audio.duration,error:audio.error?.code}:null},seek,pause,play:toggle,capture:()=>stage?.capture()}
})
onBeforeUnmount(()=>{disposed=true;loadGeneration++;cancelAnimationFrame(animation);clearInterval(fallbackTimer);pause();audio?.removeAttribute('src');audio?.load();stage?.destroy();if(mediaUrl)URL.revokeObjectURL(mediaUrl);for(const url of Object.values(thumbs.value))URL.revokeObjectURL(url);assetCache.clear();document.removeEventListener('visibilitychange',visibility);window.removeEventListener('pagehide',rememberPosition);delete (window as any).__h5Story})
defineExpose({pause})
</script>

<template>
 <div class="h5-story" :class="{'play-only':playOnly}">
  <StoryPlaybackShell v-if="playOnly" :title="displayTitle||context?.title||''" :room="activeRoom" :roles="playbackRoles" :speaker="activeLine[0]?.speaker||''" :role-id="activeLine[0]?.role_id||null" :text="activeLine.map(c=>c.text).join(' ')" :time="time" :duration="duration" :playing="playing" :ready="ready" :loading="loading" :error="error" :speed="speed" :muted="muted" :subtitles="showSubtitles" :auto-next="autoNext" @toggle="toggle" @seek="seek" @step="stepCue" @role="selectRole" @speed="speed=$event;setSpeed()" @mute="toggleMute" @subtitles="showSubtitles=!showSubtitles" @auto-next="toggleAutoNext" @work="returnToFlow" @edit="changeView"><template #stage><div ref="host" class="stage-host"></div></template></StoryPlaybackShell>
  <template v-if="!playOnly">
  <div v-if="flowId||parentDelivery" class="flow-nav"><HtmlButton @click="returnToFlow">返回作品</HtmlButton><HtmlButton @click="changeView">返回播放</HtmlButton></div>
  <header class="story-header"><div><small>我的传说 · 编辑舞台</small><h1>{{displayTitle||context?.title||'编辑舞台'}}</h1><p>{{directingMode?'确认人物和服装，让 AI 完成其余视觉编排。':'查看、播放和编辑已有配音的剧情演绎。'}}</p></div></header>
  <p v-if="loading" class="message">正在打开舞台…</p><p v-if="error" id="h5-error" role="alert" class="error">{{error}}</p><p v-if="notice&&!directingMode" role="status" class="notice">{{notice}}</p>
  <div v-if="context?.diagnostics.length" class="error"><p v-for="d in context.diagnostics" :key="d.code+d.scene_id">{{d.message}}</p></div>
  <p v-if="!directingMode&&commander&&preview?.schema_version==='spine_stage_preview/3.1'&&catalog?.actors.some(a=>a.face_profile&&!a.face_profile.eyes.surprised)" class="notice">这个保存版本使用较早的表情资源。<HtmlButton :disabled="busy||dirty" @click="upgradeFaces">补充圆眼表情（另存版本）</HtmlButton><span v-if="dirty">请先保存当前草稿。</span></p>
  <div v-if="directingEnabled" class="notice"><HtmlButton id="h5-ai-mode" :disabled="busy" @click="pause();directingMode=!directingMode;castingConfirmed=false">{{directingMode?'返回演绎编辑':'AI 编排演绎'}}</HtmlButton><p v-if="directingMode">只需确认人物和服装。AI 会重新选择场景、站位、动作和表情，独立审查后自动另存正式 H5；保留原配音和历史版本。</p></div>
  <main v-if="context" class="workspace" :class="{'casting-mode':directingMode}">
   <section class="config-panel panel"><h2>1 · 选择人物</h2><p class="hint">最多三位角色出镜，其余角色保留声音。</p>
    <div v-for="r in roles" :key="r.role_id" class="role-row"><HtmlButton :class="{active:selectedRole===r.role_id}" @click="selectedRole=r.role_id">{{r.name}}</HtmlButton><span>{{catalog?.actors.find(a=>a.actor_id===selected[r.role_id])?.display_name||'画外音'}}</span><HtmlButton v-if="selected[r.role_id]" class="quiet" @click="removeActor(r.role_id)">改为画外音</HtmlButton></div>
    <div v-if="!commander" class="filters"><HtmlInput v-model="search" placeholder="搜索服装、职业或风格" aria-label="搜索角色外观"/><select v-model="category" aria-label="角色风格"><option value="">全部风格</option><option v-for="(label,id) in categoryLabels" :key="id" :value="id">{{label}}</option></select></div>
    <div class="character-grid"><HtmlButton v-for="a in choices" :key="a.actor_id" :data-character="a.actor_id" class="character" :class="{active:selected[selectedRole]===a.actor_id}" :disabled="busy||(!selected[selectedRole]&&chosenActors.length>=3)" @click="chooseActor(a.actor_id)"><img v-if="thumbs[a.actor_id]" :src="thumbs[a.actor_id]" :alt="a.display_name"><span>{{a.display_name}}</span></HtmlButton></div>
    <template v-if="commander"><p class="hint">挑选你喜欢的服装。</p><div class="filters"><HtmlInput v-model="search" placeholder="搜索服装或风格"/><select v-model="category"><option value="">全部服装</option><option v-for="(label,id) in categoryLabels" :key="id" :value="id">{{label}}</option></select></div><div class="character-grid wardrobe"><HtmlButton v-for="o in wardrobe" :key="o.outfit_id" :data-outfit="o.outfit_id" class="character" :class="{active:outfits[selectedRole]===o.outfit_id}" @click="chooseOutfit(selectedRole,o.outfit_id)"><img v-if="thumbs[o.outfit_id]" :src="thumbs[o.outfit_id]" :alt="o.display_name"><span>{{o.display_name}}</span></HtmlButton></div></template>
    <div v-for="a in chosenActors" :key="a.role_id" class="outfit-row"><HtmlLabel>{{a.name}} · {{commander?'初始服装':'头部与发型'}}<select :value="outfits[a.role_id]" @change="chooseOutfit(a.role_id,($event.target as HTMLSelectElement).value)"><option v-for="o in catalog?.outfits.filter(o=>o.actor_id===a.actor_id)" :key="o.outfit_id" :value="o.outfit_id">{{o.display_name}}</option></select></HtmlLabel></div>
    <div v-if="directingMode" class="buttons"><HtmlLabel><HtmlInput id="h5-casting-confirm" v-model="castingConfirmed" type="checkbox" :disabled="busy"/>我确认以上人物和服装，其余由 AI 编排</HtmlLabel><HtmlButton id="h5-direct" class="primary" :disabled="busy||!castingConfirmed||!directingEnabled||!commander||!chosenActors.length" @click="direct">{{busy?'正在提交……':'重新编排舞台'}}</HtmlButton></div>
   </section>
   <section v-show="!directingMode" class="theatre-panel panel"><div v-if="preview?.schema_version==='spine_stage_preview/3.0'" class="notice"><p>旧版使用套装头部时，升级将统一为已确认的男女指挥官头部与发型。原保存版本保留。</p><HtmlButton id="h5-upgrade" :disabled="busy" @click="upgrade">另存为固定指挥官版本</HtmlButton></div><div class="panel-heading"><h2>剧情舞台</h2><span>{{activeRoom}}</span></div><div class="theatre"><div ref="host" class="stage-host"></div><div v-if="!ready" class="stage-empty">确认人物与各分场场景后<br>点击“应用场景设置”</div></div>
    <div class="subtitle"><p v-for="c in activeLine" :key="c.source.line_id"><b>{{c.speaker}}</b> {{c.text}}</p><span v-if="!activeLine.length">{{ready?'当前无对白':'字幕将与已有配音同步显示'}}</span></div>
    <HtmlInput id="h5-seek" type="range" min="0" :max="duration" step=".01" :value="time" aria-label="剧情播放进度" :disabled="!ready" @input="seek(Number(($event.target as HTMLInputElement).value))"/>
    <div class="transport"><HtmlButton id="h5-play" class="primary" :disabled="!ready||busy" @click="toggle">{{playing?'暂停':'播放'}}</HtmlButton><HtmlButton :disabled="!ready" @click="seek(0)">重播定位</HtmlButton><select v-model.number="speed" aria-label="播放倍速" @change="setSpeed"><option :value=".5">½ 倍速</option><option :value="1">正常速度</option><option :value="1.5">1.5 倍速</option></select><span>{{fmt(time)}} / {{fmt(duration)}}</span></div>
    <p class="hint">{{dirty?'舞台显示上次保存版本，当前草稿尚未应用。':''}}</p>
    <p v-if="parentDelivery" class="hint">保存后会生成新版本，原来的作品仍然可以播放。</p><div class="buttons"><HtmlButton v-if="deliveryEnabled" id="h5-deliver" :disabled="!preview||busy" @click="deliver">保存新版本</HtmlButton><HtmlButton :disabled="!preview||busy" @click="share">复制作品链接</HtmlButton></div>
    <HtmlLabel v-if="savedVersions.length" class="history">恢复保存版本<select aria-label="恢复保存版本" :value="preview?.preview_sha256||''" :disabled="busy" @change="restore(($event.target as HTMLSelectElement).value)"><option v-for="(v,i) in savedVersions" :key="v.id" :value="v.id">{{i===0?'最近保存':'第 '+(savedVersions.length-i)+' 次保存'}}</option></select></HtmlLabel>
   </section>
   <section v-if="!directingMode" class="scenes-panel panel"><h2>2 · 分场方案</h2><p class="hint">选择场景，调整角色的站位和朝向。</p>
    <div class="scene-tabs"><HtmlButton v-for="(s,i) in context.scenes" :key="s.episode_id+s.scene_id" :class="{active:selectedScene===i}" @click="selectedScene=i">第 {{i+1}} 场 · {{s.title}}</HtmlButton></div>
    <template v-if="currentScene"><div class="scene-detail"><p><b>原文地点：</b>{{context.scenes[selectedScene].location||'原文未指定地点'}} <span class="hint">{{fmt((context.scenes[selectedScene].start_frame||0)/context.sample_rate)}}–{{fmt((context.scenes[selectedScene].end_frame||0)/context.sample_rate)}}</span></p>
     <HtmlLabel>使用场景<select id="h5-scene" :value="currentScene.scene.scene_id" @change="chooseScene(selectedScene,($event.target as HTMLSelectElement).value)"><option value="">请选择场景</option><option v-for="room in rooms?.scenes" :key="room.scene_id" :value="room.scene_id">{{room.display_name}}</option></select></HtmlLabel><HtmlButton v-if="selectedScene>0" @click="copyPrevious(selectedScene)">复用上一场配置</HtmlButton>
     <p v-if="currentRoom" class="hint">{{currentRoom.description}} · {{currentRoom.blocking?.targets.length||0}} 个物件停靠点</p>
     <div v-for="a in currentScene.actors" :key="a.instance_id" class="placement"><b>{{roleName(a.instance_id)}}</b><HtmlLabel><HtmlInput v-model="a.visible" type="checkbox" @change="currentScene.confirmed=false;mark()"/>开场在场</HtmlLabel><HtmlLabel>横向站位<HtmlInput v-model.number="a.position[0]" type="number" min=".06" max=".94" step=".01" @change="currentScene.confirmed=false;mark()"/></HtmlLabel><HtmlLabel>朝向<select v-model.number="a.facing" @change="currentScene.confirmed=false;mark()"><option :value="1">向右</option><option :value="-1">向左</option></select></HtmlLabel></div>
     <details><summary>场景备注</summary><HtmlLabel class="full-label">备注<HtmlInput v-model="currentScene.note" maxlength="500" placeholder="记下这场戏的想法（选填）" @input="mark"/></HtmlLabel></details><HtmlLabel class="confirm"><HtmlInput v-model="currentScene.confirmed" type="checkbox" :disabled="!currentRoom" @change="mark"/>使用这个场景与站位</HtmlLabel>
    </div></template>
    <div class="buttons"><HtmlLabel><HtmlInput v-model="dialogue" type="checkbox" @change="mark"/>启用双人对白朝向</HtmlLabel><HtmlButton id="h5-generate" class="primary" :disabled="busy||!chosenActors.length||!!context.diagnostics.length" @click="generate">{{busy?'正在准备……':'应用场景设置'}}</HtmlButton></div><p v-if="preview" class="hint">重新应用场景会更新动作，请先保存当前调整。</p>
   </section>
   <section v-if="!directingMode&&preview&&draft" class="review-panel panel"><h2>3 · 动作与表情</h2><details><summary>查看剧情动作</summary>
    <details v-if="preview.interpretation_notes?.length" class="notice"><summary>演绎说明 · {{preview.interpretation_notes.length}} 项</summary><p v-for="(note,i) in preview.interpretation_notes" :key="i">{{note.message}}</p></details>
    <div v-if="!preview.coverage.length" class="notice">还没有安排动作，可以从下面添加。</div>
    <div v-for="item in preview.coverage" :key="item.id" class="coverage" :data-coverage="item.id"><div class="panel-heading"><b>{{item.text}}</b><span class="badge" :class="item.status">{{item.presentation?presentationLabels[item.presentation]:coverageLabels[item.status]}}</span></div><p>{{item.message}}</p><div class="buttons"><HtmlButton @click="seek(item.start_frame/context.sample_rate)">定位原文</HtmlButton><HtmlButton v-if="item.status!=='unsupported'&&item.status!=='not_executed'" @click="correct(item)">校正动作</HtmlButton></div><div v-if="['unsupported','needs_correction','omitted'].includes(item.status)" class="omission"><HtmlInput v-model="omitText[item.id]" placeholder="填写本版不演出的说明" maxlength="500"/><HtmlButton @click="omit(item)">记录省略</HtmlButton><small v-if="decisions.some(d=>d.coverage_id===item.id)">处理意见已加入草稿</small></div></div>
    <div v-for="d in preview.diagnostics.filter(d=>d.code==='UNBOUND_ROLE')" :key="d.message" class="notice">{{d.message}}</div>
    </details><div class="panel-heading"><h3>角色动作</h3><HtmlButton id="h5-add-cue" @click="editCue()">添加动作</HtmlButton></div>
    <div v-for="c in draft.cues" :key="c.id" class="cue-row"><HtmlLabel><HtmlInput v-model="c.enabled" type="checkbox" @change="mark"/>{{roleName(c.instance_id)}}</HtmlLabel><span>{{c.type==='body.play'?actionLabels[c.params.action!]:c.type==='actor.change_outfit'?'换装':c.type==='actor.move_to'?'移动':'位置／朝向'}} · {{fmt(c.start_frame/draft.sample_rate)}}–{{fmt(c.end_frame/draft.sample_rate)}}</span><HtmlButton @click="editCue(c)">编辑</HtmlButton><HtmlButton class="quiet" @click="deleteCue(c.id)">删除</HtmlButton></div>
    <div v-if="editing" class="cue-editor"><h3>{{form.id?'编辑':'添加'}}动作</h3><div class="form-grid"><HtmlLabel>人物<select v-model="form.instanceId"><option v-for="a in draft.actors" :key="a.instance_id" :value="a.instance_id">{{a.name}}</option></select></HtmlLabel><HtmlLabel>指令<select v-model="form.kind"><option v-if="commander" value="outfit">换装（保持身份与头部）</option><option value="move">移动</option><option value="body">动作</option><option value="place">位置／朝向／在场状态</option></select></HtmlLabel><HtmlLabel>开始秒<HtmlInput v-model.number="form.start" type="number" min="0" step=".01"/></HtmlLabel><HtmlLabel v-if="!['place','outfit'].includes(form.kind)">结束秒<HtmlInput v-model.number="form.end" type="number" min="0" step=".01"/></HtmlLabel><template v-if="form.kind==='body'"><HtmlLabel>动作<select v-model="form.action"><option v-for="(_,name) in formActor?.actions" :key="name" :value="name">{{actionLabels[name]||name}}</option></select></HtmlLabel><HtmlLabel>播放方式<select v-model="form.mode"><option value="loop">循环</option><option value="once">一次</option><option value="hold">保持末帧</option></select></HtmlLabel></template><template v-else-if="form.kind==='outfit'"><HtmlLabel>服装<select v-model="form.outfitId"><option v-for="o in catalog?.outfits.filter(o=>o.actor_id===formActor?.actor_id)" :key="o.outfit_id" :value="o.outfit_id">{{o.display_name}}</option></select></HtmlLabel></template><template v-else><HtmlLabel v-if="form.kind==='move'">移动目标<select v-model="form.target"><option value="">指定坐标</option><option v-for="p in editingRoom?.blocking?.targets" :key="p.id" :value="'object:'+p.id">物件 · {{p.label}}</option><option v-for="a in draft.actors.filter(a=>a.instance_id!==form.instanceId)" :key="a.instance_id" :value="'actor:'+a.instance_id">人物 · {{a.name}}</option></select></HtmlLabel><HtmlLabel>横向位置<HtmlInput v-model.number="form.x" type="number" min=".06" max=".94" step=".01" :disabled="!!form.target&&form.kind==='move'"/></HtmlLabel><HtmlLabel v-if="form.kind==='place'">朝向<select v-model.number="form.facing"><option :value="1">向右</option><option :value="-1">向左</option></select></HtmlLabel><HtmlLabel><HtmlInput v-model="form.visible" type="checkbox"/>{{form.kind==='move'?'移动结束后留在场内':'在场'}}</HtmlLabel></template></div><p class="hint">{{form.sourceText||'为角色添加一个动作。'}}</p><div class="buttons"><HtmlButton class="primary" @click="addCue">加入草稿</HtmlButton><HtmlButton @click="editing=false">取消</HtmlButton></div></div>
    <div v-if="commander" class="faces"><h3>口型与家具</h3><HtmlLabel v-for="a in draft.actors" :key="a.instance_id"><HtmlInput v-model="mouthEnabled[a.instance_id]" :disabled="!hasFace(a.instance_id)" type="checkbox" @change="mark"/>{{a.name}} · {{hasFace(a.instance_id)?'对白自动开合嘴':'保留原生表情'}} </HtmlLabel><p class="hint">带独立面部的角色可调整口型和表情；人形保留原生表情，坐姿与躺卧可在“添加动作”中安排。</p><HtmlButton id="h5-add-seat" :disabled="!draft.actors.some(a=>hasFace(a.instance_id))" @click="editSeat()">安排角色入座</HtmlButton><div v-for="c in furniture" :key="c.id" class="cue-row"><HtmlLabel><HtmlInput v-model="c.enabled" type="checkbox" @change="mark"/>{{roleName(c.instance_id)}}</HtmlLabel><span>{{seatLabel(c)}} · {{fmt(c.start_frame/draft.sample_rate)}}–{{fmt(c.end_frame/draft.sample_rate)}} · {{c.source?'原文动作':'自定义动作'}}</span><HtmlButton @click="editSeat(c)">编辑</HtmlButton><HtmlButton @click="furniture=furniture.filter(x=>x.id!==c.id);mark()">删除</HtmlButton></div>
    <div v-if="seatEditing" class="cue-editor"><h3>坐家具时间</h3><div class="form-grid"><HtmlLabel>人物<select v-model="seatForm.instanceId"><option v-for="a in draft.actors.filter(a=>hasFace(a.instance_id))" :key="a.instance_id" :value="a.instance_id">{{a.name}}</option></select></HtmlLabel><HtmlLabel>开始接近秒<HtmlInput v-model.number="seatForm.start" type="number" min="0" step=".01"/></HtmlLabel><HtmlLabel>实际座位<select v-model="seatForm.seatId"><option value="">请选择座位</option><option v-for="s in seatRoom?.commander_seats" :key="s.seat_id" :value="s.seat_id">{{s.label}}</option></select></HtmlLabel><HtmlLabel>到达秒<HtmlInput v-model.number="seatForm.arrive" type="number" min="0" step=".01"/></HtmlLabel><HtmlLabel><HtmlInput v-model="seatForm.hold" type="checkbox"/>保持至本场结束</HtmlLabel><template v-if="!seatForm.hold"><HtmlLabel>起身秒<HtmlInput v-model.number="seatForm.leave" type="number" min="0" step=".01"/></HtmlLabel><HtmlLabel>离开完成秒<HtmlInput v-model.number="seatForm.end" type="number" min="0" step=".01"/></HtmlLabel></template></div><p class="hint">{{seatForm.sourceText||'为角色安排一个座位。'}}</p><HtmlButton @click="addSeat">加入草稿</HtmlButton><HtmlButton @click="seatEditing=false">取消</HtmlButton></div></div>
    <details class="faces"><summary>表情</summary><HtmlLabel v-if="commander">通道<select v-model="faceForm.channel" @change="faceForm.preset='native'"><option value="eyes">眼部</option><option value="mouth">嘴部（优先于自动口型）</option></select></HtmlLabel><div class="form-grid"><HtmlLabel>人物<select v-model="faceForm.instanceId" @change="faceForm.preset='native'"><option v-for="a in draft.actors" :key="a.instance_id" :value="a.instance_id">{{a.name}}</option></select></HtmlLabel><HtmlLabel>表情<select v-model="faceForm.preset"><option v-for="p in faceOptions" :key="p" :value="p">{{commander&&faceForm.channel==='mouth'?({native:'原生嘴部',open:'开嘴',closed:'闭嘴'} as Record<string,string>)[p]:faceLabels[p]}}</option></select></HtmlLabel><HtmlLabel>开始秒<HtmlInput v-model.number="faceForm.start" type="number" min="0" step=".01"/></HtmlLabel><HtmlLabel>结束秒<HtmlInput v-model.number="faceForm.end" type="number" min="0" step=".01"/></HtmlLabel></div><HtmlButton @click="addFace">添加表情</HtmlButton><div v-for="c in expressions" :key="c.id" class="cue-row"><span>{{roleName(c.instance_id)}} · {{expressionLabel(c)}} · {{fmt(c.start_frame/draft.sample_rate)}}–{{fmt(c.end_frame/draft.sample_rate)}}</span><HtmlButton @click="expressions=expressions.filter(x=>x.id!==c.id);mark()">删除</HtmlButton></div></details>
    <div class="save-bar"><span>{{dirty?'有未应用草稿':'当前版本已保存'}}</span><HtmlButton id="h5-save" class="primary" :disabled="busy" @click="save(false)">应用并保存</HtmlButton></div>
   </section>
  </main>
  </template>
 </div>
</template>
<style scoped>
.workspace.casting-mode{grid-template-columns:1fr;max-width:960px}*{box-sizing:border-box}.h5-story{background:#fff6e6;color:#553b29;min-height:100vh;padding:24px;font:14px/1.6 'Microsoft YaHei',system-ui,sans-serif}.story-header{max-width:1500px;margin:0 auto 20px;background:#79402e;color:#f7f5e9;border-radius:16px;padding:26px 30px;display:flex;align-items:center;justify-content:space-between;gap:16px}.story-header small{letter-spacing:2px;color:#f2d7b4}.story-header h1{font-size:27px;margin:5px 0}.story-header p{margin:0;color:#ecd3b7}.status{font-size:12px;background:#ffffff18;padding:7px 12px;border-radius:18px;white-space:nowrap}.workspace{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.15fr);gap:20px;max-width:1500px;margin:auto}.panel{background:#fff;border:1px solid #ead8c3;border-radius:15px;padding:20px;min-width:0}.panel h2{font-size:19px;margin:0 0 8px}.panel h3{font-size:15px;margin:12px 0}.hint{font-size:12px;color:#92775e;margin:8px 0}.role-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:8px 0}.role-row span{font-size:12px;flex:1}.filters{display:flex;gap:8px;margin:14px 0}.filters .native-input{min-width:0;flex:1}.native-button,.native-input,select{font:inherit;color:inherit}.native-button{cursor:pointer;background:#fff9f0;border:1px solid #e3cbb2;border-radius:7px;padding:7px 11px;line-height:1.5;margin:0;min-height:34px}.native-button:disabled{opacity:.45;cursor:default}.primary,.native-button.active{background:#bd4c2d;border-color:#bd4c2d;color:#fff}.quiet{background:transparent;font-size:11px;color:#8b7867}.character-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;max-height:340px;overflow:auto;padding:3px}.character{padding:0;overflow:hidden;background:#f1f3ee;display:flex;flex-direction:column;align-items:stretch;min-width:0}.character img{width:100%;height:110px;object-fit:contain}.character span{display:block;font-size:10px;padding:6px 4px;line-height:1.5}.character.active{outline:2px solid #397b5c;background:#e8efdf;color:#244f3e}.outfit-row{margin-top:10px}.outfit-row .native-label{display:flex;align-items:center;gap:10px;font-size:12px}.outfit-row select{flex:1;min-width:0}select,.native-input[type=number],.native-input[type=text]{background:white;border:1px solid #ccd6c9;border-radius:6px;padding:7px;max-width:100%}.native-input[type=checkbox]{accent-color:#bd4c2d}.panel-heading{display:flex;align-items:center;justify-content:space-between;gap:10px}.panel-heading>span{font-size:12px;color:#75846f}.theatre-panel{align-self:start;position:static}.theatre{position:relative;aspect-ratio:16/9;border-radius:11px;overflow:hidden;background:#e6eadd;margin-top:10px}.stage-host{position:absolute;inset:0}.stage-empty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;text-align:center;color:#8b9a86;line-height:2}.subtitle{min-height:70px;background:#f2f5ed;border-radius:9px;padding:12px;margin:12px 0;font-size:13px}.subtitle p{margin:0}.subtitle span{color:#929d88}.transport{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.transport span{margin-left:auto;font-size:11px;font-variant-numeric:tabular-nums}.native-input[type=range]{width:100%;accent-color:#bd4c2d}.buttons{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:12px}.buttons .native-label{font-size:12px}.history{display:flex;align-items:center;gap:8px;margin-top:14px;font-size:11px}.history select{flex:1;min-width:0}.scenes-panel,.review-panel{grid-column:1/-1}.scene-tabs{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}.scene-tabs .native-button{font-size:12px}.scene-detail{background:#fcf5e9;padding:16px;border-radius:10px}.scene-detail>.native-label:not(.confirm){display:inline-flex;gap:8px;align-items:center}.scene-detail>.native-button{margin-left:8px}.placement{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin:14px 0}.placement .native-label{font-size:12px}.placement .native-input[type=number]{width:85px}.full-label{width:100%;margin:12px 0}.full-label .native-input{flex:1;min-width:0}.confirm{display:block;font-size:13px;margin-top:12px}.coverage{border:1px solid #ead8c3;border-radius:10px;padding:14px;margin:12px 0}.coverage p{font-size:12px;color:#7c8975}.badge{padding:3px 8px;border-radius:5px;font-size:11px!important;white-space:nowrap}.badge.compiled,.badge.manual{background:#e2efdf;color:#36704c}.badge.unsupported,.badge.needs_correction{background:#fff0dc;color:#916421}.badge.omitted{background:#eee6dd;color:#826b51}.omission{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:9px}.omission .native-input{flex:1;min-width:160px;font-size:12px}.omission small{color:#648161}.cue-row{display:flex;align-items:center;gap:9px;flex-wrap:wrap;padding:8px 0;border-bottom:1px solid #edf0e8;font-size:12px}.cue-row span{flex:1}.cue-row .native-button{font-size:11px}.cue-editor{background:#fbefdc;padding:16px;border-radius:10px;margin:14px 0}.form-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:10px 0}.form-grid .native-label{display:flex;flex-direction:column;gap:5px;font-size:12px;min-width:0}.form-grid .native-label select,.form-grid .native-label .native-input{width:100%;min-width:0}.form-grid .native-input[type=checkbox]{width:auto}.faces{margin:18px 0;font-size:13px}.save-bar{border-top:1px solid #e0e5d9;display:flex;align-items:center;justify-content:flex-end;gap:10px;padding-top:16px;flex-wrap:wrap}.save-bar>span{margin-right:auto;color:#7c8976;font-size:12px}.message,.notice,.error{max-width:1500px;margin:10px auto;padding:10px 15px;border-radius:8px;font-size:13px;overflow-wrap:anywhere}.notice{background:#f5ead6}.error{background:#f9e9df;color:#974631}.role-row .native-button,.transport .native-button{white-space:nowrap}.native-button:focus-visible,.native-input:focus-visible,select:focus-visible{outline:3px solid #d7b45e;outline-offset:2px}
@media(max-width:900px){.h5-story{padding:14px}.workspace{grid-template-columns:minmax(0,1fr)}.theatre-panel{position:static;grid-row:1}.config-panel{grid-row:2}.panel{padding:16px}.story-header{padding:20px;align-items:flex-start}.story-header h1{font-size:23px}.story-header .status{white-space:normal}.form-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.scenes-panel,.review-panel{grid-column:1}.character-grid{max-height:290px}.placement{gap:9px}.scene-tabs{gap:6px}.scene-tabs .native-button{max-width:100%;white-space:normal}}
@media(max-width:400px){.h5-story{padding:10px}.panel{padding:12px}.story-header{padding:16px;flex-direction:column;gap:8px}.character-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.filters{flex-wrap:wrap}.filters .native-input{width:100%;flex-basis:100%}.scene-detail{padding:10px}.scene-detail>.native-label:not(.confirm){flex-wrap:wrap}.scene-detail>.native-button{margin:8px 0 0}.panel-heading{align-items:flex-start;flex-wrap:wrap}.full-label .native-input{flex-basis:100%}.transport .native-button,.transport select{font-size:12px}.form-grid{gap:8px}.save-bar{justify-content:flex-start}.coverage{padding:10px}.coverage .buttons .native-button{font-size:12px}.omission .native-input{min-width:0;flex-basis:100%}}
</style>

<style scoped>
.flow-nav{display:flex;justify-content:space-between;max-width:1500px;margin:0 auto 12px;gap:12px}
.h5-story.play-only{padding:0;background:#2d211b}
.play-only .workspace{display:block;max-width:1100px}.play-only .config-panel,.play-only .scenes-panel,.play-only .review-panel,.play-only>.notice,.play-only .theatre-panel>.hint,.play-only .theatre-panel>.buttons,.play-only .theatre-panel>.history{display:none}.play-only .theatre-panel{position:static}.play-only .story-header{max-width:1100px}
</style>

