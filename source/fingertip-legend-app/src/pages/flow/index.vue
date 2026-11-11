<script setup lang="ts">
import {computed,ref} from 'vue'
import {onLoad,onShow,onHide,onUnload} from '@dcloudio/uni-app'
import FeatureLayout from '../../components/FeatureLayout.vue'
import VoiceRevisionEditor from '../../components/VoiceRevisionEditor.vue'
import FlowCastingPicker from '../../components/FlowCastingPicker.vue'
import {api} from '../../services/api'
import {readProductionArtifact,downloadProductionMedia,releaseProductionMedia} from '../../services/production-media'
import type {ProductionFlow,FlowCasting} from '../../types/flow'
import type {WorkSummary} from '../../types/works'
import {openDestination,versionKind} from '../../utils/works'
import type {StoryDeliveryView,AudioRemakeSelection} from '../../types/production'
import type {VoiceRevisionState} from '../../utils/voice-revision'
import {jobStatus,productionError,requestKey} from '../../utils/production'
import {errorMessage,goPage,requireSession} from '../../utils/navigation'
const id=ref(''),flow=ref<ProductionFlow>(),error=ref(''),busy=ref(false),tab=ref('stage'),script=ref<StoryDeliveryView>(),audioUrl=ref('')
const showHistory=ref(false)
const history=ref<ProductionFlow[]>([]),historyNext=ref<number|null>(null),historyBusy=ref(false)
const workSummary=ref<WorkSummary>()
const instruction=ref(''),operation=ref('revise'),retain=ref(''),transform=ref(''),editing=ref(''),casting=ref<FlowCasting|null>(null)
const voiceState=ref<VoiceRevisionState>({ready:false,bindings:{},changedCount:0,affectedSegments:0}),selected=ref<AudioRemakeSelection[]>([])
let timer:ReturnType<typeof setTimeout>|undefined,active=true,epoch=0,loading=false,mediaKey=''
const sound=computed(()=>flow.value?.jobs.sound)
const ready=computed(()=>flow.value?.available_outputs||[])
const canEdit=computed(()=>!!sound.value&&['succeeded','failed','cancelled'].includes(sound.value.status)&&sound.value.unsettled_calls===0&&['StoryDelivery','RenderManifest'].every(k=>sound.value!.artifacts.some(a=>a.kind===k)))
const recoveryActions=computed(()=>flow.value?.recovery?.actions||[])
const partial=computed(()=>!!sound.value&&!sound.value.artifacts.some(a=>a.kind==='AudioExecutionArrangement')&&['AudioCache','VoiceBindings'].every(k=>sound.value!.artifacts.some(a=>a.kind===k))&&sound.value.request.audio_delivery_policy!=='audio_first')
const roles=computed(()=>script.value?.character_snapshot.map(r=>({id:r.role_id,name:r.canon?.name||r.role_id}))||[])
const resultTabs=computed(()=>[{id:'script',name:'剧本'},...(flow.value?.output!=='script'?[{id:'audio',name:'声音'}]:[]),...(flow.value?.output==='h5'?[{id:'stage',name:'舞台'}]:[])])
const phases=computed(()=>{
 const jobs=flow.value?.jobs;const s=jobs?.sound?.stages||[],v=jobs?.stage?.stages||[]
 const aggregate=(names:string[],stages:typeof s)=>{const found=stages.filter(x=>names.includes(x.name));return found.some(x=>x.status==='failed')?'failed':found.some(x=>x.status==='needs_input')?'needs_input':found.some(x=>x.status==='cancelled')?'cancelled':found.length&&found.every(x=>x.status==='succeeded')?'succeeded':found.some(x=>x.status==='running')?'running':'queued'}
 return [{name:'剧本生成',status:aggregate(['story'],s)},{name:'声音制作',status:aggregate(['sound_plan','audio'],s)},{name:'舞台编排',status:aggregate(['performance_context','spine_direct','spine_compile','performance_import'],v)},{name:'作品完成',status:aggregate(['performance_package'],v)}].filter((_,i)=>i===0||flow.value?.output!=='script'&&(i===1||flow.value?.output==='h5'))
})
async function versions(more=false){if(!flow.value||historyBusy.value)return;historyBusy.value=true;const token=epoch;try{const [result,summary]=await Promise.all([api.flows(more?(historyNext.value||0):0,flow.value.work_id),api.work(flow.value.work_id)]);if(token!==epoch)return;history.value=more?[...history.value,...result.items]:result.items;historyNext.value=result.next_offset;workSummary.value=summary}catch{}finally{historyBusy.value=false}}
async function load(){
 if(!active||!id.value||loading||busy.value)return;loading=true;const token=epoch;clearTimeout(timer)
 try{const next=await api.flow(id.value);if(token!==epoch)return;const changed=flow.value?.status!==next.status;flow.value=next;error.value='';if(!resultTabs.value.some(t=>t.id===tab.value))tab.value=next.output==='audio'?'audio':'script';if(changed)await versions();if(tab.value==='audio'&&next.available_outputs.includes('audio'))await selectTab('audio')
 const ref=next.jobs.sound?.artifacts.find(a=>a.kind==='StoryDelivery')
 if(ref&&!script.value){const value=await readProductionArtifact<StoryDeliveryView>(next.sound_job_id!,ref);if(token!==epoch)return;script.value=value}
 if(!casting.value)casting.value=next.casting
 if(!history.value.length)await versions()
 }catch(e){if(token===epoch)error.value=errorMessage(e)}finally{if(token===epoch){loading=false;if(active)timer=setTimeout(load,3000)}}
}
function stop(){active=false;epoch++;loading=false;clearTimeout(timer)}
function syncLocation(){
 if(!active||typeof window==='undefined'||!window.location.hash.startsWith('#/pages/flow/index?'))return
 const next=new URLSearchParams(window.location.hash.split('?')[1]).get('id')||''
 if(next&&next!==id.value){epoch++;clearTimeout(timer);id.value=next;flow.value=undefined;workSummary.value=undefined;script.value=undefined;history.value=[];casting.value=null;editing.value='';selected.value=[];mediaKey='';releaseProductionMedia(audioUrl.value);audioUrl.value='';loading=false;load()}
}
onLoad(q=>{id.value=String(q?.id||'');if(typeof window!=='undefined')window.addEventListener('hashchange',syncLocation);if(requireSession()){active=true;load()}})
onShow(()=>{if(requireSession()){active=true;syncLocation();load()}});onHide(stop);onUnload(()=>{stop();if(typeof window!=='undefined')window.removeEventListener('hashchange',syncLocation);releaseProductionMedia(audioUrl.value)})
async function selectTab(value:string){tab.value=value;if(value!=='audio'||!sound.value)return;const ref=sound.value.artifacts.find(a=>a.kind==='AudioMix');if(!ref||mediaKey===ref.sha256)return;const token=epoch;try{const url=await downloadProductionMedia(sound.value.id,ref);if(token!==epoch){releaseProductionMedia(url);return}releaseProductionMedia(audioUrl.value);audioUrl.value=url;mediaKey=ref.sha256}catch(e){error.value=errorMessage(e)}}
function openStage(edit=false){if(!flow.value?.stage_job_id)return;goPage('performance',{profile:'life_v2',deliveryId:flow.value.stage_job_id,flowId:flow.value.id,view:edit?'edit':'play'})}
async function control(action:'cancel'|'resume'|'reconcile'){if(!flow.value||busy.value)return;busy.value=true;epoch++;loading=false;clearTimeout(timer);try{flow.value=await api.controlFlow(flow.value.id,action,flow.value.revision);error.value='';await versions()}catch(e){error.value=errorMessage(e)}finally{busy.value=false;if(active)timer=setTimeout(load,3000)}}
async function remake(){if(flow.value?.recovery)await revise(flow.value.recovery.remake_kind,true)}
async function revise(kind:string,acknowledgeUnknown=false){
 if(!flow.value||busy.value)return;busy.value=true;error.value=''
 try{const body:Record<string,unknown>={expected_revision:flow.value.revision,kind}
 if(acknowledgeUnknown)body.acknowledge_unknown_source=true
 if(['revise','continue','remix'].includes(kind)){if(!instruction.value.trim())throw Error('请填写修改要求');body.instruction=instruction.value.trim();if(kind==='remix'){if(!retain.value.trim()||!transform.value.trim())throw Error('请填写保留内容和改编方向');body.retain_elements=[retain.value.trim()];body.transform_elements=[transform.value.trim()]}}
 if(kind==='stage'){if(!casting.value)throw Error('请选择出镜人物和服装');body.casting=casting.value}
 if(kind==='audio'){body.regenerate=selected.value;if(voiceState.value.changedCount)body.voices=voiceState.value.bindings;if(!selected.value.length&&!voiceState.value.changedCount)throw Error('请选择需要重制的台词或更换音色')}
 if(kind==='audio_full'&&voiceState.value.changedCount)body.voices=voiceState.value.bindings
 const signature=JSON.stringify(body),storage='flow-revision:'+flow.value.id,pending=uni.getStorageSync(storage),key=pending?.signature===signature?pending.key:requestKey()
 uni.setStorageSync(storage,{signature,key});const next=await api.reviseFlow(flow.value.id,{...body,idempotency_key:key});uni.removeStorageSync(storage);goPage('flow',{id:next.id})
 }catch(e){error.value=errorMessage(e)}finally{busy.value=false}
}
function toggle(ep:string,sc:string,line:string){const i=selected.value.findIndex(v=>v.episode_id===ep&&v.scene_id===sc&&v.line_id===line);if(i>=0)selected.value.splice(i,1);else selected.value.push({episode_id:ep,scene_id:sc,line_id:line})}
function picked(line:string){return selected.value.some(x=>x.line_id===line)}
</script>
<template><FeatureLayout :title="flow?.title||'作品制作'" subtitle="从一个灵感，到一场属于你的演出" active="stories"><view class="page-content">
<text v-if="error" class="error-copy" role="alert">{{error}}</text><view v-if="!flow" class="paper-panel">正在读取作品…</view>
<template v-else><view class="paper-panel"><view class="row-between"><text class="section-title serif">{{flow.title}}</text><text class="pill">{{jobStatus[flow.status]}}</text></view>
<view v-for="phase in (flow.status==='succeeded'?[]:phases)" :key="phase.name" class="phase"><text>{{phase.name}}</text><text>{{phase.status==='queued'?'等待开始':jobStatus[phase.status as keyof typeof jobStatus]||phase.status}}</text></view>
<text v-if="flow.error_detail?.message" class="error-copy spaced" role="alert">{{flow.error_detail.message}}</text><text v-if="flow.recovery" class="small-note spaced" role="status">{{flow.recovery.message}}</text>
<button v-if="workSummary?.play&&workSummary.playable_flow_id!==flow.id&&!ready.includes('h5')" class="outline-button full-button" @tap="openDestination(workSummary.play)">播放上一完成版本</button><text class="small-note spaced">{{versionKind[flow.kind]||'作品版本'}} · {{new Date(flow.created_at).toLocaleString()}}</text><text v-if="flow.error_code&&!flow.recovery" class="error-copy spaced">{{productionError(flow.error_code)}}</text>
<button v-if="['queued','running'].includes(flow.status)" class="outline-button full-button" :disabled="busy" @tap="control('cancel')">取消制作</button>
<button v-if="recoveryActions.includes('reconcile')" class="outline-button full-button" :disabled="busy" @tap="control('reconcile')">查询结果</button>
<button v-if="recoveryActions.includes('resume')" class="warm-button full-button" :disabled="busy" @tap="control('resume')">继续制作</button>
<button v-if="recoveryActions.includes('remake')" class="outline-button full-button" :disabled="busy" @tap="remake">重新制作</button>
</view>
<view class="paper-panel section-gap"><view class="chip-list"><button v-for="item in resultTabs" :key="item.id" class="chip" :class="{selected:tab===item.id}" @tap="selectTab(item.id)">{{item.name}}</button></view>
<view v-if="tab==='stage'" class="result"><template v-if="ready.includes('h5')"><text class="section-title serif">故事准备好了</text><button class="warm-button full-button" @tap="openStage()">播放作品</button><button class="outline-button full-button" @tap="openStage(true)">调整舞台</button></template><text v-else class="small-note">{{ready.includes('audio')?(['failed','needs_input','cancelled'].includes(flow.status)?'声音已可试听，舞台制作已暂停，可处理后继续。':'声音已可试听，舞台完成后可在这里播放。'):'完成后将在这里呈现你的舞台作品。'}}</text></view>
<view v-if="tab==='audio'" class="result"><audio v-if="audioUrl" :src="audioUrl" :name="flow.title" controls/><text v-else class="small-note">{{ready.includes('audio')?'正在读取声音…':(['queued','running'].includes(flow.status)?'声音正在准备中。':'这次制作还没有完成声音。')}}</text></view>
<view v-if="tab==='script'" class="result"><template v-if="script"><view v-for="ep in script.episodes" :key="ep.episode_id"><text class="section-title serif">{{ep.title}}</text><view v-for="sc in ep.scenes" :key="sc.scene_id"><text class="scene-title">{{sc.title}}</text><text v-for="line in sc.utterances" :key="line.line_id" class="script-line">{{line.text}}</text></view></view></template><text v-else class="small-note">{{['queued','running'].includes(flow.status)?'剧本正在准备中。':'这次制作还没有完成剧本。'}}</text></view>
<view v-if="flow.offstage_roles.length" class="spaced"><text class="field-label">角色说明</text><text v-for="role in flow.offstage_roles" :key="role.role_id" class="small-note">{{role.name}} · 画外音</text></view></view>
<view v-if="canEdit" class="paper-panel section-gap"><text class="section-title serif">让故事继续</text><text class="small-note spaced">正在修改 {{new Date(flow.created_at).toLocaleDateString()}} 的版本。</text><view class="chip-list spaced"><button class="chip" @tap="editing=editing==='story'?'':'story'">修改剧本</button><button class="chip" @tap="editing=editing==='audio'?'':'audio'">修改声音</button><button v-if="flow.output==='h5'" class="chip" @tap="editing=editing==='stage'?'':'stage'">人物与服装</button></view>
<view v-if="editing==='story'"><view class="chip-list spaced"><button v-for="item in [{id:'revise',name:'修改'},{id:'continue',name:'续写'},{id:'remix',name:'改编'}]" :key="item.id" class="chip" :class="{selected:operation===item.id}" @tap="operation=item.id">{{item.name}}</button></view><textarea v-model="instruction" class="form-textarea spaced" aria-label="修改要求" placeholder="写下希望调整的内容"/><template v-if="operation==='remix'"><input v-model="retain" class="form-input spaced" placeholder="希望保留的内容"/><input v-model="transform" class="form-input spaced" placeholder="希望改变的内容"/></template><text class="small-note spaced">{{flow.output==='script'?'将生成新剧本，原版本保留。':flow.output==='audio'?'将生成新剧本和声音，原版本保留。':'将制作新的剧本、声音和舞台版本，原作品仍可播放。'}}</text><button class="warm-button full-button" :disabled="busy" @tap="revise(operation)">制作新版本</button></view>
<view v-if="editing==='audio'&&script&&sound"><VoiceRevisionEditor v-if="sound.artifacts.some(a=>a.kind==='VoiceBindings')" :job="sound" :delivery="script" :disabled="busy" :whole="!partial" @revision-change="voiceState=$event" @close="editing=''"/>
<template v-if="partial"><text class="field-label">选择需要重新生成的台词或音效</text><view v-for="ep in script.episodes" :key="ep.episode_id"><view v-for="sc in ep.scenes" :key="sc.scene_id"><button v-for="line in sc.utterances.filter(l=>['dialogue','narration','sfx'].includes(l.kind))" :key="line.line_id" class="select-line" :class="{selected:picked(line.line_id)}" @tap="toggle(ep.episode_id,sc.scene_id,line.line_id)">{{picked(line.line_id)?'✓ ':''}}{{line.text}}</button></view></view><button class="warm-button full-button" :disabled="busy" @tap="revise('audio')">重制所选声音并更新舞台</button></template>
<text class="small-note spaced">重新制作声音会使用同一剧本生成整段声音，并更新舞台时间；原版本保留。</text><button class="outline-button full-button" :disabled="busy" @tap="revise('audio_full')">重新制作整段声音与舞台</button></view>
<view v-if="editing==='stage'"><FlowCastingPicker v-model="casting" :roles="roles" :disabled="busy"/><text class="small-note">只重做舞台，复用当前声音。</text><button class="warm-button full-button" :disabled="busy||!ready.includes('audio')" @tap="revise('stage')">按新人物服装编排舞台</button></view></view>
<view class="paper-panel section-gap"><button class="history-toggle" @tap="showHistory=!showHistory">历史版本 {{showHistory?'收起':'展开'}}</button><template v-if="showHistory"><button v-for="version in history" :key="version.id" class="version" :disabled="version.id===flow.id" @tap="goPage('flow',{id:version.id})">{{new Date(version.created_at).toLocaleString()}} · {{versionKind[version.kind]||'作品版本'}} · {{jobStatus[version.id===flow.id?flow.status:version.status]}}{{version.id===flow.id?' · 当前':''}}</button><button v-if="historyNext!==null" class="outline-button full-button" :disabled="historyBusy" @tap="versions(true)">更早版本</button><button v-if="flow.parent_id" class="version" @tap="goPage('flow',{id:flow.parent_id})">查看来源版本</button></template></view>
</template></view></FeatureLayout></template>
<style scoped>.history-toggle{display:flex;width:100%;font-size:28rpx;color:#856143;justify-content:space-between}.phase{display:flex;justify-content:space-between;gap:20rpx;padding:22rpx 0;border-bottom:1px solid #eadcc9;font-size:26rpx}.phase text:last-child{color:#8d765d}.result{margin-top:28rpx}.script-line{display:block;white-space:pre-wrap;line-height:1.9;font-size:28rpx;margin:18rpx 0}.scene-title{display:block;font-size:30rpx;color:#8f5538;margin-top:25rpx}.version,.select-line{display:block;width:100%;padding:22rpx 0;text-align:left;font-size:25rpx;line-height:1.7;border-bottom:1px solid #eadcc9}.select-line.selected{color:#a8452d;background:#fff1de}audio{max-width:100%}</style>




