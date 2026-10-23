<script setup lang="ts">
import { computed,nextTick,onMounted,onBeforeUnmount,ref,watch } from 'vue'
import { api } from '../services/api'
import { stageAsset,stageAudio } from '../services/stage-assets'
import { playbackArtifacts,releaseProductionMedia } from '../services/production-media'
import { SpineStage } from '../../../my-legend-stage/src/SpineStage'
import { cuesAt,type StageActor,type StageCatalog,type StagePreview } from '../../../my-legend-stage/src/types'
import { formatTime } from '../utils/performance'

const props=defineProps<{productionId?:string}>()
const catalog=ref<StageCatalog>(),preview=ref<StagePreview>(),bindings=ref<StageActor[]>([])
const loading=ref(true),error=ref(''),visualError=ref(''),ready=ref(false),playing=ref(false),time=ref(0),speed=ref(1),loop=ref(true)
const selected=ref(''),action=ref('attack'),audioReady=ref(false),audioDuration=ref(0)
const actor=computed(()=>bindings.value.find(a=>a.role_id===selected.value))
const asset=computed(()=>catalog.value?.actors.find(a=>a.actor_id===actor.value?.actor_id))
const actions=computed(()=>Object.keys(asset.value?.animations||{}))
const duration=computed(()=>audioDuration.value||(asset.value?.animations[action.value]?.duration||1))
const cues=computed(()=>preview.value?cuesAt(preview.value.stage,time.value):[])
const labels:Record<string,string>={attack:'攻击',move:'移动',reload:'换弹',die:'倒下',victory:'胜利',victoryloop:'胜利待机',wait:'待机',s:'技能',s1:'技能'}
function back(){uni.navigateBack({fail:()=>uni.reLaunch({url:'/pages/stories/index'})})}
let stage:SpineStage|undefined,audio:HTMLAudioElement|undefined,mediaUrl='',audioDigest='',frame=0,last=0,disposed=false
function draw(){if(!ready.value)return;try{stage?.renderAt(time.value,cues.value.flatMap(c=>c.role_id?[c.role_id]:[]),{roleId:selected.value,animation:action.value,loop:loop.value})}catch(e){visualError.value=String(e);ready.value=false;stage?.destroy()}}
function pause(){playing.value=false;audio?.pause();draw()}
function seek(value:number){time.value=Math.max(0,Math.min(duration.value,value));if(audio&&audioReady.value)audio.currentTime=time.value;draw()}
async function toggle(){if(playing.value){pause();return}if(time.value>=duration.value)seek(0);if(audio){try{await audio.play()}catch{error.value='浏览器未能播放声音，请再次点击播放';return}}playing.value=true;last=performance.now()}
function chooseRole(id:string){selected.value=id;if(!actions.value.includes(action.value))action.value=actions.value.includes('attack')?'attack':actions.value[0];if(!audio)seek(0);draw()}
function chooseAction(name:string){action.value=name;loop.value=name!=='die';if(!audio)seek(0);draw()}
function tick(now:number){if(disposed)return;if(playing.value){if(audio){time.value=audio.currentTime}else{time.value+=(now-last)/1000*speed.value;if(time.value>=duration.value){if(loop.value)time.value%=duration.value;else{time.value=duration.value;pause()}}}draw()}last=now;frame=requestAnimationFrame(tick)}
watch(speed,rate=>{if(audio)audio.playbackRate=rate})
watch(loop,draw)
function visibility(){if(document.hidden)pause()}
function cleanup(){disposed=true;cancelAnimationFrame(frame);pause();audio?.removeAttribute('src');audio?.load();if(mediaUrl)releaseProductionMedia(mediaUrl);stage?.destroy();document.removeEventListener('visibilitychange',visibility)}
defineExpose({pause})
onMounted(async()=>{
  document.addEventListener('visibilitychange',visibility)
  frame=requestAnimationFrame(tick)
  try{
    // Audio remains independently usable if catalog or GPU loading fails.
    if(props.productionId){
      const playback=await api.independentPlayback(props.productionId)
      const ref=playbackArtifacts(playback,props.productionId).find(a=>a.kind==='AudioMix')
      if(!ref)throw new Error('作品尚未交付声音')
      audioDigest=ref.sha256
      audioDuration.value=playback.manifest!.duration_seconds!
      mediaUrl=await stageAudio(props.productionId,ref)
      if(disposed){releaseProductionMedia(mediaUrl);return}
      audio=new Audio(mediaUrl);audio.preload='auto'
      audio.onended=()=>{time.value=audio!.currentTime;pause()}
      audio.onpause=()=>{playing.value=false}
      audio.onplay=()=>{playing.value=true}
      audio.onerror=()=>{pause();error.value='声音文件播放失败'}
      audioReady.value=true
    }
    try{
      if(props.productionId){
        preview.value=await api.stagePreview(props.productionId)
        if(preview.value.job_id!==props.productionId||preview.value.audio.artifact.sha256!==audioDigest)throw new Error('演绎与声音版本不一致，请重新打开')
        bindings.value=preview.value.stage.actors
      }
      catalog.value=await api.stageCatalog()
      if(disposed)return
      if(preview.value&&catalog.value.catalog_sha256!==preview.value.catalog_sha256)throw new Error('素材目录已变化，请重新打开预览')
      if(!preview.value)bindings.value=catalog.value.actors.map((a,i,all)=>({role_id:a.actor_id,name:a.display_name,actor_id:a.actor_id,actor_version:a.version,x:(i+1)/(all.length+1),animation:a.idle_animation,loop:true}))
      selected.value=bindings.value[0]?.role_id||''
      if(!actions.value.includes(action.value))action.value=actions.value[0]
      await nextTick();if(disposed)return
      const host=document.getElementById('spine-stage-host');if(!host)throw new Error('舞台尚未就绪')
      stage=new SpineStage();await stage.mount(host,catalog.value,bindings.value,stageAsset)
      if(disposed)return
      ready.value=true;draw()
      if(!audio){playing.value=true;last=performance.now()}
    }catch(e){visualError.value=e instanceof Error?e.message:String(e)}
  }catch(e){error.value=e instanceof Error?e.message:String(e)}finally{loading.value=false}
})
onBeforeUnmount(cleanup)
</script>

