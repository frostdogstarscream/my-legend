// The pinned 2.1.25 JS runtime references an undefined `boneIndex` in both
// flip timelines. Our single-frame sampler always starts from setup pose,
// so selecting the last key at or before t gives the exact discrete state.
export function installLegacyFixes(core:any) {
  for(const [type,property] of [['FlipXTimeline','flipX'],['FlipYTimeline','flipY']]) {
    core[type].prototype.apply=function(skeleton:any,_last:number,time:number) {
      const keys=this.frames
      for(let i=keys.length-2;i>=0;i-=2){
        if(time>=keys[i]){skeleton.bones[this.boneIndex][property]=!!keys[i+1];return}
      }
    }
  }
}

// Legacy JS leaves unspecified FFD elements as array holes (Java initializes
// them to zero). Expand sparse offsets before that reader adds mesh vertices.
export function expandLegacyDeforms(raw:any) {
  for(const animation of Object.values(raw.animations) as any[]) {
    for(const [skinName,slots] of Object.entries(animation.ffd||{}) as any[]) {
      for(const [slotName,meshes] of Object.entries(slots) as any[]) {
        for(const [name,frames] of Object.entries(meshes) as any[]) {
          const attachment=raw.skins[skinName][slotName][name]
          let count=attachment.vertices.length
          if(attachment.type==='skinnedmesh'){
            count=0
            for(let i=0;i<attachment.vertices.length;){const n=attachment.vertices[i];count+=n*2;i+=1+n*4}
          }
          for(const frame of frames){
            const expanded=new Array(count).fill(0),offset=frame.offset||0
            for(let i=0;i<(frame.vertices||[]).length;i++)expanded[offset+i]=frame.vertices[i]
            frame.vertices=expanded;frame.offset=0
          }
        }
      }
    }
  }
  return raw
}
