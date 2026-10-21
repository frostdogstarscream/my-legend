<script setup lang="ts">
withDefaults(defineProps<{ variant?: 'login' | 'home' | 'performance' }>(), { variant: 'home' })
</script>

<template>
  <view class="brand" :class="`brand--${variant}`">
    <view class="brand-heading">
      <text class="brand-title serif">我的传说</text>
      <view class="feather-slot"><image class="feather" src="/static/figma/feather.svg" aria-hidden="true" /></view>
    </view>
    <view class="brand-tagline">
      <view class="brand-line" />
      <text>{{ variant === 'login' ? '用 AI 点亮想象 · 让故事活起来' : '让故事活起来' }}</text>
      <text class="brand-star">✦</text>
      <view class="brand-line brand-line--end" />
    </view>
  </view>
</template>

<style scoped>
.brand { display: flex; flex-direction: column; align-items: center; padding: 4rpx 0 8rpx; }
.brand-heading { display: flex; align-items: center; justify-content: center; }
.brand-title { display: block; font-size: 63rpx; line-height: 79rpx; letter-spacing: 3.8rpx; color: #fef08a; background: linear-gradient(#fff 0%,#fef08a 35%,#f59e0b 75%,#b45309); background-clip: text; -webkit-background-clip: text; -webkit-text-fill-color: transparent; filter: drop-shadow(0 4rpx 5rpx #450a0a); }
.feather-slot { position: relative; width: 61rpx; height: 27rpx; }
.feather { position: absolute; width: 115rpx; height: 122rpx; left: -22rpx; top: -57rpx; }
.brand-tagline { display: flex; align-items: center; gap: 15rpx; padding-top: 4rpx; white-space: nowrap; color: #fde68a; font-family: 'Legend Brush', serif; font-size: 23rpx; letter-spacing: 5.7rpx; line-height: 32rpx; }
.brand-line { width: 38rpx; height: 1px; background: linear-gradient(90deg,transparent,#fde68a); }
.brand-line--end { transform: rotate(180deg); }
.brand-star { font-size: 19rpx; letter-spacing: 0; }
.brand--login { padding-top: 8rpx; }
.brand--login .brand-title { font-size: 67rpx; line-height: 84rpx; color: white; background: none; -webkit-text-fill-color: white; filter: none; }
.brand--login .brand-tagline { font-family: inherit; color: #ffffffe6; font-size: 21rpx; letter-spacing: 3.5rpx; padding-top: 8rpx; }
.brand--login .brand-line { background: linear-gradient(90deg,transparent,#ffffffb3); }
.brand--performance{align-items:flex-start}.brand--performance .brand-title{color:white;background:none;-webkit-text-fill-color:white;filter:none;font-size:54rpx;line-height:68rpx}.brand--performance .brand-tagline{font-family:inherit;font-size:19rpx;letter-spacing:7rpx;color:#fff1dc;gap:8rpx}.brand--performance .brand-line,.brand--performance .brand-star{display:none}.brand--performance .feather-slot{width:52rpx}
</style>
