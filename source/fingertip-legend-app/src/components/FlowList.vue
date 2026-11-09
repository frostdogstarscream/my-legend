<script setup lang="ts">
import {computed,ref} from 'vue'
import {onShow} from '@dcloudio/uni-app'
import {api} from '../services/api'
import type {ProductionFlow} from '../types/flow'
import {goPage,errorMessage} from '../utils/navigation'
import {jobStatus} from '../utils/production'
const props=defineProps<{search?:string;filter?:string;latest?:boolean}>()
const emit=defineEmits<{'loaded':[workIds:string[]]}>()
const flows=ref<ProductionFlow[]>([]),error=ref(''),next=ref<number|null>(null),busy=ref(false)
const visible=computed(()=>flows.value.filter(f=>(!props.search||f.title.includes(props.search))&&(!props.filter||props.filter==='全部'||props.filter==='已完成'&&f.status==='succeeded'||props.filter==='草稿'&&['failed','needs_input','cancelled'].includes(f.status))).slice(0,props.latest?1:undefined))
async function load(more=false){if(busy.value)return;busy.value=true;try{const result=await api.flows(more?(next.value||0):0);flows.value=more?[...flows.value,...result.items]:result.items;next.value=result.next_offset;emit('loaded',flows.value.map(f=>f.work_id));error.value=''}catch(e){error.value=errorMessage(e)}finally{busy.value=false}}
onShow(()=>load())
function open(flow:ProductionFlow){if(flow.available_outputs.includes('h5')&&flow.stage_job_id)goPage('performance',{profile:'life_v2',deliveryId:flow.stage_job_id,flowId:flow.id,view:'play'});else goPage('flow',{id:flow.id})}
</script>
<template><view class="flow-list"><view v-if="error" class="paper-panel"><text class="error-copy">{{error}}</text><button class="outline-button" @tap="load()">重新读取</button></view><view v-for="f in visible" :key="f.id" class="paper-panel"><view class="row-between"><text class="section-title serif">{{f.title}}</text><text class="pill">{{jobStatus[f.status]}}</text></view><text class="small-note spaced">{{f.recovery?.message||(f.output==='script'?'剧本作品':f.output==='audio'?'剧本与广播剧':f.available_outputs.includes('h5')?'舞台作品就绪':f.available_outputs.includes('audio')?(['failed','needs_input','cancelled'].includes(f.status)?'声音可试听 · 舞台待处理':'声音可试听 · 舞台制作中'):'完整舞台作品')}}</text><button class="warm-button full-button" @tap="open(f)">{{f.available_outputs.includes('h5')?'打开作品':'查看制作进度'}} ›</button></view><button v-if="!latest&&next!==null" class="outline-button full-button" :disabled="busy" @tap="load(true)">更多作品</button></view></template>
<style scoped>.flow-list{position:relative;display:flex;flex-direction:column;gap:22rpx;margin-bottom:24rpx}</style>

