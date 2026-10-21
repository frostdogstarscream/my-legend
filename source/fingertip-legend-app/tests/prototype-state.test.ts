import { afterEach,beforeEach,describe,expect,it,vi } from 'vitest'
import { cueIndexAt,clampTime,formatTime } from '../src/utils/performance'
const storage=new Map<string,unknown>()
beforeEach(()=>{vi.resetModules();storage.clear();vi.stubGlobal('uni',{getStorageSync:(key:string)=>storage.has(key)?JSON.parse(JSON.stringify(storage.get(key))):undefined,setStorageSync:(key:string,value:unknown)=>storage.set(key,JSON.parse(JSON.stringify(value)))});vi.useFakeTimers();vi.setSystemTime(new Date('2026-10-15T00:00:00Z'))})
afterEach(()=>vi.useRealTimers())
describe('原型状态与历史数据',()=>{
  it('旧版草稿迁移后保留内容，新增演绎样例只出现一次',async()=>{storage.set('zj-shell:demo-stories:v1',[{id:'old-draft',title:'用户原草稿',idea:'保留原内容',genre:'悬疑推理',status:'draft'}]);const repo=await import('../src/services/story-repository');expect(repo.getStory('old-draft').idea).toBe('保留原内容');repo.saveStory({...repo.getStory('old-draft'),title:'继续编辑'});expect(repo.readStories().filter(s=>s.id==='demo-performance-001')).toHaveLength(1);expect(repo.getStory('old-draft').title).toBe('继续编辑')})
  it('离开进度页再回来，任务沿用原 ID 并按已用时间完成',async()=>{const repo=await import('../src/services/story-repository');const task=repo.startDemoTask('demo-draft-001');expect(repo.startDemoTask('demo-draft-001').id).toBe(task.id);vi.advanceTimersByTime(4000);expect(repo.getDemoTask(task.id).progress).toBe(50);vi.advanceTimersByTime(4100);expect(repo.getDemoTask(task.id).status).toBe('succeeded');expect(repo.getStory('demo-draft-001').chapters?.[0].source).toBe('demo')})
  it('失败保留用户灵感，重试创建新任务',async()=>{const repo=await import('../src/services/story-repository');const original=repo.getStory('demo-draft-001').idea;const failed=repo.startDemoTask('demo-draft-001',true);vi.advanceTimersByTime(8100);expect(repo.getDemoTask(failed.id).status).toBe('failed');expect(repo.getStory('demo-draft-001').idea).toBe(original);expect(repo.startDemoTask('demo-draft-001').id).not.toBe(failed.id)})
  it('重复确认同一商品不会再次扣减演示余额',async()=>{const wallet=await import('../src/services/demo-wallet');expect(wallet.redeemDemoProduct('demo-sku-001').balance).toBe(950);expect(wallet.redeemDemoProduct('demo-sku-001').balance).toBe(950);expect(wallet.readDemoWallet().ownedProductIds).toEqual(['demo-sku-001'])})
  it('余额不足时不写入权益',async()=>{storage.set('my-legend:demo-wallet:v1',{balance:10,ownedProductIds:[]});const wallet=await import('../src/services/demo-wallet');expect(()=>wallet.redeemDemoProduct('demo-sku-001')).toThrow('灵感币不足');expect(wallet.readDemoWallet()).toEqual({balance:10,ownedProductIds:[]})})
})
describe('播放器时间轴',()=>{
  const cues=[{speakerId:'a',text:'a',start:0,end:10.8},{speakerId:'b',text:'b',start:10.8,end:22.4},{speakerId:'c',text:'c',start:22.4,end:34.1}]
  it('分段边界准确切换人物，总时长仍归属最后一句',()=>{expect(cueIndexAt(cues,10.799)).toBe(0);expect(cueIndexAt(cues,10.8)).toBe(1);expect(cueIndexAt(cues,22.4)).toBe(2);expect(cueIndexAt(cues,34.1)).toBe(2)})
  it('非法和越界进度不会传播为 NaN 或超出媒体时长',()=>{expect(clampTime(NaN,34.1)).toBe(0);expect(clampTime(-4,34.1)).toBe(0);expect(clampTime(55,34.1)).toBe(34.1)})
  it('显示分钟与小数秒，舍入跨分钟时正确进位',()=>{expect(formatTime(28)).toBe('00:28.0');expect(formatTime(64.1)).toBe('01:04.1');expect(formatTime(59.99)).toBe('01:00.0')})
})
