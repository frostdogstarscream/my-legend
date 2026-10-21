<script setup lang="ts">
import { ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import FeatureLayout from '../../components/FeatureLayout.vue'
import type { Character } from '../../types/domain'
import { api } from '../../services/api'
import { errorMessage,goTab,requireSession,showMessage } from '../../utils/navigation'
const role=ref<Character|null>(null),error=ref('')
onLoad(async q=>{if(!requireSession())return;try{role.value=(await api.characters()).find(c=>c.id===q?.id)||null;if(!role.value)error.value='没有找到这个伙伴'}catch(e){error.value=errorMessage(e)}})
function useRole(){if(!role.value)return;uni.setStorageSync('my-legend:selected-role',role.value.id);goTab('create')}
</script>
<template><FeatureLayout title="角色详情" subtitle="你的故事伙伴"><view class="page-content"><text v-if="error" class="error-copy">{{error}}</text><template v-if="role"><view class="role-portrait-panel"><image :src="role.avatarUrl" class="role-portrait" mode="aspectFit"/><text class="role-name serif">{{role.name}}</text><view class="chip-list role-tags"><text v-for="tag in role.tags" :key="tag" class="pill violet">{{tag}}</text></view><text class="copy">{{role.description}}</text></view><view class="paper-panel section-gap"><text class="section-title serif">伙伴档案</text><text class="copy">{{role.biography}}</text><text class="field-label">适合题材</text><text class="small-note">国风奇幻 / 冒险成长 / 生活治愈</text></view><view class="paper-panel section-gap"><text class="field-label">{{role.voiceDescription||'角色声音'}}</text><text class="copy">“一起去看看，故事会把我们带向哪里。”</text><button class="outline-button full-button" @tap="showMessage('角色试听音频尚未提供')">试听角色声音</button></view><button class="warm-button full-button" @tap="useRole">用{{role.name}}创作故事</button></template></view></FeatureLayout></template>
<style scoped>.role-portrait-panel{text-align:center;padding:12rpx 20rpx 24rpx}.role-portrait{width:250rpx;height:280rpx;margin:0 auto;border-radius:32rpx;background:#f3ecff}.role-name{display:block;font-size:56rpx;margin:26rpx 0}.role-tags{justify-content:center;margin-bottom:26rpx}</style>
