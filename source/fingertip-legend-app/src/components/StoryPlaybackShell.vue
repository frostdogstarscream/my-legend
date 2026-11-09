<script setup lang="ts">
import {ref,onMounted,onBeforeUnmount} from 'vue'
import BrandLogo from './BrandLogo.vue'
import AppIcon from './AppIcon.vue'
import AppTabbar from './AppTabbar.vue'
import AppSheet from './AppSheet.vue'
import HtmlButton from './HtmlButton.vue'
import HtmlInput from './HtmlInput.vue'
defineProps<{title:string;room:string;roles:{id:string;name:string;portrait?:string;offstage:boolean}[];speaker:string;roleId:string|null;text:string;time:number;duration:number;playing:boolean;ready:boolean;loading:boolean;error:string;speed:number;muted:boolean;subtitles:boolean;autoNext:boolean}>()
const emit=defineEmits<{toggle:[];seek:[seconds:number];step:[direction:number];role:[id:string];speed:[rate:number];mute:[];subtitles:[];autoNext:[];work:[];edit:[]}>()
const settings=ref(false),fullscreen=ref(false)
const fmt=(v:number)=>Math.floor(v/60)+':'+(v%60).toFixed(1).padStart(4,'0')
function key(event:KeyboardEvent){if(event.key==='Escape')fullscreen.value=false}
onMounted(()=>window.addEventListener('keydown',key));onBeforeUnmount(()=>window.removeEventListener('keydown',key))
</script>
<template>
 <div class="story-playback" :class="{'is-fullscreen':fullscreen}">
  <div class="brand-row"><BrandLogo variant="performance"/><span class="season-stamp">秋记</span></div>
  <section class="cast-panel paper">
   <HtmlButton class="work-heading" aria-label="查看作品与版本" @click="emit('work')"><AppIcon name="book-open" :size="25"/><span class="work-copy"><strong class="serif">{{title||'正在读取作品'}} · 剧情演绎</strong><small>剧本 · 声音 · 舞台作品</small></span><AppIcon name="chevron-right" :size="18"/></HtmlButton>
   <div class="cast-label"><b>本章故事角色</b><small>{{roles.length}} 位角色</small></div>
   <div class="cast-grid"><HtmlButton v-for="role in roles" :key="role.id" class="cast-card" :class="{selected:roleId===role.id}" :aria-label="`定位${role.name}的台词`" :disabled="!ready" @click="emit('role',role.id)"><img v-if="role.portrait" :src="role.portrait" :alt="role.name"/><span v-else class="portrait-fallback">{{role.name.slice(0,1)}}</span><b>{{role.name}}<small v-if="role.offstage"> · 画外音</small></b></HtmlButton></div>
  </section>
  <p v-if="loading" class="status-copy">正在读取作品与舞台素材…</p><p v-if="error" class="error-copy paper" role="alert">{{error}}</p>
  <div class="stage-panel"><slot name="stage"/><span class="room-label">{{room}}</span><HtmlButton class="stage-fullscreen" :aria-label="fullscreen?'退出全屏':'全屏查看'" @click="fullscreen=!fullscreen"><AppIcon :name="fullscreen?'minimize':'maximize'" :size="22" :light="true"/></HtmlButton><div v-if="!ready" class="stage-loading">{{error?'舞台暂未加载完成':loading?'正在准备舞台…':'暂无可播放舞台'}}</div><p v-if="fullscreen&&subtitles" class="fullscreen-subtitle">{{speaker}}{{text?'：'+text:'当前无对白'}}</p></div>
  <section class="dialogue-panel paper">
   <div class="dialogue-heading"><b>{{speaker||'旁白'}}</b><small>当前对白</small></div>
   <div class="dialogue-row"><img v-if="roles.find(r=>r.id===roleId)?.portrait" class="speaker-portrait" :src="roles.find(r=>r.id===roleId)?.portrait" :alt="speaker"/><span v-else class="speaker-placeholder">{{(speaker||'旁白').slice(0,1)}}</span><p class="dialogue-bubble">{{subtitles?(text||'静听片刻，故事正在继续。'):'字幕已关闭'}}</p></div>
   <div class="playback-section"><HtmlButton class="sound-row" :disabled="!ready" :aria-pressed="!muted" @click="emit('mute')"><AppIcon name="volume-2" :size="18"/><b>声音</b><small>{{muted?'关闭':'开启'}}</small></HtmlButton>
    <div class="time-row"><HtmlInput id="h5-seek" type="range" min="0" :max="Math.max(duration,.01)" step=".01" :value="time" :disabled="!ready" aria-label="剧情播放进度" @input="emit('seek',Number(($event.target as HTMLInputElement).value))"/><span>{{fmt(time)}} / {{fmt(duration)}}</span></div>
    <div class="playback-controls"><HtmlButton aria-label="上一句" :disabled="!ready" @click="emit('step',-1)"><span class="control-pill"><AppIcon name="skip-back" :size="21"/></span><span>上一句</span></HtmlButton><HtmlButton id="h5-play" :aria-label="playing?'暂停播放':'开始播放'" :disabled="!ready" @click="emit('toggle')"><span class="play-circle"><AppIcon :name="playing?'pause':'play'" :size="27" :light="true"/></span><span>{{playing?'暂停播放':'开始播放'}}</span></HtmlButton><HtmlButton aria-label="下一句" :disabled="!ready" @click="emit('step',1)"><span class="control-pill"><AppIcon name="skip-forward" :size="21"/></span><span>下一句</span></HtmlButton><HtmlButton aria-label="播放设置" @click="settings=true"><span class="settings-circle"><AppIcon name="sliders-horizontal" :size="22"/></span><span>播放设置</span></HtmlButton></div>
   </div>
   <div class="work-actions"><HtmlButton @click="emit('work')">剧本与作品版本</HtmlButton><HtmlButton @click="emit('edit')">编辑舞台</HtmlButton></div>
  </section>
  <div class="app-navigation"><AppTabbar active="stories" tone="warm"/></div>
  <AppSheet title="播放设置" :open="settings" @close="settings=false"><label class="speed-setting">播放倍速<select :value="speed" aria-label="播放倍速" @change="emit('speed',Number(($event.target as HTMLSelectElement).value))"><option :value=".5">½ 倍速</option><option :value="1">正常速度</option><option :value="1.5">1.5 倍速</option></select></label><HtmlButton class="setting-row" :aria-pressed="subtitles" @click="emit('subtitles')">显示当前对白<span>{{subtitles?'开启':'关闭'}}</span></HtmlButton><HtmlButton class="setting-row" :aria-pressed="autoNext" @click="emit('autoNext')">自动进入下一句<span>{{autoNext?'开启':'关闭'}}</span></HtmlButton></AppSheet>
 </div>
