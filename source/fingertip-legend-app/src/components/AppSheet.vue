<script setup lang="ts">
defineProps<{ title: string; open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()
</script>
<template><view v-if="open" class="sheet-shade" @tap="emit('close')"><view class="sheet" role="dialog" :aria-label="title" @tap.stop><view class="sheet-heading"><text class="serif">{{ title }}</text><button aria-label="关闭弹层" @tap="emit('close')">关闭</button></view><view class="sheet-body"><slot /></view><view v-if="$slots.footer" class="sheet-footer"><slot name="footer" /></view></view></view></template>
<style scoped>
.sheet-shade{position:fixed;inset:0;z-index:90;background:#251a1580;display:flex;align-items:flex-end;justify-content:center}.sheet{width:100%;max-width:var(--app-width);max-height:85dvh;overflow:hidden;display:flex;flex-direction:column;padding:36rpx 32rpx calc(42rpx + env(safe-area-inset-bottom,0px));background:#fff9f0;border-radius:36rpx 36rpx 0 0;box-shadow:0 -12rpx 40rpx #49311d22}.sheet-heading{display:flex;justify-content:space-between;align-items:center;margin-bottom:28rpx;font-size:36rpx}.sheet-heading button{min-height:44px;min-width:44px;padding:16rpx;color:#856143;font-size:26rpx}
.sheet-heading{flex-shrink:0;margin-bottom:16px}.sheet-body{min-height:0;overflow-y:auto;overscroll-behavior:contain}.sheet-footer{flex-shrink:0;padding-top:12px}.sheet-footer :deep(.full-button){margin-top:0}
</style>
