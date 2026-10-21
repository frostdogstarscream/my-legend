<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ src: string; kind: 'new' | 'continue' | 'characters' | 'shop' }>()
const crops = {
  new: { left: '-91%', top: '-13.244%', width: '204%', height: '142.783%' },
  continue: { left: '-109.173%', top: '-3.032%', width: '330.81%', height: '151.50%' },
  characters: { left: '-10.058%', top: '.058%', width: '120.13%', height: '355.769%' },
  shop: { left: '-10.032%', top: '-.333%', width: '122.08%', height: '362.745%' },
}
const crop = computed(() => crops[props.kind])
</script>

<template>
  <view class="illustration-crop" :class="`illustration-crop--${kind}`">
    <image class="illustration-source" :style="crop" :src="src" mode="aspectFill" aria-hidden="true" />
  </view>
</template>

<style scoped>
.illustration-crop { position: absolute; overflow: hidden; border-radius: 27rpx; pointer-events: none; }
.illustration-source { position: absolute; max-width: none; }
.illustration-crop--new { left: 48.767%; top: 9.187%; width: 47.671%; height: 68.032%; }
.illustration-crop--continue { left: 36.712%; top: 7.545%; width: 30.137%; height: 65.788%; }
.illustration-crop--characters, .illustration-crop--shop { left: 5.742%; top: 3.85%; width: 86.275%; height: 29.131%; border-radius: 19rpx; }
.illustration-crop--shop { top: 3.955%; height: 28.571%; }
</style>