</template>
<style scoped>
*{box-sizing:border-box}.story-playback{position:relative;max-width:460px;min-height:100vh;margin:auto;padding:10px 8px calc(100px + env(safe-area-inset-bottom));color:#8e2820;background:#7d3828 url('/static/figma/home-background.png') center/cover;font:14px/1.5 'Microsoft YaHei',sans-serif}.brand-row{display:flex;justify-content:space-between;align-items:center;padding:0 12px 10px}.season-stamp{border:1px solid #d8bd89;border-radius:3px;color:#fff0cf;background:#8e282070;padding:5px 7px;writing-mode:vertical-rl;transform:rotate(5deg)}.paper{border:1px solid #efd2b8;border-radius:21px;background:#fff4e5f5;overflow:hidden}.native-button{font:inherit;color:inherit;margin:0;border:0;background:none;cursor:pointer;line-height:1.5}.native-button:disabled{opacity:.45;cursor:default}.native-button:focus-visible,select:focus-visible,input:focus-visible{outline:3px solid #b56026;outline-offset:2px}.work-heading{display:flex;align-items:center;gap:10px;text-align:left;width:100%;padding:12px;border-bottom:1px solid #ebcbb0}.work-copy{flex:1;min-width:0}.work-copy strong{display:block;font-size:16px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.work-copy small{display:block;color:#9e7e64;font-size:11px}.cast-label{display:flex;justify-content:space-between;padding:8px 12px 5px}.cast-label small{color:#a7866c}.cast-grid{display:flex;gap:8px;padding:0 10px 10px;overflow-x:auto}.cast-card{flex:1;min-width:76px;max-width:150px;border:1px solid #ead0b9;border-radius:11px;background:#fff8ed;overflow:hidden;padding:0}.cast-card.selected{box-shadow:inset 0 0 0 2px #b44834}.cast-card img,.portrait-fallback{display:block;width:100%;height:57px;object-fit:contain}.portrait-fallback{font:30px/57px serif;background:#f1dfca}.cast-card b{display:block;border-top:1px solid #ecd8c0;font-size:12px}.cast-card small{font-size:10px;font-weight:400}.stage-panel{position:relative;aspect-ratio:16/9;border:1px solid #ebcbb0;border-radius:13px;overflow:hidden;margin:10px 0;background:#211a16}.stage-panel :deep(.stage-host){position:absolute;inset:0}.room-label{position:absolute;left:8px;top:7px;padding:2px 6px;background:#211a1680;color:#fff6e7;border-radius:5px;font-size:10px;pointer-events:none}.stage-fullscreen{position:absolute;right:8px;top:8px;display:flex;justify-content:center;align-items:center;width:38px;height:38px;background:#793b28cf;border:1px solid #efc89e;border-radius:50%;z-index:2}.stage-loading{position:absolute;inset:0;display:grid;place-items:center;color:#fff0dc}.dialogue-panel{padding:12px 10px;min-height:270px;display:flex;flex-direction:column}.dialogue-heading{display:flex;justify-content:space-between;padding-left:65px;margin-bottom:8px}.dialogue-heading small{color:#b18168;font-size:11px}.dialogue-row{display:flex;gap:10px;align-items:flex-start}.speaker-portrait,.speaker-placeholder{width:55px;height:65px;object-fit:contain;background:#f4e3ce;border:1px solid #e6c4a3;border-radius:10px;flex-shrink:0}.speaker-placeholder{font:28px/65px serif;text-align:center}.dialogue-bubble{flex:1;min-width:0;margin:0;min-height:70px;padding:10px 12px;background:#fffdf6;border:1px solid #ebcbb0;border-radius:12px;font-size:14px;font-weight:700;color:#793b28;overflow-wrap:anywhere}.playback-section{padding-top:25px;margin-top:auto}.sound-row{display:flex;align-items:center;gap:8px;width:100%;text-align:left;background:#fffbf2;border:1px solid #ead0b9;border-radius:9px;padding:7px 10px}.sound-row small{color:#b18168}.time-row{display:flex;align-items:center;gap:8px;margin:8px 0}.time-row input{flex:1;min-width:0;accent-color:#bc4d31}.time-row>span{font-size:10px;white-space:nowrap;color:#b18168}.playback-controls{display:flex;gap:8px;align-items:center;justify-content:space-between}.playback-controls>.native-button{display:flex;flex:1;min-width:0;flex-direction:column;align-items:center;gap:7px;padding:6px 0;font-size:12px}.control-pill{width:58px;height:27px;border:1px solid #edc4a5;border-radius:99px;background:#f8e7d5;display:grid;place-items:center}.play-circle{width:42px;height:42px;display:grid;place-items:center;background:#c65438;border:3px solid #fff2dd;border-radius:50%;box-shadow:0 0 0 3px #edccb4}.settings-circle{width:30px;height:30px;display:grid;place-items:center;background:#fffaf0;border:1px solid #edc4a5;border-radius:50%}.work-actions{display:flex;justify-content:space-between;border-top:1px solid #ead0b9;margin-top:10px;padding-top:6px;font-size:12px}.work-actions .native-button{padding:7px}.speed-setting{display:flex;justify-content:space-between;gap:15px;color:#793b28}.speed-setting select{padding:8px;background:#fff;border:1px solid #d6b899;border-radius:8px;color:#793b28}.setting-row{display:flex;width:100%;justify-content:space-between;padding:18px 0;border-bottom:1px solid #eadcc9}.status-copy,.error-copy{padding:12px;background:#fff4e5}.is-fullscreen{position:fixed;inset:0;z-index:80;max-width:none;padding:0;background:#171310;display:flex;flex-direction:column;justify-content:center}.is-fullscreen .brand-row,.is-fullscreen .cast-panel,.is-fullscreen .app-navigation,.is-fullscreen .dialogue-heading,.is-fullscreen .dialogue-row,.is-fullscreen .sound-row,.is-fullscreen .work-actions{display:none}.is-fullscreen .stage-panel{flex:1;min-height:0;aspect-ratio:auto;margin:0;border:0;border-radius:0}.is-fullscreen .stage-panel :deep(.stage-host){inset:0;width:100%;height:100%;display:flex;align-items:center;justify-content:center}.is-fullscreen :deep(.stage-host canvas){width:auto!important;height:auto!important;max-width:100%;max-height:100%;aspect-ratio:16/9;object-fit:contain}.is-fullscreen .dialogue-panel{flex-shrink:0;min-height:0;border-radius:0;background:#fff4e5;padding:5px 16px}.is-fullscreen .playback-section{padding:0}.is-fullscreen .playback-controls{max-width:500px;margin:auto}.fullscreen-subtitle{position:absolute;bottom:8px;left:5%;right:5%;text-align:center;color:white;background:#211a1699;padding:6px;font-size:14px;pointer-events:none}@media(max-width:340px){.story-playback{padding-left:5px;padding-right:5px}.work-copy strong{font-size:14px}.dialogue-bubble{font-size:13px;padding:8px}.playback-controls{gap:3px}.control-pill{width:48px}.dialogue-panel{min-height:260px}}
</style>
<style scoped>
.time-row .native-input{flex:1;width:100%;min-width:0;accent-color:#bc4d31}.cast-card{max-width:none}.story-playback :deep(.tabbar){max-width:460px}
.is-fullscreen{min-height:0;height:100vh}.is-fullscreen :deep(.stage-host > *){width:100%!important;height:100%!important;object-fit:contain}
@media(orientation:portrait){.is-fullscreen{inset:auto;left:50%;top:50%;width:100vh;height:100vw;transform:translate(-50%,-50%) rotate(90deg)}}
</style>

<style scoped>
.story-playback { max-width: var(--app-width); min-height: 100dvh; display: flex; flex-direction: column; padding: 8px 10px calc(var(--tabbar-height) + 10px); }
.brand-row { padding: 0 8px 8px; }
.cast-panel { flex-shrink: 0; }
.work-heading { min-height: 56px; padding: 8px 12px; }
.cast-label { padding: 6px 12px 4px; font-size: 12px; }
.cast-grid { padding-bottom: 8px; }
.cast-card img, .portrait-fallback { height: 44px; }
.portrait-fallback { line-height: 44px; }
.cast-card b { line-height: 23px; }
.stage-panel { flex: 0 0 auto; width: 100%; margin: 10px 0; }
.stage-fullscreen { width: 44px; height: 44px; }
.dialogue-panel { flex: 1; min-height: 0; padding: 10px; }
.dialogue-heading { margin-bottom: 6px; padding-left: 54px; }
.speaker-portrait, .speaker-placeholder { width: 44px; height: 54px; }
.speaker-placeholder { line-height: 54px; }
.dialogue-bubble { min-height: 60px; max-height: 112px; overflow-y: auto; font-size: 14px; line-height: 1.6; padding: 8px 10px; }
.playback-section { padding-top: 10px; }
.sound-row { min-height: 44px; padding: 6px 10px; }
.time-row { margin: 0; min-height: 32px; }
.time-row .native-input { height: 32px; }
.time-row > span { font-size: 11px; }
.playback-controls > .native-button { min-height: 64px; padding: 2px 0; gap: 2px; }
.work-actions { margin-top: 6px; padding-top: 0; }
.work-actions .native-button { min-height: 44px; }
.story-playback :deep(.tabbar) { max-width: var(--app-width); }
.is-fullscreen { max-width: none; min-height: 0; padding: 0; }
.is-fullscreen .stage-panel { flex: 1; margin: 0; }
.is-fullscreen .dialogue-panel { flex: 0 0 auto; padding: 4px 16px; }
.is-fullscreen .playback-section { padding: 0; }
@media (max-width: 359px) { .story-playback { padding-left: 8px; padding-right: 8px; } .cast-card { display: flex; align-items: center; min-width: 110px; padding: 4px; } .cast-card img, .portrait-fallback { width: 40px; height: 40px; } .cast-card b { flex: 1; border: 0; } .brand-row { padding-bottom: 4px; } .work-copy strong { font-size: 14px; } }
</style>
