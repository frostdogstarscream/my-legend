<script setup lang="ts">
import {ref,onMounted,onBeforeUnmount} from 'vue'
import {onHide,onShow} from '@dcloudio/uni-app'
import {api} from '../services/api'
import {stageAsset} from '../services/stage-assets'
import {LifeStage} from '../../../my-legend-stage/src/LifeStage'
import type {LifeBinding} from '../../../my-legend-stage/src/life-types'
const props=defineProps<{binding:{catalog_sha256:string;actor_id:string;outfit_id:string}}>()
const host=ref<HTMLElement>(),error=ref(''),ready=ref(false),playing=ref(true),action=ref('idle'),actions=ref<string[]>([]),facing=ref<1|-1>(1)
const labels:Record<string,string>={idle:'待机',walk:'行走',sit:'坐姿',pick:'被提起',lie:'躺卧',victory:'胜利',victoryloop:'胜利循环'}
let stage:LifeStage|undefined,binding:LifeBinding,raf=0,start=0,elapsed=0,disposed=false,visible=true
function draw(now:number){if(disposed)return;if(ready.value&&visible){if(playing.value){elapsed+=(now-start)/1000}start=now;stage?.render({sample_frame:0,actors:[{instance_id:'preview',position:[.5,.86],facing:facing.value,visible:true,outfit_id:binding.initial_outfit_id,action:action.value,time:elapsed,mode:'loop',speaking:false}],camera:{target_instance_id:null,zoom:1,x:.5}})}else start=now;raf=requestAnimationFrame(draw)}
function choose(value:string){action.value=value;elapsed=0;playing.value=true}
onHide(()=>{visible=false});onShow(()=>{visible=true;start=performance.now()})
onMounted(async()=>{try{const catalog=await api.h5Catalog(props.binding.catalog_sha256),actor=catalog.actors.find(a=>a.actor_id===props.binding.actor_id)!,outfit=catalog.outfits.find(o=>o.outfit_id===props.binding.outfit_id)!
 const durations=Object.fromEntries(Object.entries(actor.action_map).filter(([name])=>outfit.allowed_actions.includes(name)).map(([name,raw])=>[name,Math.max(...Object.values(outfit.parts).map(id=>catalog.skeletons.find(s=>s.asset_id===id)!.animations[raw].duration))]))
 binding={instance_id:'preview',role_id:'preview',name:actor.display_name,actor_id:actor.actor_id,actor_revision:actor.revision,initial_outfit_id:outfit.outfit_id,position:[.5,.86],facing:1,visible:true,actions:durations};actions.value=Object.keys(durations)
 if(disposed)return;stage=new LifeStage();await stage.mount(host.value!,catalog,[binding],stageAsset);if(disposed)return;ready.value=true;start=performance.now();raf=requestAnimationFrame(draw)
 }catch(e){error.value=e instanceof Error?e.message:String(e)}})
onBeforeUnmount(()=>{disposed=true;cancelAnimationFrame(raf);stage?.destroy()})
</script>
<template><view class="paper-panel section-gap"><text class="section-title serif">生活动作</text><text class="small-note spaced">选择动作查看这个角色的原生演出。</text><text v-if="error" class="error-copy">{{error}}</text><text v-else-if="!ready" class="small-note">正在准备角色…</text><div ref="host" class="native-stage"/><view class="chip-list"><button v-for="item in actions" :key="item" class="chip" :class="{selected:action===item}" @tap="choose(item)">{{labels[item]||item}}</button></view><view v-if="ready" class="chip-list spaced"><button class="outline-button" @tap="playing=!playing">{{playing?'暂停动作':'播放动作'}}</button><button class="outline-button" @tap="facing=facing===1?-1:1">转身</button></view><text class="small-note spaced">在故事舞台的“添加动作”中，可安排这些动作的时间和位置。</text></view></template>
<style scoped>.native-stage{width:100%;aspect-ratio:16/9;background:linear-gradient(#e8dfcd,#fff8e9);border-radius:14px;overflow:hidden;margin:12px 0}.spaced{display:block;margin-top:12px}.chip-list.spaced{display:flex}</style>
