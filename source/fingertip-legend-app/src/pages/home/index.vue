<script setup lang="ts">
import { ref } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import BrandLogo from '../../components/BrandLogo.vue'
import AppTabbar from '../../components/AppTabbar.vue'
import StoryCard from '../../components/StoryCard.vue'
import { api } from '../../services/api'
import type { HomeSummary } from '../../types/domain'
import { errorMessage, goPage, goTab, requireSession, showPending } from '../../utils/navigation'

const summary = ref<HomeSummary | null>(null), loading = ref(false), error = ref('')
async function load() {
  if (!requireSession()) return
  loading.value = true; error.value = ''
  try { summary.value = await api.home() }
  catch (e) { error.value = errorMessage(e) }
  finally { loading.value = false }
}
onShow(load)
</script>

<template>
  <view class="home-screen">
    <image class="home-background" src="/static/figma/home-background.png" mode="aspectFill" aria-hidden="true" />
    <view class="home-shade" />
    <view class="home-header">
      <view class="user-bar">
        <button class="user-summary" aria-label="打开我的资料" @tap="goTab('profile')">
          <image class="user-avatar" :src="summary?.user.avatarUrl || '/static/figma/avatar.png'" mode="aspectFill" />
          <view class="user-copy"><view class="user-name-row"><text class="user-name">{{ summary?.user.displayName || '加载中' }}</text><text v-if="summary?.user.vipDays" class="vip-badge">♔ VIP</text></view><text class="vip-days">{{ summary ? (summary.user.vipDays > 0 ? `会员剩余 ${summary.user.vipDays} 天 ›` : '开启故事新旅程') : '正在读取资料' }}</text></view>
        </button>
        <view class="user-actions">
          <button class="coin-button" aria-label="我的灵感币" @tap="goTab('profile')"><text>🪙</text><text class="coin-count">{{ summary?.user.balance ?? '—' }}</text><text class="coin-plus">+</text></button>
          <button class="circle-button" aria-label="通知" @tap="showPending('消息通知', '通知列表入口已预留，后续接入站内消息服务。')"><image src="/static/figma/notification.svg" /><view class="notification-dot" /></button>
          <button class="circle-button" aria-label="设置" @tap="goTab('profile')"><image src="/static/figma/settings.svg" /></button>
        </view>
      </view>
      <BrandLogo />
    </view>
    <view class="workbench">
      <view v-if="error" class="home-error"><text>{{ error }}</text><button @tap="load">重试</button></view>
      <view v-else-if="loading && !summary" class="home-loading">正在打开你的故事世界…</view>
      <StoryCard kind="new" @open="goTab('create')" />
      <StoryCard kind="continue" :story="summary?.latestStory" @open="goTab('stories')" />
      <view class="small-cards"><StoryCard kind="characters" @open="goTab('characters')" /><StoryCard kind="shop" @open="goPage('shop')" /></view>
    </view>
    <button class="ai-companion" aria-label="AI 伴读" @tap="showPending('AI 伴读', '伴读入口已预留。后续由后台提供对话、TTS 音频和播放会话；当前尚未接入语音服务。')"><image class="ai-avatar" src="/static/figma/ai-avatar.png" mode="aspectFill" /><image class="ai-microphone" src="/static/figma/microphone.svg" /></button>
    <AppTabbar active="home" />
  </view>
</template>

<style scoped>
.home-screen { position: relative; display: flex; flex-direction: column; min-height: max(100vh, 1490rpx); padding-bottom: calc(126rpx + var(--bottom-inset)); overflow: hidden; }
.home-background, .home-shade { position: absolute; inset: 0; width: 100%; height: 100%; }
.home-shade { background: linear-gradient(#451a0373,transparent 50%,#00000073); }
.home-header { position: relative; padding-top: var(--status-inset); }
.user-bar { display: flex; align-items: center; justify-content: space-between; padding: 8rpx 30rpx; min-height: 92rpx; }
.user-summary { display: flex; align-items: center; min-width: 0; text-align: left; }
.user-avatar { width: 76rpx; height: 76rpx; border: 4rpx solid #fcd34d; border-radius: 50%; box-shadow: 0 8rpx 12rpx #0000001a; }
.user-copy { padding-left: 15rpx; }
.user-name-row { display: flex; align-items: center; gap: 11rpx; }
.user-name { font-size: 27rpx; line-height: 38rpx; color: white; font-weight: 700; white-space: nowrap; max-width: 140rpx; overflow: hidden; text-overflow: ellipsis; text-shadow: 0 2rpx 4rpx #0006; }
.vip-badge { font-size: 19rpx; line-height: 29rpx; font-weight: 900; padding: 1rpx 11rpx; border-radius: 8rpx; color: #451a03; background: linear-gradient(90deg,#fbbf24,#d97706); }
.vip-days { display: block; color: #fde68ae6; font-size: 21rpx; line-height: 32rpx; }
.user-actions { display: flex; align-items: center; gap: 15rpx; }
.coin-button { display: flex; align-items: center; gap: 8rpx; padding: 8rpx 19rpx; background: #00000073; border: 2rpx solid #fbbf2466; border-radius: 999rpx; color: #fef3c7; font-size: 23rpx; line-height: 31rpx; }
.coin-count { font-weight: 700; }
.coin-plus { color: #fcd34d; font-weight: 700; }
.circle-button { position: relative; width: 57rpx; height: 57rpx; display: flex; align-items: center; justify-content: center; background: #0006; border: 2rpx solid #ffffff1a; border-radius: 50%; }
.circle-button image { width: 31rpx; height: 31rpx; }
.notification-dot { position: absolute; top: 4rpx; right: 6rpx; width: 11rpx; height: 11rpx; border-radius: 50%; background: #ef4444; }
.workbench { position: relative; flex: 1; display: flex; flex-direction: column; justify-content: flex-end; gap: 15rpx; min-height: 899rpx; padding: 25rpx 27rpx 23rpx; }
.small-cards { display: flex; gap: 15rpx; }
.ai-companion { position: absolute; z-index: 5; bottom: calc(1034rpx + var(--bottom-inset)); right: 22rpx; display: flex; flex-direction: column; align-items: center; gap: 8rpx; padding: 12rpx; border: 2rpx solid #fbbf2499; border-radius: 999rpx; background: #000000d9; box-shadow: 0 20rpx 45rpx #0004; }
.ai-avatar { width: 61rpx; height: 61rpx; border: 2rpx solid #fcd34d; border-radius: 50%; }
.ai-microphone { width: 27rpx; height: 27rpx; }
.home-error { display: flex; align-items: center; justify-content: space-between; gap: 20rpx; background: #fff5e9; color: #9d331c; padding: 16rpx 24rpx; border-radius: 18rpx; font-size: 22rpx; }
.home-error button { font-weight: 700; min-width: 70rpx; }
.home-loading { color: #fff4d5; text-align: center; font-size: 24rpx; margin-bottom: 20rpx; }
</style>
