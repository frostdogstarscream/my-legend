<script setup lang="ts">
import { goTab, type TabId } from '../utils/navigation'
withDefaults(defineProps<{ active: TabId; tone?: 'default' | 'warm' }>(),{tone:'default'})
const tabs: { id: TabId; label: string; icon: string }[] = [
  { id: 'home', label: '首页', icon: 'home' },
  { id: 'create', label: '创作', icon: 'create' },
  { id: 'stories', label: '故事', icon: 'stories' },
  { id: 'characters', label: '角色', icon: 'characters' },
  { id: 'profile', label: '我的', icon: 'profile' },
]
</script>

<template>
  <view class="tabbar" :class="{'tabbar--warm':tone==='warm'}" role="navigation" aria-label="主导航">
    <button v-for="tab in tabs" :key="tab.id" class="tab" :class="{ 'tab--active': active === tab.id }" :aria-label="tab.label" :aria-current="active === tab.id ? 'page' : undefined" @tap="goTab(tab.id)">
      <view class="tab-inner">
        <image :src="`/static/figma/${tab.icon}.svg`" class="tab-icon" aria-hidden="true" />
        <text>{{ tab.label }}</text>
      </view>
    </button>
  </view>
</template>

<style scoped>
.tabbar { position: fixed; z-index: 20; left: 0; right: 0; bottom: 0; display: flex; align-items: flex-start; padding: 15rpx 30rpx calc(38rpx + var(--bottom-inset)); border-top: 2rpx solid #e2e8f0cc; background: #fffffff2; backdrop-filter: blur(16px); }
.tab { flex: 1; min-width: 0; min-height: 70rpx; display: flex; justify-content: center; align-items: flex-start; color: #64748b; }
.tab-inner { display: flex; flex-direction: column; align-items: center; gap: 4rpx; font-size: 19rpx; line-height: 29rpx; }
.tab-icon { width: 38rpx; height: 38rpx; }
.tab:not(.tab--active):first-child .tab-icon { filter: brightness(0) saturate(100%) invert(44%) sepia(14%) saturate(619%) hue-rotate(176deg) brightness(95%) contrast(85%); }
.tab--active .tab-inner { flex-direction: row; padding: 7rpx 24rpx; gap: 8rpx; background: #2563eb; border-radius: 999rpx; box-shadow: 0 12rpx 25rpx #3b82f64d; font-size: 21rpx; font-weight: 700; line-height: 31rpx; color: white; }
.tab--active .tab-icon { width: 27rpx; height: 27rpx; filter: brightness(0) invert(1); }
@media (min-width: 461px) { .tabbar { max-width: 393px; left: 50%; right: auto; width: 100%; transform: translateX(-50%); } }
.tabbar--warm{background:#fff5e5;border-radius:35rpx 35rpx 0 0;border-color:#e8c7a6}.tabbar--warm .tab{color:#b18168}.tabbar--warm .tab--active .tab-inner{background:#fff8ec;color:#9e3425;box-shadow:0 0 0 2rpx #e4ba91;flex-direction:column;padding:8rpx 20rpx;border-radius:35rpx;margin-top:-27rpx}.tabbar--warm .tab--active .tab-icon{filter:brightness(.6) sepia(1) saturate(6);width:44rpx;height:44rpx}
</style>
