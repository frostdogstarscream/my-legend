<script setup lang="ts">
import { ref,watch } from 'vue'
import { onLoad,onShow } from '@dcloudio/uni-app'
import FeatureLayout from '../../components/FeatureLayout.vue'
import AppSheet from '../../components/AppSheet.vue'
import ProductionChoices from '../../components/ProductionChoices.vue'
import type { OutputKind, ProductionCapabilities } from '../../types/production'
import { requestKey } from '../../utils/production'
import { api } from '../../services/api'
import { config } from '../../config'
import { normalizeRoleIds } from '../../utils/roles'
import type { Character } from '../../types/domain'
import { errorMessage,goPage,goTab,requireSession,showMessage } from '../../utils/navigation'
const id=ref(''),title=ref(''),genre=ref('国风奇幻'),idea=ref(''),roles=ref<Character[]>([]),roleIds=ref<string[]>([]),busy=ref(false),error=ref(''),chooseRoles=ref(false)
const genres=['国风奇幻','冒险成长','悬疑推理','生活治愈']
const output=ref<OutputKind>('audio'),mode=ref<'automatic'|'reviewed'>('automatic'),targetDuration=ref(90),capabilities=ref<ProductionCapabilities|null>(null)
onShow(async()=>{if(config.useMock||!requireSession())return;try{capabilities.value=await api.independentCapabilities()}catch(e){error.value=errorMessage(e)}})
let hydrated=false
onLoad(async q=>{id.value=String(q?.id||'');if(!requireSession())return;try{if(id.value){const story=await api.story(id.value);title.value=story.title;genre.value=story.genre;idea.value=story.idea;roleIds.value=story.roleIds||[]}else{const saved=uni.getStorageSync('my-legend:creation-form');if(saved){title.value=saved.title||'';idea.value=saved.idea||'';genre.value=saved.genre||genre.value;roleIds.value=saved.roleIds||[]}}}catch(e){error.value=errorMessage(e)}finally{hydrated=true}})
onShow(async()=>{if(!requireSession())return;try{roles.value=await api.characters();const picked=uni.getStorageSync('my-legend:selected-role');if(picked&&!roleIds.value.includes(picked))roleIds.value.push(picked);uni.removeStorageSync('my-legend:selected-role');roleIds.value=normalizeRoleIds(roleIds.value,roles.value.map(r=>r.id),config.localBackend)}catch(e){error.value=errorMessage(e)}})
watch([title,idea,genre,roleIds],()=>{if(hydrated&&!id.value)uni.setStorageSync('my-legend:creation-form',{title:title.value,idea:idea.value,genre:genre.value,roleIds:roleIds.value})},{deep:true})
function toggleRole(roleId:string){roleIds.value=roleIds.value.includes(roleId)?roleIds.value.filter(r=>r!==roleId):[...roleIds.value,roleId]}
async function save(generate=false){
  if(busy.value)return;error.value=''
  if(!title.value.trim()){error.value='先给故事起一个名字';return}
  if(!idea.value.trim()){error.value='写下一点灵感再开始';return}
  busy.value=true
  try{
    roleIds.value=normalizeRoleIds(roleIds.value,roles.value.map(r=>r.id),config.localBackend)
    const input={title:title.value.trim(),genre:genre.value,idea:idea.value.trim(),roleIds:[...roleIds.value]}
    if(generate&&!config.useMock){
      if(!roleIds.value.length)throw new Error('请先选择参与故事的角色')
      if(!capabilities.value?.outputs.includes(output.value))throw new Error('所选制作服务暂不可用，请稍后重试')
      const body={...(id.value?{work_id:id.value}:{}),source:{kind:'creative' as const,title:input.title,idea:input.idea,genre:input.genre,character_ids:input.roleIds,target_duration_seconds:targetDuration.value},
        outputs:[output.value],mode:output.value==='visual'?mode.value:'automatic' as const,visual_profile:'pixel_performance/1' as const,
        ...(output.value!=='visual'?{review_policy:'listen_first' as const}:{}),
        ...(output.value==='audio'?{sound_workflow:'sound_bridge/1' as const,audio_delivery_policy:'audio_first' as const}:{})}
      const fingerprint=JSON.stringify(body),pending=uni.getStorageSync('my-legend:pending-production')
      const key=pending?.fingerprint===fingerprint?pending.key:requestKey()
      uni.setStorageSync('my-legend:pending-production',{fingerprint,key})
      const task=await api.startIndependent({...body,idempotency_key:key})
      uni.removeStorageSync('my-legend:pending-production');uni.removeStorageSync('my-legend:creation-form');goPage('work',{id:task.id});return
    }
    const story=id.value?await api.updateDraft(id.value,input):await api.createDraft(input);id.value=story.id
    uni.removeStorageSync('my-legend:creation-form')
    if(generate){const task=await api.startGeneration(story.id);goPage('progress',{taskId:task.id})}else{showMessage('草稿已保存');goTab('stories')}
  }catch(e){error.value=errorMessage(e)}finally{busy.value=false}
}
</script>
<template><FeatureLayout title="创作新故事" subtitle="让每一个灵感，都找到自己的世界" active="create"><view class="page-content"><view class="paper-panel"><view class="row-between"><text class="section-title serif">让想象落在纸上</text><text class="pill">{{id?'继续创作':'新灵感'}}</text></view><text class="field-label">故事名称</text><input v-model="title" class="form-input" aria-label="故事名称" placeholder="例如：山海奇遇录" maxlength="40" :disabled="busy"/><text class="field-label">故事灵感</text><textarea v-model="idea" class="form-textarea" aria-label="故事灵感" placeholder="主角是谁？发生在哪里？你希望遇见什么？" maxlength="2000" :disabled="busy"/><text class="form-count">{{idea.length}} / 2000</text><text class="field-label">选择题材</text><view class="chip-list"><button v-for="item in genres" :key="item" class="chip" :class="{selected:genre===item}" :aria-pressed="genre===item" @tap="genre=item">{{item}}</button></view><view class="row-between spaced"><text class="field-label">参与角色</text><button class="text-link" @tap="chooseRoles=true">选择角色 ›</button></view><view class="selected-roles"><text v-if="!roleIds.length" class="small-note">选择你想带进故事的伙伴</text><view v-for="role in roles.filter(r=>roleIds.includes(r.id))" :key="role.id" class="selected-role"><image :src="role.avatarUrl" mode="aspectFill"/><text>{{role.name}}</text></view></view><ProductionChoices v-if="!config.useMock" v-model:output="output" v-model:mode="mode" v-model:duration="targetDuration" :capabilities="capabilities"/><text v-if="error" class="error-copy" role="alert">{{error}}</text><button class="warm-button full-button" :disabled="busy" :loading="busy" @tap="save(true)">{{config.useMock?'开始生成':'开始制作作品'}}</button><button class="outline-button full-button" :disabled="busy" @tap="save(false)">先保存草稿</button><text class="small-note spaced">{{config.useMock?'本地体验：生成过程使用示例章节，灵感和草稿保存在本机。':'制作使用真实 AI 服务并消耗额度。提交后可离开页面，稍后在故事集中查看进度与结果。'}}</text></view></view><AppSheet title="带上你的伙伴" :open="chooseRoles" @close="chooseRoles=false"><button v-for="role in roles" :key="role.id" class="role-choice" :class="{selected:roleIds.includes(role.id)}" :aria-pressed="roleIds.includes(role.id)" @tap="toggleRole(role.id)"><image :src="role.avatarUrl" mode="aspectFill"/><view><text>{{role.name}}</text><text class="small-note">{{role.description}}</text></view><text>{{roleIds.includes(role.id)?'已选择':'选择'}}</text></button><button class="warm-button full-button" @tap="chooseRoles=false">选好了</button></AppSheet></FeatureLayout></template>
<style scoped>.form-count{display:block;text-align:right;color:#a4927c;font-size:21rpx;margin:12rpx 0 20rpx}.text-link{padding:14rpx;color:#7955a4;font-size:23rpx}.selected-roles{display:flex;gap:20rpx;flex-wrap:wrap;min-height:64rpx}.selected-role{display:flex;align-items:center;gap:10rpx;padding:10rpx 18rpx 10rpx 10rpx;background:#f3ecff;border-radius:99rpx}.selected-role image{width:48rpx;height:48rpx;border-radius:50%}.role-choice{display:flex;align-items:center;gap:20rpx;text-align:left;padding:22rpx 12rpx;width:100%;border-bottom:2rpx solid #eadcc9}.role-choice image{width:100rpx;height:100rpx;border-radius:20rpx}.role-choice>view{flex:1}.role-choice>text{font-size:23rpx;color:#856143}.role-choice.selected>text{color:#b7432d;font-weight:700}</style>
