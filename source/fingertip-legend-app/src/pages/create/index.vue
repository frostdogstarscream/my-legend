<script setup lang="ts">
import {ref,watch,computed} from 'vue'
import {onLoad,onReady,onShow} from '@dcloudio/uni-app'
import CharacterPortrait from '../../components/CharacterPortrait.vue'
import FeatureLayout from '../../components/FeatureLayout.vue'
import ProductionChoices from '../../components/ProductionChoices.vue'
import FlowCastingPicker from '../../components/FlowCastingPicker.vue'
import type {FlowCasting} from '../../types/flow'
import type {CreationDraft,CreationPayload} from '../../types/works'
import type {ProductionCapabilities} from '../../types/production'
import type {Character} from '../../types/domain'
import {api} from '../../services/api'
import {config} from '../../config'
import {sessionState} from '../../stores/session'
import {selectionScope,readSelection,writeSelection,hasSelection,activateSelection,migrateSelection,finishSelection} from '../../stores/creation-selection'
import {requestKey} from '../../utils/production'
import {errorMessage,goPage,goTab,requireSession,showMessage} from '../../utils/navigation'
const id=ref(''),title=ref(''),genre=ref('国风奇幻'),idea=ref(''),roles=ref<Character[]>([]),roleIds=ref<string[]>([]),busy=ref(false),error=ref('')
const selectedRoles=computed(()=>roleIds.value.map(id=>roles.value.find(r=>r.id===id)).filter((r):r is Character=>!!r))
const genres=['国风奇幻','冒险成长','悬疑推理','生活治愈']
const casting=ref<FlowCasting|null>(null),output=ref<'script'|'audio'|'h5'>('h5'),mode=ref<'automatic'|'reviewed'>('automatic'),capabilities=ref<ProductionCapabilities|null>(null)
const draft=ref<CreationDraft>(),hydrated=ref(false),notice=ref('')
const conflictingLocal=ref<CreationPayload>()
let initialDraftId=''
let explicitDraft=false
const pendingDraftKey=()=> 'my-legend:creation-pending:'+sessionState.session?.user.id
const cacheKey=()=> 'my-legend:creation:'+sessionState.session?.user.id+':'+(draft.value?.id||initialDraftId||id.value||'new')
const payload=():CreationPayload=>({source:{kind:'creative',title:title.value.trim(),idea:idea.value.trim(),genre:genre.value,character_ids:[...roleIds.value],target_duration_seconds:null},output:output.value,casting:casting.value})
function apply(value:CreationPayload){title.value=value.source.title;idea.value=value.source.idea;genre.value=value.source.genre;roleIds.value=[...value.source.character_ids];output.value=value.output;casting.value=value.casting}
function pickedRole(){
 if(!hasSelection()){writeSelection(roleIds.value);return}
 const next=readSelection(),old=new Set(roleIds.value)
 if(casting.value){const value={...casting.value,bindings:{...casting.value.bindings},initial_outfits:{...casting.value.initial_outfits}};for(const key of Object.keys(value.bindings))if(!next.includes(key)){delete value.bindings[key];delete value.initial_outfits[key]}
 for(const key of next){const preset=roles.value.find(r=>r.id===key)?.stageBinding;if(!old.has(key)&&preset&&preset.catalog_sha256===value.catalog_sha256&&Object.keys(value.bindings).length<3){value.bindings[key]=preset.actor_id;value.initial_outfits[key]=preset.outfit_id}}
 casting.value=Object.keys(value.bindings).length?value:null}
 roleIds.value=next
}
function chooseOnPage(){writeSelection(roleIds.value);goTab('characters')}
async function load(){if(!requireSession())return;error.value='';try{
 roles.value=await api.characters();if(!config.useMock)try{capabilities.value=await api.independentCapabilities()}catch{notice.value='暂时无法开始制作，你可以先把灵感存下来。'}
 if(initialDraftId){draft.value=await api.creationDraft(initialDraftId);if(draft.value.flow_id){uni.removeStorageSync(pendingDraftKey());goPage('flow',{id:draft.value.flow_id});return}apply(draft.value.payload)}
 else if(id.value){const old=await api.story(id.value);title.value=old.title;idea.value=old.idea;genre.value=old.genre;roleIds.value=old.roleIds||[]}
 const saved=uni.getStorageSync(cacheKey());if(saved?.payload&&(!draft.value||saved.baseRevision===draft.value.revision)){apply(saved.payload);notice.value='已接着上次的内容打开。'}else if(saved?.payload){conflictingLocal.value=saved.payload;uni.setStorageSync(cacheKey()+':conflict',saved.payload);notice.value='后台草稿已有新版本，已读取后台内容；可以恢复本机未提交内容后再核对保存。'}
 if(!conflictingLocal.value)conflictingLocal.value=uni.getStorageSync(cacheKey()+':conflict')||undefined
 if(!hasSelection())writeSelection(roleIds.value);pickedRole();hydrated.value=true
}catch(e){if(!explicitDraft&&(id.value||initialDraftId)&&(e as {status?:number}).status===404){const saved=uni.getStorageSync(cacheKey());id.value='';initialDraftId='';draft.value=undefined;uni.removeStorageSync(pendingDraftKey());activateSelection('new');if(saved?.payload){apply(saved.payload);uni.setStorageSync(cacheKey(),saved)}await load();return}error.value=errorMessage(e)}}
let routeOptions:Record<string,any>|undefined
onLoad(q=>{routeOptions=q})
onReady(()=>{if(!requireSession())return;const q=routeOptions;const route=typeof window!=='undefined'&&window.location.hash.startsWith('#/pages/create/index')?{id:new URLSearchParams(window.location.hash.split('?')[1]||'').get('id'),draftId:new URLSearchParams(window.location.hash.split('?')[1]||'').get('draftId')}:q;id.value=String(route?.id||'');explicitDraft=!!(route?.id||route?.draftId);const scope=selectionScope();initialDraftId=String(route?.draftId||(!id.value&&uni.getStorageSync(pendingDraftKey()))||(!id.value&&scope!=='new'&&!scope.startsWith('legacy:')?scope:'')||'');if(!id.value&&!initialDraftId&&scope.startsWith('legacy:'))id.value=scope.slice(7);activateSelection(initialDraftId||(id.value?'legacy:'+id.value:'new'));load()})
async function reconnect(){if(!hydrated.value){await load();return}try{roles.value=await api.characters();capabilities.value=await api.independentCapabilities();error.value=''}catch(e){error.value=errorMessage(e)}}
onShow(async()=>{if(!hydrated.value||!requireSession())return;pickedRole();if(!config.useMock)try{capabilities.value=await api.independentCapabilities()}catch(e){error.value=errorMessage(e)}})
watch([title,idea,genre,roleIds,casting,output],()=>{if(hydrated.value){uni.setStorageSync(cacheKey(),{baseRevision:draft.value?.revision,payload:payload()});writeSelection(roleIds.value)}},{deep:true})
function restoreLocal(){if(!conflictingLocal.value)return;apply(conflictingLocal.value);conflictingLocal.value=undefined;uni.removeStorageSync(cacheKey()+':conflict');notice.value='已恢复本机未提交内容，请核对人物服装后保存。'}
async function persist(){
 const data=payload(),oldCache=cacheKey()
 if(draft.value)draft.value=await api.updateCreationDraft(draft.value.id,{...data,expected_revision:draft.value.revision})
 else{const signature=JSON.stringify(data),keyName=cacheKey()+':save',pending=uni.getStorageSync(keyName),key=pending?.signature===signature?pending.key:requestKey();uni.setStorageSync(keyName,{signature,key});draft.value=await api.createCreationDraft({...data,idempotency_key:key,...(id.value?{legacy_story_id:id.value}:{})});uni.removeStorageSync(keyName)}
 if(oldCache!==cacheKey())uni.removeStorageSync(oldCache)
 migrateSelection(draft.value.id,roleIds.value)
 uni.setStorageSync(pendingDraftKey(),draft.value.id)
 uni.setStorageSync(cacheKey(),{baseRevision:draft.value.revision,payload:data});return draft.value
}
async function save(generate=false){
 if(busy.value||!hydrated.value)return;error.value=''
 if(generate&&!idea.value.trim()){error.value='写下一点灵感再开始';return}
 busy.value=true
 try{
  if(config.useMock){const input={title:title.value.trim(),idea:idea.value.trim(),genre:genre.value,roleIds:roleIds.value};const story=id.value?await api.updateDraft(id.value,input):await api.createDraft(input);if(generate){const task=await api.startGeneration(story.id);goPage('progress',{taskId:task.id})}else goTab('stories');return}
  if(generate){if(!roleIds.value.length)throw Error('请先选择参与故事的角色');if(roleIds.value.some(r=>!roles.value.some(a=>a.id===r)))throw Error('有角色已不在当前目录中，请重新选择');if(output.value==='h5'&&!capabilities.value?.h5_flow?.configured)throw Error('暂时无法开始制作，可以先保存草稿，稍后重新连接。');if(output.value!=='h5'&&!capabilities.value?.outputs.includes(output.value))throw Error('所选制作服务暂不可用');if(output.value==='h5'&&!casting.value)throw Error('请至少为一名出镜角色选择人物和服装')}
  const saved=await persist();if(saved.flow_id){goPage('flow',{id:saved.flow_id});return}
  if(!generate){uni.removeStorageSync(pendingDraftKey());showMessage('完整草稿已保存');goTab('stories');return}
  const data=payload();const flow=await api.createFlow({...data,idempotency_key:'draft:'+saved.id+':'+saved.revision,draft_id:saved.id,expected_draft_revision:saved.revision})
  uni.removeStorageSync(cacheKey());uni.removeStorageSync(pendingDraftKey());finishSelection();goPage('flow',{id:flow.id})
 }catch(e){error.value=errorMessage(e);if(/DRAFT_REVISION_CONFLICT/.test(error.value)){uni.setStorageSync(cacheKey(),{baseRevision:draft.value?.revision,payload:payload()});uni.setStorageSync(cacheKey()+':conflict',payload());error.value='草稿已在其他页面更新。当前填写内容已保存在本机，请重新打开草稿核对后再提交。'}}finally{busy.value=false}
}
</script>
<template><FeatureLayout title="创作新故事" subtitle="让每一个灵感，都找到自己的世界" active="create" @reconnect="reconnect"><view class="page-content creation-content"><view class="paper-panel"><view class="row-between"><text class="section-title serif">让想象落在纸上</text><text class="pill">{{id||draft?'继续创作':'新灵感'}}</text></view><text class="field-label">故事名称（选填）</text><input v-model="title" class="form-input" aria-label="故事名称" placeholder="留空，让故事为自己起个名字" maxlength="40" :disabled="busy"/><text class="field-label">故事灵感</text><textarea v-model="idea" class="form-textarea" aria-label="故事灵感" placeholder="主角是谁？发生在哪里？你希望遇见什么？" maxlength="2000" :disabled="busy"/><text class="form-count">{{idea.length}} / 2000</text><text class="field-label">选择题材</text><view class="chip-list"><button v-for="item in genres" :key="item" class="chip" :class="{selected:genre===item}" :aria-pressed="genre===item" @tap="genre=item">{{item}}</button></view><view class="row-between spaced"><text class="field-label">参与角色</text><button class="text-link" @tap="chooseOnPage">选择角色 ›</button></view><view class="selected-roles"><text v-if="!roleIds.length" class="small-note">选择你想带进故事的伙伴</text><view v-for="role in selectedRoles" :key="role.id" class="selected-role"><CharacterPortrait :role="role" mode="aspectFill"/><text>{{role.name}}</text></view></view><ProductionChoices v-if="!config.useMock" v-model:output="output" v-model:mode="mode" :capabilities="capabilities"/><FlowCastingPicker :key="JSON.stringify([casting?.catalog_sha256,draft?.id])" v-if="hydrated&&!config.useMock&&output==='h5'" v-model="casting" :roles="selectedRoles" :disabled="busy"/><text v-if="notice" class="small-note spaced">{{notice}}</text><button v-if="conflictingLocal" class="outline-button full-button" @tap="restoreLocal">恢复本机未提交内容</button><text v-if="casting&&output==='h5'" class="small-note spaced">{{Object.keys(casting.bindings).length}} 位角色出镜，场景会随故事自动安排。</text><text v-if="error" class="error-copy" role="alert">{{error}}</text><button v-if="!config.useMock&&(!capabilities||output==='h5'&&!capabilities.h5_flow?.configured)" class="outline-button full-button" @tap="reconnect">重新连接</button><button class="warm-button full-button" :disabled="busy||!hydrated" :loading="busy" @tap="save(true)">{{config.useMock?'开始生成':'一键制作作品'}}</button><button class="outline-button full-button" :disabled="busy" @tap="save(false)">先保存草稿</button></view></view></FeatureLayout></template>
<style scoped>.form-count{display:block;text-align:right;color:#a4927c;font-size:21rpx;margin:12rpx 0 20rpx}.text-link{padding:14rpx;color:#7955a4;font-size:23rpx}.selected-roles{display:flex;gap:20rpx;flex-wrap:wrap;min-height:64rpx}.selected-role{display:flex;align-items:center;gap:10rpx;padding:10rpx 18rpx 10rpx 10rpx;background:#f3ecff;border-radius:99rpx}.selected-role :deep(.character-portrait){width:48rpx;height:48rpx;border-radius:50%}.role-choice{display:flex;align-items:center;gap:20rpx;text-align:left;padding:22rpx 12rpx;width:100%;border-bottom:2rpx solid #eadcc9}.role-choice :deep(.character-portrait){width:100rpx;height:100rpx;border-radius:20rpx}.role-choice>view:not(.character-portrait){flex:1}.role-choice>text{font-size:23rpx;color:#856143}.role-choice.selected>text{color:#b7432d;font-weight:700}
.creation-content .form-textarea { height: 132px; min-height: 132px; }
.creation-content .row-between:first-child { flex-wrap: wrap; gap: 8px; }
.text-link { min-height: 44px; font-size: 14px; }
.role-choice > view { min-width: 0; }
.role-choice > text { flex-shrink: 0; }
</style>
