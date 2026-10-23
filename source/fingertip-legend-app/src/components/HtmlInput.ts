// H5-only controls: uni-app's input adapter does not implement range, checkbox or file.
import {defineComponent,h} from 'vue'
export default defineComponent({
  inheritAttrs:false,
  props:{modelValue:{type:[String,Number,Boolean],default:undefined},modelModifiers:{type:Object,default:()=>({})},tag:{type:String,default:'input'}},
  emits:['update:modelValue','input','change'],
  setup(props,{attrs,emit}){
    function update(event:Event,kind:'input'|'change'){
      const input=event.target as HTMLInputElement
      const value=input.type==='checkbox'?input.checked:props.modelModifiers.number?Number(input.value):input.value
      emit('update:modelValue',value);emit(kind,event)
    }
    return ()=>h(props.tag,{...attrs,
      ...(attrs.type==='checkbox'?{checked:props.modelValue??attrs.checked}:{value:props.modelValue??attrs.value}),
      onInput:(e:Event)=>update(e,'input'),onChange:(e:Event)=>update(e,'change')})
  },
})
