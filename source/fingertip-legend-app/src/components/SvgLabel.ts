import {defineComponent,h} from 'vue'

// The uni-app template compiler rewrites <text> into uni-text, including inside
// SVG. Render a native SVG text node so labels remain visible and scalable.
export default defineComponent({
  inheritAttrs:false,
  setup(_,{attrs,slots}){return ()=>h('text',{
    ...attrs,style:{font:"13px 'Microsoft YaHei',sans-serif",textAnchor:'middle',
      paintOrder:'stroke',stroke:'#19342e',strokeWidth:4,strokeLinejoin:'round',fill:'#fffaf0'},
  },slots.default?.())},
})
