<script setup lang="ts">
const props=withDefaults(defineProps<{modelValue?:string|number|boolean;value?:string|number;type?:string;modelModifiers?:Record<string,boolean>}>(),{modelValue:undefined})
const emit=defineEmits<{(e:'update:modelValue',value:string|number|boolean):void;(e:'input',event:Event):void;(e:'change',event:Event):void}>()
const nativeTag='input'
function update(event:Event){const input=event.target as HTMLInputElement;const value=props.type==='checkbox'?input.checked:props.modelModifiers?.number&&input.value!==''?Number(input.value):input.value;emit('update:modelValue',value)}
function input(event:Event){update(event);emit('input',event)}
function change(event:Event){update(event);emit('change',event)}
</script>
<template><component class="native-input" :is="nativeTag" :type="type||'text'" :value="type==='checkbox'?undefined:(modelValue??value)" :checked="type==='checkbox'?!!modelValue:undefined" @input="input" @change="change"/></template>
