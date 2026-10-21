<script setup lang="ts">
import type { OutputKind, ProductionCapabilities } from '../types/production'
defineProps<{output:OutputKind;mode:'automatic'|'reviewed';duration:number;capabilities:ProductionCapabilities|null}>()
defineEmits<{(e:'update:output',value:OutputKind):void;(e:'update:mode',value:'automatic'|'reviewed'):void;(e:'update:duration',value:number):void}>()
const choices:{value:OutputKind;label:string}[]=[{value:'script',label:'故事剧本'},{value:'audio',label:'剧本与广播剧'},{value:'visual',label:'剧本、声音与演绎'}]
</script>
<template><view class="spaced production-choices"><text class="field-label">这次想制作什么？</text><view class="chip-list"><button v-for="choice in choices" :key="choice.value" class="chip" :class="{selected:output===choice.value}" :disabled="!capabilities?.outputs.includes(choice.value)" :aria-pressed="output===choice.value" @tap="$emit('update:output',choice.value)">{{choice.label}}</button></view>
  <text class="field-label spaced">制作方式</text><text v-if="output!=='visual'" class="small-note spaced">先生成，再试听修改。不等待大纲确认或内容审核；作品交付后由你判断效果。</text><view v-else class="chip-list"><button class="chip" :class="{selected:mode==='automatic'}" @tap="$emit('update:mode','automatic')">自动完成</button><button class="chip" :class="{selected:mode==='reviewed'}" @tap="$emit('update:mode','reviewed')">先确认大纲</button></view>
  <text class="field-label spaced">目标时长</text><picker :range="capabilities?.target_duration_seconds||[90]" :value="Math.max(0,(capabilities?.target_duration_seconds||[90]).indexOf(duration))" @change="$emit('update:duration',(capabilities?.target_duration_seconds||[90])[Number($event.detail.value)])"><view class="duration-choice">约 {{duration}} 秒　›</view></picker>
  <text v-if="output!=='script'" class="small-note spaced">系统按角色身份绑定音色；旁白单独配音。</text><text v-if="output==='visual'" class="small-note spaced">首版制作基础像素演绎，支持出入场、移动、转向及简单反应。复杂动作超出能力时会保留剧本和声音，并说明问题。</text>
  <text v-if="!capabilities" class="small-note spaced">正在确认制作服务是否可用…</text>
</view></template>
<style scoped>.duration-choice{padding:24rpx;border:2rpx solid #e7d6bf;border-radius:18rpx;color:#795f45;background:#fffaf0}.production-choices{padding-bottom:24rpx}</style>
