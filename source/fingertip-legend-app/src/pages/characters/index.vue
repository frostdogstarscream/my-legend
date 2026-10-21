<script setup lang="ts">
import { ref } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import FeatureLayout from '../../components/FeatureLayout.vue'
import { api } from '../../services/api'
import type { Character } from '../../types/domain'
import { errorMessage,goPage,goTab,requireSession } from '../../utils/navigation'
const roles=ref<Character[]>([]),error=ref(''),loading=ref(true)
async function load(){if(!requireSession())return;loading.value=true;error.value='';try{roles.value=await api.characters()}catch(e){error.value=errorMessage(e)}finally{loading.value=false}}
onShow(load)
</script>
<template><FeatureLayout title="我的角色" subtitle="故事里的伙伴，也在等你的下一次出发" active="characters"><view class="page-content"><view class="row-between"><text class="section-title serif">已拥有的伙伴</text><text class="small-note">{{roles.length}} 位</text></view><view v-if="loading" class="state-panel">正在寻找伙伴…</view><view v-else-if="error" class="state-panel"><text class="error-copy">{{error}}</text><button class="warm-button" @tap="load">重试</button></view><view v-else class="character-list"><view v-for="role in roles" :key="role.id" class="paper-panel role-item"><image :src="role.avatarUrl" class="role-avatar" mode="aspectFill"/><view class="role-copy"><text class="role-name serif">{{role.name}}</text><text class="role-tags">{{role.tags?.join(' · ')}}</text><text class="small-note">{{role.description}}</text><button class="role-detail-button" @tap="goPage('role-detail',{id:role.id})">查看角色 ›</button></view></view></view><view class="paper-panel section-gap"><text class="section-title serif">让伙伴走进你的故事</text><text class="copy muted">选择角色后，带上他们的性格与想象，一起开启新的旅程。</text><button class="warm-button full-button" @tap="goTab('create')">带上伙伴，开始创作</button></view><button class="outline-button full-button" @tap="goPage('shop')">去商城遇见更多伙伴</button></view></FeatureLayout></template>
<style scoped>.character-list{display:flex;flex-direction:column;gap:24rpx}.role-item{display:flex;gap:25rpx;align-items:center;background:#fbf7ff;border-color:#e5d9f0;padding:28rpx}.role-avatar{width:140rpx;height:174rpx;border-radius:23rpx;background:#f1e5ff}.role-copy{flex:1;min-width:0}.role-name{font-size:42rpx;color:#352360;display:block}.role-tags{display:block;color:#7955a4;font-size:22rpx;margin:10rpx 0}.role-detail-button{margin-top:18rpx;padding:14rpx 25rpx;background:#7955a4;border-radius:99rpx;color:white;font-size:23rpx}</style>
