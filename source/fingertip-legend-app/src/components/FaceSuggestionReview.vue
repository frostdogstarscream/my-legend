<script setup lang="ts">
import {ref} from 'vue'
import HtmlInput from './HtmlInput'
import type {LifeScript} from '../../../my-legend-stage/src/life-types'
import type {FaceSuggestions} from '../services/face-suggestions'
const props=defineProps<{value:FaceSuggestions;script:LifeScript;stale:boolean}>()
const emit=defineEmits<{(e:'accept',ids:string[]):void;(e:'cancel'):void;(e:'seek',seconds:number):void}>()
const selected=ref<string[]>([]),removed=ref<string[]>([])
const labels={surprised:'惊讶（圆眼）',squint:'挤眼（强调）',neutral:'原生表情'}
const seconds=(f:number)=>Number((f/props.script.sample_rate).toFixed(3))
function choose(id:string,event:Event){selected.value=(event.target as HTMLInputElement).checked?[...selected.value,id]:selected.value.filter(x=>x!==id)}
function remove(id:string){removed.value.push(id);selected.value=selected.value.filter(x=>x!==id)}
</script>
<template>
  <div class="suggestion-review">
    <p>离线规则建议，共 {{value.suggestions.length}} 条。逐条选择后加入草稿；可在下方表情简表调整演员、表情及时间，最后点击“应用并预览”。</p>
    <p v-if="stale" role="alert">草稿或版本已变化，请撤销未应用修改或重新生成建议。</p>
    <p v-if="!value.suggestions.length" class="suggestion-empty">没有足够明确且可绑定到实测语音的表情证据，保持当前演出。</p>
    <div class="scroll" v-if="value.suggestions.length"><table><thead><tr><th>接受</th><th>演员 / 表情 / 时间</th><th>来源与证据</th><th>检查</th></tr></thead><tbody>
      <tr v-for="(s,i) in value.suggestions.filter(s=>!removed.includes(s.face_cue.id))" :key="s.face_cue.id">
        <td><HtmlInput type="checkbox" :aria-label="'接受建议 '+i" :checked="selected.includes(s.face_cue.id)" @change="choose(s.face_cue.id,$event)"/></td>
        <td>{{script.actors.find(a=>a.instance_id===s.face_cue.instance_id)?.name}} · {{labels[s.face_cue.emotion!]}}<br/>{{seconds(s.face_cue.start_frame)}}–{{seconds(s.face_cue.end_frame)}} 秒</td>
        <td>“{{s.evidence.quote}}”<small>{{s.evidence.source.line_id}} · 明确原文证据</small></td>
        <td><button :aria-label="'定位建议 '+i" @click="emit('seek',seconds(s.face_cue.start_frame))">定位</button><button :aria-label="'删除建议 '+i" @click="remove(s.face_cue.id)">删除</button></td>
      </tr>
    </tbody></table></div>
    <button :disabled="stale||!selected.length" @click="emit('accept',selected)">将所选建议加入草稿</button><button @click="emit('cancel')">关闭建议</button>
    <details v-if="value.diagnostics.length"><summary>查看弃权与冲突说明（{{value.diagnostics.length}}）</summary><ul><li v-for="(d,i) in value.diagnostics" :key="i"><strong>{{d.code}}</strong> · {{d.message}}<small v-if="d.quote">{{d.source?.line_id}}：“{{d.quote}}”</small></li></ul></details>
  </div>
</template>
<style scoped>
.suggestion-review p,small{font-size:13px;line-height:1.7}small{display:block;color:#8b6c58}.scroll{overflow:auto}table{width:100%;font-size:13px;border-collapse:collapse}th,td{text-align:left;padding:10px;border-bottom:1px solid #e5d8c7}button{font:inherit;padding:8px;margin:8px 8px 8px 0;border:1px solid #d9c5ad;border-radius:7px;background:#fffdf7;color:#674932;cursor:pointer}button:disabled{opacity:.5}details{margin-top:12px}li{font-size:13px;margin:8px 0}
</style>
