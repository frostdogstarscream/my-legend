import {describe,it,expect,vi} from 'vitest'
import {safeDestination,returnAfterLogin} from '../src/utils/login-return'
describe('login destination',()=>{
 it('accepts only local pages and keeps the selected delivery',()=>{
  expect(safeDestination('/pages/flow/index?id=owned-flow')).toBe(true)
  expect(safeDestination('/pages/performance/index?profile=life_v2&deliveryId=version')).toBe(true)
  for(const value of ['https://example.com','//example.com','/pages/login/index','/pages/home/index#bad','/pages/home/index\n',null])expect(safeDestination(value)).toBe(false)
 })
 it('consumes a valid destination exactly once',()=>{
  const storage=new Map([['my-legend:login-return','/pages/flow/index?id=flow']]);const reLaunch=vi.fn()
  vi.stubGlobal('uni',{getStorageSync:(k:string)=>storage.get(k),removeStorageSync:(k:string)=>storage.delete(k),reLaunch})
  returnAfterLogin();expect(reLaunch).toHaveBeenLastCalledWith({url:'/pages/flow/index?id=flow'})
  returnAfterLogin();expect(reLaunch).toHaveBeenLastCalledWith({url:'/pages/home/index'})
  vi.unstubAllGlobals()
 })
})
