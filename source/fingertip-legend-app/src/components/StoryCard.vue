<script setup lang="ts">
import { computed } from 'vue'
import type { Story } from '../types/domain'
import type {WorkSummary} from '../types/works'
import {workStatus} from '../utils/works'
import IllustrationCrop from './IllustrationCrop.vue'
const props = defineProps<{ kind: 'new' | 'continue' | 'characters' | 'shop'; story?: Story | null;work?:WorkSummary }>()
const emit = defineEmits<{ (e: 'open'): void }>()
const content = computed(() => ({
  new: { title: '新建故事', subtitle: '用想象力，创造另一个世界', action: '开始创作', image: 'new-story.png' },
  continue: { title: '继续故事', subtitle: '每个故事，都值得续写', action: '进入故事', image: 'continue-story.png' },
  characters: { title: '角色一览', subtitle: '遇见更多有趣的角色', action: '查看角色', image: 'characters.png' },
  shop: { title: '角色商城', subtitle: '收集喜欢的伙伴', action: '进入商城', image: 'shop.png' },
})[props.kind])
const progress = computed(() => Math.min(100, Math.max(0, Number(props.story?.progress) || 0)))
</script>

<template>
  <view class="story-card" :class="`story-card--${kind}`" @tap="emit('open')">
    <IllustrationCrop v-if="kind !== 'continue'" :src="`/static/figma/${content.image}`" :kind="kind" />
    <view class="card-copy">
      <text class="card-title serif">{{ content.title }}</text>
      <text class="card-subtitle">{{ content.subtitle }}</text>
      <button class="card-button" :aria-label="content.action" @tap.stop="emit('open')">
        <text>{{ content.action }}</text><text class="chevron" aria-hidden="true">›</text>
      </button>
    </view>
    <view v-if="kind === 'continue'" class="latest-story">
      <view class="latest-story-top">
        <image class="story-cover" :src="story?.coverUrl || '/static/figma/home-background.png'" mode="aspectFill" aria-hidden="true" />
        <view class="latest-info">
          <text class="latest-title">{{ work?.title || story?.title || '还没有故事' }}</text>
          <text class="latest-chapter">{{ work ? (work.recovery?.message || workStatus[work.status]) : story?.chapter || '写下第一份灵感' }}</text>
        </view>
      </view>
      <view v-if="work" class="progress-row"><text>{{work.kind==='draft'?'继续创作':work.play?'可播放作品':'查看制作'}}</text></view>
      <view v-else-if="story" class="progress-row" :aria-label="`故事进度 ${progress}%`">
        <view class="progress-track"><view class="progress-fill" :style="{ width: `${progress}%` }" /></view>
        <text>{{ progress }}%</text>
      </view>
    </view>
  </view>
</template>

<style scoped>
.story-card { position: relative; width: 100%; height: 270rpx; border: 4rpx solid #fff4d5; border-radius: 38rpx; overflow: hidden; background: linear-gradient(159deg,#fffdf4,#fff0d7); box-shadow: 0 15rpx 38rpx -8rpx #b4530933, inset 0 0 0 4rpx #d19a4c30; }
.card-copy { position: relative; display: flex; flex-direction: column; align-items: flex-start; padding: 34rpx 0 0 38rpx; width: 47%; }
.card-title { font-size: 48rpx; line-height: 60rpx; white-space: nowrap; }
.card-subtitle { margin-top: 13rpx; font-size: 21rpx; line-height: 32rpx; font-weight: 700; color: #856143; white-space: nowrap; }
.card-button { display: flex; align-items: center; justify-content: center; gap: 17rpx; height: 57rpx; min-width: 189rpx; margin-top: 36rpx; padding: 0 25rpx; background: #cf502a; color: #fff7ed; border-radius: 999rpx; font-size: 25rpx; font-weight: 700; box-shadow: inset 0 2rpx #ffffff59; }
.chevron { font-family: Arial,sans-serif; font-size: 42rpx; line-height: 1; font-weight: 400; }
.story-card--continue { height: 228rpx; background: linear-gradient(162deg,#eaf9ff,#caeaff); border-color: #f2fbff; color: #153c70; }
.story-card--continue .card-copy { width: 35%; padding: 19rpx 0 0 32rpx; }
.story-card--continue .card-title { font-size: 42rpx; line-height: 52rpx; }
.story-card--continue .card-subtitle { font-size: 19rpx; line-height: 29rpx; margin-top: 12rpx; color: #426781; }
.story-card--continue .card-button { height: 52rpx; min-width: 170rpx; margin-top: 29rpx; padding: 0 21rpx; font-size: 23rpx; background: #347d9f; }
.latest-story { position: absolute; left: 68.219%; top: 7.545%; width: 28.767%; height: 80.471%; padding: 15rpx 11rpx 0; border: 2rpx solid #fffffff2; border-radius: 23rpx; background: white; }
.latest-story-top { display: flex; gap: 10rpx; align-items: flex-start; }
.story-cover { width: 48rpx; height: 74rpx; border-radius: 8rpx; }
.latest-info { flex: 1; min-width: 0; }
.latest-title { display: block; font-size: 19rpx; line-height: 25rpx; font-weight: 900; color: #29485b; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.latest-chapter { display: block; font-size: 15rpx; line-height: 22rpx; color: #687888; margin-top: 14rpx; }
.progress-row { display: flex; align-items: center; gap: 8rpx; margin-top: 24rpx; font-size: 17rpx; font-weight: 700; color: #29485b; }
.progress-track { height: 11rpx; flex: 1; overflow: hidden; border-radius: 999rpx; background: #d8d6ca; }
.progress-fill { height: 100%; border-radius: 999rpx; background: #347d9f; }
.story-card--characters, .story-card--shop { height: 341rpx; width: calc((100% - 15rpx) / 2); }
.story-card--characters { background: linear-gradient(135deg,#f8f1ff,#e9dfff); color: #352360; border-color: #f8f1ff; }
.story-card--characters .card-copy, .story-card--shop .card-copy { padding: 139rpx 0 0; width: 100%; align-items: center; }
.story-card--characters .card-title, .story-card--shop .card-title { font-size: 42rpx; line-height: 52rpx; }
.story-card--characters .card-subtitle, .story-card--shop .card-subtitle { margin-top: 9rpx; font-size: 19rpx; line-height: 29rpx; font-weight: 400; color: #795e6e; }
.story-card--characters .card-button, .story-card--shop .card-button { height: 55rpx; min-width: 174rpx; margin-top: 20rpx; padding: 0 23rpx; font-size: 23rpx; }
.story-card--characters .card-button { background: #7955a4; }

.story-card--new { height: 156px; flex-shrink: 0; }
.story-card--new .card-copy { padding: 16px 0 0 18px; }
.card-button { min-height: 44px; margin-top: 12px; font-size: 14px; }
.story-card--continue { height: 144px; flex-shrink: 0; }
.story-card--continue .card-copy { width: 50%; padding: 14px 0 0 16px; }
.story-card--continue .card-button { min-height: 44px; margin-top: 12px; font-size: 14px; }
.latest-story { left: 52%; top: 12px; bottom: 12px; width: 44%; height: auto; padding: 12px; border-radius: 14px; }
.latest-story-top { gap: 0; }
.story-cover { display: none; }
.latest-title { font-size: 14px; line-height: 1.45; white-space: normal; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.latest-chapter { font-size: 11px; margin-top: 5px; }
.progress-row { font-size: 11px; margin-top: 8px; }
@media (max-width: 359px) { .card-subtitle { font-size: 10px; } .story-card--continue .card-subtitle { font-size: 10px; } .latest-story { padding: 10px; } }
</style>
