<script setup lang="ts">
import {ref,watch,onBeforeUnmount,computed} from 'vue'
import {api} from '../services/api'
import {readProductionArtifact} from '../services/production-media'
import type {IndependentJob,StoryDeliveryView} from '../types/production'
import {voiceRows,voiceRevision,type VoiceChoice,type VoiceRow,type VoiceRevisionState} from '../utils/voice-revision'
import {errorMessage} from '../utils/navigation'

const props=defineProps<{job:IndependentJob;delivery:StoryDeliveryView;disabled:boolean}>()
const emit=defineEmits<{'revision-change':[value:VoiceRevisionState];close:[]}>()
const rows=ref<VoiceRow[]>([]),choices=ref<VoiceChoice[]>([]),loading=ref(false),error=ref('')
let sequence=0
const state=computed(()=>voiceRevision(rows.value,choices.value))
watch(state,value=>emit('revision-change',value),{immediate:true})
const bindingRef=computed(()=>props.job.artifacts.find(ref=>ref.kind==='VoiceBindings'))
async function load(){
  const token=++sequence;loading.value=true;error.value='';rows.value=[];choices.value=[]
  try{
    const ref=bindingRef.value
    if(!ref||ref.source_version_id!==props.delivery.story_version_id||props.job.source_version_id!==props.delivery.story_version_id)throw new Error('作品音色版本不一致，请刷新作品。')
    const [options,bindings]=await Promise.all([api.productionOptions(),readProductionArtifact<unknown>(props.job.id,ref)])
    if(token!==sequence)return
    if(!Array.isArray(options.voices)||!options.voices.length||options.voices.some(v=>!v||typeof v.id!=='string'||!v.id||typeof v.name!=='string'||!v.name)||new Set(options.voices.map(v=>v.id)).size!==options.voices.length)throw new Error('可用音色列表暂不可用，请重试。')
    choices.value=options.voices;rows.value=voiceRows(props.delivery,bindings)
  }catch(e){if(token===sequence)error.value=errorMessage(e)}finally{if(token===sequence)loading.value=false}
}
watch(()=>`${props.job.id}:${props.job.source_version_id}:${bindingRef.value?.sha256}`,load,{immediate:true})
onBeforeUnmount(()=>{sequence++})
function select(row:VoiceRow,event:{detail:{value:string|number}}){
  if(props.disabled)return
  const index=Number(event.detail.value)
  if(Number.isInteger(index)&&choices.value[index])row.selected=choices.value[index].id
}
const voiceName=(id:string)=>choices.value.find(choice=>choice.id===id)?.name||'原音色暂不可用，请重新选择'
</script>

<template><view class="paper-panel section-gap">
  <view class="row-between"><text class="section-title serif">更换角色音色</text><button class="close-voices" :disabled="disabled" @tap="emit('close')">取消修改</button></view>
  <text class="small-note spaced">更换后会重制该角色在本剧本中的所有配音。其他角色及未改动的音效优先复用。</text>
  <text v-if="loading" class="copy spaced">正在读取本作品的音色…</text>
  <view v-if="error"><text class="error-copy" role="alert">{{error}}</text><button class="outline-button full-button" :disabled="loading||disabled" @tap="load">重新加载音色</button></view>
  <view v-for="row in rows" :key="row.identity" class="voice-row">
    <view class="voice-role"><text class="field-label">{{row.name}}</text><text class="small-note">{{row.segments}} 段配音</text><text v-if="row.selected!==row.original" class="small-note">原音色：{{voiceName(row.original)}}</text></view>
    <picker mode="selector" :range="choices" range-key="name" :value="Math.max(0,choices.findIndex(v=>v.id===row.selected))" :disabled="disabled" :aria-label="'选择'+row.name+'的音色'" @change="select(row,$event)"><view class="voice-choice">{{voiceName(row.selected)}} ›</view></picker>
  </view>
  <text v-if="rows.length&&!state.ready" class="error-copy">请为每个角色选择当前可用的音色，再提交制作。</text>
  <text v-if="state.changedCount" class="small-note spaced">已更换 {{state.changedCount}} 个声音角色，涉及 {{state.affectedSegments}} 段配音。提交后才会开始制作。</text>
  <text v-else-if="rows.length" class="small-note spaced">尚未更换音色。当前作品保持原样。</text>
</view></template>
<style scoped>.voice-row{display:flex;align-items:center;justify-content:space-between;gap:20rpx;padding:25rpx 0;border-bottom:1px solid #eadcc9}.voice-role{min-width:0;flex:1}.voice-role text{display:block}.voice-choice{max-width:300rpx;padding:15rpx 0;color:#995b36;font-size:26rpx;line-height:1.6}.close-voices{font-size:24rpx;color:#795f45;flex-shrink:0;padding:12rpx}</style>
