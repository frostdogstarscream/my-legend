<script setup lang="ts">
import HtmlInput from './HtmlInput'
import type {LifeScript} from '../../../my-legend-stage/src/life-types'
import type {FaceProfile} from '../../../my-legend-stage/src/basic-face'
import type {DirectionTrack,DirectionCue,FaceTrack,FaceCue} from '../../../my-legend-stage/src/performance-tracks'
const props=defineProps<{script:LifeScript;direction?:DirectionTrack;faceTrack?:FaceTrack;profiles?:FaceProfile[];time:number}>()
const emit=defineEmits<{(e:'update:direction',v:DirectionTrack):void;(e:'update:faceTrack',v:FaceTrack):void}>()
const modes:Record<DirectionCue['mode'],string>={target:'面向谁',left:'朝左',right:'朝右',hold:'保持',away:'背对谁',turn:'转身'}
const clone=<T,>(v:T):T=>JSON.parse(JSON.stringify(v))
const seconds=(frame:number)=>Number((frame/props.script.sample_rate).toFixed(4))
const value=(event:Event)=>(event.target as HTMLInputElement).value
function editDirection(index:number,change:Partial<DirectionCue>){const t=clone(props.direction!);t.cues[index]={...t.cues[index],...change,rule_id:'manual'};emit('update:direction',t)}
function mode(index:number,m:DirectionCue['mode']){const c=props.direction!.cues[index];editDirection(index,{mode:m,target_instance_id:['target','away'].includes(m)?props.script.actors.find(a=>a.instance_id!==c.instance_id)?.instance_id||null:null})}
function actor(index:number,iid:string){const c=props.direction!.cues[index];editDirection(index,{instance_id:iid,target_instance_id:['target','away'].includes(c.mode)?props.script.actors.find(a=>a.instance_id!==iid)?.instance_id||null:null})}
function bounds(){const start=Math.min(props.script.total_frames-1,Math.max(0,Math.round(props.time*props.script.sample_rate)));return {start_frame:start,end_frame:Math.min(props.script.total_frames,start+2*props.script.sample_rate)}}
function addDirection(){const t=clone(props.direction!);t.cues.push({id:'direction_'+crypto.randomUUID(),instance_id:props.script.actors[0].instance_id,...bounds(),enabled:true,
  mode:props.script.actors.length>1?'target':'hold',target_instance_id:props.script.actors[1]?.instance_id||null,source:null,source_text:'人工修正',rule_id:'manual'});emit('update:direction',t)}