<template>
  <view class="spine-page">
    <view class="heading"><button aria-label="返回" @tap="back">‹ 返回</button><text class="eyebrow">我的传说 · 动作演绎</text></view>
    <view class="intro"><text class="title">{{preview?.title||'战斗动作试演'}}</text><text class="description">{{productionId?'已有配音 · 动作试听联动':'选择演员，查看素材中的原始战斗动作'}}</text></view>
    <view class="theatre">
      <view class="stage-grid"/><view class="stage-floor"/>
      <view id="spine-stage-host" class="stage-host"/>
      <text class="stage-label">{{labels[action]||action}} · {{actor?.name||'准备演员'}}</text>
      <view v-if="loading" class="stage-message">正在准备演员与舞台…</view>
      <view v-else-if="visualError" class="stage-message">{{visualError}}<text v-if="audioReady">声音仍可正常播放</text></view>
      <view class="stage-names"><text v-for="a in bindings" :key="a.role_id" :class="{active:a.role_id===selected}">{{a.name}}</text></view>
    </view>
    <view v-if="error" role="alert" class="error">{{error}}</view>
    <view class="control-panel">
      <text class="section-label">出场演员</text>
      <view class="chips"><button v-for="a in bindings" :key="a.role_id" :class="{selected:a.role_id===selected}" @tap="chooseRole(a.role_id)">{{a.name}}</button></view>
      <text class="section-label">动作</text>
      <view class="chips actions"><button v-for="name in actions" :key="name" :class="{selected:name===action}" @tap="chooseAction(name)">{{labels[name]||name}}</button></view>
      <view v-if="preview" class="subtitle"><text v-for="(cue,i) in cues" :key="i">{{cue.speaker}}：{{cue.text}}</text><text v-if="!cues.length">等待下一句对白…</text></view>
      <view class="timeline"><slider :value="time" :max="Math.max(duration,.01)" :step=".01" :block-size="16" activeColor="#b8432e" @changing="seek($event.detail.value)" @change="seek($event.detail.value)"/><text>{{formatTime(time)}} / {{formatTime(duration)}}</text></view>
      <view class="transport"><button class="secondary" @tap="seek(0)">重播</button><button class="play" :disabled="loading||(!ready&&!audioReady)" @tap="toggle">{{playing?'暂停':'播放'}}</button><button class="secondary" @tap="loop=!loop">{{loop?'动作循环':'动作单次'}}</button></view>
      <view class="speed"><text>播放速度</text><button v-for="rate in [.5,1,1.5,2]" :key="rate" :class="{selected:speed===rate}" @tap="speed=rate">{{rate}}×</button></view>
      <text class="footnote">{{preview?'选中的动作随音频时间播放，拖动进度可同步定位；动作由你选择。':'点击动作立即切换；倒下默认播放一次，其余动作可循环。'}} 本轮不含口型。</text>
    </view>
  </view>
