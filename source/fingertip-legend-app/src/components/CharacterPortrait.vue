<script setup lang="ts">
import {ref,watch,onBeforeUnmount} from 'vue'
import type {Character} from '../types/domain'
import {stageAsset} from '../services/stage-assets'
const props=defineProps<{role:Character;mode?:string}>()
const src=ref(''),failed=ref(false)
let sequence=0,objectUrl=''
function release(){if(objectUrl)URL.revokeObjectURL(objectUrl);objectUrl=''}
watch(()=>[props.role.id,props.role.thumbnail?.sha256],async()=>{
 const current=++sequence;release();src.value=props.role.avatarUrl;failed.value=false
 if(!props.role.thumbnail)return
 try{const bytes=await stageAsset(props.role.thumbnail);if(current!==sequence)return;objectUrl=URL.createObjectURL(new Blob([bytes],{type:props.role.thumbnail.media_type}));src.value=objectUrl}catch{if(current===sequence)failed.value=true}
},{immediate:true})
onBeforeUnmount(()=>{sequence++;release()})
</script>
<template><view class="character-portrait"><image v-if="src" :src="src" :mode="role.thumbnail?'aspectFit':(mode||'aspectFill')" :aria-label="role.name"/><text v-else>{{failed?'暂无法加载形象':role.name.slice(0,2)}}</text></view></template>
<style scoped>.character-portrait{display:inline-flex;align-items:center;justify-content:center;overflow:hidden;flex-shrink:0}.character-portrait image{width:100%;height:100%}.character-portrait text{font-size:20rpx;color:#856143}</style>
