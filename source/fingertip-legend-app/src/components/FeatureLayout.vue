<script setup lang="ts">
import AppTabbar from './AppTabbar.vue'
import { goBack, type TabId } from '../utils/navigation'
import {connection} from '../stores/connection'
import {api} from '../services/api'
const emit=defineEmits<{reconnect:[]}>()
async function reconnect(){if(connection.reconnecting)return;connection.reconnecting=true;try{await api.characters();connection.offline=false;emit('reconnect')}catch{connection.offline=true}finally{connection.reconnecting=false}}
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
    <view v-if="connection.offline" class="connection-message" role="status"><text>连接暂时中断，填写内容会保留。</text><button :disabled="connection.reconnecting" @tap="reconnect">{{connection.reconnecting?'连接中…':'重新连接'}}</button></view>
    <slot />
    <AppTabbar v-if="active" :active="active" />
  </view>
</template>

<style scoped>
.feature-layout { min-height: 100dvh; background: linear-gradient(#fff4df,#fffbf2 50%); }
.connection-message{padding:12px 16px;background:#fff1d5;font-size:13px;display:flex;align-items:center;gap:8px}.connection-message button{min-width:80px;min-height:44px;color:#9e3425;font-size:13px}
.feature-hero { position: relative; padding: calc(var(--status-inset) + 8px) 16px 16px; color: white; overflow: hidden; }
.feature-background, .feature-shade { position: absolute; inset: 0; width: 100%; height: 100%; }
.feature-background { object-position: center 30%; }
.feature-shade { background: linear-gradient(90deg,#451a03cc,#451a0380); }
.feature-header { position: relative; display: flex; align-items: center; gap: 8px; font-size: 24px; line-height: 1.5; }
.back-button { min-width: 44px; min-height: 44px; font-family: Arial,sans-serif; font-size: 62rpx; }
.feature-subtitle { position: relative; display: block; padding-left: 52px; font-size: 13px; color: #fff0c1; margin-top: 6rpx; }
</style>
