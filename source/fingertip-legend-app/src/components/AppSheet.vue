<script setup lang="ts">
defineProps<{ title: string; open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()
</script>
<template><view v-if="open" class="sheet-shade" @tap="emit('close')"><view class="sheet" role="dialog" :aria-label="title" @tap.stop><view class="sheet-heading"><text class="serif">{{ title }}</text><button aria-label="关闭弹层" @tap="emit('close')">关闭</button></view><slot /></view></view></template>
<style scoped>
.sheet-shade{position:fixed;inset:0;z-index:90;background:#251a1580;display:flex;align-items:flex-end;justify-content:center}.sheet{width:100%;max-width:460px;max-height:85vh;overflow-y:auto;padding:36rpx 32rpx calc(42rpx + env(safe-area-inset-bottom,0px));background:#fff9f0;border-radius:36rpx 36rpx 0 0;box-shadow:0 -12rpx 40rpx #49311d22}.sheet-heading{display:flex;justify-content:space-between;align-items:center;margin-bottom:28rpx;font-size:36rpx}.sheet-heading button{min-height:70rpx;padding:16rpx;color:#856143;font-size:26rpx}
</style>