</template>

<style scoped>
.spine-page{min-height:100vh;background:#f5eddf;color:#493a30;padding:24px 18px 40px;box-sizing:border-box}.heading{display:flex;align-items:center;justify-content:space-between;max-width:960px;margin:auto}.heading button{font-size:14px;color:#89422c;margin:0;padding:4px 10px}.eyebrow{font-size:12px;letter-spacing:2px;color:#956f59}.intro{max-width:960px;margin:24px auto 20px}.title{display:block;font-size:28px;font-weight:700;line-height:1.4}.description{display:block;margin-top:8px;color:#947562;font-size:14px}.theatre{position:relative;max-width:960px;aspect-ratio:16/9;margin:auto;overflow:hidden;border-radius:22px;background:radial-gradient(ellipse at 50% 30%,#455754 0%,#243736 60%,#182728 100%);box-shadow:0 12px 24px #50331d20;border:1px solid #8f9c891f}.stage-grid{position:absolute;inset:0;opacity:.12;background-image:linear-gradient(#afc4b6 1px,transparent 1px),linear-gradient(90deg,#afc4b6 1px,transparent 1px);background-size:40px 40px;mask-image:linear-gradient(transparent,#000)}.stage-floor{position:absolute;left:0;right:0;top:82%;height:18%;border-top:1px solid #a2af8b44;background:#15262680}.stage-host{position:absolute;inset:0}.stage-label{position:absolute;left:16px;top:14px;font-size:12px;color:#e1dfbb;letter-spacing:1px}.stage-names{position:absolute;bottom:10px;left:12.5%;right:12.5%;display:flex;justify-content:space-around;color:#adbcad;font-size:12px}.stage-names text{flex:1;text-align:center}.stage-names .active{color:#ffe2a1}.stage-message{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;flex-direction:column;color:#f1e8cd;font-size:14px;padding:30px;text-align:center;background:#182728aa}.stage-message text{margin-top:10px}.control-panel{max-width:920px;margin:20px auto 0;border:1px solid #e5d8c7;border-radius:20px;background:#fffaf1;padding:20px}.section-label{font-size:12px;color:#9c7760;display:block;margin-bottom:10px}.chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:20px}.chips button,.speed button{margin:0;line-height:1.5;font-size:14px;padding:9px 18px;border:1px solid #e6d8c6;border-radius:10px;color:#695140;background:#fffdf7}.chips .selected{background:#813f2d;color:#fff3dd;border-color:#813f2d}.actions button{padding:8px 16px}.subtitle{min-height:54px;display:flex;flex-direction:column;justify-content:center;padding:14px;border-radius:12px;background:#f2e7d5;font-size:15px;line-height:1.7}.timeline{display:flex;align-items:center;gap:10px}.timeline slider{flex:1;margin-left:0}.timeline text{font-size:12px;font-variant-numeric:tabular-nums}.transport{display:flex;align-items:center;justify-content:center;gap:18px;margin:12px 0 22px}.transport button{margin:0;line-height:1.6;border-radius:12px;padding:11px 22px;font-size:14px}.play{background:#b8432e;color:white;min-width:120px}.secondary{background:#efe4d3;color:#79543d}.speed{display:flex;align-items:center;justify-content:center;gap:8px;font-size:12px;color:#997861}.speed button{font-size:12px;padding:5px 9px}.speed .selected{border-color:#b8432e;color:#b8432e}.footnote{display:block;font-size:12px;color:#a18671;line-height:1.8;margin-top:20px;text-align:center}.error{max-width:920px;margin:12px auto;background:#fae3dc;color:#9f3529;padding:14px;border-radius:12px}@media(max-width:600px){.spine-page{padding:16px 12px 30px}.title{font-size:24px}.intro{margin:22px 4px 18px}.theatre{border-radius:14px}.control-panel{padding:16px}.stage-label{font-size:10px;top:10px;left:12px}.stage-names{font-size:10px;bottom:6px}.transport{gap:10px}.transport button{padding:10px 16px}.chips button{padding:8px 12px}.heading button{padding-left:0}.eyebrow{letter-spacing:1px}}
</style>
<style scoped>
.transport button{white-space:nowrap;flex-shrink:0}.transport .secondary{padding:11px 14px}.transport .play{min-width:100px}
</style>
