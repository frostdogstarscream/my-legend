import { describe, expect, it } from 'vitest'
import { progressNote, stageStatus, requiresPlanRevision } from '../src/utils/production'
import type { IndependentJob } from '../src/types/production'

const job=(status:IndependentJob['status'])=>({status} as IndependentJob)
describe('制作失败与暂停的状态',()=>{
  it('失败后依赖阶段不会显示仍在排队或自动继续',()=>{
    expect(stageStatus(job('failed'),'queued')).toBe('等待上游修复')
    expect(stageStatus(job('failed'),'succeeded')).toBe('制作完成')
    expect(progressNote(job('failed'))).not.toContain('后台会继续')
  })
  it('运行、人工处理和取消保持不同含义',()=>{
    expect(stageStatus(job('running'),'queued')).toBe('排队中')
    expect(stageStatus(job('needs_input'),'queued')).toBe('等待处理')
    expect(stageStatus(job('cancelled'),'queued')).toBe('未继续制作')
    expect(progressNote(job('running'))).toContain('后台会继续')
    expect(progressNote(job('needs_input'))).not.toContain('后台会继续')
    expect(progressNote(job('succeeded'))).toContain('制作已结束')
  })
})

 it('已耗尽修复的方案错误不能直接重发生成',()=>{
  expect(requiresPlanRevision('ARRANGEMENT_EMPTY_AUDIO_INTERVAL')).toBe(true)
  expect(requiresPlanRevision('AUDIO_CHOICES_REPAIR_FAILED')).toBe(true)
  expect(requiresPlanRevision('SOUND_BRIDGE_CLASSIFICATION_FAILED')).toBe(true)
  expect(requiresPlanRevision('SOUND_BRIDGE_PACKAGE_INVALID')).toBe(true)
  expect(requiresPlanRevision('STORY_DRAFT_REPAIR_CONTRACT_INVALID')).toBe(true)
  expect(requiresPlanRevision('EXTERNAL_RESULT_UNKNOWN')).toBe(false)
  expect(requiresPlanRevision(null)).toBe(false)
 })
