<script setup lang="ts">
import {ref,watch,onBeforeUnmount} from 'vue'
import {onShow,onHide} from '@dcloudio/uni-app'
import {api} from '../services/api'
import type {WorkSummary} from '../types/works'
import {openDestination,defaultWorkDestination,workStatus} from '../utils/works'
import {errorMessage} from '../utils/navigation'
const props=defineProps<{search?:string;filter?:string}>()
const items=ref<WorkSummary[]>([]),next=ref<number|null>(null),error=ref(''),busy=ref(false)
let seq=0,timer:ReturnType<typeof setTimeout>|undefined,active=true
async function load(more=false){const token=++seq;busy.value=true;clearTimeout(timer);try{const r=await api.works(more?(next.value||0):0,props.search||'',({'全部':'all','草稿':'drafts','已完成':'completed','制作中':'active','待处理':'attention'} as Record<string,string>)[props.filter||'全部']);if(token!==seq)return;items.value=more?[...items.value,...r.items.filter(x=>!items.value.some(y=>y.id===x.id))]:r.items;next.value=r.next_offset;error.value=''}catch(e){if(token===seq)error.value=errorMessage(e)}finally{if(token===seq){busy.value=false;if(active&&next.value===null&&items.value.some(x=>['queued','running'].includes(x.status)))timer=setTimeout(()=>load(),5000)}}}
watch(()=>[props.search,props.filter],()=>{clearTimeout(timer);seq++;timer=setTimeout(()=>load(),250)})
onShow(()=>{active=true;load()});onHide(()=>{active=false;seq++;clearTimeout(timer)});onBeforeUnmount(()=>{active=false;seq++;clearTimeout(timer)})
</script>
<template><view class="works-list"><view v-if="error" class="paper-panel"><text class="error-copy">{{error}}</text><button class="outline-button" @tap="load()">重试</button></view><text v-if="busy&&!items.length" class="small-note">正在打开故事集…</text><view v-if="!busy&&!error&&!items.length" class="paper-panel">这里还没有对应的作品，去创作一个新故事吧。</view><view v-for="work in items" :key="work.id" class="paper-panel work-item" :data-work-id="work.id"><view class="row-between"><text class="section-title serif">{{work.title}}</text><text class="pill">{{workStatus[work.status]||work.status}}</text></view><text class="small-note spaced">{{new Date(work.updated_at).toLocaleString()}}</text><text class="small-note spaced">{{work.recovery?.message||(work.kind==='draft'?'接着上次的灵感继续写':work.available_outputs.includes('h5')?'故事已经准备好了':work.available_outputs.includes('audio')?'声音已可试听':work.available_outputs.includes('script')?'剧本已可阅读':'随时回来查看进度')}}</text><button class="warm-button full-button" @tap="openDestination(defaultWorkDestination(work))">{{work.kind==='draft'?'继续编辑草稿':work.status==='succeeded'?(work.play?'播放作品':'打开作品'):'查看故事'}}</button><button v-if="work.play&&work.status!=='succeeded'" class="outline-button full-button" @tap="openDestination(work.play)">播放上一完成版本</button><button v-if="work.play&&work.status==='succeeded'" class="text-link" @tap="openDestination(work.open)">查看故事 · 修改 ›</button></view><button v-if="next!==null" class="outline-button full-button" :disabled="busy" @tap="load(true)">更多作品</button></view></template>
<style scoped>.works-list{display:flex;flex-direction:column;gap:22rpx}.work-item .row-between{gap:16rpx}.work-item .section-title{min-width:0;overflow-wrap:anywhere}.work-item .pill{flex-shrink:0}.text-link{font-size:25rpx;color:#9e4831;padding:18rpx 0}
.work-item { padding: 18px; }
.work-item .section-title { font-size: 20px; line-height: 1.4; }
.work-item .row-between { align-items: flex-start; }
.work-item .spaced { margin-top: 10px; }
.work-item .full-button { margin-top: 14px; }
.work-item .text-link { min-height: 44px; margin-top: 4px; padding: 10px 0; }
</style>
