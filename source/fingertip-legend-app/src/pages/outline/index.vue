<script setup lang="ts">
import { ref } from 'vue'
import { onLoad, onShow } from '@dcloudio/uni-app'
import FeatureLayout from '../../components/FeatureLayout.vue'
import { api } from '../../services/api'
import type { GenerationTask } from '../../types/domain'
import { errorMessage, goPage, requireSession } from '../../utils/navigation'
const taskId = ref(''), task = ref<GenerationTask|null>(null), feedback = ref(''), busy = ref(false), error = ref('')
onLoad(q => { taskId.value = String(q?.taskId || '') })
async function load() {
  if (!requireSession()) return
  error.value = ''
  try { task.value = await api.generationTask(taskId.value) } catch(e) { error.value = errorMessage(e) }
}
onShow(load)
async function submit(action: 'approve'|'revise') {
  const plan = task.value?.plan
  if (!plan || busy.value) return
  if (action === 'revise' && !feedback.value.trim()) { error.value = '先写下希望修改的地方'; return }
  busy.value = true; error.value = ''
  try {
    task.value = action === 'approve'
      ? await api.approveOutline(taskId.value, plan.revision, plan.fingerprint)
      : await api.reviseOutline(taskId.value, plan.revision, plan.fingerprint, feedback.value.trim())
    goPage('progress', {taskId:taskId.value})
  } catch(e) { error.value = errorMessage(e) } finally { busy.value = false }
}
</script>
<template>
  <FeatureLayout title="确认故事大纲" subtitle="先确定故事走向，再让文字生长">
    <view class="page-content">
      <view v-if="error" class="paper-panel"><text class="error-copy" role="alert">{{error}}</text><button class="outline-button full-button" @tap="load">重新加载最新大纲</button></view>
      <template v-if="task?.plan">
        <view class="paper-panel">
          <view class="row-between"><text class="pill">第 {{task.plan.revision}} 版大纲</text><text class="small-note">{{task.plan.targetEpisodes}} 集 · 目标约 {{task.plan.targetDurationSec}} 秒/集</text></view>
          <text class="outline-title serif">{{task.plan.content.title}}</text><text class="copy">{{task.plan.content.premise}}</text>
          <view v-for="(label,key) in {beginning:'开场',development:'发展',climax:'高潮',ending:'结局'}" :key="key" class="outline-section"><text class="field-label">{{label}}</text><text class="copy">{{task.plan.content[key]}}</text></view>
          <view v-if="task.plan.content.character_portrayals?.length" class="outline-section"><text class="field-label">出场人物</text><text v-for="actor in task.plan.content.character_portrayals" :key="actor.role_id" class="actor-copy">{{actor.story_name}}<text v-if="actor.speech_style"> · {{actor.speech_style}}</text></text></view>
        </view>
        <view v-for="episode in task.plan.content.episode_outlines" :key="episode.index" class="paper-panel section-gap"><text class="section-title serif">第 {{episode.index}} 集 · {{episode.title}}</text><text class="copy">{{episode.core_goal}}</text><text v-for="(beat,index) in episode.major_beats" :key="index" class="beat-copy">{{index+1}}. {{beat}}</text></view>
        <view v-if="task.status==='awaiting_approval'" class="paper-panel section-gap">
          <text class="field-label">希望调整哪些地方？</text><textarea v-model="feedback" class="form-textarea" aria-label="大纲修改意见" placeholder="例如：结局更温暖，减少人物，让冲突集中在同一个场景。" maxlength="2000" :disabled="busy" />
          <button class="outline-button full-button" :disabled="busy" @tap="submit('revise')">按意见修改大纲</button><button class="warm-button full-button" :disabled="busy" :loading="busy" @tap="submit('approve')">确认大纲，生成正文</button>
          <text class="small-note spaced">修改和确认后会调用真实 AI 服务。确认只针对当前这一版；正文完成后还需制作声音和视觉演绎。</text>
        </view>
        <button v-else class="warm-button full-button" @tap="goPage('progress',{taskId})">查看当前创作进度</button>
      </template>
      <view v-else-if="!error" class="paper-panel"><text class="copy">大纲正在准备或已经进入下一阶段。</text><button class="outline-button full-button" @tap="goPage('progress',{taskId})">查看创作进度</button></view>
    </view>
  </FeatureLayout>
</template>
<style scoped>.outline-title{display:block;font-size:44rpx;margin:30rpx 0 22rpx;line-height:1.5}.outline-section{margin-top:30rpx}.beat-copy,.actor-copy{display:block;margin-top:18rpx;line-height:1.85;color:#624a38;font-size:27rpx;white-space:pre-wrap;overflow-wrap:anywhere}</style>
