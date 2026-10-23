// Local asset acceptance: validates every sampled vertex and seek determinism.
// Run from repository root after prepare_spine_preview.py; no network calls.
import fs from 'node:fs'
import assert from 'node:assert/strict'
import ts from '../../source/fingertip-legend-app/node_modules/typescript/lib/typescript.js'
const root='runtime/my-legend-spine',read=(name)=>fs.readFileSync(`${root}/${name}`,'utf8')
const load=(js)=>import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'))
const {installLegacyFixes,expandLegacyDeforms}=await load(ts.transpileModule(fs.readFileSync('source/my-legend-stage/src/legacy-fixes.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2020}}).outputText)
const catalog=JSON.parse(read('catalog.json')),spine=(await load(read(`content/${catalog.runtime.sha256}`))).default
installLegacyFixes(spine)
const report=[]
for(const asset of catalog.actors){
  const raw=expandLegacyDeforms(JSON.parse(read(`content/${asset.skeleton.sha256}`)))
  const atlas=new spine.Atlas(read(`content/${asset.atlas.sha256}`),{load:(page,name)=>{
    const png=fs.readFileSync(`${root}/content/${asset.pages[name].sha256}`)
    page.width=png.readUInt32BE(16);page.height=png.readUInt32BE(20)
  },unload:()=>{}})
  const data=new spine.SkeletonJson(new spine.AtlasAttachmentLoader(atlas)).readSkeletonData(raw)
  data.bones.forEach((b,i)=>{b.flipX=raw.bones[i].flipX;b.flipY=raw.bones[i].flipY})
  data.animations.forEach(a=>a.duration=asset.animations[a.name].duration)
  const sk=new spine.Skeleton(data);sk.slots.forEach(s=>s.attachmentVertices=[])
  function sample(animation,t){
    sk.setToSetupPose();animation.apply(sk,-1,t,false,null);sk.updateWorldTransform()
    const result=[]
    for(const slot of sk.drawOrder){
      const attachment=slot.attachment;if(!attachment||!attachment.uvs)continue
      const vertices=new Float32Array(attachment.uvs.length)
      if(attachment.type===0)attachment.computeVertices(sk.x,sk.y,slot.bone,vertices)
      else attachment.computeWorldVertices(sk.x,sk.y,slot,vertices)
      assert(vertices.every(Number.isFinite),`${asset.actor_id}/${animation.name}/${t}/${slot.data.name}`)
      assert(Array.from(attachment.uvs).every(v=>Number.isFinite(v)&&v>=0&&v<=1),'UV outside atlas')
      if(attachment.triangles)assert(attachment.triangles.every(i=>i>=0&&i<vertices.length/2),'invalid triangle')
      result.push([slot.data.name,attachment.name,...vertices])
    }
    return result
  }
  let samples=0
  for(const animation of data.animations){
    const start=sample(animation,0),mid=sample(animation,animation.duration*.37)
    for(let n=0;n<=30;n++){sample(animation,animation.duration*n/30);samples++}
    assert.deepEqual(sample(animation,0),start,'seek to zero changed pose')
    assert.deepEqual(sample(animation,animation.duration*.37),mid,'backward seek changed pose')
  }
  report.push({actor:asset.actor_id,animations:data.animations.length,samples,all_vertices_finite:true,seek_deterministic:true})
}
fs.writeFileSync(`${root}/math-verification.json`,JSON.stringify(report,null,2))
console.log(JSON.stringify(report,null,2))
