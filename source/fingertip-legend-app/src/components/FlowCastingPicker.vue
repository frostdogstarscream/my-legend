<script setup lang="ts">
import {ref,watch,onBeforeUnmount} from 'vue'
import {api} from '../services/api'
import {stageAsset} from '../services/stage-assets'
import type {Character} from '../types/domain'
import type {FlowCasting} from '../types/flow'
import type {LifeCatalog} from '../../../my-legend-stage/src/life-types'
const props=defineProps<{roles:Pick<Character,'id'|'name'|'stageBinding'>[];modelValue:FlowCasting|null;disabled?:boolean}>()
const emit=defineEmits<{'update:modelValue':[value:FlowCasting|null]}>()
const catalog=ref<LifeCatalog>(),error=ref(''),bindings=ref<Record<string,string>>({}),outfits=ref<Record<string,string>>({}),thumbs=ref<Record<string,string>>({})
let sequence=0
const knownRoles=new Set(props.modelValue?props.roles.map(r=>r.id):[])
function publish(){if(!catalog.value)return;const ids=new Set(props.roles.map(r=>r.id));const selected=Object.fromEntries(Object.entries(bindings.value).filter(([r,a])=>ids.has(r)&&a));if(!Object.keys(selected).length){emit('update:modelValue',null);return}emit('update:modelValue',{catalog_sha256:catalog.value.catalog_sha256,bindings:selected,initial_outfits:Object.fromEntries(Object.keys(selected).map(r=>[r,outfits.value[r]])),casting_confirmed:true})}
function actor(role:string,value:string){bindings.value[role]=value;if(value)outfits.value[role]=props.roles.find(r=>r.id===role)?.stageBinding?.outfit_id||catalog.value!.actors.find(a=>a.actor_id===value)!.default_outfit_id!;publish()}
function actors(role:string){const preset=props.roles.find(r=>r.id===role)?.stageBinding;if(preset)return [{actor_id:'',display_name:'画外音'},...((bindings.value[role]||Object.values(bindings.value).filter(Boolean).length<3)?[{actor_id:preset.actor_id,display_name:'出镜'}]:[])];return [{actor_id:'',display_name:'画外音'},...(catalog.value?.actors||[])].filter(a=>!a.actor_id||bindings.value[role]||Object.values(bindings.value).filter(Boolean).length<3)}
async function load(){const token=++sequence;try{catalog.value=await api.h5Catalog(props.modelValue?.catalog_sha256);if(token!==sequence)return;bindings.value={...props.modelValue?.bindings};outfits.value={...props.modelValue?.initial_outfits};sync();for(const entry of [...catalog.value.outfits.filter(o=>Object.values(outfits.value).includes(o.outfit_id)),...catalog.value.actors,...catalog.value.outfits.filter(o=>!Object.values(outfits.value).includes(o.outfit_id))]){if(!entry.thumbnail)continue;const id='outfit_id' in entry?entry.outfit_id:entry.actor_id;const bytes=await stageAsset(entry.thumbnail);if(token!==sequence)return;thumbs.value[id]=URL.createObjectURL(new Blob([bytes],{type:'image/png'}))}}catch(e){error.value=String(e)}}
function sync(){if(!catalog.value)return;for(const key of knownRoles)if(!props.roles.some(r=>r.id===key))knownRoles.delete(key);for(const key of Object.keys(bindings.value))if(!props.roles.some(r=>r.id===key)){delete bindings.value[key];delete outfits.value[key];knownRoles.delete(key)}for(const role of props.roles){if(knownRoles.has(role.id))continue;knownRoles.add(role.id);const preset=role.stageBinding;if(!preset)continue;if(preset.catalog_sha256!==catalog.value.catalog_sha256){error.value='角色素材目录已变化，请重新选择角色后再制作';continue}if(Object.values(bindings.value).filter(Boolean).length<3){bindings.value[role.id]=preset.actor_id;outfits.value[role.id]=preset.outfit_id}}publish()}
watch(()=>props.roles.map(r=>r.id).join('|'),sync);load()
watch(()=>props.modelValue,value=>{bindings.value={...value?.bindings};outfits.value={...value?.initial_outfits}},{deep:true})
onBeforeUnmount(()=>{sequence++;Object.values(thumbs.value).forEach(URL.revokeObjectURL)})
</script>
<template><view class="casting"><text class="field-label">角色出场</text><text class="small-note">最多三位角色出镜，也可以只保留声音。</text><text v-if="error" class="error-copy">{{error}}</text><text v-if="!roles.length" class="small-note spaced">先选择参与故事的角色。</text>
<view v-for="role in roles" :key="role.id" class="cast-row" :class="{'cast-row--preset':role.stageBinding&&bindings[role.id]}"><text class="field-label">{{role.name}}</text><view class="chip-list"><button v-for="a in actors(role.id)" :key="a.actor_id" class="chip" :class="{selected:(bindings[role.id]||'')===a.actor_id}" :disabled="disabled" @tap="actor(role.id,a.actor_id)">{{a.display_name}}</button></view>
<view v-if="bindings[role.id]" class="outfits"><button v-for="o in catalog?.outfits.filter(o=>o.actor_id===bindings[role.id]&&(!role.stageBinding||o.outfit_id===role.stageBinding.outfit_id))" :key="o.outfit_id" :aria-label="`${role.name}：${o.display_name}`" class="outfit" :class="{selected:outfits[role.id]===o.outfit_id}" :disabled="disabled" @tap="outfits[role.id]=o.outfit_id;publish()"><image v-if="thumbs[o.outfit_id]" :src="thumbs[o.outfit_id]" mode="aspectFit"/><text>{{o.display_name}}</text></button></view></view>
</view></template>
<style scoped>.casting{margin:28rpx 0}.cast-row{padding:18rpx 0;border-bottom:1px solid #eadcc9}.outfits{display:flex;gap:12rpx;overflow-x:auto;padding:16rpx 3rpx}.outfit{flex:0 0 136rpx;font-size:21rpx;line-height:1.5;padding:8rpx;border:2rpx solid #eadcc9;border-radius:15rpx}.outfit.selected{border-color:#a8452d;background:#f8e8d9}.outfit image{display:block;width:116rpx;height:130rpx}.outfit text{white-space:normal}
.cast-row--preset { display: grid; grid-template-columns: 72px minmax(0,1fr); grid-template-rows: auto auto; gap: 6px 12px; align-items: center; padding: 16px 0; }
.cast-row--preset > .field-label { grid-column: 2; margin: 0; align-self: end; }
.cast-row--preset > .chip-list { grid-column: 2; align-self: start; gap: 8px; }
.cast-row--preset > .outfits { grid-column: 1; grid-row: 1 / 3; padding: 0; overflow: hidden; }
.cast-row--preset .outfit { flex: 0 0 72px; padding: 4px; }
.cast-row--preset .outfit image { width: 62px; height: 86px; }
.cast-row--preset .outfit text { display: none; }
</style>
