<script setup lang="ts">
import {onBeforeUnmount,onMounted,ref} from 'vue'
import SpinePerformance from './SpinePerformance.vue'
import LifePerformance from './LifePerformance.vue'
const props=defineProps<{productionId?:string;initialProfile?:string;previewId?:string}>()
const profile=ref(props.initialProfile==='life_v1'?'life_v1':'battle'),player=ref<{pause:()=>void}>()
const currentJob=ref(props.productionId||''),currentPreview=ref(props.previewId||'')
const initialQuery=new URLSearchParams(window.location.hash.split('?')[1]||'')
const currentScene=ref(initialQuery.get('scene')||''),currentSceneCatalog=ref(initialQuery.get('sceneCatalog')||'')
function switchProfile(value:string){player.value?.pause();profile.value=value}
function routeChanged(){
  if(!window.location.hash.startsWith('#/pages/performance/index'))return
  const query=new URLSearchParams(window.location.hash.split('?')[1]||'')
  player.value?.pause();currentJob.value=query.get('productionId')||'';currentPreview.value=query.get('previewId')||''
  profile.value=query.get('profile')==='life_v1'?'life_v1':'battle'
  currentScene.value=query.get('scene')||'';currentSceneCatalog.value=query.get('sceneCatalog')||''
}
onMounted(()=>window.addEventListener('hashchange',routeChanged))
onBeforeUnmount(()=>window.removeEventListener('hashchange',routeChanged))
defineExpose({pause:()=>player.value?.pause()})
</script>
<template>
  <view class="stage-mode"><button :class="{active:profile==='battle'}" @tap="switchProfile('battle')">战斗素材预览</button><button :class="{active:profile==='life_v1'}" @tap="switchProfile('life_v1')">生活动作与编排</button></view>
  <LifePerformance v-if="profile==='life_v1'" :key="currentJob+'|'+currentPreview+'|'+currentScene+'|'+currentSceneCatalog" ref="player" :production-id="currentJob" :preview-id="currentPreview" :initial-scene="currentScene" :initial-scene-catalog="currentSceneCatalog"/>
  <SpinePerformance v-else :key="currentJob" ref="player" :production-id="currentJob"/>
</template>
<style scoped>.stage-mode{display:flex;justify-content:center;gap:10px;padding:14px;background:#eee3d2}.stage-mode button{margin:0;font-size:14px;padding:8px 20px;border:1px solid #dbc6b1;border-radius:10px;color:#704c39}.stage-mode .active{background:#813f2d;color:#fff5e7}</style>
