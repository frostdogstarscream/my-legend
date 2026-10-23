<script setup lang="ts">
import {onBeforeUnmount,ref,watch} from 'vue'
import {stageAsset} from '../services/stage-assets'
import {sceneObjectDepth,type SceneCatalog,type SceneObject,type SceneRef} from '../../../my-legend-stage/src/scene-types'
const props=defineProps<{catalog:SceneCatalog;selected?:SceneRef;disabled?:boolean}>()
const emit=defineEmits<{select:[id:string]}>()
const urls=ref<Record<string,string>>({}),error=ref('')
let generation=0
function release(){for(const url of Object.values(urls.value))URL.revokeObjectURL(url);urls.value={}}
watch(()=>props.catalog.catalog_sha256,async()=>{
  const run=++generation;release();error.value=''
  const refs=new Map(props.catalog.scenes.flatMap(s=>s.objects.map(o=>[o.image.sha256,o.image] as const)))
  const result=await Promise.allSettled([...refs].map(async([digest,ref])=>{
    const bytes=await stageAsset(ref)
    if(run===generation)urls.value[digest]=URL.createObjectURL(new Blob([bytes],{type:'image/png'}))
  }))
  if(run===generation&&result.some(r=>r.status==='rejected'))error.value='部分缩略图暂不可用，请重新打开；当前舞台保持不变。'
},{immediate:true})
onBeforeUnmount(()=>{generation++;release()})
function style(o:SceneObject,index:number){
  const sourceSize=o.source_size||[o.width,o.height]
  return {left:(o.position[0]-o.width*o.anchor[0])/9.6+'%',top:(o.position[1]-o.height*o.anchor[1])/5.4+'%',
    width:o.width/9.6+'%',height:o.height/5.4+'%',zIndex:Math.round(sceneObjectDepth(o,index)*100),
    backgroundImage:urls.value[o.image.sha256]?`url("${urls.value[o.image.sha256]}")`:undefined,
    backgroundSize:o.repeat?`${sourceSize[0]*o.tile_scale/o.width*100}% ${sourceSize[1]*o.tile_scale/o.height*100}%`:'100% 100%',
    backgroundRepeat:o.repeat?'repeat':'no-repeat',backgroundColor:o.tint!=='#ffffff'?o.tint:undefined,
    backgroundBlendMode:o.tint!=='#ffffff'?'multiply':undefined}
}
</script>
<template>
  <div class="scene-picker" aria-label="主题场景">
    <button class="scene-card" :class="{selected:!selected}" :disabled="disabled" aria-label="选择原始背景" @click="emit('select','')"><div class="original-room"><span/></div><strong>原始背景</strong><small>保留简洁舞台</small></button>
    <button v-for="scene in catalog.scenes" :key="scene.scene_id" class="scene-card" :class="{selected:selected?.scene_id===scene.scene_id&&selected?.catalog_sha256===catalog.catalog_sha256}" :disabled="disabled" :aria-label="'选择'+scene.display_name+'场景'" @click="emit('select',scene.scene_id)">
      <div class="room-thumbnail" aria-hidden="true"><div v-for="(o,i) in scene.objects" :key="o.id" class="room-object" :style="style(o,i)"/></div><strong>{{scene.display_name}}</strong><small>{{scene.seats.length?'含已校准家具样例':'固定布景 · 站立 / 行走'}}</small>
    </button>
  </div>
  <p v-if="error" class="thumbnail-error">{{error}}</p>
  <details class="theme-index"><summary>查看家具主题来源（{{catalog.themes.length}} 项）</summary><p>固定场景是上方已编排的布景。其他主题保留素材索引；资源关联、完整配套与场景验收分别记录。</p><div class="theme-scroll"><table><thead><tr><th>名称</th><th>名称已查到</th><th>资源已定位</th><th>完整配套已验证</th><th>场景已验收</th></tr></thead><tbody><tr v-for="theme in catalog.themes" :key="theme.source_key"><td :title="theme.note">{{theme.name}}</td><td>是</td><td>{{theme.resources_located?'已定位':'待核对'}}</td><td>{{theme.pairing_verified?'是':'见说明'}}</td><td>{{theme.scene_review==='accepted'?'已确认':'待确认'}}</td></tr></tbody></table></div></details>
</template>
<style scoped>
.scene-picker{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.scene-card{display:block;min-width:0;width:100%;margin:0;padding:7px;border:1px solid #dac7b0;border-radius:11px;background:#fff8ed;text-align:left;cursor:pointer;line-height:1.4}.scene-card.selected{border:2px solid #8b422d;padding:6px;background:#f6e5d4}.scene-card:disabled{opacity:.6;cursor:default}.scene-card strong,.scene-card small{display:block;margin:6px 3px}.scene-card strong{font-size:14px;color:#493a30}.scene-card small{font-size:11px;color:#8b6c58}.room-thumbnail,.original-room{position:relative;width:100%;aspect-ratio:16/9;overflow:hidden;border-radius:5px;isolation:isolate}.room-object{position:absolute}.original-room{background:radial-gradient(ellipse at 50% 25%,#58685a,#243736 70%)}.original-room span{position:absolute;inset:82.5% 0 0;background:#142c2b}.theme-index{margin-top:14px;font-size:13px;color:#795238}.theme-index summary{cursor:pointer}.theme-index p{line-height:1.6}.theme-scroll{max-height:320px;overflow:auto}table{border-collapse:collapse;width:100%;min-width:490px}th,td{padding:8px;text-align:left;border-bottom:1px solid #e5d8c7}th{position:sticky;top:0;background:#f4e7d5}.thumbnail-error{font-size:12px;color:#9f3529}@media(max-width:650px){.scene-picker{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
