import { describe, expect, it } from 'vitest'
import { normalizeRoleIds } from '../src/utils/roles'
describe('角色目录迁移',()=>{
  it('旧演示角色与新选择重复时合并为相同人物',()=>{
    expect(normalizeRoleIds(['demo-char-001','demo-char-002','legend-moyu','legend-lingmo'],['legend-moyu','legend-lingmo'],true)).toEqual(['legend-moyu','legend-lingmo'])
  })
  it('不可用角色不会藏在表单里被提交',()=>{
    expect(normalizeRoleIds(['unavailable','legend-moyu'],['legend-moyu'],true)).toEqual(['legend-moyu'])
  })
  it('其他后台不使用本机角色别名',()=>{
    expect(normalizeRoleIds(['demo-char-001'],['demo-char-001'])).toEqual(['demo-char-001'])
  })
})
