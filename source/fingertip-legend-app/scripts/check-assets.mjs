import { readFile, readdir, stat } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import path from 'node:path'
const root = path.resolve(import.meta.dirname, '..')
const assetRoot = path.join(root, 'src', 'static', 'figma')
const files = await readdir(assetRoot)
const result = []
for (const file of files) {
  const full = path.join(assetRoot, file), info = await stat(full)
  if (!info.size) throw new Error(`Empty asset: ${file}`)
  const data = await readFile(full)
  const entry = { file, bytes: info.size, sha256: createHash('sha256').update(data).digest('hex') }
  if (file.endsWith('.svg')) {
    const opening = data.toString('utf8').match(/<svg\b[^>]*>/)?.[0]
    if (!opening) throw new Error(`Invalid SVG: ${file}`)
    entry.width = opening.match(/\bwidth="([^"]+)"/)?.[1]
    entry.height = opening.match(/\bheight="([^"]+)"/)?.[1]
    if (!entry.width || !entry.height) throw new Error(`Missing SVG dimensions: ${file}`)
  }
  result.push(entry)
}
for (const name of ['legend-serif', 'legend-brush', 'legend-sans']) {
  const data = await readFile(path.join(root, 'src', 'static', 'fonts', `${name}.ttf`))
  if (data.subarray(0,4).toString('hex') !== '00010000') throw new Error(`Expected TrueType font: ${name}`)
}
for (const folder of ['performance','icons']) {
  for (const name of await readdir(path.join(root,'src','static',folder))) {
    if (!/\.(png|svg)$/.test(name)) continue
    const full=path.join(root,'src','static',folder,name), data=await readFile(full)
    if (!data.length) throw new Error(`Empty asset: ${folder}/${name}`)
    if(name.endsWith('.svg')&&!data.toString('utf8').includes('<svg'))throw new Error(`Invalid SVG: ${name}`)
    if(name.endsWith('.png')&&data.subarray(0,8).toString('hex')!=='89504e470d0a1a0a')throw new Error(`Invalid PNG: ${name}`)
    result.push({file:folder+'/'+name,bytes:data.length,sha256:createHash('sha256').update(data).digest('hex')})
  }
}
console.log(JSON.stringify({ name:'我的传说', source: 'https://www.figma.com/design/HFFS73txWzLko3LrkXr83v?node-id=5-124', assets: result, count: result.length }, null, 2))
