<script setup lang="ts">
import { computed, ref } from 'vue'
import { onLoad,onShow,onHide,onUnload } from '@dcloudio/uni-app'
import FeatureLayout from '../../components/FeatureLayout.vue'
import { api } from '../../services/api'
import { config } from '../../config'
import type { GenerationTask } from '../../types/domain'
import { errorMessage,goPage,goTab,requireSession } from '../../utils/navigation'
const taskId=ref(''),task=ref<GenerationTask|null>(null),error=ref(''),busy=ref(false)
const title=computed(()=>task.value?.status==='awaiting_approval'?'故事大纲准备好了':task.value?.status==='succeeded'?'故事准备好了':task.value?.status==='failed'?'创作暂时停下了':task.value?.status==='cancelled'?'本次创作已停止':'故事正在生长')
let timer:ReturnType<typeof setTimeout>|undefined,visible=false,requestVersion=0
function stop(){visible=false;requestVersion++;clearTimeout(timer)}
onLoad(q=>{taskId.value=String(q?.taskId||'')})
async function poll(){if(!visible)return;const version=++requestVersion;error.value='';try{const result=await api.generationTask(taskId.value);if(!visible||version!==requestVersion)return;task.value=result;if(result.status==='running')timer=setTimeout(poll,config.useMock?900:2500)}catch(e){if(visible&&version===requestVersion)error.value=errorMessage(e)}}
function resume(){if(!requireSession())return;clearTimeout(timer);visible=true;void poll()}
onShow(resume);onHide(stop);onUnload(stop)
async function retry(){if(!task.value||busy.value)return;busy.value=true;try{const next=config.useMock?await api.startGeneration(task.value.storyId):await api.retryGeneration(task.value.id);taskId.value=next.id;task.value=next;resume()}catch(e){error.value=errorMessage(e)}finally{busy.value=false}}
async function cancel(){if(!task.value||busy.value)return;busy.value=true;try{task.value=await api.cancelGeneration(task.value.id);resume()}catch(e){error.value=errorMessage(e)}finally{busy.value=false}}
</script>
<template><FeatureLayout :title="title" subtitle="让每一个灵感，都找到自己的世界"><view class="page-content"><view class="paper-panel progress-panel">
<image class="progress-art" src="/static/figma/new-story.png" mode="aspectFill"/><text class="section-title serif">{{title}}</text>
<text v-if="error" class="error-copy" role="alert">{{error}}</text>
<template v-else><view class="row-between spaced"><text>{{task?.message||'正在读取进度'}}</text><text class="progress-number">{{task?.progress||0}}%</text></view><view class="task-progress spaced"><view :style="{width:(task?.progress||0)+'%'}"/></view>
<view class="phase-list"><text :class="{done:!!task?.plan||task?.phase==='writing'||task?.status==='succeeded'}">01　生成故事大纲</text><text :class="{done:task?.phase==='writing'||task?.status==='succeeded'}">02　查看、修改并确认大纲</text><text :class="{done:task?.status==='succeeded'}">03　生成正文与演绎标注</text></view>
<text v-if="task?.errorCode" class="error-copy">错误标识：{{task.errorCode}}</text></template>
<button v-if="error" class="warm-button full-button" @tap="resume">重新加载</button>
<button v-if="task?.status==='awaiting_approval'" class="warm-button full-button" @tap="goPage('outline',{taskId:task.id})">查看并确认大纲</button>
<button v-if="task?.status==='succeeded'" class="warm-button full-button" @tap="goPage('detail',{id:task.storyId})">查看故事</button>
<button v-if="task?.status==='failed'" class="warm-button full-button" :disabled="busy" @tap="retry">重试当前任务</button>
<button v-if="!config.useMock&&(task?.status==='running'||task?.status==='awaiting_approval')" class="outline-button full-button" :disabled="busy" @tap="cancel">停止本次创作</button>
<button class="outline-button full-button" @tap="goTab('stories')">返回我的故事</button>
<text class="small-note spaced">{{config.useMock?'当前展示本地演示进度，完成后提供固定示例章节；不会调用 AI。':task?.status==='awaiting_approval'?'正文尚未开始。请先查看大纲，确认后再继续。':'可以先离开，稍后从故事详情返回创作进度。阶段百分比不代表剩余时间。'}}</text>
</view></view></FeatureLayout></template>
<style scoped>.progress-panel{text-align:center}.progress-art{width:100%;height:260rpx;border-radius:24rpx;margin-bottom:32rpx}.progress-number{font-size:40rpx;color:#cf502a;font-weight:700}.phase-list{display:flex;flex-direction:column;gap:32rpx;text-align:left;padding:46rpx 0;color:#b1a393}.phase-list .done{color:#337553}</style>