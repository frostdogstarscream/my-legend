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

.tabbar { position: fixed; z-index: 20; inset: auto 0 0; display: flex; height: var(--tabbar-height); padding: 6px 8px calc(10px + var(--bottom-inset)); border-top: 1px solid #e2e8f0cc; background: #fffffff2; backdrop-filter: blur(16px); }
.tab { flex: 1; min-width: 0; min-height: 48px; display: flex; align-items: center; justify-content: center; color: #64748b; }
.tab-inner { display: flex; flex-direction: column; justify-content: center; align-items: center; gap: 4px; min-width: 56px; min-height: 50px; border-radius: 14px; font-size: 11px; line-height: 16px; }
.tab-icon { width: 22px; height: 22px; }
.tab:not(.tab--active):first-child .tab-icon { filter: brightness(0) saturate(100%) invert(44%) sepia(14%) saturate(619%) hue-rotate(176deg); }
.tab--active .tab-inner { color: #1d4ed8; background: #eaf1ff; font-weight: 700; }
.tab--active .tab-icon { filter: brightness(0) saturate(100%) invert(30%) sepia(90%) saturate(2500%) hue-rotate(210deg); }
.tabbar--warm { background: #fff5e5f5; border-color: #e8c7a6; }
.tabbar--warm .tab { color: #946246; }
.tabbar--warm .tab--active .tab-inner { background: #f8e5ce; color: #9e3425; }
.tabbar--warm .tab--active .tab-icon { filter: brightness(.6) sepia(1) saturate(6); }
@media (min-width: 461px) { .tabbar { max-width: var(--app-width); left: 50%; right: auto; width: 100%; transform: translateX(-50%); } }

</style>
