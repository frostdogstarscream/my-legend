<script setup lang="ts">
import type { OutputKind, ProductionCapabilities } from '../types/production'
defineProps<{output:OutputKind;mode:'automatic'|'reviewed';capabilities:ProductionCapabilities|null}>()
defineEmits<{(e:'update:output',value:OutputKind):void;(e:'update:mode',value:'automatic'|'reviewed'):void}>()
const choices:{value:OutputKind;label:string}[]=[{value:'script',label:'故事剧本'},{value:'audio',label:'剧本与广播剧'},{value:'h5',label:'完整舞台作品'}]
</script>
<template><view class="spaced production-choices"><text class="field-label">这次想制作什么？</text><view class="chip-list"><button v-for="choice in choices" :key="choice.value" class="chip" :class="{selected:output===choice.value}" :disabled="choice.value==='h5'?!capabilities?.h5_flow?.configured:!capabilities?.outputs.includes(choice.value)" :aria-pressed="output===choice.value" @tap="$emit('update:output',choice.value)">{{choice.label}}</button></view>

  <text v-if="output==='h5'" class="small-note spaced">角色会走进故事里，有对白，也有相应的场景。</text>
  <text v-if="!capabilities" class="small-note spaced">正在准备…</text>
</view></template>
