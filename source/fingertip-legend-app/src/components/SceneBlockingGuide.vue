<script setup lang="ts">
import type {SceneDefinition} from '../../../my-legend-stage/src/scene-types'
import SvgLabel from './SvgLabel'
defineProps<{scene:SceneDefinition;camera:{x:number;y:number;zoom:number}}>()
</script>

<template>
  <svg v-if="scene.blocking" class="blocking-guide" viewBox="0 0 960 540" role="img" aria-label="场景走位参考：通道、对白站位与物件停靠点">
    <g :transform="`translate(${camera.x} ${camera.y}) scale(${camera.zoom})`">
      <rect :x="scene.blocking.walk_lane[0]*960" :y="scene.blocking.walk_lane[1]*540" :width="(scene.blocking.walk_lane[2]-scene.blocking.walk_lane[0])*960" :height="(scene.blocking.walk_lane[3]-scene.blocking.walk_lane[1])*540" rx="16" fill="#b0f7dc" fill-opacity=".22" stroke="#c4ffe7" stroke-dasharray="7 5"/>
      <g v-for="(point,i) in scene.blocking.dialogue_slots" :key="'slot'+i" class="dialogue">
        <ellipse :cx="point[0]*960" :cy="point[1]*540" rx="40" ry="12"/>
        <SvgLabel :x="point[0]*960" :y="point[1]*540-23">对白 {{i+1}}</SvgLabel>
      </g>
      <g v-for="point in scene.blocking.entrances" :key="point.id" class="entrance">
        <circle :cx="point.position[0]*960" :cy="point.position[1]*540" r="9"/>
        <SvgLabel :x="point.position[0]*960" :y="point.position[1]*540+40">{{point.label}}</SvgLabel>
      </g>
      <g v-for="(point,i) in scene.blocking.targets" :key="point.id" class="target">
        <line :x1="point.position[0]*960" :y1="point.position[1]*540" :x2="point.position[0]*960" :y2="495+(i%2)*22" stroke-dasharray="3 3"/>
        <path :d="`M ${point.position[0]*960} ${point.position[1]*540-7} l 7 7 -7 7 -7 -7 Z`"/>
        <SvgLabel :x="point.position[0]*960" :y="501+(i%2)*22">{{point.label}}</SvgLabel>
      </g>
    </g>
  </svg>
</template>

<style scoped>
.blocking-guide{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}.blocking-guide text{font:13px 'Microsoft YaHei',sans-serif;text-anchor:middle;paint-order:stroke;stroke:#19342e;stroke-width:4px;stroke-linejoin:round;fill:#fffaf0}.dialogue ellipse{fill:#d7fff133;stroke:#defff2;stroke-width:2}.entrance circle{fill:#d7fff1;stroke:#194333;stroke-width:2}.target path{fill:#ffd88a;stroke:#765126;stroke-width:2}.target line{stroke:#ffe0a4;stroke-width:2}
</style>
