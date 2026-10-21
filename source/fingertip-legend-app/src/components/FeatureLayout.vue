<script setup lang="ts">
import AppTabbar from './AppTabbar.vue'
import { goBack, type TabId } from '../utils/navigation'
defineProps<{ title: string; subtitle?: string; active?: TabId }>()
</script>

<template>
  <view class="feature-layout">
    <view class="feature-hero">
      <image class="feature-background" src="/static/figma/home-background.png" mode="aspectFill" aria-hidden="true" />
      <view class="feature-shade" />
      <view class="feature-header"><button class="back-button" aria-label="返回" @tap="goBack">‹</button><text class="serif">{{ title }}</text></view>
      <text v-if="subtitle" class="feature-subtitle">{{ subtitle }}</text>
    </view>
    <slot />
    <AppTabbar v-if="active" :active="active" />
  </view>
</template>

<style scoped>
.feature-layout { min-height: 100vh; background: linear-gradient(#fff4df,#fffbf2 50%); }
.feature-hero { position: relative; padding: calc(var(--status-inset) + 22rpx) 32rpx 44rpx; color: white; overflow: hidden; }
.feature-background, .feature-shade { position: absolute; inset: 0; width: 100%; height: 100%; }
.feature-background { object-position: center 30%; }
.feature-shade { background: linear-gradient(90deg,#451a03cc,#451a0380); }
.feature-header { position: relative; display: flex; align-items: center; gap: 18rpx; font-size: 46rpx; line-height: 1.5; }
.back-button { min-width: 70rpx; min-height: 70rpx; font-family: Arial,sans-serif; font-size: 62rpx; }
.feature-subtitle { position: relative; display: block; padding-left: 88rpx; font-size: 24rpx; color: #fff0c1; margin-top: 6rpx; }
</style>
