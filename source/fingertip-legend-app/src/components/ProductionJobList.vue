<script setup lang="ts">
import { computed,ref } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import { api } from '../services/api'
import type { IndependentJob } from '../types/production'
import { jobStatus,jobTitle } from '../utils/production'
import { errorMessage,goPage } from '../utils/navigation'
const props=defineProps<{search:string;filter:string}>()
const emit=defineEmits<{(e:'loaded',workIds:string[]):void}>()
const jobs=ref<IndependentJob[]>([]),error=ref(''),busy=ref(false),next=ref<number|null>(null)
const visible=computed(()=>jobs.value.filter(job=>(!props.search||jobTitle(job).toLowerCase().includes(props.search.trim().toLowerCase()))&&
  (props.filter==='全部'||props.filter==='已完成'&&job.status==='succeeded'||props.filter==='草稿'&&['failed','needs_input','cancelled'].includes(job.status))))
async function load(more=false){if(busy.value)return;busy.value=true;error.value='';try{const result=await api.independentJobs(more?(next.value||0):0);jobs.value=more?[...jobs.value,...result.items.filter(j=>!jobs.value.some(old=>old.id===j.id))]:result.items;next.value=result.next_offset;emit('loaded',Array.from(new Set(jobs.value.map(j=>j.work_id))))}catch(e){error.value=errorMessage(e)}finally{busy.value=false}}
onShow(()=>load())
function results(job:IndependentJob){return [{kind:'StoryDelivery',label:'剧本'},{kind:'AudioMix',label:'广播剧'},{kind:'Video',label:'演绎'}].filter(x=>job.artifacts.some(a=>a.kind===x.kind)).map(x=>x.label+'就绪').join(' · ')||'已交给后台制作'}
</script>
<template><view class="production-list"><view v-if="error" class="paper-panel"><text class="error-copy">{{error}}</text><button class="outline-button full-button" @tap="load(false)">重新读取制作记录</button></view>
  <view v-for="job in visible" :key="job.id" class="paper-panel production-item"><view class="row-between"><text class="work-title serif">{{jobTitle(job)}}</text><text class="pill">{{jobStatus[job.status]}}</text></view><text class="small-note spaced">{{results(job)}}</text><text class="small-note">{{new Date(job.created_at).toLocaleString()}}</text><button class="outline-button full-button" @tap="goPage('work',{id:job.id})">{{job.status==='succeeded'?'打开作品':'查看进度与结果'}} ›</button></view>
  <button v-if="next!==null" class="text-link" :disabled="busy" @tap="load(true)">加载更多制作记录</button>
</view></template>
<style scoped>.production-list{display:flex;flex-direction:column;gap:24rpx;margin-bottom:24rpx}.work-title{font-size:34rpx;line-height:1.6;overflow-wrap:anywhere;flex:1;min-width:0}.production-item .pill{flex-shrink:0}.text-link{padding:20rpx;color:#795f45;font-size:25rpx}</style>