function removeDirection(index:number){const t=clone(props.direction!);t.cues.splice(index,1);emit('update:direction',t)}
const profile=(iid:string)=>props.profiles?.find(p=>p.actor_id===props.script.actors.find(a=>a.instance_id===iid)?.actor_id)
const capable=()=>props.script.actors.filter(a=>profile(a.instance_id)?.eyes)
function editFace(index:number,change:Partial<FaceCue>){const t=clone(props.faceTrack!);t.cues[index]={...t.cues[index],...change};emit('update:faceTrack',t)}
function addFace(type:FaceCue['type']){const a=capable()[0];if(!a)return;const t=clone(props.faceTrack!);const b=bounds();if(type==='blink')b.end_frame=Math.min(props.script.total_frames,b.start_frame+Math.round(.12*props.script.sample_rate));t.cues.push({id:'face_'+crypto.randomUUID(),instance_id:a.instance_id,type,...b,enabled:true,emotion:type==='emotion'?'neutral':null});emit('update:faceTrack',t)}
function removeFace(index:number){const t=clone(props.faceTrack!);t.cues.splice(index,1);emit('update:faceTrack',t)}
</script>
<template>
  <section v-if="direction" class="track-panel">
    <h2>双人对白关系与朝向轨道</h2><p>两名可见演员默认互相面向。人工修正优先；行走、离场使用移动方向；其他时间执行剧本朝向。“保持”冻结区间开始前的方向，“转身”翻转该方向。</p>
    <button @click="addDirection">添加朝向修正</button>
    <div class="scroll"><table><thead><tr><th>启用 / 来源</th><th>演员</th><th>朝向</th><th>起止时间（秒）</th><th>操作</th></tr></thead><tbody>
      <tr v-for="(c,i) in direction.cues" :key="c.id">
        <td><HtmlInput type="checkbox" :checked="c.enabled" :aria-label="'启用朝向 '+i" @change="editDirection(i,{enabled:($event.target as HTMLInputElement).checked})"/><small>{{c.rule_id==='manual'?'人工修正':'剧本指令'}} · {{c.source_text}}</small></td>
        <td><select :value="c.instance_id" :aria-label="'朝向演员 '+i" @change="actor(i,value($event))"><option v-for="a in script.actors" :key="a.instance_id" :value="a.instance_id">{{a.name}}</option></select></td>
        <td><select :value="c.mode" :aria-label="'朝向方式 '+i" @change="mode(i,value($event) as DirectionCue['mode'])"><option v-for="(label,key) in modes" :key="key" :value="key">{{label}}</option></select><select v-if="c.mode==='target'||c.mode==='away'" :value="c.target_instance_id" :aria-label="'面向目标 '+i" @change="editDirection(i,{target_instance_id:value($event)})"><option v-for="a in script.actors.filter(a=>a.instance_id!==c.instance_id)" :key="a.instance_id" :value="a.instance_id">{{a.name}}</option></select></td>
        <td><HtmlInput type="number" min="0" step=".01" :aria-label="'朝向开始 '+i" :value="seconds(c.start_frame)" @change="editDirection(i,{start_frame:Math.round(Number(value($event))*script.sample_rate)})"/><HtmlInput type="number" min="0" step=".01" :aria-label="'朝向结束 '+i" :value="seconds(c.end_frame)" @change="editDirection(i,{end_frame:Math.round(Number(value($event))*script.sample_rate)})"/></td>
        <td><button :aria-label="'删除朝向 '+i" @click="removeDirection(i)">删除</button></td>
      </tr><tr v-if="!direction.cues.length"><td colspan="5">当前没有朝向覆盖，使用默认对白关系与移动方向。</td></tr>
    </tbody></table></div>
  </section>
  <section v-if="faceTrack" class="track-panel">
    <h2>表情与手工 FaceTrack</h2><p>表情已按实际附件逐角色核对；“挤眼”不自动解释为高兴。手工眨眼、原生眨眼及程序眨眼均优先于表情，结束后恢复当前表情。躺卧、拾取继续保留原生面部。</p>
    <button :disabled="!capable().length" @click="addFace('emotion')">添加表情</button><button :disabled="!capable().length" @click="addFace('blink')">添加眨眼</button>
    <div class="scroll"><table><thead><tr><th>启用</th><th>演员 / 类型</th><th>表情</th><th>起止时间（秒）</th><th>操作</th></tr></thead><tbody>
      <tr v-for="(c,i) in faceTrack.cues" :key="c.id">
        <td><HtmlInput type="checkbox" :checked="c.enabled" :aria-label="'启用面部 '+i" @change="editFace(i,{enabled:($event.target as HTMLInputElement).checked})"/></td>
        <td><select :value="c.instance_id" :aria-label="'面部演员 '+i" @change="editFace(i,{instance_id:value($event),emotion:c.type==='emotion'?'neutral':null})"><option v-for="a in capable()" :key="a.instance_id" :value="a.instance_id">{{a.name}}</option></select><small>{{c.type==='emotion'?'表情':'手工闭眼区间'}}</small></td>
        <td><select v-if="c.type==='emotion'" :value="c.emotion" :aria-label="'表情语义 '+i" @change="editFace(i,{emotion:value($event) as FaceCue['emotion']})"><option value="neutral">原生表情</option><option v-for="(e,key) in profile(c.instance_id)?.emotions" :key="key" :value="key">{{e?.label}}</option></select><span v-else>闭眼，结束后恢复</span></td>
        <td><HtmlInput type="number" min="0" step=".01" :aria-label="'面部开始 '+i" :value="seconds(c.start_frame)" @change="editFace(i,{start_frame:Math.round(Number(value($event))*script.sample_rate)})"/><HtmlInput type="number" min="0" step=".01" :aria-label="'面部结束 '+i" :value="seconds(c.end_frame)" @change="editFace(i,{end_frame:Math.round(Number(value($event))*script.sample_rate)})"/></td>
        <td><button :aria-label="'删除面部 '+i" @click="removeFace(i)">删除</button></td>
      </tr><tr v-if="!faceTrack.cues.length"><td colspan="5">尚无手工表情；基础口型与眨眼继续生效。</td></tr>
    </tbody></table></div>
    <p>修改轨道后点击“应用并预览”生成新版本。同一演员的同类区间不能重叠，表情与眨眼可以重叠。</p>
  </section>
</template>
<style scoped>
.track-panel{box-sizing:border-box;max-width:1100px;margin:0 auto 20px;padding:20px;border:1px solid #e5d8c7;border-radius:16px;background:#fffaf1;color:#493a30}.track-panel h2{font-size:17px;margin:0 0 12px}.track-panel p,small{font-size:12px;line-height:1.7;color:#8b6c58}small{display:block}button,select,input{font:inherit;border:1px solid #d9c5ad;border-radius:7px;padding:7px;background:#fffdf7;color:#674932;font-size:13px}button{cursor:pointer;margin-right:8px}button:disabled{opacity:.5}.scroll{overflow:auto;margin-top:14px}table{width:100%;border-collapse:collapse;text-align:left;font-size:13px}td,th{padding:10px;border-bottom:1px solid #e5d8c7;vertical-align:top}td input[type=number]{display:block;width:110px;margin-bottom:4px}select{max-width:170px;display:block;margin-bottom:5px}
</style>
