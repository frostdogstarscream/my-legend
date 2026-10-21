<script setup lang="ts">
withDefaults(defineProps<{ name: string; size?: number; light?: boolean }>(), { size: 24, light: false })
</script>
<template><image :src="`/static/icons/${name}.svg`" :style="{width:size+'px',height:size+'px',filter:light?'brightness(0) invert(1)':'none'}" mode="aspectFit" aria-hidden="true" /></template>
